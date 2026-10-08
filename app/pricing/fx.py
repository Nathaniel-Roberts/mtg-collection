"""Exchange rates to AUD.

Frankfurter v1 serves ECB reference rates with no key (https://frankfurter.dev). A manual
override (settings table, then environment) wins over fetched rates. The rate used is
stored next to every daily value rollup so history is reproducible.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import httpx

from app import db
from app.config import Settings

log = logging.getLogger(__name__)

BASES = ("USD", "EUR")


@dataclass(frozen=True)
class Rates:
    usd_aud: float | None
    eur_aud: float | None
    source: str
    day: str | None

    def aud(self, usd: str | None, eur: str | None, quantity: int = 1) -> float | None:
        """AUD for a Scryfall price pair (times quantity): USD first, EUR if USD is missing."""
        if usd is not None and self.usd_aud:
            return round(float(usd) * self.usd_aud * quantity, 2)
        if eur is not None and self.eur_aud:
            return round(float(eur) * self.eur_aud * quantity, 2)
        return None


def fetch_frankfurter(base_url: str, http: httpx.Client | None = None) -> dict[str, float]:
    client = http or httpx.Client(timeout=15.0)
    try:
        rates: dict[str, float] = {}
        for base in BASES:
            response = client.get(
                f"{base_url.rstrip('/')}/latest", params={"base": base, "symbols": "AUD"}
            )
            response.raise_for_status()
            rates[base] = float(response.json()["rates"]["AUD"])
        return rates
    finally:
        if http is None:
            client.close()


def store_rates(conn: sqlite3.Connection, day: str, rates: dict[str, float], source: str) -> None:
    with db.transaction(conn):
        for base, rate in rates.items():
            conn.execute(
                "INSERT INTO fx_rates (day, base, quote, rate, source) VALUES (?, ?, 'AUD', ?, ?) "
                "ON CONFLICT(day, base, quote) DO UPDATE SET rate = excluded.rate, source = excluded.source",
                (day, base, rate, source),
            )


def _latest_stored(conn: sqlite3.Connection, base: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT day, rate, source FROM fx_rates WHERE base = ? AND quote = 'AUD' ORDER BY day DESC LIMIT 1",
        (base,),
    ).fetchone()


def _override(conn: sqlite3.Connection, settings: Settings, base: str) -> float | None:
    value = db.get_setting(conn, f"fx_override_{base.lower()}_aud")
    if value:
        try:
            return float(value)
        except ValueError:
            log.warning("Ignoring bad FX override for %s: %r", base, value)
    env = settings.fx_usd_aud if base == "USD" else settings.fx_eur_aud
    return env


def current_rates(conn: sqlite3.Connection, settings: Settings) -> Rates:
    """Rates in effect now: overrides first, then the latest stored fetch."""
    usd_override = _override(conn, settings, "USD")
    eur_override = _override(conn, settings, "EUR")
    usd_row = _latest_stored(conn, "USD")
    eur_row = _latest_stored(conn, "EUR")
    usd = usd_override if usd_override is not None else (usd_row["rate"] if usd_row else None)
    eur = eur_override if eur_override is not None else (eur_row["rate"] if eur_row else None)
    if usd_override is not None or eur_override is not None:
        source = "manual"
    else:
        source = usd_row["source"] if usd_row else "none"
    day = usd_row["day"] if usd_row and usd_override is None else None
    return Rates(usd_aud=usd, eur_aud=eur, source=source, day=day)


def refresh_rates(
    conn: sqlite3.Connection, settings: Settings, day: str, http: httpx.Client | None = None
) -> Rates:
    """Fetch today's rates when the provider is frankfurter; manual mode just reads overrides."""
    if settings.fx_provider == "frankfurter":
        try:
            rates = fetch_frankfurter(settings.frankfurter_base, http)
            store_rates(conn, day, rates, "frankfurter")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("FX fetch failed, keeping the last stored rate: %s", exc)
    return current_rates(conn, settings)
