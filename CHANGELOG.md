# Changelog

All notable changes to this project are recorded here. Dates are DD/MM/YYYY.

## 0.1.0 - 09/10/2026

- Docker image (amd64 and arm64) published to GHCR by GitHub Actions on every push to
  main and every `v*` tag; docker-compose with cloudflared, healthcheck and a named
  volume; deployment, Access, MCP connection, backup and release notes in the README.
- MCP server at `/mcp` (Streamable HTTP, mcp SDK 2.3): search_collection, search_cards,
  get_card, collection_summary, list_decks, get_deck, create_deck, update_deck,
  add_cards_to_deck, remove_cards_from_deck, validate_deck, suggest_from_collection,
  deck_conflicts, delete_deck. Accepts a Cloudflare Access JWT (person or service token)
  or the app-issued bearer token.
- PWA: scan screen with camera capture, one-tap confirm, printing picker and name
  search fallback; collection list and grid with filters, sort and value summary; card
  detail with owned rows, printings, price history and legality; decks with validation,
  conflicts, text import and export and suggestions from the collection; CSV import with
  preview; settings and status with manual sync, FX overrides and database backup.
- Scanner: CollectorVision corner detection and embedding, RapidOCR collector-line
  reading on an aspect-correct re-warp, resolver with confidence gating, scan records
  with kept crops for a test set.
- Backend: SQLite schema and migrations, Scryfall bulk catalogue sync (gzipped JSONL),
  daily price snapshots, Frankfurter AUD rates with manual override, collection entries
  with tags, deck storage with format validation and cross-deck conflict warnings, CSV
  import for ManaBox, Moxfield, Deckbox, Archidekt, TCGplayer, Dragon Shield and a
  generic layout, Cloudflare Access JWT verification, JSON API under `/api/v1`.
