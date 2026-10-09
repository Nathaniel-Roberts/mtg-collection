from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db, scheduler
from app.api.deps import get_conn, get_settings, get_user
from app.config import Settings

router = APIRouter(prefix="/api/v1", tags=["system"], dependencies=[Depends(get_user)])


class SyncRequest(BaseModel):
    kind: Literal["all", "catalogue", "prices", "fx"] = "all"
    force: bool = False


class SettingsUpdate(BaseModel):
    fx_override_usd_aud: float | None = None
    fx_override_eur_aud: float | None = None
    default_condition: str | None = None
    default_finish: str | None = None
    scan_auto_confirm: bool | None = None
    clear: list[str] = []


SETTING_KEYS = (
    "fx_override_usd_aud",
    "fx_override_eur_aud",
    "default_condition",
    "default_finish",
    "scan_auto_confirm",
)


@router.get("/status")
def status(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    out = scheduler.status(conn, request.app.state.runner, settings)
    out["version"] = request.app.state.version
    out["access_enabled"] = settings.access_enabled
    out["dev_mode"] = settings.dev_mode
    out["scanner"] = request.app.state.scanner.status()
    return out


@router.post("/sync/run", status_code=202)
async def run_sync(body: SyncRequest, request: Request):
    runner = request.app.state.runner
    if runner.busy:
        raise HTTPException(status_code=409, detail=f"A job is already running ({runner.current})")

    async def go() -> None:
        try:
            await asyncio.to_thread(runner.run, body.kind, force=body.force)
        except Exception:  # logged by the runner
            pass

    request.app.state.background.add(asyncio.create_task(go()))
    return {"started": body.kind}


@router.get("/settings")
def read_settings(conn: sqlite3.Connection = Depends(get_conn)):
    return {key: db.get_setting(conn, key) for key in SETTING_KEYS}


@router.put("/settings")
def write_settings(body: SettingsUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    with db.transaction(conn):
        for key in body.clear:
            if key in SETTING_KEYS:
                db.set_setting(conn, key, None)
        for key in SETTING_KEYS:
            value = getattr(body, key)
            if value is not None:
                db.set_setting(
                    conn, key, str(value).lower() if isinstance(value, bool) else str(value)
                )
    return {key: db.get_setting(conn, key) for key in SETTING_KEYS}


@router.get("/backup")
def backup(settings: Settings = Depends(get_settings)):
    """A consistent copy of the SQLite database, made with the online backup API."""
    import sqlite3 as _sqlite3
    import tempfile
    from datetime import datetime

    from fastapi.responses import FileResponse

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(tempfile.gettempdir()) / f"mtg-collection-{stamp}.db"
    source = _sqlite3.connect(str(settings.db_path))
    dest = _sqlite3.connect(str(target))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return FileResponse(target, media_type="application/vnd.sqlite3", filename=target.name)


@router.get("/formats")
def formats():
    from app import rules

    return rules.format_summary()
