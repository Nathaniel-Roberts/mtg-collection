from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import collection
from app.api.deps import get_conn, get_rates, get_user, paging
from app.auth import Identity
from app.importers import csv as csv_import
from app.pricing.fx import Rates
from app.pricing.snapshots import value_history

router = APIRouter(prefix="/api/v1", tags=["collection"])


class EntryCreate(BaseModel):
    card_id: str
    finish: str = "nonfoil"
    condition: str = "NM"
    language: str | None = None
    quantity: int = Field(1, ge=1, le=10000)
    notes: str | None = None
    tags: list[str] | None = None


class EntryUpdate(BaseModel):
    quantity: int | None = Field(None, ge=0, le=10000)
    finish: str | None = None
    condition: str | None = None
    language: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


@router.get("/collection")
def list_entries(
    q: str | None = None,
    set: str | None = None,  # noqa: A002
    colors: str | None = None,
    identity: str | None = None,
    type: str | None = None,  # noqa: A002
    rarity: str | None = None,
    finish: str | None = None,
    condition: str | None = None,
    language: str | None = None,
    tag: str | None = None,
    format: str | None = None,  # noqa: A002
    cmc_min: float | None = None,
    cmc_max: float | None = None,
    sort: str = "name",
    pages: tuple[int, int] = Depends(paging),
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    _: Identity = Depends(get_user),
):
    page, per_page = pages
    rows, total = collection.list_entries(
        conn,
        q=q,
        set_code=set,
        colors=colors,
        identity=identity,
        type_line=type,
        rarity=rarity,
        finish=finish,
        condition=condition,
        language=language,
        tag=tag,
        legal_in=format,
        cmc_min=cmc_min,
        cmc_max=cmc_max,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    return {
        "items": [collection.entry_view(r, rates) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/collection", status_code=201)
def add_entry(
    body: EntryCreate,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    user: Identity = Depends(get_user),
):
    try:
        row = collection.add(conn, source="manual", added_by=user.label, **body.model_dump())
    except collection.CollectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return collection.entry_view(row, rates)


@router.get("/collection/summary")
def summary(
    group_by: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    _: Identity = Depends(get_user),
):
    groups = [g.strip() for g in group_by.split(",")] if group_by else None
    return collection.summary(conn, rates, groups)


@router.get("/collection/value-history")
def history(
    days: int = 365, conn: sqlite3.Connection = Depends(get_conn), _: Identity = Depends(get_user)
):
    return {"items": value_history(conn, days)}


@router.get("/collection/export.csv")
def export(conn: sqlite3.Connection = Depends(get_conn), _: Identity = Depends(get_user)):
    return PlainTextResponse(
        collection.export_csv(conn),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="collection.csv"'},
    )


@router.post("/collection/import")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    format: str = Form("auto"),  # noqa: A002
    dry_run: bool = Form(True),
    conn: sqlite3.Connection = Depends(get_conn),
    user: Identity = Depends(get_user),
):
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV is larger than 20 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        fmt, rows = csv_import.parse(text, format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    csv_import.resolve(conn, rows, request.app.state.name_index)
    out = csv_import.preview(rows, fmt)
    if not dry_run:
        out["applied"] = csv_import.apply(conn, rows, fmt, added_by=user.label)
    return out


@router.get("/tags")
def tags(conn: sqlite3.Connection = Depends(get_conn), _: Identity = Depends(get_user)):
    return {"items": collection.list_tags(conn)}


@router.get("/collection/{entry_id}")
def get_entry(
    entry_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    _: Identity = Depends(get_user),
):
    row = collection.get_entry(conn, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such entry")
    return collection.entry_view(row, rates)


@router.patch("/collection/{entry_id}")
def patch_entry(
    entry_id: int,
    body: EntryUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    _: Identity = Depends(get_user),
):
    if collection.get_entry(conn, entry_id) is None:
        raise HTTPException(status_code=404, detail="No such entry")
    try:
        row = collection.update(conn, entry_id, **body.model_dump())
    except collection.CollectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        return {"deleted": True}
    return collection.entry_view(row, rates)


@router.delete("/collection/{entry_id}")
def delete_entry(
    entry_id: int, conn: sqlite3.Connection = Depends(get_conn), _: Identity = Depends(get_user)
):
    if not collection.remove(conn, entry_id):
        raise HTTPException(status_code=404, detail="No such entry")
    return {"deleted": True}
