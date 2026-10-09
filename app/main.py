"""Application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from app import db, scheduler
from app.api import cards, collection, decks, scan, system
from app.auth import AccessVerifier
from app.catalogue import NameIndex
from app.config import Settings, load_settings
from app.mcp_server import build_mcp_server, mcp_asgi_app
from app.scan.service import Scanner
from app.scryfall.client import ScryfallClient

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def app_version() -> str:
    try:
        return version("mtg-collection")
    except PackageNotFoundError:
        return "0.0.0"


def create_app(settings: Settings | None = None, *, run_scheduler: bool = True) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.dev_mode else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = db.connect(settings.db_path)
        try:
            applied = db.migrate(conn)
            if applied:
                log.info("Applied migrations: %s", ", ".join(applied))
        finally:
            conn.close()
        if settings.dev_mode:
            log.warning(
                "DEV_MODE is on: every request acts as %s. Never expose this.",
                settings.dev_user_email,
            )
        elif not settings.access_enabled:
            log.error(
                "Cloudflare Access is not configured (CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD). "
                "All requests except /healthz will be refused."
            )
        task = (
            asyncio.create_task(scheduler.run_forever(app.state.runner, settings))
            if run_scheduler
            else None
        )
        if run_scheduler and settings.scanner_enabled:
            app.state.background.add(asyncio.create_task(asyncio.to_thread(app.state.scanner.warm)))
        try:
            async with app.state.mcp_server.session_manager.run():
                yield
        finally:
            if task:
                task.cancel()
            for t in list(app.state.background):
                t.cancel()
            app.state.scryfall.close()

    app = FastAPI(
        title="MTG Collection",
        version=app_version(),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.version = app_version()
    app.state.access_verifier = AccessVerifier(settings)
    app.state.scryfall = ScryfallClient(settings.scryfall_user_agent, settings.scryfall_api_base)
    app.state.runner = scheduler.JobRunner(settings, app.state.scryfall)
    app.state.name_index = NameIndex()
    app.state.background = set()
    app.state.scanner = Scanner(settings)
    app.state.mcp_server = build_mcp_server(settings, app.state.name_index, app_version())
    mcp_endpoint = mcp_asgi_app(app.state.mcp_server, settings, app.state.access_verifier)
    for mcp_path in ("/mcp", "/mcp/"):
        app.router.routes.append(
            Route(mcp_path, endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"], name="mcp")
        )

    app.include_router(system.router)
    app.include_router(cards.router)
    app.include_router(collection.router)
    app.include_router(decks.router)
    app.include_router(scan.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        conn = db.connect(settings.db_path)
        try:
            conn.execute("SELECT 1 FROM schema_migrations LIMIT 1")
        finally:
            conn.close()
        return {"status": "ok"}

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        index = STATIC_DIR / "index.html"
        if index.exists():

            @app.get("/", include_in_schema=False)
            def root() -> FileResponse:
                return FileResponse(index)

        sw = STATIC_DIR / "sw.js"
        if sw.exists():
            # Served from the root so the service worker scope covers the whole app.
            @app.get("/sw.js", include_in_schema=False)
            def service_worker() -> FileResponse:
                return FileResponse(
                    sw, media_type="text/javascript", headers={"Cache-Control": "no-cache"}
                )

    return app
