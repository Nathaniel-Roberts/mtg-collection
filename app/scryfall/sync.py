"""Catalogue sync: Scryfall bulk file and set list into the local tables."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app import db
from app.scryfall.bulk import iter_bulk_download
from app.scryfall.client import ScryfallClient

log = logging.getLogger(__name__)

BULK_KIND = "default_cards"
BATCH = 2000


@dataclass
class SyncResult:
    run_id: int
    cards: int
    sets: int
    skipped: bool = False
    bulk_updated_at: str | None = None


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO sync_runs (kind, started_at, status) VALUES (?, ?, 'running')",
        (kind, db.now_iso()),
    )
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, detail: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?",
        (db.now_iso(), status, json.dumps(detail), run_id),
    )


def last_run(conn: sqlite3.Connection, kind: str, status: str = "ok") -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_runs WHERE kind = ? AND status = ? ORDER BY id DESC LIMIT 1",
        (kind, status),
    ).fetchone()


def _colours(values: list[str] | None) -> str:
    order = "WUBRG"
    return "".join(c for c in order if c in (values or []))


def _face_images(obj: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Image URI dicts for the front and (if any) back face."""
    if obj.get("image_uris"):
        return obj["image_uris"], None
    faces = obj.get("card_faces") or []
    front = faces[0].get("image_uris", {}) if faces else {}
    back = faces[1].get("image_uris") if len(faces) > 1 else None
    return front, back


def _joined(obj: dict[str, Any], key: str) -> str | None:
    if obj.get(key) is not None:
        return obj[key]
    faces = obj.get("card_faces") or []
    parts = [f.get(key) for f in faces if f.get(key)]
    return " // ".join(parts) if parts else None


PRICE_KEYS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "eur_etched", "tix")


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _price_usd(prices: dict[str, Any]) -> float | None:
    for key in ("usd", "usd_foil", "usd_etched"):
        value = prices.get(key)
        if value:
            try:
                return float(value)
            except ValueError:
                continue
    return None


def card_row(obj: dict[str, Any], run_id: int, now: str) -> dict[str, Any]:
    """Map a Scryfall card object onto a ``cards`` row."""
    front, back = _face_images(obj)
    faces = obj.get("card_faces") or []
    colors = obj.get("colors")
    if colors is None and faces:
        merged: list[str] = []
        for face in faces:
            merged.extend(face.get("colors") or [])
        colors = merged
    oracle_id = obj.get("oracle_id")
    if oracle_id is None and faces:
        oracle_id = faces[0].get("oracle_id")
    face_summary = [
        {
            "name": f.get("name"),
            "type_line": f.get("type_line"),
            "oracle_text": f.get("oracle_text"),
            "mana_cost": f.get("mana_cost"),
            "image_normal": (f.get("image_uris") or {}).get("normal"),
        }
        for f in faces
    ] or None
    return {
        "id": obj["id"],
        "oracle_id": oracle_id,
        "name": obj["name"],
        "printed_name": obj.get("printed_name") or _joined(obj, "printed_name"),
        "lang": obj.get("lang", "en"),
        "set_code": obj["set"],
        "collector_number": str(obj["collector_number"]),
        "released_at": obj.get("released_at"),
        "rarity": obj.get("rarity", "common"),
        "layout": obj.get("layout", "normal"),
        "type_line": _joined(obj, "type_line"),
        "oracle_text": _joined(obj, "oracle_text"),
        "mana_cost": _joined(obj, "mana_cost"),
        "cmc": obj.get("cmc"),
        "colors": _colours(colors),
        "color_identity": _colours(obj.get("color_identity")),
        "keywords": json.dumps(obj.get("keywords") or []),
        "power": obj.get("power") or (faces[0].get("power") if faces else None),
        "toughness": obj.get("toughness") or (faces[0].get("toughness") if faces else None),
        "loyalty": obj.get("loyalty") or (faces[0].get("loyalty") if faces else None),
        "legalities": json.dumps(obj.get("legalities") or {}),
        "finishes": json.dumps(obj.get("finishes") or []),
        "promo": int(bool(obj.get("promo"))),
        "digital": int(bool(obj.get("digital"))),
        "paper": int("paper" in (obj.get("games") or [])),
        "reprint": int(bool(obj.get("reprint"))),
        "full_art": int(bool(obj.get("full_art"))),
        "frame": obj.get("frame"),
        "border_color": obj.get("border_color"),
        "artist": obj.get("artist"),
        "illustration_id": obj.get("illustration_id")
        or (faces[0].get("illustration_id") if faces else None),
        "edhrec_rank": obj.get("edhrec_rank"),
        "image_small": front.get("small"),
        "image_normal": front.get("normal"),
        "image_large": front.get("large"),
        "image_art_crop": front.get("art_crop"),
        "image_back_normal": back.get("normal") if back else None,
        "card_faces": json.dumps(face_summary) if face_summary else None,
        "prices": json.dumps(obj.get("prices") or {}),
        "price_usd": _price_usd(obj.get("prices") or {}),
        **{k: _num((obj.get("prices") or {}).get(k)) for k in PRICE_KEYS},
        "legal_formats": " "
        + " ".join(
            f for f, v in (obj.get("legalities") or {}).items() if v in ("legal", "restricted")
        )
        + " ",
        "scryfall_uri": obj.get("scryfall_uri"),
        "updated_at": now,
        "seen_in_sync": run_id,
    }


