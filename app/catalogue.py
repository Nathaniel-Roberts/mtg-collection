"""Catalogue queries: search, autocomplete, printings, card views."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from rapidfuzz import fuzz, process

from app.pricing.fx import Rates

CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")
FINISHES = ("nonfoil", "foil", "etched")
SORTS = {
    "name": "c.name COLLATE NOCASE ASC, c.released_at DESC",
    "released": "c.released_at DESC, c.collector_number ASC",
    "usd": "CAST(json_extract(c.prices, '$.usd') AS REAL) DESC NULLS LAST, c.name ASC",
    "edhrec": "c.edhrec_rank ASC NULLS LAST, c.name ASC",
    "cmc": "c.cmc ASC, c.name ASC",
}
PRICE_KEYS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "eur_etched", "tix")

_FTS_TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression (prefix match per word)."""
    words = [w.replace('"', "") for w in _FTS_TOKEN.findall(text)]
    words = [w for w in words if w]
    if not words:
        return None
    return " ".join(f'"{w}"*' for w in words)


def card_view(
    row: sqlite3.Row | dict[str, Any], rates: Rates, owned_quantity: int | None = None
) -> dict[str, Any]:
    r = dict(row)
    prices = json.loads(r.get("prices") or "{}")
    view = {
        "id": r["id"],
        "oracle_id": r.get("oracle_id"),
        "name": r["name"],
        "printed_name": r.get("printed_name"),
        "lang": r.get("lang"),
        "set_code": r["set_code"],
        "set_name": r.get("set_name"),
        "collector_number": r["collector_number"],
        "released_at": r.get("released_at"),
        "rarity": r.get("rarity"),
        "layout": r.get("layout"),
        "type_line": r.get("type_line"),
        "oracle_text": r.get("oracle_text"),
        "mana_cost": r.get("mana_cost"),
        "cmc": r.get("cmc"),
        "colors": r.get("colors", ""),
        "color_identity": r.get("color_identity", ""),
        "keywords": json.loads(r.get("keywords") or "[]"),
        "power": r.get("power"),
        "toughness": r.get("toughness"),
        "loyalty": r.get("loyalty"),
        "finishes": json.loads(r.get("finishes") or "[]"),
        "legalities": json.loads(r.get("legalities") or "{}"),
        "promo": bool(r.get("promo")),
        "digital": bool(r.get("digital")),
        "paper": bool(r.get("paper", 1)),
        "artist": r.get("artist"),
        "edhrec_rank": r.get("edhrec_rank"),
        "image_small": r.get("image_small"),
        "image_normal": r.get("image_normal"),
        "image_large": r.get("image_large"),
        "image_art_crop": r.get("image_art_crop"),
        "image_back_normal": r.get("image_back_normal"),
        "card_faces": json.loads(r["card_faces"]) if r.get("card_faces") else None,
        "scryfall_uri": r.get("scryfall_uri"),
        "prices": {
            **{k: prices.get(k) for k in PRICE_KEYS},
            "aud": rates.aud(prices.get("usd"), prices.get("eur")),
            "aud_foil": rates.aud(prices.get("usd_foil"), prices.get("eur_foil")),
            "aud_etched": rates.aud(prices.get("usd_etched"), prices.get("eur_etched")),
        },
    }
    if owned_quantity is not None:
        view["owned_quantity"] = owned_quantity
    elif "owned_quantity" in r:
        view["owned_quantity"] = r["owned_quantity"]
    return view


CARD_SELECT = (
    "SELECT c.*, s.name AS set_name, "
    "(SELECT COALESCE(SUM(e.quantity), 0) FROM collection_entries e "
    " JOIN cards c2 ON c2.id = e.card_id WHERE c2.oracle_id = c.oracle_id) AS owned_quantity "
    "FROM cards c JOIN sets s ON s.code = c.set_code"
)


def get_card(conn: sqlite3.Connection, card_id: str) -> sqlite3.Row | None:
    return conn.execute(f"{CARD_SELECT} WHERE c.id = ?", (card_id,)).fetchone()


