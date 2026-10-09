# MTG Collection

Self-hosted Magic: The Gathering collection manager: phone-camera scanning, a database of
everything you own with daily Scryfall prices and AUD conversion, deck building, and an
MCP server so an AI assistant can see the collection and build decks from it.

Status: Phase 3 (backend, PWA and scanner). See ARCHITECTURE.md for the design and
RESEARCH.md for the research behind it. The MCP server and the Docker release pipeline
arrive in the next phases.

## How identification works

A photo goes through four stages: CollectorVision finds the card's corners and dewarps
it, its milo embedder matches the artwork against a catalogue of every Scryfall printing,
RapidOCR reads the collector line on the bottom edge to pin the exact printing, and a
resolver merges the two into a ranked list. The first scan after a start loads the models
and downloads the image catalogue (about 35 MB) into the data volume.

## Run locally

```sh
cp .env.example .env            # set DEV_MODE=true and DATA_DIR=./data for development
uv sync
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
```

Open http://127.0.0.1:8000. The camera only works over HTTPS or on localhost, so test
the scanner on the machine itself or through the tunnel. On NixOS the native wheels need
`LD_LIBRARY_PATH` pointing at `stdenv.cc.cc.lib`, `zlib`, `glib` and `libGL`.

The first start downloads the Scryfall bulk file (about 80 MB) and loads roughly 120k
printings into SQLite, which takes a few minutes. `GET /api/v1/status` shows progress.

## Tests

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Licence

AGPL-3.0-or-later. See LICENSE.