CARD_COLUMNS = list(
    card_row({"id": "x", "name": "x", "set": "x", "collector_number": "1"}, 0, "").keys()
)
_CARD_INSERT = (
    f"INSERT INTO cards ({', '.join(CARD_COLUMNS)}) VALUES ({', '.join(':' + c for c in CARD_COLUMNS)}) "
    "ON CONFLICT(id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in CARD_COLUMNS if c != "id")
)


def set_row(obj: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "code": obj["code"],
        "name": obj["name"],
        "set_type": obj.get("set_type", "unknown"),
        "released_at": obj.get("released_at"),
        "card_count": obj.get("card_count", 0),
        "parent_set_code": obj.get("parent_set_code"),
        "digital": int(bool(obj.get("digital"))),
        "icon_svg_uri": obj.get("icon_svg_uri"),
        "updated_at": now,
    }


_SET_INSERT = (
    "INSERT INTO sets (code, name, set_type, released_at, card_count, parent_set_code, digital, "
    "icon_svg_uri, updated_at) VALUES (:code, :name, :set_type, :released_at, :card_count, "
    ":parent_set_code, :digital, :icon_svg_uri, :updated_at) ON CONFLICT(code) DO UPDATE SET "
    "name = excluded.name, set_type = excluded.set_type, released_at = excluded.released_at, "
    "card_count = excluded.card_count, parent_set_code = excluded.parent_set_code, "
    "digital = excluded.digital, icon_svg_uri = excluded.icon_svg_uri, updated_at = excluded.updated_at"
)


def upsert_sets(conn: sqlite3.Connection, sets: Iterable[dict[str, Any]]) -> int:
    now = db.now_iso()
    rows = [set_row(s, now) for s in sets]
    with db.transaction(conn):
        conn.executemany(_SET_INSERT, rows)
    return len(rows)


def upsert_cards(conn: sqlite3.Connection, cards: Iterable[dict[str, Any]], run_id: int) -> int:
    """Upsert card objects in batches. Unknown set codes get a placeholder set row."""
    now = db.now_iso()
    known_sets = {r[0] for r in conn.execute("SELECT code FROM sets")}
    total = 0
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal total
        if not batch:
            return
        with db.transaction(conn):
            for row in batch:
                if row["set_code"] not in known_sets:
                    conn.execute(
                        _SET_INSERT,
                        set_row(
                            {
                                "code": row["set_code"],
                                "name": row["set_code"].upper(),
                                "set_type": "unknown",
                            },
                            now,
                        ),
                    )
                    known_sets.add(row["set_code"])
            conn.executemany(_CARD_INSERT, batch)
        total += len(batch)
        batch.clear()

    for obj in cards:
        if obj.get("object", "card") != "card":
            continue
        batch.append(card_row(obj, run_id, now))
        if len(batch) >= BATCH:
            flush()
    flush()
    return total


def mark_canonical(conn: sqlite3.Connection) -> int:
    """Flag one printing per oracle_id: English, paper, non-digital, most recent."""
    with db.transaction(conn):
        conn.execute("DROP TABLE IF EXISTS temp.canon")
        conn.execute(
            """
            CREATE TEMP TABLE canon AS
            SELECT rid FROM (
              SELECT rowid AS rid, ROW_NUMBER() OVER (
                PARTITION BY COALESCE(oracle_id, id)
                ORDER BY (lang = 'en') DESC, paper DESC, digital ASC, released_at DESC, collector_number
              ) AS rn FROM cards
            ) WHERE rn = 1
            """
        )
        conn.execute(
            "UPDATE cards SET is_canonical = 0 WHERE is_canonical = 1 "
            "AND rowid NOT IN (SELECT rid FROM temp.canon)"
        )
        cur = conn.execute(
            "UPDATE cards SET is_canonical = 1 WHERE is_canonical = 0 "
            "AND rowid IN (SELECT rid FROM temp.canon)"
        )
        conn.execute("DROP TABLE temp.canon")
    return cur.rowcount


def sync_catalogue(
    conn: sqlite3.Connection,
    client: ScryfallClient,
    force: bool = False,
    cards: Iterable[dict[str, Any]] | None = None,
    sets: Iterable[dict[str, Any]] | None = None,
) -> SyncResult:
    """Refresh sets and cards. ``cards``/``sets`` override the network for tests."""
    run_id = start_run(conn, "catalogue")
    try:
        bulk_updated_at: str | None = None
        if cards is None:
            item = client.bulk_item(BULK_KIND)
            bulk_updated_at = item.get("updated_at")
            previous = last_run(conn, "catalogue")
            if previous and not force and previous["detail"]:
                if json.loads(previous["detail"]).get("bulk_updated_at") == bulk_updated_at:
                    finish_run(conn, run_id, "skipped", {"bulk_updated_at": bulk_updated_at})
                    return SyncResult(run_id, 0, 0, skipped=True, bulk_updated_at=bulk_updated_at)
            cards = iter_bulk_download(client, item["jsonl_download_uri"])
        if sets is None:
            sets = client.sets()
        n_sets = upsert_sets(conn, sets)
        n_cards = upsert_cards(conn, cards, run_id)
        mark_canonical(conn)
        conn.execute("ANALYZE")
        conn.execute("INSERT INTO cards_fts(cards_fts) VALUES ('optimize')")
        finish_run(
            conn,
            run_id,
            "ok",
            {"cards": n_cards, "sets": n_sets, "bulk_updated_at": bulk_updated_at},
        )
        log.info("Catalogue sync: %d cards, %d sets", n_cards, n_sets)
        return SyncResult(run_id, n_cards, n_sets, bulk_updated_at=bulk_updated_at)
    except Exception as exc:
        finish_run(conn, run_id, "error", {"error": str(exc)})
        raise
