"""Scanner lifecycle: lazy model loading, serialised identification, scan records."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import catalogue, collection, db
from app.config import Settings
from app.pricing.fx import Rates
from app.scan.pipeline import Identifier, ScanContext, decode_image

log = logging.getLogger(__name__)


class ScannerUnavailable(RuntimeError):
    pass


class Scanner:
    """Owns the models and the identifier. Loading happens once, in a worker thread."""

    def __init__(
        self, settings: Settings, build: Callable[[Settings], Identifier] | None = None
    ) -> None:
        self.settings = settings
        self._build = build or build_identifier
        self._identifier: Identifier | None = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self.loading = False
        self.error: str | None = None
        self.loaded_at: float | None = None

    @property
    def ready(self) -> bool:
        return self._identifier is not None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.scanner_enabled,
            "ready": self.ready,
            "loading": self.loading,
            "error": self.error,
            "versions": self._identifier.versions if self._identifier else {},
            "loaded_at": self.loaded_at,
        }

    def warm(self) -> None:
        """Load models and the catalog. Safe to call repeatedly."""
        if self._identifier is not None or not self.settings.scanner_enabled:
            return
        with self._load_lock:
            if self._identifier is not None:
                return
            self.loading = True
            self.error = None
            started = time.perf_counter()
            try:
                self._identifier = self._build(self.settings)
                self.loaded_at = time.time()
                log.info("Scanner ready in %.1fs", time.perf_counter() - started)
            except Exception as exc:
                self.error = str(exc)
                log.exception("Scanner failed to load")
            finally:
                self.loading = False

    def identify(
        self, image_bytes: bytes, *, prewarped: bool = False, hint_set: str | None = None
    ) -> ScanContext:
        if not self.settings.scanner_enabled:
            raise ScannerUnavailable("The scanner is disabled (SCANNER_ENABLED=false)")
        if self._identifier is None:
            self.warm()
        if self._identifier is None:
            raise ScannerUnavailable(
                self.error or "The scanner is still loading; try again shortly"
            )
        ctx = ScanContext(image=decode_image(image_bytes), prewarped=prewarped, hint_set=hint_set)
        with self._lock:  # one identification at a time keeps memory predictable
            return self._identifier.identify(ctx)


def build_identifier(settings: Settings) -> Identifier:
    """Real stages: CollectorVision detection and embedding, RapidOCR, database resolver."""
    import collector_vision as cvg
    from rapidocr import RapidOCR

    from app.scan.detect import DetectStage
    from app.scan.embed import EmbedStage
    from app.scan.ocr import OCRStage
    from app.scan.resolve import ResolveStage

    cache_dir = settings.data_dir / "cv-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    detector = cvg.NeuralCornerDetector(provider="cpu")
    catalog = cvg.CatalogV2.load(
        settings.cv_catalog, cache_dir=cache_dir, offline=settings.cv_offline
    )
    # Detection defaults scale the short side up to 736 px, which turns a wide strip into
    # a 3000 px image and a one-second detection. Cap the long side instead.
    ocr = RapidOCR(
        params={
            "Global.use_cls": False,
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "Global.text_score": 0.4,
        }
    )

    def connect() -> sqlite3.Connection:
        return db.connect(settings.db_path)

    def known_sets() -> set[str]:
        conn = connect()
        try:
            return {r[0] for r in conn.execute("SELECT code FROM sets")}
        finally:
            conn.close()

    try:
        cv_version = importlib.metadata.version("collectorvision")
    except importlib.metadata.PackageNotFoundError:
        cv_version = "unknown"
    versions = {
        "collectorvision": cv_version,
        "catalog": f"{getattr(catalog, 'source', '?')}/{getattr(catalog, 'algo_key', '?')}",
        "catalog_rows": len(getattr(catalog, "card_ids", []) or []),
        "ocr": "rapidocr",
    }
    stages = [
        DetectStage(detector),
        EmbedStage(catalog),
        OCRStage(ocr, known_sets),
        ResolveStage(connect, gap=settings.scan_gap, min_score=settings.scan_min_score),
    ]
    return Identifier(stages, versions)


# --- scan records ---------------------------------------------------------------------------


def scans_dir(settings: Settings) -> Path:
    path = settings.data_dir / "scans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_scan(conn: sqlite3.Connection, settings: Settings, ctx: ScanContext) -> int:
    with db.transaction(conn):
        cur = conn.execute(
            "INSERT INTO scans (created_at, result, outcome) VALUES (?, ?, 'pending')",
            (db.now_iso(), json.dumps(ctx.summary())),
        )
        scan_id = int(cur.lastrowid)
    if ctx.crop is not None and settings.scan_keep_images > 0:
        path = scans_dir(settings) / f"{scan_id}.jpg"
        try:
            ctx.crop.save(path, "JPEG", quality=85)
            conn.execute("UPDATE scans SET image_path = ? WHERE id = ?", (str(path), scan_id))
            prune_images(conn, settings)
        except OSError as exc:
            log.warning("Could not keep scan image: %s", exc)
    return scan_id


def prune_images(conn: sqlite3.Connection, settings: Settings) -> None:
    rows = conn.execute(
        "SELECT id, image_path FROM scans WHERE image_path IS NOT NULL ORDER BY id DESC LIMIT -1 OFFSET ?",
        (settings.scan_keep_images,),
    ).fetchall()
    for row in rows:
        Path(row["image_path"]).unlink(missing_ok=True)
        conn.execute("UPDATE scans SET image_path = NULL WHERE id = ?", (row["id"],))


def scan_response(
    conn: sqlite3.Connection, scan_id: int, ctx: ScanContext, rates: Rates
) -> dict[str, Any]:
    ids = [c.card_id for c in ctx.candidates]
    rows = catalogue.get_cards(conn, ids)
    views = {cid: catalogue.card_view(rows[cid], rates) for cid in ids if cid in rows}
    match = None
    if ctx.match and ctx.match.card_id in views:
        match = {
            "card": views[ctx.match.card_id],
            "score": round(ctx.match.score, 4),
            "reason": ctx.match.reason,
            "method": ctx.method,
            "confident": ctx.confident,
        }
    return {
        "scan_id": scan_id,
        "match": match,
        "candidates": [
            {"card": views[c.card_id], "score": round(c.score, 4), "reason": c.reason}
            for c in ctx.candidates
            if c.card_id in views
        ],
        "ocr": ctx.ocr,
        "detection": ctx.detection,
        "timings_ms": {k: round(v) for k, v in ctx.timings_ms.items()},
        "errors": ctx.errors,
    }


def confirm_scan(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    card_id: str,
    finish: str,
    condition: str,
    language: str | None,
    quantity: int,
    tags: list[str] | None,
    added_by: str | None,
) -> sqlite3.Row:
    row = conn.execute("SELECT result FROM scans WHERE id = ?", (scan_id,)).fetchone()
    if row is None:
        raise LookupError("No such scan")
    result = json.loads(row["result"])
    best = (result.get("match") or {}).get("card_id")
    outcome = "confirmed_best" if best == card_id else "corrected"
    entry = collection.add(
        conn,
        card_id=card_id,
        finish=finish,
        condition=condition,
        language=language,
        quantity=quantity,
        tags=tags,
        source="scan",
        added_by=added_by,
    )
    conn.execute(
        "UPDATE scans SET chosen_card_id = ?, outcome = ? WHERE id = ?", (card_id, outcome, scan_id)
    )
    return entry


def reject_scan(conn: sqlite3.Connection, scan_id: int) -> bool:
    cur = conn.execute("UPDATE scans SET outcome = 'rejected' WHERE id = ?", (scan_id,))
    return cur.rowcount > 0


def recent_scans(conn: sqlite3.Connection, rates: Rates, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, created_at, outcome, chosen_card_id, result, image_path IS NOT NULL AS has_image "
        "FROM scans ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    ids = [
        r["chosen_card_id"] or (json.loads(r["result"]).get("match") or {}).get("card_id")
        for r in rows
    ]
    cards = catalogue.get_cards(conn, [i for i in ids if i])
    out = []
    for r, cid in zip(rows, ids, strict=True):
        result = json.loads(r["result"])
        out.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "outcome": r["outcome"],
                "card": catalogue.card_view(cards[cid], rates) if cid in cards else None,
                "method": result.get("method"),
                "confident": result.get("confident"),
                "total_ms": (result.get("timings_ms") or {}).get("total"),
                "has_image": bool(r["has_image"]),
            }
        )
    return out


def accuracy(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT outcome, COUNT(*) AS n FROM scans GROUP BY outcome").fetchall()
    return {r["outcome"] or "pending": r["n"] for r in rows}
