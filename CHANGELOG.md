# Changelog

All notable changes to this project are recorded here. Dates are DD/MM/YYYY.

## Unreleased

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
