"""Owned cards: entries, tags, summaries, export."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from app import db
from app.catalogue import CONDITIONS, FINISHES, _like_contains, card_view, fts_query
from app.pricing.fx import Rates
from app.pricing.snapshots import EUR_FOR_FINISH, USD_FOR_FINISH


class CollectionError(ValueError):
    pass


TAG_SEP = chr(31)  # unit separator, never appears in a tag name

ENTRY_SELECT = (
    "SELECT e.*, c.name, c.oracle_id, c.set_code, c.collector_number, c.rarity, c.type_line, c.colors, "
    "c.color_identity, c.cmc, c.mana_cost, c.lang, c.image_small, c.image_normal, c.prices, c.finishes, "
    "c.legalities, c.layout, c.released_at, c.edhrec_rank, c.keywords, c.oracle_text, c.printed_name, "
    "c.image_back_normal, c.card_faces, c.scryfall_uri, c.artist, c.promo, c.digital, c.paper, c.power, "
    "c.toughness, c.loyalty, c.image_large, c.image_art_crop, s.name AS set_name, "
    "(SELECT group_concat(tag, char(31)) FROM entry_tags t WHERE t.entry_id = e.id) AS tag_list "
    "FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id JOIN sets s ON s.code = c.set_code"
)


def _validate(finish: str, condition: str, quantity: int) -> None:
    if finish not in FINISHES:
        raise CollectionError(f"finish must be one of {', '.join(FINISHES)}")
    if condition not in CONDITIONS:
        raise CollectionError(f"condition must be one of {', '.join(CONDITIONS)}")
    if quantity < 0:
        raise CollectionError("quantity cannot be negative")


def _tags_of(row: dict[str, Any]) -> list[str]:
    return sorted(row["tag_list"].split(TAG_SEP)) if row.get("tag_list") else []


def entry_view(row: sqlite3.Row, rates: Rates) -> dict[str, Any]:
    r = dict(row)
    card = card_view({**r, "id": r["card_id"]}, rates)
    card.pop("owned_quantity", None)
    usd, eur = _finish_prices(json.loads(r.get("prices") or "{}"), r["finish"])
    unit_aud = rates.aud(usd, eur)
    return {
        "id": r["id"],
        "card": card,
        "finish": r["finish"],
        "condition": r["condition"],
        "language": r["language"],
        "quantity": r["quantity"],
        "notes": r.get("notes"),
        "tags": _tags_of(r),
        "source": r.get("source"),
        "added_by": r.get("added_by"),
        "added_at": r["added_at"],
        "updated_at": r["updated_at"],
        "unit_price": {"usd": usd, "eur": eur, "aud": unit_aud},
        "value": {
            "usd": round(float(usd) * r["quantity"], 2) if usd else None,
            "eur": round(float(eur) * r["quantity"], 2) if eur else None,
            "aud": rates.aud(usd, eur, r["quantity"]),
        },
    }


def _finish_prices(prices: dict[str, Any], finish: str) -> tuple[str | None, str | None]:
    if finish == "foil":
        return prices.get("usd_foil"), prices.get("eur_foil")
    if finish == "etched":
        return prices.get("usd_etched"), prices.get("eur_etched")
    return prices.get("usd"), prices.get("eur")


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute(f"{ENTRY_SELECT} WHERE e.id = ?", (entry_id,)).fetchone()


def add(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    finish: str = "nonfoil",
    condition: str = "NM",
    language: str | None = None,
    quantity: int = 1,
    notes: str | None = None,
    tags: list[str] | None = None,
    source: str = "manual",
    added_by: str | None = None,
) -> sqlite3.Row:
    """Add copies. Merges into the matching row and returns it."""
    _validate(finish, condition, quantity)
    if quantity == 0:
        raise CollectionError("quantity must be at least 1 when adding")
    card = conn.execute("SELECT id, lang FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        raise CollectionError(f"Unknown card id {card_id}")
    language = (language or card["lang"]).lower()
    now = db.now_iso()
    with db.transaction(conn):
        existing = conn.execute(
            "SELECT id FROM collection_entries WHERE card_id = ? AND finish = ? AND condition = ? AND language = ?",
            (card_id, finish, condition, language),
        ).fetchone()
        if existing:
            entry_id = existing["id"]
            conn.execute(
                "UPDATE collection_entries SET quantity = quantity + ?, updated_at = ?, "
                "notes = COALESCE(?, notes) WHERE id = ?",
                (quantity, now, notes, entry_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO collection_entries (card_id, finish, condition, language, quantity, notes, "
                "source, added_by, added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, finish, condition, language, quantity, notes, source, added_by, now, now),
            )
            entry_id = int(cur.lastrowid)
        if tags:
            _set_tags(conn, entry_id, tags, replace=False)
    row = get_entry(conn, entry_id)
    assert row is not None
    return row


def update(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    quantity: int | None = None,
    finish: str | None = None,
    condition: str | None = None,
    language: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> sqlite3.Row | None:
    """Update an entry. Quantity 0 deletes it. Returns the (possibly merged) row or None."""
    current = get_entry(conn, entry_id)
    if current is None:
        return None
    new_finish = finish or current["finish"]
    new_condition = condition or current["condition"]
    new_language = (language or current["language"]).lower()
    new_quantity = current["quantity"] if quantity is None else quantity
    _validate(new_finish, new_condition, new_quantity)
    now = db.now_iso()
    with db.transaction(conn):
        if new_quantity == 0:
            conn.execute("DELETE FROM collection_entries WHERE id = ?", (entry_id,))
            _prune_tags(conn)
            return None
        clash = conn.execute(
            "SELECT id FROM collection_entries WHERE card_id = ? AND finish = ? AND condition = ? "
            "AND language = ? AND id != ?",
            (current["card_id"], new_finish, new_condition, new_language, entry_id),
        ).fetchone()
        if clash:
            conn.execute(
                "UPDATE collection_entries SET quantity = quantity + ?, updated_at = ? WHERE id = ?",
                (new_quantity, now, clash["id"]),
            )
            conn.execute("DELETE FROM collection_entries WHERE id = ?", (entry_id,))
            entry_id = clash["id"]
        else:
            conn.execute(
                "UPDATE collection_entries SET quantity = ?, finish = ?, condition = ?, language = ?, "
                "notes = ?, updated_at = ? WHERE id = ?",
                (
                    new_quantity,
                    new_finish,
                    new_condition,
                    new_language,
                    current["notes"] if notes is None else notes,
                    now,
                    entry_id,
                ),
            )
        if tags is not None:
            _set_tags(conn, entry_id, tags, replace=True)
    return get_entry(conn, entry_id)


def remove(conn: sqlite3.Connection, entry_id: int) -> bool:
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM collection_entries WHERE id = ?", (entry_id,))
        _prune_tags(conn)
    return cur.rowcount > 0


def _set_tags(conn: sqlite3.Connection, entry_id: int, tags: list[str], replace: bool) -> None:
    clean = sorted({t.strip() for t in tags if t and t.strip()})
    if replace:
        conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
    for tag in clean:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
        conn.execute(
            "INSERT OR IGNORE INTO entry_tags (entry_id, tag) VALUES (?, ?)", (entry_id, tag)
        )
    _prune_tags(conn)


def _prune_tags(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM tags WHERE name NOT IN (SELECT tag FROM entry_tags)")


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.name, COUNT(et.entry_id) AS entries FROM tags t LEFT JOIN entry_tags et ON et.tag = t.name "
        "GROUP BY t.name ORDER BY t.name"
    ).fetchall()
    return [dict(r) for r in rows]


def list_entries(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    set_code: str | None = None,
    colors: str | None = None,
    identity: str | None = None,
    type_line: str | None = None,
    rarity: str | None = None,
    finish: str | None = None,
    condition: str | None = None,
    language: str | None = None,
    tag: str | None = None,
    legal_in: str | None = None,
    cmc_min: float | None = None,
    cmc_max: float | None = None,
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
        params.append("".join(ch for ch in "WUBRG" if ch in colors.upper()))
    if identity is not None:
        allowed = identity.upper()
        for letter in "WUBRG":
            if letter not in allowed:
                where.append(f"instr(c.color_identity, '{letter}') = 0")
    if type_line:
        where.append("c.type_line LIKE ? ESCAPE '\\'")
        params.append(_like_contains(type_line))
    if rarity:
        where.append("c.rarity = ?")
        params.append(rarity)
    if finish:
        where.append("e.finish = ?")
        params.append(finish)
    if condition:
        where.append("e.condition = ?")
        params.append(condition)
    if language:
        where.append("e.language = ?")
        params.append(language.lower())
    if tag:
        where.append("e.id IN (SELECT entry_id FROM entry_tags WHERE tag = ?)")
        params.append(tag)
    if legal_in:
        where.append("instr(c.legal_formats, ?) > 0")
        params.append(f" {legal_in} ")
    if cmc_min is not None:
        where.append("c.cmc >= ?")
        params.append(cmc_min)
    if cmc_max is not None:
        where.append("c.cmc <= ?")
        params.append(cmc_max)
    clause = " AND ".join(where) or "1 = 1"
    orders = {
        "name": "c.name COLLATE NOCASE, c.released_at DESC, e.finish",
        "added": "e.added_at DESC, e.id DESC",
        "value": f"(e.quantity * CAST(COALESCE({USD_FOR_FINISH}, {EUR_FOR_FINISH}) AS REAL)) DESC NULLS LAST, c.name",
        "quantity": "e.quantity DESC, c.name",
        "released": "c.released_at DESC, c.name",
        "cmc": "c.cmc, c.name",
    }
    order = orders.get(sort, orders["name"])
    total = conn.execute(
        f"SELECT COUNT(*) FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id WHERE {clause}",
        params,
    ).fetchone()[0]
    offset = (max(1, page) - 1) * per_page
    page_sql = (
        f"SELECT e.id FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id WHERE {clause} "
        f"ORDER BY {order} LIMIT ? OFFSET ?"
    )
    ids = [r[0] for r in conn.execute(page_sql, [*params, per_page, offset])]
    if not ids:
        return [], total
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"{ENTRY_SELECT} WHERE e.id IN ({marks}) ORDER BY {order}", ids).fetchall()
    return rows, total


def owned_by_oracle(conn: sqlite3.Connection, oracle_ids: list[str]) -> dict[str, int]:
    if not oracle_ids:
        return {}
    marks = ",".join("?" * len(oracle_ids))
    rows = conn.execute(
        "SELECT c.oracle_id, SUM(e.quantity) AS qty FROM collection_entries e "
        "CROSS JOIN cards c ON c.id = e.card_id "
        f"WHERE c.oracle_id IN ({marks}) GROUP BY c.oracle_id",
        oracle_ids,
    ).fetchall()
    return {r["oracle_id"]: r["qty"] for r in rows}


def owned_printings(conn: sqlite3.Connection, oracle_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.id, e.card_id, c.set_code, c.collector_number, e.finish, e.condition, e.language, e.quantity "
        "FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id WHERE c.oracle_id = ? "
        "ORDER BY c.released_at DESC, e.finish",
        (oracle_id,),
    ).fetchall()
    return [dict(r) for r in rows]


DEFAULT_GROUPS = ("color_identity", "type", "set", "rarity", "finish", "condition", "language")
_TYPE_ORDER = (
    "Creature",
    "Planeswalker",
    "Battle",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
    "Other",
)


def _type_bucket(type_line: str | None) -> str:
    front = (type_line or "").split(" // ")[0]
    return next((t for t in _TYPE_ORDER[:-1] if t in front), "Other")


def summary(
    conn: sqlite3.Connection, rates: Rates, group_by: list[str] | None = None
) -> dict[str, Any]:
    """Totals and breakdowns in one pass over the entries (narrow columns only)."""
    groups = [
        g for g in (group_by if group_by is not None else DEFAULT_GROUPS) if g in DEFAULT_GROUPS
    ]
    rows = conn.execute(
        "SELECT e.quantity, e.finish, e.condition, e.language, c.oracle_id, c.color_identity, "
        "c.type_line, c.set_code, c.rarity, c.usd, c.usd_foil, c.usd_etched, c.eur, c.eur_foil, "
        "c.eur_etched FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id"
    ).fetchall()
    totals: dict[str, Any] = {
        "cards": set(),
        "copies": 0,
        "entries": 0,
        "usd": 0.0,
        "eur": 0.0,
        "aud": 0.0,
        "priced_entries": 0,
    }
    agg: dict[str, dict[str, dict[str, Any]]] = {g: {} for g in groups}

    def bump(group: str, key: str, r: sqlite3.Row, usd: float | None, aud: float | None) -> None:
        slot = agg[group].setdefault(
            key, {"k": key, "cards": set(), "copies": 0, "usd": 0.0, "aud": 0.0}
        )
        slot["cards"].add(r["oracle_id"])
        slot["copies"] += r["quantity"]
        if usd is not None:
            slot["usd"] += usd * r["quantity"]
        if aud is not None:
            slot["aud"] += aud * r["quantity"]

    for r in rows:
        if r["finish"] == "foil":
            usd, eur = r["usd_foil"], r["eur_foil"]
        elif r["finish"] == "etched":
            usd, eur = r["usd_etched"], r["eur_etched"]
        else:
            usd, eur = r["usd"], r["eur"]
        aud = rates.aud(usd, eur)
        totals["cards"].add(r["oracle_id"])
        totals["copies"] += r["quantity"]
        totals["entries"] += 1
        if usd is not None or eur is not None:
            totals["priced_entries"] += 1
        if usd is not None:
            totals["usd"] += usd * r["quantity"]
        if eur is not None:
            totals["eur"] += eur * r["quantity"]
        if aud is not None:
            totals["aud"] += aud * r["quantity"]
        keys = {
            "color_identity": r["color_identity"] or "C",
            "type": _type_bucket(r["type_line"]),
            "set": r["set_code"],
            "rarity": r["rarity"],
            "finish": r["finish"],
            "condition": r["condition"],
            "language": r["language"],
        }
        for group in groups:
            bump(group, keys[group], r, usd, aud)

    has_rates = bool(rates.usd_aud or rates.eur_aud)
    out: dict[str, Any] = {
        "totals": {
            "cards": len(totals["cards"]),
            "copies": totals["copies"],
            "entries": totals["entries"],
            "usd": round(totals["usd"], 2),
            "eur": round(totals["eur"], 2),
            "aud": round(totals["aud"], 2) if has_rates else None,
            "priced_entries": totals["priced_entries"],
        },
        "rates": {
            "usd_aud": rates.usd_aud,
            "eur_aud": rates.eur_aud,
            "source": rates.source,
            "day": rates.day,
        },
    }
    for group in groups:
        items = [
            {
                "k": s["k"],
                "cards": len(s["cards"]),
                "copies": s["copies"],
                "usd": round(s["usd"], 2),
                "aud": round(s["aud"], 2) if has_rates else None,
            }
            for s in agg[group].values()
        ]
        if group == "type":
            items.sort(key=lambda s: _TYPE_ORDER.index(s["k"]))
        else:
            items.sort(key=lambda s: (-s["copies"], s["k"]))
        if group == "set":
            items = items[:30]
        out[f"by_{group}"] = items
    out["top_cards"] = top_entries(conn, rates, 20)
    return out


def top_entries(conn: sqlite3.Connection, rates: Rates, limit: int = 20) -> list[dict[str, Any]]:
    """Entries by total value, fetched narrow first then hydrated."""
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT e.id FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id "
            f"ORDER BY (e.quantity * COALESCE({USD_FOR_FINISH}, {EUR_FOR_FINISH})) DESC NULLS LAST LIMIT ?",
            (limit,),
        )
    ]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"{ENTRY_SELECT} WHERE e.id IN ({marks})", ids).fetchall()
    views = [entry_view(r, rates) for r in rows]
    views.sort(key=lambda v: -(v["value"]["usd"] or v["value"]["eur"] or 0))
    return views


EXPORT_COLUMNS = [
    "Name",
    "Set code",
    "Set name",
    "Collector number",
    "Foil",
    "Rarity",
    "Quantity",
    "Scryfall ID",
    "Condition",
    "Language",
    "Tags",
    "Notes",
]
_EXPORT_CONDITION = {
    "NM": "near_mint",
    "LP": "lightly_played",
    "MP": "moderately_played",
    "HP": "heavily_played",
    "DMG": "damaged",
}


def export_csv(conn: sqlite3.Connection) -> str:
    """ManaBox-compatible CSV (plus Tags and Notes columns ManaBox ignores)."""
    rows = conn.execute(f"{ENTRY_SELECT} ORDER BY c.name, c.released_at, e.finish").fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                r["name"],
                r["set_code"],
                r["set_name"],
                r["collector_number"],
                "normal" if r["finish"] == "nonfoil" else r["finish"],
                r["rarity"],
                r["quantity"],
                r["card_id"],
                _EXPORT_CONDITION[r["condition"]],
                r["language"],
                " ".join(_tags_of(dict(r))),
                r["notes"] or "",
            ]
        )
    return out.getvalue()
