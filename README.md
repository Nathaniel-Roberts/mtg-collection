# MTG Collection

Self-hosted Magic: The Gathering collection manager: phone-camera scanning, a database of
everything you own with daily Scryfall prices and AUD conversion, deck building, and an
MCP server so an AI assistant can see the collection and build decks from it.

Status: Phase 2 (backend, database, Scryfall sync, pricing). See ARCHITECTURE.md for the
design and RESEARCH.md for the research behind it. The PWA, scanner, MCP server and
Docker release pipeline arrive in later phases.

## Run locally

```sh
cp .env.example .env            # set DEV_MODE=true and DATA_DIR=./data for development
uv sync
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
```

The first start downloads the Scryfall bulk file (about 80 MB) and loads roughly 120k
printings into SQLite, which takes a few minutes. `GET /api/v1/status` shows progress.

## Tests

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Licence

AGPL-3.0-or-later. See LICENSE.
