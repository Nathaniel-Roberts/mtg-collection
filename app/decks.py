"""Decks: storage, views, text import and export, cross-deck conflicts."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app import catalogue, collection, db, rules
from app.pricing.fx import Rates

ROLES = ("main", "commander", "companion", "sideboard", "maybeboard")
COUNTED_ROLES = ("main", "commander", "companion")


class DeckError(ValueError):
    pass


def _check_format(format_key: str) -> str:
    key = format_key.strip().lower()
    if key not in rules.FORMATS:
        raise DeckError(f"Unknown format {format_key!r}; known: {', '.join(rules.FORMATS)}")
    return key


def create(
    conn: sqlite3.Connection,
    *,
    name: str,
    format_key: str,
    description: str | None = None,
    created_by: str | None = None,
) -> int:
    if not name.strip():
        raise DeckError("A deck needs a name")
    key = _check_format(format_key)
    now = db.now_iso()
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO decks (name, format, description, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), key, description, created_by, now, now),
        )
    return int(cur.lastrowid)


def update(
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    name: str | None = None,
    format_key: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
) -> bool:
    fields: list[str] = []
    params: list[Any] = []
    if name is not None:
        if not name.strip():
            raise DeckError("A deck needs a name")
        fields.append("name = ?")
        params.append(name.strip())
    if format_key is not None:
        fields.append("format = ?")
        params.append(_check_format(format_key))
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if archived is not None:
        fields.append("archived = ?")
        params.append(int(archived))
    if not fields:
        return exists(conn, deck_id)
    fields.append("updated_at = ?")
    params.append(db.now_iso())
    params.append(deck_id)
    with db.transaction(conn):
        cur = conn.execute(f"UPDATE decks SET {', '.join(fields)} WHERE id = ?", params)
    return cur.rowcount > 0


def delete(conn: sqlite3.Connection, deck_id: int) -> bool:
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    return cur.rowcount > 0


def exists(conn: sqlite3.Connection, deck_id: int) -> bool:
    return conn.execute("SELECT 1 FROM decks WHERE id = ?", (deck_id,)).fetchone() is not None


def set_cards(
    conn: sqlite3.Connection, deck_id: int, cards: list[dict[str, Any]], *, mode: str = "add"
) -> None:
    """``cards``: [{card_id, quantity, role}]. mode 'add' increments, 'set' replaces quantities.
    Quantity 0 (or a negative result) removes the row."""
    if not exists(conn, deck_id):
        raise DeckError(f"No deck with id {deck_id}")
    now = db.now_iso()
    with db.transaction(conn):
        for item in cards:
            role = item.get("role") or "main"
            if role not in ROLES:
                raise DeckError(f"role must be one of {', '.join(ROLES)}")
            card_id = item["card_id"]
            if conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone() is None:
                raise DeckError(f"Unknown card id {card_id}")
            qty = int(item.get("quantity", 1))
            current = conn.execute(
                "SELECT quantity FROM deck_cards WHERE deck_id = ? AND card_id = ? AND role = ?",
                (deck_id, card_id, role),
            ).fetchone()
            new_qty = qty if mode == "set" or current is None else current["quantity"] + qty
            if new_qty <= 0:
                conn.execute(
                    "DELETE FROM deck_cards WHERE deck_id = ? AND card_id = ? AND role = ?",
                    (deck_id, card_id, role),
                )
            elif current is None:
                conn.execute(
                    "INSERT INTO deck_cards (deck_id, card_id, role, quantity) VALUES (?, ?, ?, ?)",
                    (deck_id, card_id, role, new_qty),
                )
            else:
                conn.execute(
                    "UPDATE deck_cards SET quantity = ? WHERE deck_id = ? AND card_id = ? AND role = ?",
                    (new_qty, deck_id, card_id, role),
                )
        conn.execute("UPDATE decks SET updated_at = ? WHERE id = ?", (now, deck_id))


def remove_cards(conn: sqlite3.Connection, deck_id: int, cards: list[dict[str, Any]]) -> None:
    """Remove ``quantity`` copies (all copies when quantity is None) of each card in the given role,
    or in every role when role is None."""
    if not exists(conn, deck_id):
        raise DeckError(f"No deck with id {deck_id}")
    with db.transaction(conn):
        for item in cards:
            roles = [item["role"]] if item.get("role") else list(ROLES)
            for role in roles:
                row = conn.execute(
                    "SELECT quantity FROM deck_cards WHERE deck_id = ? AND card_id = ? AND role = ?",
                    (deck_id, item["card_id"], role),
                ).fetchone()
                if row is None:
                    continue
                qty = item.get("quantity")
                if qty is None or row["quantity"] - int(qty) <= 0:
                    conn.execute(
                        "DELETE FROM deck_cards WHERE deck_id = ? AND card_id = ? AND role = ?",
                        (deck_id, item["card_id"], role),
                    )
                else:
                    conn.execute(
                        "UPDATE deck_cards SET quantity = quantity - ? WHERE deck_id = ? AND card_id = ? AND role = ?",
                        (int(qty), deck_id, item["card_id"], role),
                    )
        conn.execute("UPDATE decks SET updated_at = ? WHERE id = ?", (db.now_iso(), deck_id))


def deck_card_rows(conn: sqlite3.Connection, deck_id: int, rates: Rates) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT dc.quantity, dc.role, c.*, s.name AS set_name FROM deck_cards dc "
        "JOIN cards c ON c.id = dc.card_id JOIN sets s ON s.code = c.set_code WHERE dc.deck_id = ? "
        "ORDER BY CASE dc.role WHEN 'commander' THEN 0 WHEN 'companion' THEN 1 WHEN 'main' THEN 2 "
        "WHEN 'sideboard' THEN 3 ELSE 4 END, "
        "c.cmc, c.name",
        (deck_id,),
    ).fetchall()
    oracle_ids = list({r["oracle_id"] for r in rows if r["oracle_id"]})
    owned = collection.owned_by_oracle(conn, oracle_ids)
    out = []
    for r in rows:
        view = catalogue.card_view(r, rates, owned_quantity=owned.get(r["oracle_id"], 0))
        view["quantity"] = r["quantity"]
        view["role"] = r["role"]
        out.append(view)
    return out


def get(
    conn: sqlite3.Connection, deck_id: int, rates: Rates, *, with_validation: bool = True
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        return None
    cards = deck_card_rows(conn, deck_id, rates)
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    for c in cards:
        by_role[c["role"]].append(c)
    counts = {role: sum(c["quantity"] for c in by_role[role]) for role in ROLES}
    value_aud = sum(
        (c["prices"].get("aud") or 0.0) * c["quantity"] for c in cards if c["role"] in COUNTED_ROLES
    )
    value_usd = sum(
        float(c["prices"].get("usd") or 0.0) * c["quantity"]
        for c in cards
        if c["role"] in COUNTED_ROLES
    )
    out = {
        **dict(row),
        "archived": bool(row["archived"]),
        "format_name": rules.FORMATS[row["format"]].name
        if row["format"] in rules.FORMATS
        else row["format"],
        "cards": by_role,
        "counts": counts,
        "total": counts["main"] + counts["commander"],
        "value": {
            "usd": round(value_usd, 2),
            "aud": round(value_aud, 2) if rates.usd_aud or rates.eur_aud else None,
        },
    }
    if with_validation:
        out["validation"] = rules.validate(
            conn, row["format"], cards, deck_id=deck_id, rates=rates
        ).as_dict()
    return out


def list_all(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT d.*, "
        "(SELECT COALESCE(SUM(quantity), 0) FROM deck_cards WHERE deck_id = d.id "
        "AND role IN ('main','commander','companion')) AS cards "
        "FROM decks d "
        + ("" if include_archived else "WHERE d.archived = 0 ")
        + "ORDER BY d.updated_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["archived"] = bool(d["archived"])
        d["format_name"] = (
            rules.FORMATS[d["format"]].name if d["format"] in rules.FORMATS else d["format"]
        )
        d["owned_percent"] = _owned_percent(conn, d["id"])
        out.append(d)
    return out


def _owned_percent(conn: sqlite3.Connection, deck_id: int) -> float:
    rows = conn.execute(
        "SELECT c.oracle_id, SUM(dc.quantity) AS need FROM deck_cards dc CROSS JOIN cards c ON c.id = dc.card_id "
        "WHERE dc.deck_id = ? AND dc.role IN ('main','commander','companion') GROUP BY c.oracle_id",
        (deck_id,),
    ).fetchall()
    if not rows:
        return 100.0
    owned = collection.owned_by_oracle(conn, [r["oracle_id"] for r in rows])
    need = sum(r["need"] for r in rows)
    have = sum(min(r["need"], owned.get(r["oracle_id"], 0)) for r in rows)
    return round(100.0 * have / need, 1)


# --- text lists -------------------------------------------------------------------------------

_LINE = re.compile(
    r"^\s*(?P<qty>\d+)?\s*x?\s*(?P<name>[^(\n]+?)\s*(?:\((?P<set>[A-Za-z0-9]{2,6})\)\s*(?P<num>[A-Za-z0-9★\-]+)?)?\s*(?:\*F\*|\*E\*)?\s*$"
)
_SECTION = {
    "commander": "commander",
    "commanders": "commander",
    "companion": "companion",
    "deck": "main",
    "main": "main",
    "mainboard": "main",
    "sideboard": "sideboard",
    "maybeboard": "maybeboard",
    "considering": "maybeboard",
}


def parse_text(text: str) -> list[dict[str, Any]]:
    """Parse an MTGA or Moxfield style list into [{name, quantity, set, number, role}]."""
    role = "main"
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "#")):
            continue
        header = line.rstrip(":").lower()
        if header in _SECTION:
            role = _SECTION[header]
            continue
        if line.lower().startswith("about") or line.lower().startswith("name "):
            continue
        m = _LINE.match(line)
        if not m or not m.group("name"):
            continue
        out.append(
            {
                "name": m.group("name").strip(),
                "quantity": int(m.group("qty") or 1),
                "set": (m.group("set") or "").lower() or None,
                "number": m.group("num"),
                "role": role,
            }
        )
    return out


def resolve_lines(
    conn: sqlite3.Connection,
    lines: list[dict[str, Any]],
    name_index: catalogue.NameIndex,
    *,
    prefer_owned: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn parsed lines into {card_id, quantity, role}; unresolved lines are returned separately."""
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for line in lines:
        row = None
        if line.get("set") and line.get("number"):
            row = catalogue.by_set_and_number(conn, line["set"], line["number"])
        if row is None:
            row = resolve_name(
                conn, line["name"], name_index, set_code=line.get("set"), prefer_owned=prefer_owned
            )
        if row is None:
            unresolved.append(line)
        else:
            resolved.append(
                {"card_id": row["id"], "quantity": line["quantity"], "role": line["role"]}
            )
    return resolved, unresolved