def get_cards(conn: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"{CARD_SELECT} WHERE c.id IN ({marks})", ids).fetchall()
    return {r["id"]: r for r in rows}


def printings(conn: sqlite3.Connection, oracle_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"{CARD_SELECT} WHERE c.oracle_id = ? "
        "ORDER BY c.paper DESC, c.released_at DESC, c.set_code, c.collector_number",
        (oracle_id,),
    ).fetchall()


def by_set_and_number(
    conn: sqlite3.Connection, set_code: str, number: str, lang: str | None = None
) -> sqlite3.Row | None:
    candidates = [number, number.lstrip("0") or number]
    for n in dict.fromkeys(candidates):
        params: list[Any] = [set_code.lower(), n]
        sql = f"{CARD_SELECT} WHERE c.set_code = ? AND c.collector_number = ?"
        if lang:
            sql += " AND c.lang = ?"
            params.append(lang)
        sql += " ORDER BY (c.lang = 'en') DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if row:
            return row
    return None


_PREFER = "ORDER BY (c.lang = 'en') DESC, c.paper DESC, c.digital ASC, c.released_at DESC LIMIT 1"


def by_exact_name(
    conn: sqlite3.Connection, name: str, set_code: str | None = None
) -> sqlite3.Row | None:
    """Latest paper printing with this exact (case-insensitive) name, optionally in a set."""
    sql = f"{CARD_SELECT} WHERE c.name = ? COLLATE NOCASE"
    params: list[Any] = [name]
    if set_code:
        sql += " AND c.set_code = ?"
        params.append(set_code.lower())
    row = conn.execute(f"{sql} {_PREFER}", params).fetchone()
    if row is None and " // " not in name:
        # A single face of a double-faced card.
        row = conn.execute(
            f"{CARD_SELECT} WHERE (c.name LIKE ? OR c.name LIKE ?) COLLATE NOCASE {_PREFER}",
            (f"{name} // %", f"% // {name}"),
        ).fetchone()
    return row


class NameIndex:
    """Distinct card names for fuzzy matching, rebuilt lazily after each catalogue sync."""

    def __init__(self) -> None:
        self._names: list[str] = []
        self._version: int = -1

    def names(self, conn: sqlite3.Connection) -> list[str]:
        version = int(
            conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM sync_runs WHERE kind = 'catalogue'"
            ).fetchone()[0]
        )
        if version != self._version:
            self._names = [
                r[0] for r in conn.execute("SELECT DISTINCT name FROM cards ORDER BY name")
            ]
            self._version = version
        return self._names

    def best(self, conn: sqlite3.Connection, query: str, cutoff: int = 85) -> str | None:
        names = self.names(conn)
        if not names:
            return None
        match = process.extractOne(query, names, scorer=fuzz.WRatio, score_cutoff=cutoff)
        return match[0] if match else None


def fuzzy_name(
    conn: sqlite3.Connection, index: NameIndex, query: str, set_code: str | None = None
) -> sqlite3.Row | None:
    exact = by_exact_name(conn, query, set_code)
    if exact:
        return exact
    name = index.best(conn, query)
    return by_exact_name(conn, name, set_code) if name else None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_prefix(value: str) -> str:
    return _escape_like(value) + "%"


def _like_contains(value: str) -> str:
    return "%" + _escape_like(value) + "%"


