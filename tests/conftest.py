from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.pricing.fx import Rates, store_rates
from app.scryfall.bulk import iter_bulk_file
from app.scryfall.sync import sync_catalogue

FIXTURES = Path(__file__).parent / "fixtures"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "dev_mode": True,
        "sync_on_start": False,
        "fx_provider": "manual",
        "scanner_enabled": False,
        "_env_file": None,
    }
    values.update(overrides)
    settings = Settings(**values)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def seed_catalogue(conn: sqlite3.Connection) -> None:
    sets = json.loads((FIXTURES / "sets.json").read_text())["data"]
    sync_catalogue(conn, client=None, cards=iter_bulk_file(FIXTURES / "cards.jsonl"), sets=sets)  # type: ignore[arg-type]
    store_rates(conn, "2026-10-08", {"USD": 1.5, "EUR": 1.6}, "test")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = db.connect(settings.db_path)
    db.migrate(connection)
    seed_catalogue(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def rates() -> Rates:
    return Rates(usd_aud=1.5, eur_aud=1.6, source="test", day="2026-10-08")


@pytest.fixture
def client(settings: Settings, conn: sqlite3.Connection) -> Iterator[TestClient]:
    app = create_app(settings, run_scheduler=False)
    with TestClient(app) as test_client:
        yield test_client


def card_id(conn: sqlite3.Connection, name: str, set_code: str | None = None) -> str:
    sql = "SELECT id FROM cards WHERE name = ?"
    params: list = [name]
    if set_code:
        sql += " AND set_code = ?"
        params.append(set_code)
    sql += " ORDER BY released_at DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    assert row is not None, f"fixture card {name!r} missing"
    return row["id"]
