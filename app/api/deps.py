"""Shared FastAPI dependencies."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends, Request

from app import db
from app.auth import Identity, require_user
from app.config import Settings
from app.pricing.fx import Rates, current_rates


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_rates(
    conn: sqlite3.Connection = Depends(get_conn), settings: Settings = Depends(get_settings)
) -> Rates:
    return current_rates(conn, settings)


def get_user(identity: Identity = Depends(require_user)) -> Identity:
    return identity


def paging(page: int = 1, per_page: int = 50) -> tuple[int, int]:
    return max(1, page), max(1, min(per_page, 200))