def autocomplete(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[str]:
    q = q.strip()
    if not q:
        return []
    base = (
        "SELECT name, MAX(lang = 'en') AS en FROM cards WHERE name LIKE ? ESCAPE '\\' {extra} "
        "GROUP BY name ORDER BY en DESC, length(name) ASC, name ASC LIMIT ?"
    )
    rows = conn.execute(base.format(extra=""), (_like_prefix(q), limit)).fetchall()
    if len(rows) < limit:
        more = conn.execute(
            base.format(extra="AND name NOT LIKE ? ESCAPE '\\'"),
            (_like_contains(q), _like_prefix(q), limit - len(rows)),
        ).fetchall()
        rows.extend(more)
    return [r["name"] for r in rows]


def _normalise_colours(value: str) -> str:
    letters = {ch.upper() for ch in value if ch.upper() in "WUBRG"}
    return "".join(c for c in "WUBRG" if c in letters)


def search(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    set_code: str | None = None,
    colors: str | None = None,
    identity: str | None = None,
    type_line: str | None = None,
    rarity: str | None = None,
    cmc_min: float | None = None,
    cmc_max: float | None = None,
    legal_in: str | None = None,
    owned: bool = False,
    unique: str = "cards",
    paper_only: bool = True,
    sort: str = "name",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[sqlite3.Row], int]:
    where: list[str] = []
    params: list[Any] = []
    if q:
        expr = fts_query(q)
        if expr:
            where.append("c.rowid IN (SELECT rowid FROM cards_fts WHERE cards_fts MATCH ?)")
            params.append(expr)
    if set_code:
        where.append("c.set_code = ?")
        params.append(set_code.lower())
    if colors is not None:
        where.append("c.colors = ?")
        params.append(_normalise_colours(colors))
    if identity is not None:
        allowed = _normalise_colours(identity)
        for letter in "WUBRG":
            if letter not in allowed:
                where.append(f"instr(c.color_identity, '{letter}') = 0")
    if type_line:
        where.append("c.type_line LIKE ? ESCAPE '\\'")
        params.append(_like_contains(type_line))
    if rarity:
        where.append("c.rarity = ?")
        params.append(rarity)
    if cmc_min is not None:
        where.append("c.cmc >= ?")
        params.append(cmc_min)
    if cmc_max is not None:
        where.append("c.cmc <= ?")
        params.append(cmc_max)
    if legal_in:
        where.append("json_extract(c.legalities, ?) IN ('legal', 'restricted')")
        params.append(f"$.{legal_in}")
    if paper_only:
        where.append("c.paper = 1")
    if owned:
        where.append(
            "c.oracle_id IN (SELECT c2.oracle_id FROM collection_entries e "
            "JOIN cards c2 ON c2.id = e.card_id)"
        )
    clause = " AND ".join(where) or "1 = 1"
    order = SORTS.get(sort, SORTS["name"])

    if unique == "prints":
        base = f"{CARD_SELECT} WHERE {clause}"
        count_sql = f"SELECT COUNT(*) FROM cards c WHERE {clause}"
    else:
        # One row per oracle_id: the most recent paper printing, English first.
        base = (
            f"{CARD_SELECT} WHERE c.rowid IN ("
            "  SELECT rowid FROM (SELECT c.rowid, ROW_NUMBER() OVER (PARTITION BY c.oracle_id "
            "    ORDER BY (c.lang = 'en') DESC, c.paper DESC, c.digital ASC, c.released_at DESC, "
            f"    c.collector_number) AS rn FROM cards c WHERE {clause}) WHERE rn = 1)"
        )
        count_sql = f"SELECT COUNT(DISTINCT c.oracle_id) FROM cards c WHERE {clause}"
    total = conn.execute(count_sql, params).fetchone()[0]
    offset = (max(1, page) - 1) * per_page
    rows = conn.execute(
        f"{base} ORDER BY {order} LIMIT ? OFFSET ?", [*params, per_page, offset]
    ).fetchall()
    return rows, total


def list_sets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.*,
          (SELECT COUNT(DISTINCT c.oracle_id) FROM collection_entries e
             JOIN cards c ON c.id = e.card_id WHERE c.set_code = s.code) AS owned_cards,
          (SELECT COALESCE(SUM(e.quantity), 0) FROM collection_entries e
             JOIN cards c ON c.id = e.card_id WHERE c.set_code = s.code) AS owned_copies
        FROM sets s ORDER BY s.released_at DESC, s.name
        """
    ).fetchall()
    return [dict(r) for r in rows]
