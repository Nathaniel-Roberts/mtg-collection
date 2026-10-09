"""MCP server: the collection and deck tools, served at /mcp over Streamable HTTP.

Authentication sits in front of the transport as ASGI middleware. A request passes with
a verified Cloudflare Access JWT (a signed-in person or a service token) or with the
app-issued bearer token; see ARCHITECTURE.md section 10 for why both exist.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import catalogue, collection, db, decks, rules, suggest
from app.auth import ACCESS_COOKIE, ACCESS_HEADER, AccessError, AccessVerifier, Identity
from app.config import Settings
from app.pricing import fx
from app.pricing.fx import Rates
from app.pricing.snapshots import card_price_history

log = logging.getLogger(__name__)

INSTRUCTIONS = """This server exposes one person's paper Magic: The Gathering collection.

- search_collection and collection_summary cover cards they own; search_cards covers every
  Magic card in the local Scryfall catalogue and marks how many copies are owned.
- Prices are Scryfall's daily values: USD (TCGplayer), EUR (Cardmarket) and an AUD
  conversion at the stored exchange rate. They refresh once a day.
- Decks are saved in the same database and appear in the web app immediately. Ownership
  matches on the card, not the printing: any owned printing satisfies a deck slot.
- validate_deck is rule-based (deck size, copy limits, commander and colour identity,
  format legality from Scryfall). Check rulings yourself for unusual cards.
- suggest_from_collection is a heuristic ranking (colour identity, EDHREC rank, theme
  words, role classification), not a judgement of card quality.
- deck_conflicts lists cards that more decks need than copies owned; decks are lists,
  not reservations, so this is a warning rather than a block.
