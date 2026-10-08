"""The daily job and its in-process scheduler.

One job, once per local day at SYNC_HOUR (and at startup when the catalogue is empty or
stale): catalogue sync from the Scryfall bulk file, price snapshot, FX refresh, value
rollup. Each step is recorded in ``sync_runs``. Runs in a worker thread so the event loop
stays free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app import db
from app.config import Settings
from app.pricing import fx, snapshots
from app.scryfall.client import ScryfallClient
from app.scryfall.sync import finish_run, last_run, start_run, sync_catalogue

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(hours=36)
LAST_RUN_KEY = "last_daily_run_day"


def local_now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.tz))


def local_day(settings: Settings) -> str:
    return local_now(settings).strftime("%Y-%m-%d")


class JobRunner:
    """Serialises job execution; the API and the scheduler share one instance."""

    def __init__(self, settings: Settings, client: ScryfallClient) -> None:
        self.settings = settings
        self.client = client
        self._lock = threading.Lock()
        self.current: str | None = None
        self.last_error: str | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None

    def run(self, kind: str = "all", *, force: bool = False) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError(f"A job is already running ({self.current})")
        self.current = kind
        conn = db.connect(self.settings.db_path)
        try:
            return self._run(conn, kind, force)
        except Exception as exc:
            self.last_error = str(exc)
            log.exception("Job %s failed", kind)
            raise
        finally:
            conn.close()
            self.current = None
            self._lock.release()

    def _run(self, conn: sqlite3.Connection, kind: str, force: bool) -> dict[str, Any]:
        day = local_day(self.settings)
        out: dict[str, Any] = {"day": day}
        if kind in ("all", "catalogue"):
            result = sync_catalogue(conn, self.client, force=force)
            out["catalogue"] = {
                "cards": result.cards,
                "sets": result.sets,
                "skipped": result.skipped,
            }
        if kind in ("all", "fx"):
            run_id = start_run(conn, "fx")
            try:
                rates = fx.refresh_rates(conn, self.settings, day)
                finish_run(
                    conn,
                    run_id,
                    "ok",
                    {"usd_aud": rates.usd_aud, "eur_aud": rates.eur_aud, "source": rates.source},
                )
                out["fx"] = {
                    "usd_aud": rates.usd_aud,
                    "eur_aud": rates.eur_aud,
                    "source": rates.source,
                }
            except Exception as exc:
                finish_run(conn, run_id, "error", {"error": str(exc)})
                raise
        if kind in ("all", "prices"):
            run_id = start_run(conn, "prices")
            try:
                n = snapshots.snapshot_prices(conn, day)
                rates = fx.current_rates(conn, self.settings)
                totals = snapshots.rollup_value(conn, day, rates)
                finish_run(conn, run_id, "ok", {"snapshots": n, **totals})
                out["prices"] = {"snapshots": n, **totals}
            except Exception as exc:
                finish_run(conn, run_id, "error", {"error": str(exc)})
                raise
        if kind == "all":
            db.set_setting(conn, LAST_RUN_KEY, day)
        return out


def catalogue_is_stale(conn: sqlite3.Connection, settings: Settings) -> bool:
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    if count == 0:
        return True
    row = last_run(conn, "catalogue")
    skipped = last_run(conn, "catalogue", status="skipped")
    latest = max((r for r in (row, skipped) if r is not None), key=lambda r: r["id"], default=None)
    if latest is None or not latest["finished_at"]:
        return True
    finished = datetime.fromisoformat(latest["finished_at"].replace("Z", "+00:00"))
    return datetime.now(finished.tzinfo) - finished > STALE_AFTER


def due_now(conn: sqlite3.Connection, settings: Settings) -> bool:
    now = local_now(settings)
    hh, mm = (int(x) for x in settings.sync_hour.split(":"))
    if (now.hour, now.minute) < (hh, mm):
        return False
    return db.get_setting(conn, LAST_RUN_KEY) != now.strftime("%Y-%m-%d")


async def run_forever(runner: JobRunner, settings: Settings, *, interval: float = 60.0) -> None:
    if settings.sync_on_start:
        conn = db.connect(settings.db_path)
        try:
            stale = catalogue_is_stale(conn, settings)
        finally:
            conn.close()
        if stale:
            log.info("Catalogue is empty or stale; running the daily job now")
            try:
                await asyncio.to_thread(runner.run, "all")
            except Exception:
                log.exception("Startup sync failed; will retry on schedule")
    while True:
        await asyncio.sleep(interval)
        try:
            conn = db.connect(settings.db_path)
            try:
                due = due_now(conn, settings)
            finally:
                conn.close()
            if due and not runner.busy:
                await asyncio.to_thread(runner.run, "all")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled job failed")


def status(conn: sqlite3.Connection, runner: JobRunner, settings: Settings) -> dict[str, Any]:
    runs: dict[str, Any] = {}
    for kind in ("catalogue", "fx", "prices", "cv_catalog"):
        row = conn.execute(
            "SELECT * FROM sync_runs WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        runs[kind] = (
            None
            if row is None
            else {**dict(row), "detail": json.loads(row["detail"]) if row["detail"] else None}
        )
    rates = fx.current_rates(conn, settings)
    return {
        "running": runner.current,
        "last_error": runner.last_error,
        "last_daily_run_day": db.get_setting(conn, LAST_RUN_KEY),
        "next_run": f"{settings.sync_hour} {settings.tz}",
        "runs": runs,
        "catalogue": {
            "cards": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
            "sets": conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0],
        },
        "fx": {
            "usd_aud": rates.usd_aud,
            "eur_aud": rates.eur_aud,
            "source": rates.source,
            "day": rates.day,
        },
    }
