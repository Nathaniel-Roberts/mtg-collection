from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import decks, rules, suggest
from app.api.deps import get_conn, get_rates, get_user
from app.auth import Identity
from app.pricing.fx import Rates

router = APIRouter(prefix="/api/v1", tags=["decks"], dependencies=[Depends(get_user)])


class DeckCreate(BaseModel):
    name: str
    format: str
    description: str | None = None


class DeckUpdate(BaseModel):
    name: str | None = None
    format: str | None = None
    description: str | None = None
    archived: bool | None = None


class DeckCardItem(BaseModel):
    card_id: str
    quantity: int = Field(1, ge=0, le=999)
    role: str = "main"


class DeckCards(BaseModel):
    cards: list[DeckCardItem]
    mode: str = "set"  # set or add


class TextImport(BaseModel):
    text: str
    replace: bool = False


class SuggestRequest(BaseModel):
    commander_id: str | None = None
    color_identity: str | None = None
    theme: str | None = None
    format: str = "commander"
    exclude_deck_id: int | None = None
    limit: int = Field(40, ge=1, le=200)


def _deck_or_404(
    conn: sqlite3.Connection, deck_id: int, rates: Rates, with_validation: bool = True
):
    deck = decks.get(conn, deck_id, rates, with_validation=with_validation)
    if deck is None:
        raise HTTPException(status_code=404, detail="No such deck")
    return deck


@router.get("/decks")
def list_decks(include_archived: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": decks.list_all(conn, include_archived=include_archived)}


@router.post("/decks", status_code=201)
def create_deck(
    body: DeckCreate,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    user: Identity = Depends(get_user),
):
    try:
        deck_id = decks.create(
            conn,
            name=body.name,
            format_key=body.format,
            description=body.description,
            created_by=user.label,
        )
    except decks.DeckError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _deck_or_404(conn, deck_id, rates)


@router.get("/decks/conflicts")
def conflicts(
    include_acknowledged: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    return {"items": decks.conflicts(conn, rates, include_acknowledged=include_acknowledged)}


@router.post("/decks/conflicts/{oracle_id}/ack")
def ack_conflict(
    oracle_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
    user: Identity = Depends(get_user),
):
    if not decks.acknowledge_conflict(conn, oracle_id, rates, by=user.label):
        raise HTTPException(status_code=404, detail="No conflict for that card")
    return {"acknowledged": True}


@router.delete("/decks/conflicts/{oracle_id}/ack")
def unack_conflict(oracle_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return {"acknowledged": not decks.clear_acknowledgement(conn, oracle_id)}


@router.post("/decks/suggest")
def suggest_cards(
    body: SuggestRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    if body.format not in rules.FORMATS:
        raise HTTPException(status_code=422, detail=f"Unknown format {body.format!r}")
    return suggest.suggest(
        conn,
        rates,
        commander_id=body.commander_id,
        color_identity=body.color_identity,
        theme=body.theme,
        format_key=body.format,
        exclude_deck_id=body.exclude_deck_id,
        limit=body.limit,
    )


@router.get("/decks/{deck_id}")
def get_deck(
    deck_id: int, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    return _deck_or_404(conn, deck_id, rates)


@router.patch("/decks/{deck_id}")
def patch_deck(
    deck_id: int,
    body: DeckUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    try:
        ok = decks.update(
            conn,
            deck_id,
            name=body.name,
            format_key=body.format,
            description=body.description,
            archived=body.archived,
        )
    except decks.DeckError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="No such deck")
    return _deck_or_404(conn, deck_id, rates)


@router.delete("/decks/{deck_id}")
def delete_deck(deck_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not decks.delete(conn, deck_id):
        raise HTTPException(status_code=404, detail="No such deck")
    return {"deleted": True}


@router.put("/decks/{deck_id}/cards")
def put_cards(
    deck_id: int,
    body: DeckCards,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    try:
        decks.set_cards(conn, deck_id, [c.model_dump() for c in body.cards], mode=body.mode)
    except decks.DeckError as exc:
        raise HTTPException(
            status_code=422 if "Unknown" not in str(exc) or "card" in str(exc) else 404,
            detail=str(exc),
        ) from exc
    return _deck_or_404(conn, deck_id, rates)


@router.get("/decks/{deck_id}/validate")
def validate(
    deck_id: int, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    deck = _deck_or_404(conn, deck_id, rates)
    return deck["validation"]


@router.get("/decks/{deck_id}/export")
def export(
    deck_id: int, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    try:
        text = decks.export_text(conn, deck_id, rates)
    except decks.DeckError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(text, media_type="text/plain")


@router.post("/decks/{deck_id}/import")
def import_text(
    deck_id: int,
    body: TextImport,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    _deck_or_404(conn, deck_id, rates, with_validation=False)
    lines = decks.parse_text(body.text)
    resolved, unresolved = decks.resolve_lines(conn, lines, request.app.state.name_index)
    if body.replace:
        conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
    decks.set_cards(conn, deck_id, resolved, mode="add")
    deck = _deck_or_404(conn, deck_id, rates)
    deck["unresolved"] = unresolved
    return deck