Tools cost nothing to call; there is no external API behind them.
"""

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


# --- auth --------------------------------------------------------------------------------------


class MCPAuthError(Exception):
    pass


def _bearer_ok(headers: Mapping[str, str], settings: Settings) -> bool:
    if not settings.mcp_bearer_token:
        return False
    auth = headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip(), settings.mcp_bearer_token)


def resolve_mcp_identity(
    headers: Mapping[str, str], settings: Settings, verifier: AccessVerifier
) -> Identity:
    """Who may use the MCP endpoint. Raises MCPAuthError otherwise."""
    if _bearer_ok(headers, settings):
        return Identity(email=None, common_name="mcp-bearer", via="bearer")
    token = headers.get(ACCESS_HEADER.lower()) or headers.get(ACCESS_HEADER)
    if not token:
        for part in (headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == ACCESS_COOKIE and value:
                token = value
    if token and verifier.enabled:
        try:
            return verifier.verify(token)
        except AccessError as exc:
            raise MCPAuthError(str(exc)) from exc
    if settings.dev_mode:
        return Identity(email=settings.dev_user_email, via="dev")
    raise MCPAuthError("Not authenticated: send a Cloudflare Access token or the MCP bearer token")


class MCPAuthMiddleware:
    """Rejects unauthenticated requests before they reach the MCP transport."""

    def __init__(self, app: ASGIApp, settings: Settings, verifier: AccessVerifier) -> None:
        self.app = app
        self.settings = settings
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        try:
            identity = resolve_mcp_identity(headers, self.settings, self.verifier)
        except MCPAuthError as exc:
            response = JSONResponse({"error": "unauthorized", "detail": str(exc)}, status_code=401)
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["mcp_identity"] = identity
        await self.app(scope, receive, send)


# --- helpers -----------------------------------------------------------------------------------


def mcp_card(view: dict[str, Any]) -> dict[str, Any]:
    """The trimmed card shape every tool returns."""
    legal = sorted(
        k for k, v in (view.get("legalities") or {}).items() if v in ("legal", "restricted")
    )
    return {
        "id": view["id"],
        "oracle_id": view.get("oracle_id"),
        "name": view["name"],
        "set_code": view["set_code"],
        "set_name": view.get("set_name"),
        "collector_number": view["collector_number"],
        "rarity": view.get("rarity"),
        "type_line": view.get("type_line"),
        "mana_cost": view.get("mana_cost"),
        "cmc": view.get("cmc"),
        "colors": view.get("colors"),
        "color_identity": view.get("color_identity"),
        "oracle_text": view.get("oracle_text"),
        "keywords": view.get("keywords"),
        "power": view.get("power"),
        "toughness": view.get("toughness"),
        "loyalty": view.get("loyalty"),
        "legal_in": legal,
        "prices": {
            "usd": view["prices"].get("usd"),
            "usd_foil": view["prices"].get("usd_foil"),
            "aud": view["prices"].get("aud"),
            "aud_foil": view["prices"].get("aud_foil"),
        },
        "edhrec_rank": view.get("edhrec_rank"),
        "owned_quantity": view.get("owned_quantity", 0),
        "image": view.get("image_normal"),
        "scryfall_uri": view.get("scryfall_uri"),
    }


def _deck_out(conn: sqlite3.Connection, deck_id: int, rates: Rates) -> dict[str, Any]:
    deck = decks.get(conn, deck_id, rates)
    if deck is None:
        raise ToolError(f"No deck with id {deck_id}")
    cards = {
        role: [{**mcp_card(c), "quantity": c["quantity"], "role": role} for c in items]
        for role, items in deck["cards"].items()
        if items
    }
    return {
        "id": deck["id"],
        "name": deck["name"],
        "format": deck["format"],
        "format_name": deck["format_name"],
        "description": deck["description"],
        "archived": deck["archived"],
        "updated_at": deck["updated_at"],
        "counts": deck["counts"],
        "total": deck["total"],
        "value": deck["value"],
        "cards": cards,
        "validation": deck["validation"],
    }


def _resolve_card(
    conn: sqlite3.Connection, ref: str, index: catalogue.NameIndex, prefer_owned: bool = True
) -> sqlite3.Row:
    """A card id, or a name (fuzzy), to a printing row."""
    row = catalogue.get_card(conn, ref) if len(ref) == 36 and ref.count("-") == 4 else None
    if row is None:
        row = decks.resolve_name(conn, ref, index, prefer_owned=prefer_owned)
    if row is None:
        raise ToolError(f"Could not find a card matching {ref!r}")
    return row


# --- server ------------------------------------------------------------------------------------


def build_mcp_server(
    settings: Settings, name_index: catalogue.NameIndex | None = None, version: str = ""
) -> MCPServer:
    index = name_index or catalogue.NameIndex()
    server = MCPServer("mtg-collection", instructions=INSTRUCTIONS, version=version)

    def connect() -> sqlite3.Connection:
        return db.connect(settings.db_path)

    async def run(fn: Callable[[sqlite3.Connection, Rates], Any]) -> Any:
        def work() -> Any:
            conn = connect()
            try:
                return fn(conn, fx.current_rates(conn, settings))
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    def _page(limit: int, offset: int) -> tuple[int, int]:
        limit = max(1, min(int(limit), 200))
        return limit, max(0, int(offset)) // limit + 1

    @server.tool(annotations=READ)
    async def search_collection(
        query: str | None = None,
        colors: str | None = None,
        color_identity: str | None = None,
        type: str | None = None,  # noqa: A002 - tool parameter name
        set: str | None = None,  # noqa: A002
        rarity: str | None = None,
        format: str | None = None,  # noqa: A002
        tag: str | None = None,
        min_cmc: float | None = None,
        max_cmc: float | None = None,
        sort: str = "name",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the cards you own. Returns one row per owned printing, finish and condition.

        query matches name, type line and rules text. colors is an exact colour set (e.g. "UG");
        color_identity keeps cards whose identity fits within those colours (for a commander
        deck). format keeps cards legal in that format (commander, modern, pauper...). sort is
        one of name, value, edhrec, added, released, cmc. offset is rounded down to a multiple
        of limit.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            per_page, page = _page(limit, offset)
            rows, total = collection.list_entries(
                conn,
                q=query,
                colors=colors,
                identity=color_identity,
                type_line=type,
                set_code=set,
                rarity=rarity,
                legal_in=format,
                tag=tag,
                cmc_min=min_cmc,
                cmc_max=max_cmc,
                sort=sort,
                page=page,
                per_page=per_page,
            )
            items = []
            for r in rows:
                e = collection.entry_view(r, rates)
                items.append(
                    {
                        **mcp_card({**e["card"], "owned_quantity": None}),
                        "owned_quantity": e["quantity"],
                        "finish": e["finish"],
                        "condition": e["condition"],
                        "language": e["language"],
                        "tags": e["tags"],
                        "value_aud": e["value"]["aud"],
                        "value_usd": e["value"]["usd"],
                    }
                )
            return {"total": total, "items": items}

        return await run(go)

    @server.tool(annotations=READ)
    async def search_cards(
        query: str | None = None,
        colors: str | None = None,
        color_identity: str | None = None,
        type: str | None = None,  # noqa: A002
        set: str | None = None,  # noqa: A002
        rarity: str | None = None,
        format: str | None = None,  # noqa: A002
        min_cmc: float | None = None,
        max_cmc: float | None = None,
        owned_only: bool = False,
        unique: str = "cards",
        sort: str = "name",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search every Magic card in the catalogue (paper printings). owned_quantity says how
        many copies of that card, in any printing, are owned. unique is "cards" (one row per
        card) or "prints" (every printing). Filters and sort as in search_collection; sort
        also accepts usd.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            per_page, page = _page(limit, offset)
            rows, total = catalogue.search(
                conn,
                q=query,
                colors=colors,
                identity=color_identity,
                type_line=type,
                set_code=set,
                rarity=rarity,
                cmc_min=min_cmc,
                cmc_max=max_cmc,
                legal_in=format,
                owned=owned_only,
                unique=unique,
                sort=sort,
                page=page,
                per_page=per_page,
            )
            return {
                "total": total,
                "items": [mcp_card(catalogue.card_view(r, rates)) for r in rows],
            }

        return await run(go)

    @server.tool(annotations=READ)
    async def get_card(
        card_id: str | None = None,
        name: str | None = None,
        set_code: str | None = None,
        collector_number: str | None = None,
    ) -> dict[str, Any]:
        """One card in full: rules text, every owned printing with finish and condition, and
        30 days of price history. Give a card_id, a name (misspellings tolerated), or set_code
        plus collector_number.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            row = None
            if card_id:
                row = catalogue.get_card(conn, card_id)
            elif set_code and collector_number:
                row = catalogue.by_set_and_number(conn, set_code, collector_number)
            elif name:
                row = catalogue.fuzzy_name(conn, index, name, set_code)
            else:
                raise ToolError("Give card_id, name, or set_code and collector_number")
            if row is None:
                raise ToolError("No such card")
            view = catalogue.card_view(row, rates)
            out = mcp_card(view)
            out["printings_owned"] = (
                collection.owned_printings(conn, row["oracle_id"]) if row["oracle_id"] else []
            )
            out["price_history_30d"] = [
                {"day": p["day"], "usd": p["usd"], "usd_foil": p["usd_foil"]}
                for p in card_price_history(conn, row["id"], 30)
            ]
            out["printings_count"] = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE oracle_id = ? AND paper = 1", (row["oracle_id"],)
            ).fetchone()[0]
            return out

        return await run(go)

    @server.tool(annotations=READ)
    async def collection_summary(group_by: list[str] | None = None) -> dict[str, Any]:
        """Totals (cards, copies, value in USD, EUR and AUD with the rate used) and breakdowns.
        group_by may list any of color_identity, type, set, rarity, finish, condition, language;
        all of them by default. Includes the 20 most valuable rows.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            s = collection.summary(conn, rates, group_by)
            s["top_cards"] = [
                {
                    "name": t["card"]["name"],
                    "set_code": t["card"]["set_code"],
                    "finish": t["finish"],
                    "quantity": t["quantity"],
                    "value_aud": t["value"]["aud"],
                    "value_usd": t["value"]["usd"],
                }
                for t in s["top_cards"]
            ]
            return s

        return await run(go)

    @server.tool(annotations=READ)
    async def list_decks(include_archived: bool = False) -> dict[str, Any]:
        """Every saved deck with its format, card count and the share of cards owned."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            return {"items": decks.list_all(conn, include_archived=include_archived)}

        return await run(go)

    @server.tool(annotations=READ)
    async def get_deck(deck_id: int) -> dict[str, Any]:
        """A deck with its cards grouped by role (commander, companion, main, sideboard,
        maybeboard), owned quantities, totals and the current validation report."""
        return await run(lambda conn, rates: _deck_out(conn, deck_id, rates))

    @server.tool(annotations=WRITE)
    async def create_deck(
        name: str,
        format: str,
        description: str | None = None,  # noqa: A002
        commander: str | None = None,
        cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a deck. format is a Scryfall format key (commander, modern, standard, pioneer,
        legacy, vintage, pauper, paupercommander, brawl, standardbrawl, oathbreaker, duel).
        commander is a card id or name. cards is a list of {card, quantity, role} where card is
        an id or name and role defaults to main. Owned printings are preferred when a name is
        given. Returns the deck as get_deck does, plus any names that could not be resolved.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            try:
                deck_id = decks.create(
                    conn, name=name, format_key=format, description=description, created_by="mcp"
                )
            except decks.DeckError as exc:
                raise ToolError(str(exc)) from exc
            unresolved = _add(
                conn,
                deck_id,
                ([{"card": commander, "quantity": 1, "role": "commander"}] if commander else [])
                + (cards or []),
            )
            out = _deck_out(conn, deck_id, rates)
            out["unresolved"] = unresolved
            return out

        return await run(go)

    def _add(
        conn: sqlite3.Connection,
        deck_id: int,
        items: list[dict[str, Any]],
        prefer_owned: bool = True,
    ) -> list[str]:
        unresolved: list[str] = []
        resolved: list[dict[str, Any]] = []
        for item in items:
            ref = str(item.get("card") or item.get("card_id") or item.get("name") or "").strip()
            if not ref:
                continue
            try:
                row = _resolve_card(conn, ref, index, prefer_owned)
            except ToolError:
                unresolved.append(ref)
                continue
            resolved.append(
                {
                    "card_id": row["id"],
                    "quantity": int(item.get("quantity", 1)),
                    "role": item.get("role") or "main",
                }
            )
        if resolved:
            try:
                decks.set_cards(conn, deck_id, resolved, mode="add")
            except decks.DeckError as exc:
                raise ToolError(str(exc)) from exc
        return unresolved

    @server.tool(annotations=WRITE)
    async def update_deck(
        deck_id: int,
        name: str | None = None,
        format: str | None = None,  # noqa: A002
        description: str | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Rename a deck, change its format or description, or archive it."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            try:
                ok = decks.update(
                    conn,
                    deck_id,
                    name=name,
                    format_key=format,
                    description=description,
                    archived=archived,
                )
            except decks.DeckError as exc:
                raise ToolError(str(exc)) from exc
            if not ok:
                raise ToolError(f"No deck with id {deck_id}")
            return _deck_out(conn, deck_id, rates)

        return await run(go)

    @server.tool(annotations=WRITE)
    async def add_cards_to_deck(
        deck_id: int, cards: list[dict[str, Any]], prefer_owned: bool = True
    ) -> dict[str, Any]:
        """Add cards: a list of {card, quantity, role}. card is a card id or a name; quantity
        defaults to 1; role is main (default), commander, companion, sideboard or maybeboard.
        Quantities add to what is already there. Returns the deck plus unresolved names.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            if not decks.exists(conn, deck_id):
                raise ToolError(f"No deck with id {deck_id}")
            unresolved = _add(conn, deck_id, cards, prefer_owned)
            out = _deck_out(conn, deck_id, rates)
            out["unresolved"] = unresolved
            return out

        return await run(go)

    @server.tool(annotations=WRITE)
    async def remove_cards_from_deck(deck_id: int, cards: list[dict[str, Any]]) -> dict[str, Any]:
        """Remove cards: a list of {card, quantity, role}. Without quantity every copy goes;
        without role the card is removed from every role."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            if not decks.exists(conn, deck_id):
                raise ToolError(f"No deck with id {deck_id}")
            deck = decks.get(conn, deck_id, rates, with_validation=False)
            in_deck = {c["id"]: c for items in deck["cards"].values() for c in items}  # type: ignore[index]
            by_name = {c["name"].lower(): c["id"] for c in in_deck.values()}
            items = []
            for item in cards:
                ref = str(item.get("card") or item.get("card_id") or item.get("name") or "").strip()
                cid = ref if ref in in_deck else by_name.get(ref.lower())
                if cid is None:
                    row = decks.resolve_name(conn, ref, index, prefer_owned=False)
                    cid = by_name.get(row["name"].lower()) if row else None
                if cid is None:
                    continue
                items.append(
                    {"card_id": cid, "quantity": item.get("quantity"), "role": item.get("role")}
                )
            decks.remove_cards(conn, deck_id, items)
            return _deck_out(conn, deck_id, rates)

        return await run(go)

    @server.tool(annotations=READ)
    async def validate_deck(deck_id: int) -> dict[str, Any]:
        """Format legality (deck size, copy limits, commander, colour identity, banned and
        not-legal cards) plus an owned-versus-missing report with the estimated cost of the
        missing cards and which other decks share them."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            deck = decks.get(conn, deck_id, rates)
            if deck is None:
                raise ToolError(f"No deck with id {deck_id}")
            return deck["validation"]

        return await run(go)

    @server.tool(annotations=READ)
    async def suggest_from_collection(
        commander: str | None = None,
        color_identity: str | None = None,
        theme: str | None = None,
        format: str = "commander",
        exclude_deck_id: int | None = None,
        limit: int = 40,  # noqa: A002
    ) -> dict[str, Any]:
        """Owned cards that fit a commander (id or name) or a colour identity, ranked by theme
        words, overlap with the commander's text, EDHREC rank and role (ramp, draw, removal,
        wipes, counters). Heuristic, not a quality judgement. exclude_deck_id skips cards already
        in that deck.
        """

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            if format not in rules.FORMATS:
                raise ToolError(f"Unknown format {format!r}")
            commander_id = (
                _resolve_card(conn, commander, index, prefer_owned=True)["id"]
                if commander
                else None
            )
            if commander_id is None and not color_identity:
                raise ToolError("Give a commander or a color_identity")
            return suggest.suggest(
                conn,
                rates,
                commander_id=commander_id,
                color_identity=color_identity,
                theme=theme,
                format_key=format,
                exclude_deck_id=exclude_deck_id,
                limit=max(1, min(int(limit), 200)),
            )

        return await run(go)

    @server.tool(annotations=READ)
    async def deck_conflicts(include_acknowledged: bool = False) -> dict[str, Any]:
        """Cards that active decks need more copies of than are owned, with the decks involved."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            items = decks.conflicts(conn, rates, include_acknowledged=include_acknowledged)
            return {
                "items": [
                    {**c, "card": mcp_card(c["card"]) if "name" in c["card"] else c["card"]}
                    for c in items
                ]
            }

        return await run(go)

    @server.tool(annotations=DESTRUCTIVE)
    async def delete_deck(deck_id: int) -> dict[str, Any]:
        """Delete a deck permanently. Prefer update_deck with archived=true unless asked to delete."""

        def go(conn: sqlite3.Connection, rates: Rates) -> dict[str, Any]:
            if not decks.delete(conn, deck_id):
                raise ToolError(f"No deck with id {deck_id}")
            return {"deleted": True, "deck_id": deck_id}

        return await run(go)

    return server


def mcp_asgi_app(server: MCPServer, settings: Settings, verifier: AccessVerifier) -> ASGIApp:
    """The /mcp endpoint: auth middleware in front of the Streamable HTTP transport.

    ``streamable_http_app`` is called for its side effect of creating the session manager
    (stateless, JSON responses); the host app routes ``StreamableHTTPASGIApp`` directly so
    there is no trailing-slash redirect.
    """
    server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    inner = StreamableHTTPASGIApp(server.session_manager)
    return MCPAuthMiddleware(inner, settings, verifier)
