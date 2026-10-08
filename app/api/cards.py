from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app import catalogue, collection
from app.api.deps import get_conn, get_rates, get_user, paging
from app.pricing.fx import Rates
from app.pricing.snapshots import card_price_history

router = APIRouter(prefix="/api/v1", tags=["cards"], dependencies=[Depends(get_user)])


@router.get("/cards/autocomplete")
def autocomplete(q: str = Query("", min_length=0), conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": catalogue.autocomplete(conn, q)}


@router.get("/cards/search")
def search(
    q: str | None = None,
    set: str | None = None,  # noqa: A002 - query parameter name
    colors: str | None = None,
    identity: str | None = None,
    type: str | None = None,  # noqa: A002
    rarity: str | None = None,
    cmc_min: float | None = None,
    cmc_max: float | None = None,
    format: str | None = None,  # noqa: A002
    owned: bool = False,
    unique: str = "cards",
    include_digital: bool = False,
    sort: str = "name",
    pages: tuple[int, int] = Depends(paging),
    conn: sqlite3.Connection = Depends(get_conn),
    rates: Rates = Depends(get_rates),
):
    page, per_page = pages
    rows, total = catalogue.search(
        conn,
        q=q,
        set_code=set,
        colors=colors,
        identity=identity,
        type_line=type,
        rarity=rarity,
        cmc_min=cmc_min,
        cmc_max=cmc_max,
        legal_in=format,
        owned=owned,
        unique=unique,
        paper_only=not include_digital,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    return {
        "items": [catalogue.card_view(r, rates) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/cards/{card_id}")
def get_card(
    card_id: str, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    row = catalogue.get_card(conn, card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such card")
    view = catalogue.card_view(row, rates)
    view["printings_count"] = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE oracle_id = ?", (row["oracle_id"],)
    ).fetchone()[0]
    view["owned_printings"] = (
        collection.owned_printings(conn, row["oracle_id"]) if row["oracle_id"] else []
    )
    view["price_history"] = card_price_history(conn, card_id, 30)
    return view


@router.get("/cards/{card_id}/printings")
def printings(
    card_id: str, conn: sqlite3.Connection = Depends(get_conn), rates: Rates = Depends(get_rates)
):
    row = catalogue.get_card(conn, card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such card")
    rows = catalogue.printings(conn, row["oracle_id"]) if row["oracle_id"] else [row]
    owned = {p["card_id"]: 0 for p in []}
    for p in collection.owned_printings(conn, row["oracle_id"]) if row["oracle_id"] else []:
        owned[p["card_id"]] = owned.get(p["card_id"], 0) + p["quantity"]
    items = []
    for r in rows:
        v = catalogue.card_view(r, rates)
        v["owned_quantity_this_printing"] = owned.get(r["id"], 0)
        items.append(v)
    return {"items": items, "total": len(items)}


@router.get("/cards/{card_id}/prices")
def prices(card_id: str, days: int = 90, conn: sqlite3.Connection = Depends(get_conn)):
    if catalogue.get_card(conn, card_id) is None:
        raise HTTPException(status_code=404, detail="No such card")
    return {"items": card_price_history(conn, card_id, days)}


@router.get("/sets")
def sets(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": catalogue.list_sets(conn)}