def resolve_name(
    conn: sqlite3.Connection,
    name: str,
    name_index: catalogue.NameIndex,
    *,
    set_code: str | None = None,
    prefer_owned: bool = True,
) -> sqlite3.Row | None:
    row = catalogue.fuzzy_name(conn, name_index, name, set_code)
    if row is None or not prefer_owned or not row["oracle_id"]:
        return row
    owned = conn.execute(
        f"{catalogue.CARD_SELECT} WHERE c.oracle_id = ? AND c.id IN (SELECT card_id FROM collection_entries) "
        "ORDER BY c.released_at DESC LIMIT 1",
        (row["oracle_id"],),
    ).fetchone()
    return owned or row


def export_text(conn: sqlite3.Connection, deck_id: int, rates: Rates) -> str:
    deck = get(conn, deck_id, rates, with_validation=False)
    if deck is None:
        raise DeckError(f"No deck with id {deck_id}")
    lines: list[str] = []
    titles = {
        "commander": "Commander",
        "companion": "Companion",
        "main": "Deck",
        "sideboard": "Sideboard",
        "maybeboard": "Maybeboard",
    }
    for role in ("commander", "companion", "main", "sideboard", "maybeboard"):
        cards = deck["cards"][role]
        if not cards:
            continue
        if lines:
            lines.append("")
        lines.append(titles[role])
        for c in cards:
            lines.append(
                f"{c['quantity']} {c['name']} ({c['set_code'].upper()}) {c['collector_number']}"
            )
    return "\n".join(lines) + "\n"


