"""SQLite connections and the migration runner.

Migrations are SQL files in ``migrations/`` named ``NNN_description.sql``, applied once
each in order and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA optimize")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction; the connection otherwise runs in autocommit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _version_of(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def pending_migrations(conn: sqlite3.Connection) -> list[Path]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"), key=_version_of)
    return [f for f in files if _version_of(f) not in applied]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every pending migration. Returns the names applied."""
    applied: list[str] = []
    for path in pending_migrations(conn):
        # executescript issues its own COMMIT first, so it cannot sit inside transaction().
        conn.executescript("BEGIN;\n" + path.read_text() + "\nCOMMIT;")
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (_version_of(path), path.name, now_iso()),
        )
        applied.append(path.name)
    return applied


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
