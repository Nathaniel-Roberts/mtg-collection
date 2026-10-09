from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import collection
from app.api.deps import get_conn, get_rates, get_user
from app.auth import Identity
from app.pricing.fx import Rates
from app.scan import service

router = APIRouter(prefix="/api/v1", tags=["scan"], dependencies=[Depends(get_user)])

MAX_IMAGE = 10 * 1024 * 1024


class Confirm(BaseModel):
    card_id: str
    finish: str = "nonfoil"
    condition: str = "NM"
    language: str | None = None
    quantity: int = Field(1, ge=1, le=999)
    tags: list[str] | None = None


@router.post("/scan")
async def scan(
    request: Request,
    image: UploadFile = File(...),
    prewarped: bool = Form(False),
    hint_set: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    data = await image.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty image")
    if len(data) > MAX_IMAGE:
        raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    scanner: service.Scanner = request.app.state.scanner
    try:
        ctx = await asyncio.to_thread(
            scanner.identify, data, prewarped=prewarped, hint_set=hint_set
        )
    except service.ScannerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read that image: {exc}") from exc
    scan_id = service.record_scan(conn, request.app.state.settings, ctx)
    return service.scan_response(conn, scan_id, ctx, rates)


@router.post("/scan/{scan_id}/confirm", status_code=201)
def confirm(
    scan_id: int,
    body: Confirm,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    user: Identity = Depends(get_user),
):
    try:
        entry = service.confirm_scan(conn, scan_id, added_by=user.label, **body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except collection.CollectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return collection.entry_view(entry, rates)


@router.post("/scan/{scan_id}/reject")
def reject(scan_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not service.reject_scan(conn, scan_id):
        raise HTTPException(status_code=404, detail="No such scan")
    return {"rejected": True}


@router.get("/scan/recent")
def recent(
    limit: int = 20, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    return {
        "items": service.recent_scans(conn, rates, max(1, min(limit, 100))),
        "outcomes": service.accuracy(conn),
    }


@router.get("/scan/{scan_id}/image")
def image(scan_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT image_path FROM scans WHERE id = ?", (scan_id,)).fetchone()
    if row is None or not row["image_path"] or not Path(row["image_path"]).exists():
        raise HTTPException(status_code=404, detail="No image kept for that scan")
    return FileResponse(row["image_path"], media_type="image/jpeg")


@router.post("/scanner/warm", status_code=202)
async def warm(request: Request):
    scanner: service.Scanner = request.app.state.scanner
    if not scanner.ready and not scanner.loading:
        request.app.state.background.add(asyncio.create_task(asyncio.to_thread(scanner.warm)))
    return scanner.status()