# --- conflicts ---------------------------------------------------------------------------------


def conflicts(
    conn: sqlite3.Connection, rates: Rates, *, include_acknowledged: bool = False
) -> list[dict[str, Any]]:
    """Cards needed by non-archived decks (counted roles) beyond the copies owned."""
    rows = conn.execute(
        "SELECT c.oracle_id, SUM(dc.quantity) AS needed FROM deck_cards dc CROSS JOIN cards c ON c.id = dc.card_id "
        "JOIN decks d ON d.id = dc.deck_id WHERE d.archived = 0 AND dc.role IN ('main','commander','companion') "
        "GROUP BY c.oracle_id"
    ).fetchall()
    needed = {r["oracle_id"]: r["needed"] for r in rows}
    owned = collection.owned_by_oracle(conn, list(needed))
    acks = {r["oracle_id"]: r for r in conn.execute("SELECT * FROM deck_conflict_acks")}
    out: list[dict[str, Any]] = []
    cleared = [oid for oid in acks if needed.get(oid, 0) - owned.get(oid, 0) <= 0]
    if cleared:
        with db.transaction(conn):
            conn.executemany(
                "DELETE FROM deck_conflict_acks WHERE oracle_id = ?", [(o,) for o in cleared]
            )
    for oracle_id, need in needed.items():
        have = owned.get(oracle_id, 0)
        shortfall = need - have
        if shortfall <= 0:
            continue
        ack = acks.get(oracle_id)
        acknowledged = ack is not None and ack["shortfall"] >= shortfall
        if acknowledged and not include_acknowledged:
            continue
        card = conn.execute(
            f"{catalogue.CARD_SELECT} WHERE c.oracle_id = ? "
            "ORDER BY (c.id IN (SELECT card_id FROM collection_entries)) DESC, c.released_at DESC LIMIT 1",
            (oracle_id,),
        ).fetchone()
        decks_using = conn.execute(
            "SELECT d.id, d.name, SUM(dc.quantity) AS quantity FROM deck_cards dc "
            "CROSS JOIN cards c ON c.id = dc.card_id "
            "JOIN decks d ON d.id = dc.deck_id WHERE c.oracle_id = ? AND d.archived = 0 "
            "AND dc.role IN ('main','commander','companion') "
            "GROUP BY d.id ORDER BY d.name",
            (oracle_id,),
        ).fetchall()
        out.append(
            {
                "card": catalogue.card_view(card, rates, owned_quantity=have)
                if card
                else {"oracle_id": oracle_id},
                "owned": have,
                "needed": need,
                "shortfall": shortfall,
                "decks": [dict(d) for d in decks_using],
                "acknowledged": acknowledged,
            }
        )
    out.sort(key=lambda c: (-c["shortfall"], c["card"].get("name", "")))
    return out


def acknowledge_conflict(
    conn: sqlite3.Connection, oracle_id: str, rates: Rates, by: str | None = None
) -> bool:
    for c in conflicts(conn, rates, include_acknowledged=True):
        if c["card"].get("oracle_id") == oracle_id:
            with db.transaction(conn):
                conn.execute(
                    "INSERT INTO deck_conflict_acks (oracle_id, shortfall, acknowledged_at, acknowledged_by) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(oracle_id) DO UPDATE SET shortfall = excluded.shortfall, "
                    "acknowledged_at = excluded.acknowledged_at, "
                    "acknowledged_by = excluded.acknowledged_by",
                    (oracle_id, c["shortfall"], db.now_iso(), by),
                )
            return True
    return False


def clear_acknowledgement(conn: sqlite3.Connection, oracle_id: str) -> bool:
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM deck_conflict_acks WHERE oracle_id = ?", (oracle_id,))
    return cur.rowcount > 0
