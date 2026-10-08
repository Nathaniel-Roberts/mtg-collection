"""Daily price snapshots and the collection value rollup."""

from __future__ import annotations

import sqlite3

from app import db
from app.pricing.fx import Rates

PRICE_KEYS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "eur_etched", "tix")

# SQL fragments that pick the price for an entry's finish from the card's numeric price
# columns (filled at sync time; parsing the prices JSON per expression was the main cost
# in summaries).
USD_FOR_FINISH = (
    "CASE e.finish WHEN 'foil' THEN c.usd_foil WHEN 'etched' THEN c.usd_etched ELSE c.usd END"
)
EUR_FOR_FINISH = (
    "CASE e.finish WHEN 'foil' THEN c.eur_foil WHEN 'etched' THEN c.eur_etched ELSE c.eur END"
)


def snapshot_prices(conn: sqlite3.Connection, day: str) -> int:
    """Record today's prices for every card that is owned or in a deck."""
    cols = ", ".join(PRICE_KEYS)
    extracts = ", ".join(
        f"json_extract(prices, '$.{k}')" for k in PRICE_KEYS
    )  # strings, as Scryfall gives them
    with db.transaction(conn):
        cur = conn.execute(
            f"""
            INSERT INTO price_snapshots (card_id, day, {cols})
            SELECT id, ?, {extracts} FROM cards
            WHERE id IN (SELECT card_id FROM collection_entries UNION SELECT card_id FROM deck_cards)
            ON CONFLICT(card_id, day) DO UPDATE SET
              usd = excluded.usd, usd_foil = excluded.usd_foil, usd_etched = excluded.usd_etched,
              eur = excluded.eur, eur_foil = excluded.eur_foil, eur_etched = excluded.eur_etched,
              tix = excluded.tix
            """,
            (day,),
        )
    return cur.rowcount


def collection_totals(conn: sqlite3.Connection, rates: Rates) -> dict[str, float | int]:
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT c.oracle_id) AS cards,
               COALESCE(SUM(e.quantity), 0) AS copies,
               COUNT(*) AS entries,
               COALESCE(SUM(e.quantity * CAST({USD_FOR_FINISH} AS REAL)), 0) AS usd,
               COALESCE(SUM(e.quantity * CAST({EUR_FOR_FINISH} AS REAL)), 0) AS eur,
               COALESCE(SUM(CASE WHEN {USD_FOR_FINISH} IS NOT NULL OR {EUR_FOR_FINISH} IS NOT NULL
                   THEN 1 ELSE 0 END), 0) AS priced_entries,
               COALESCE(SUM(e.quantity * CASE
                   WHEN {USD_FOR_FINISH} IS NOT NULL THEN CAST({USD_FOR_FINISH} AS REAL) * :usd_aud
                   WHEN {EUR_FOR_FINISH} IS NOT NULL THEN CAST({EUR_FOR_FINISH} AS REAL) * :eur_aud
                   ELSE 0 END), 0) AS aud
        FROM collection_entries e CROSS JOIN cards c ON c.id = e.card_id
        """,
        {"usd_aud": rates.usd_aud or 0.0, "eur_aud": rates.eur_aud or 0.0},
    ).fetchone()
    return {
        "cards": row["cards"],
        "copies": row["copies"],
        "entries": row["entries"],
        "usd": round(row["usd"], 2),
        "eur": round(row["eur"], 2),
        "aud": round(row["aud"], 2) if rates.usd_aud or rates.eur_aud else None,
        "priced_entries": row["priced_entries"],
    }


def rollup_value(conn: sqlite3.Connection, day: str, rates: Rates) -> dict[str, float | int]:
    totals = collection_totals(conn, rates)
    with db.transaction(conn):
        conn.execute(
            """
            INSERT INTO collection_value_daily (day, cards, entries, usd, eur, aud, usd_aud, eur_aud, priced_entries)
            VALUES (:day, :cards, :entries, :usd, :eur, :aud, :usd_aud, :eur_aud, :priced_entries)
            ON CONFLICT(day) DO UPDATE SET cards = excluded.cards, entries = excluded.entries,
              usd = excluded.usd, eur = excluded.eur, aud = excluded.aud, usd_aud = excluded.usd_aud,
              eur_aud = excluded.eur_aud, priced_entries = excluded.priced_entries
            """,
            {
                "day": day,
                "cards": totals["cards"],
                "entries": totals["entries"],
                "usd": totals["usd"],
                "eur": totals["eur"],
                "aud": totals["aud"] or 0.0,
                "usd_aud": rates.usd_aud or 0.0,
                "eur_aud": rates.eur_aud or 0.0,
                "priced_entries": totals["priced_entries"],
            },
        )
    return totals


def value_history(conn: sqlite3.Connection, days: int = 365) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collection_value_daily ORDER BY day DESC LIMIT ?", (max(1, days),)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def card_price_history(conn: sqlite3.Connection, card_id: str, days: int = 90) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM price_snapshots WHERE card_id = ? ORDER BY day DESC LIMIT ?",
        (card_id, max(1, days)),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]
