# Architecture

Phase 1 output, 09/10/2026. Builds on RESEARCH.md. This is the contract the later phases
implement; if a phase needs to deviate, it updates this file first.

## 1. Shape

One Python process serving three things on one port, plus a cloudflared sidecar:

```
phone / browser  --Cloudflare Access (OAuth IdP)-->  tunnel  -->  app:8000  /            PWA (static HTML, JS)
                                                                            /api/v1/...  JSON API
Claude Code / claude.ai  --Access service token or bearer-->  tunnel  -->  app:8000  /mcp         Streamable HTTP
                                                                            /healthz
                                                                 app process also runs the daily sync job
```

- Python 3.13, FastAPI, uvicorn. SQLite in WAL mode on a named volume. No ORM: the schema
  needs an FTS5 virtual table with sync triggers, SQLite pragmas and grouping queries that
  are SQL-shaped, and an ORM would get in the way of all three. Plain `sqlite3` with
  numbered SQL migration files applied at startup.
- Settings via pydantic-settings: typed, validated at startup, reads `.env` locally and
  the environment in Docker, and fails fast with a clear message on a bad value.
- Single tenant. Cloudflare Access decides who may enter; the app verifies the JWT and
  records the email on writes for audit, but there is one collection and one set of decks.
- The PWA is static files served by FastAPI, written with Preact and htm vendored into
  `static/vendor` (about 15 KB, MIT). That gives components and state for the scan queue
  and confirm flow without a build step or Node in the image; plain JavaScript would do it
  but gets hard to keep tidy past three screens. Charts are hand-drawn inline SVG. Service
  worker for installability and an offline shell only; every data call needs the network.
- The MCP server is the mcp SDK 2.3.x `MCPServer`, mounted at `/mcp` in the same process,
  sharing the same database and the same domain code as the API.
- Identification runs in-process: CollectorVision (ONNX via onnxruntime CPU) and RapidOCR.
  Models are in the image; the CollectorVision catalog downloads into the volume on first
  use and updates incrementally.
- Licence: AGPL-3.0-or-later for the repository, as decided in Phase 0.

Why one container: SQLite needs one writer, the scanner wants the catalog and models in
memory next to the API, and the MCP server needs the same domain code. Splitting would
add a network hop and a second deploy artefact for no gain. cloudflared is the only other
container. The compose and tunnel layout follows the pattern already running on the Home
Assistant host; nothing else is inherited from other projects.

## 2. Repository layout

```
mtg-collection/
  app/
    main.py            create_app(): routers, static mount, /mcp mount, lifespan
    config.py          pydantic-settings Settings
    db.py              connect(), transaction(), migrate()
    auth.py            Access JWT verification (PyJWT + PyJWKClient), identity dependency
    scheduler.py       daily sync loop
    scryfall/
      client.py        httpx client: User-Agent, Accept, token bucket (10/s, 2/s on search/named/collection), 429 handling
      bulk.py          fetch /bulk-data, stream default_cards jsonl.gz
      sync.py          upsert cards and sets, mark missing, record sync_runs
    pricing/
      fx.py            Frankfurter fetch, manual override
      snapshots.py     daily price snapshot + collection value rollup
    catalogue.py       card and set queries, FTS search, autocomplete, printings
    collection.py      entries, tags, summary, value
    decks.py           decks, deck cards, export and import of text lists
    rules.py           format rules table, validate(), ownership report
    suggest.py         suggest_from_collection heuristics
    importers/
      csv.py           header sniffing, per-format column maps, resolve to card ids
    scan/
      pipeline.py      Stage protocol, Identifier that chains detect -> embed -> ocr -> resolve
      detect.py        CollectorVision corner detection and dewarp
      embed.py         milo embedding and catalog sweep
      ocr.py           RapidOCR on the collector-line strip, parser
      resolve.py       merge candidates, gap gate, exact printing pin
    api/
      scan.py cards.py collection.py decks.py prices.py system.py   FastAPI routers
    mcp_server.py      MCPServer tools, auth middleware, ASGI mount
  static/              index.html, app.js, pages/*.js, styles.css, sw.js, manifest.webmanifest, icons, vendor/ (preact, htm)
  migrations/          001_initial.sql ...
  tests/
    fixtures/photos/   real phone photos named <scryfall_id>.jpg (gitignored, opt-in)
    test_scan_*.py test_mcp_*.py test_api_*.py test_rules.py test_importers.py
  Dockerfile  docker-compose.yml  .env.example  pyproject.toml  uv.lock
  README.md  CHANGELOG.md  LICENSE  RESEARCH.md  ARCHITECTURE.md
  .github/workflows/tests.yml  docker-build.yml
```

## 3. Data model

All timestamps are ISO 8601 UTC text. Money is stored as the decimal string Scryfall
gives (or REAL for computed totals); AUD is computed at read time or at rollup time with
the rate recorded next to it. Primary keys for catalogue rows are Scryfall UUIDs.

### Catalogue (replaced daily from the Scryfall bulk file)

```sql
CREATE TABLE sets (
  code TEXT PRIMARY KEY,            -- Scryfall set code
  name TEXT NOT NULL,
  set_type TEXT NOT NULL,
  released_at TEXT,
  card_count INTEGER NOT NULL DEFAULT 0,
  parent_set_code TEXT,
  digital INTEGER NOT NULL DEFAULT 0,
  icon_svg_uri TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE cards (
  id TEXT PRIMARY KEY,              -- Scryfall card id (one row per printing and language)
  oracle_id TEXT,                   -- null only for reversible_card layout
  name TEXT NOT NULL,
  printed_name TEXT,
  lang TEXT NOT NULL,
  set_code TEXT NOT NULL REFERENCES sets(code),
  collector_number TEXT NOT NULL,
  released_at TEXT,
  rarity TEXT NOT NULL,
  layout TEXT NOT NULL,
  type_line TEXT,
  oracle_text TEXT,                 -- faces joined with " // "
  mana_cost TEXT,
  cmc REAL,
  colors TEXT,                      -- "WUBRG" subset, sorted, "" for colourless
  color_identity TEXT,              -- same encoding
  keywords TEXT,                    -- JSON array
  power TEXT, toughness TEXT, loyalty TEXT,
  legalities TEXT NOT NULL,         -- JSON object as Scryfall gives it
  finishes TEXT NOT NULL,           -- JSON array: nonfoil, foil, etched
  promo INTEGER NOT NULL DEFAULT 0,
  digital INTEGER NOT NULL DEFAULT 0,
  paper INTEGER NOT NULL DEFAULT 1, -- "paper" in games
  reprint INTEGER NOT NULL DEFAULT 0,
  full_art INTEGER NOT NULL DEFAULT 0,
  frame TEXT, border_color TEXT, artist TEXT,
  illustration_id TEXT,
  edhrec_rank INTEGER,
  image_small TEXT, image_normal TEXT, image_large TEXT, image_art_crop TEXT,
  image_back_normal TEXT,           -- second face, when the card has one
  card_faces TEXT,                  -- JSON array of {name, type_line, oracle_text, mana_cost, image_normal}
  prices TEXT NOT NULL,             -- JSON object, today's Scryfall prices for every card
  scryfall_uri TEXT,
  updated_at TEXT NOT NULL,
  seen_in_sync INTEGER NOT NULL     -- sync_runs.id of the last bulk that contained it
);
CREATE INDEX cards_oracle ON cards(oracle_id);
CREATE INDEX cards_set_number ON cards(set_code, collector_number);
CREATE INDEX cards_name ON cards(name COLLATE NOCASE);
CREATE INDEX cards_illustration ON cards(illustration_id);

CREATE VIRTUAL TABLE cards_fts USING fts5(
  name, printed_name, type_line, oracle_text, content='cards', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
-- triggers keep cards_fts in step with cards
```

Rows that disappear from the bulk file are kept (collection entries may reference them)
but flagged by `seen_in_sync` so the UI can show "no longer on Scryfall".

### Collection

```sql
CREATE TABLE collection_entries (
  id INTEGER PRIMARY KEY,
  card_id TEXT NOT NULL REFERENCES cards(id),
  finish TEXT NOT NULL CHECK (finish IN ('nonfoil','foil','etched')),
  condition TEXT NOT NULL CHECK (condition IN ('NM','LP','MP','HP','DMG')),
  language TEXT NOT NULL,           -- lang code, defaults to the printing's lang
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  notes TEXT,
  source TEXT NOT NULL,             -- scan, manual, import:<format>, mcp
  added_by TEXT,                    -- Access email or 'mcp'
  added_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (card_id, finish, condition, language)
);
CREATE TABLE tags (name TEXT PRIMARY KEY);
CREATE TABLE entry_tags (
  entry_id INTEGER NOT NULL REFERENCES collection_entries(id) ON DELETE CASCADE,
  tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
  PRIMARY KEY (entry_id, tag)
);
```

One row per distinct (printing, finish, condition, language); quantity counts copies.
Adding a card that matches an existing row increments it. Quantity 0 rows are deleted.
"Date added" is the row's `added_at`. Purchase price and per-copy dates are deliberately
not tracked (confirmed 09/10/2026); importers ignore those columns. Condition codes follow
the TCGplayer and ManaBox vocabulary; importers map Moxfield and Deckbox words onto them.

### Prices

```sql
CREATE TABLE price_snapshots (
  card_id TEXT NOT NULL REFERENCES cards(id),
  day TEXT NOT NULL,                -- YYYY-MM-DD, Sydney date of the sync
  usd TEXT, usd_foil TEXT, usd_etched TEXT, eur TEXT, eur_foil TEXT, eur_etched TEXT, tix TEXT,
  PRIMARY KEY (card_id, day)
);
CREATE TABLE fx_rates (
  day TEXT NOT NULL, base TEXT NOT NULL, quote TEXT NOT NULL DEFAULT 'AUD',
  rate REAL NOT NULL, source TEXT NOT NULL,   -- frankfurter, manual
  PRIMARY KEY (day, base, quote)
);
CREATE TABLE collection_value_daily (
  day TEXT PRIMARY KEY,
  cards INTEGER NOT NULL, entries INTEGER NOT NULL,
  usd REAL NOT NULL, eur REAL NOT NULL, aud REAL NOT NULL,
  usd_aud REAL NOT NULL, eur_aud REAL NOT NULL,   -- rates used
  priced_entries INTEGER NOT NULL                 -- entries that had a price
);
```

Snapshots are taken only for cards that are in the collection or in any deck on the day
of the sync. Every card still carries today's prices in `cards.prices`, so the catalogue
shows a current value for anything. Value per entry = quantity x price for its finish
(`usd_foil` for foil, `usd_etched` for etched, `usd` otherwise; EUR likewise). Missing
price means unpriced, never zero.

### Decks

```sql
CREATE TABLE decks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  format TEXT NOT NULL,             -- key from rules.FORMATS
  description TEXT,
  created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE deck_cards (
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES cards(id),
  role TEXT NOT NULL CHECK (role IN ('main','commander','companion','sideboard','maybeboard')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (deck_id, card_id, role)
);
```

A deck references a specific printing (so the list exports cleanly), but ownership checks
match on `oracle_id`, so any printing you own satisfies the deck.

Decks are lists, not reservations. Over-commitment is reported instead: a conflict is an
oracle_id whose total quantity across all non-archived decks (main, commander and
companion roles; sideboards and maybeboards excluded) exceeds the copies owned. Conflicts
can be acknowledged so the decks page stops warning until the shortfall grows.

```sql
CREATE TABLE deck_conflict_acks (
  oracle_id TEXT PRIMARY KEY,
  shortfall INTEGER NOT NULL,       -- needed minus owned at the time of acknowledgement
  acknowledged_at TEXT NOT NULL,
  acknowledged_by TEXT
);
```

The warning for a card reappears when its current shortfall is greater than the
acknowledged one, and the row is deleted automatically when the conflict clears.

### Scanning, sync and settings

```sql
CREATE TABLE scans (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  result TEXT NOT NULL,             -- JSON: candidates, ocr, timings, versions
  chosen_card_id TEXT REFERENCES cards(id),
  outcome TEXT,                     -- confirmed_best, corrected, rejected, pending
  image_path TEXT                   -- dewarped crop under /data/scans, kept for the last SCAN_KEEP_IMAGES scans
);
CREATE TABLE sync_runs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL,   -- catalogue, prices, fx, cv_catalog
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, detail TEXT
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- plus deck_conflict_acks, section 3
-- keys: fx_override_usd_aud, fx_override_eur_aud, default_condition, default_finish, scan_auto_confirm
```

## 4. Identification pipeline

```
photo (JPEG from the phone, 1280px long edge, or a 448x448 crop if the browser dewarped)
  -> detect.py   cornelius finds four corners; dewarp to 448x448; if confidence is below the
                 sharpness gate, try the whole frame as the card; also try 180 degree rotation
  -> embed.py    milo embedding (both orientations, keep the stronger); cosine sweep over the
                 CollectorVision Scryfall catalog; top 15 hits; drop ids not in our cards table
  -> ocr.py      crop the bottom-left strip of the 448 crop (roughly y 90 to 99%, x 2 to 60%),
                 upscale x3, grey, adaptive threshold; RapidOCR; parse collector number
                 (\d{1,4})(?:/\d{1,4})? and a 3 to 5 character set code; both optional
  -> resolve.py  group hits by oracle_id; score = best cosine in group; if OCR gives set and
                 number and a printing with that set and number exists in the top group (or any
                 group within the gap), that printing is the match with method "embedding+ocr";
                 else best hit by cosine with method "embedding"; confident when the winner is at
                 least GAP above ranks 2 to 11 (bindarr's gate, default 0.10) and above 0.55
  -> candidates  ordered list: the match first, then other printings of the same oracle (most
                 recent first), then other oracle groups
```

Each stage implements `Stage.run(ctx) -> ctx` and the chain is configured in
`pipeline.build()`, so the hash fallback from the art index can be slotted in later and
tests can replace the embedder with a fake. Versions of the models and catalog are
recorded in every scan result.

Manual search and CSV import do not touch this pipeline.

## 4a. Interface design brief

Dark theme is the native theme, not a toggle bolted on. Light mode is supported through
`prefers-color-scheme` for completeness but every screen is designed dark first.

Principles, in order:

1. Works on a phone held in one hand. The scan screen is the home screen when installed.
   Primary actions sit in the bottom third. Touch targets are at least 44 px. The confirm
   step is one tap when the match is confident, with the last-used finish and condition
   preselected and the quantity stepper under the thumb.
2. Dense where it should be. The collection is a list with real information per row
   (set symbol, collector number, finish, condition, quantity, value) and a grid view with
   card images, not oversized cards with one fact each. Filters are a single sheet, not a
   sidebar.
3. Minimal, not empty. One accent colour, used for the primary action and focus states.
   Neutral greys built from one hue. No gradients, no glassmorphism, no glow, no hero
   sections, no emoji as icons, no decorative illustrations. Icons are a small set of
   consistent line icons inlined as SVG. Card images and set symbols provide all the
   colour the app needs.
4. Type and spacing do the work. System font stack (`system-ui`, San Francisco on iOS,
   Roboto on Android), a four-step type scale, a 4 px spacing grid, tabular numerals for
   prices and counts. Headings are short nouns ("Collection", "Decks"), not slogans.
5. Fast and honest. Optimistic updates for quantity changes, skeletons instead of
   spinners, inline errors with the actual message, and timings visible on the scan screen
   (how long identification took) so slow scans are diagnosable.
6. Everything reachable without the scanner: manual search with autocomplete, import,
   decks, settings and status pages are first-class, and the whole app is usable with a
   keyboard on desktop.

Screens: Scan, Collection (list and grid, filters, card detail with printings, price
history chart and owned rows), Decks (list with owned percentage and the conflict banner,
deck detail grouped by role with validation inline, text import and export), Import
(CSV preview then apply), Settings (FX override, defaults, status of syncs and models,
backup download).

Phase 3 starts by writing the design tokens (colours, spacing, type) as CSS custom
properties in one file and builds every screen from them. Anything that looks like a
generated template gets removed.

## 5. HTTP API

All under `/api/v1`, JSON, authenticated by the Access JWT (or DEV_MODE). Errors are
`{"detail": "..."}` with 400, 401, 404, 409, 422. Lists are paginated with `page`,
`per_page` (default 50, max 200) and return `{"items": [...], "total": n, "page": p}`.

A card in responses is the "card view":
`{id, oracle_id, name, printed_name, lang, set_code, set_name, collector_number,
rarity, type_line, mana_cost, cmc, colors, color_identity, finishes, layout, released_at,
image_small, image_normal, image_back_normal, prices: {usd, usd_foil, usd_etched, eur,
eur_foil, eur_etched, tix, aud, aud_foil, aud_etched}, legalities, scryfall_uri,
owned_quantity}`.

### System

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | 200 if the database opens. No auth. |
| GET | `/api/v1/status` | Last sync per kind, catalogue counts, CV catalog version, model versions, FX rate in use. |
| POST | `/api/v1/sync/run` | Body `{"kind": "catalogue" or "prices" or "fx" or "all"}`. Starts a run in the background; 409 if one is running. |
| GET | `/api/v1/settings`, PUT same | FX overrides, default condition and finish, auto-confirm toggle. |

### Catalogue

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/cards/autocomplete?q=` | Up to 20 distinct names, prefix match, English first. |
| GET | `/api/v1/cards/search` | Params: `q` (FTS over name, type, text), `set`, `colors`, `identity` (subset match), `type`, `rarity`, `cmc_min`, `cmc_max`, `format` (legal in), `owned` (true), `unique` (`prints` or `cards`, default `cards` meaning one row per oracle_id, latest paper printing), `sort` (`name`, `released`, `usd`, `edhrec`), `page`. |
| GET | `/api/v1/cards/{id}` | Card view plus `printings_count` and `price_history` summary. |
| GET | `/api/v1/cards/{id}/printings` | Every printing sharing the oracle_id, with owned quantity each. |
| GET | `/api/v1/cards/{id}/prices?days=90` | Snapshots. |
| GET | `/api/v1/sets` | Sets with card counts and owned counts. |

### Scanning

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/scan` | multipart `image` (JPEG or PNG, 10 MB max), optional `prewarped=true`, `hint_set`. Returns `{scan_id, match: {card, method, confident, score}, candidates: [{card, score, reason}], ocr: {collector_number, set_code, raw}, timings_ms: {detect, embed, ocr, total}}`. `match` is null when nothing cleared the gate; candidates are still returned. |
| POST | `/api/v1/scan/{id}/confirm` | `{card_id, finish, condition, language?, quantity, tags?}`. Creates or increments the entry, records the outcome (`confirmed_best` if card_id equals the match, else `corrected`), returns the entry. |
| POST | `/api/v1/scan/{id}/reject` | Marks outcome `rejected`, nothing added. |

### Collection

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/collection` | Entries joined to card views. Filters as for search plus `finish`, `condition`, `tag`, `language`; `sort` adds `added`, `value`, `quantity`. |
| POST | `/api/v1/collection` | `{card_id, finish, condition, language?, quantity, notes?, tags?}`. Merges into an existing row when the key matches. |
| GET, PATCH, DELETE | `/api/v1/collection/{id}` | Read, update fields (quantity 0 deletes), delete. |
| GET | `/api/v1/collection/summary` | `{cards, copies, value: {usd, eur, aud, rate_used, priced_copies}, by_color_identity, by_type, by_set (top 30), by_rarity, by_finish, by_condition, top_cards (20 by value)}`. |
| GET | `/api/v1/collection/value-history?days=365` | Rows from `collection_value_daily`. |
| GET | `/api/v1/tags` | Tags with counts. |
| POST | `/api/v1/collection/import` | multipart `file` CSV, `format` (`auto`, `manabox`, `moxfield`, `deckbox`, `archidekt`, `tcgplayer`, `dragonshield`, `generic`), `dry_run` (default true). Returns `{format_detected, rows, resolved, unresolved: [{row, reason}], preview: [...]}`. With `dry_run=false` applies and returns counts. Generic format needs `Scryfall ID` or `Set code` + `Collector number` or `Name`. |
| GET | `/api/v1/collection/export.csv` | ManaBox-compatible columns, so it round-trips. |

### Decks

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `/api/v1/decks` | List (with card counts and owned percentage) and create `{name, format, description?}`. |
| GET, PATCH, DELETE | `/api/v1/decks/{id}` | Deck with cards grouped by role, each with owned quantity. PATCH for name, format, description, archived. |
| PUT | `/api/v1/decks/{id}/cards` | `{cards: [{card_id, quantity, role}]}` adds or sets quantities (quantity 0 removes). |
| GET | `/api/v1/decks/{id}/validate` | The validation report (section 7). |
| GET | `/api/v1/decks/{id}/export?format=text` | MTGA-style list (`4 Lightning Bolt (M10) 213`), commander section first. |
| POST | `/api/v1/decks/{id}/import` | `{text}` in the same style or plain `4 Lightning Bolt`; unresolved lines returned. |
| GET | `/api/v1/decks/conflicts` | `{conflicts: [{card, owned, needed, shortfall, decks: [{id, name, quantity}], acknowledged: bool}]}` across non-archived decks. The decks page shows unacknowledged ones as a banner. |
| POST, DELETE | `/api/v1/decks/conflicts/{oracle_id}/ack` | Acknowledge (records the current shortfall) or clear an acknowledgement. |

## 6. MCP tools

Mounted at `/mcp`, Streamable HTTP, stateless, JSON responses, mcp SDK 2.3.x. Every tool
returns structured content (a JSON object) and the same serialised as text. Card objects
in tool output are a trimmed card view: `{id, name, set_code, collector_number, type_line,
mana_cost, cmc, colors, color_identity, rarity, legal_in: [...], prices: {usd, aud},
owned_quantity}`. Tool errors use `ToolError` with a plain message.

Server `instructions` tell the model: the collection is one person's paper cards; prices
are daily Scryfall values in USD with an AUD conversion; `search_cards` covers every
Magic card, `search_collection` only owned ones; validation is rule-based, check
rulings yourself for edge cases.

| Tool | Input | Output |
| --- | --- | --- |
| `search_collection` | `query?` (FTS over name, type, text), `colors?`, `color_identity?` (subset of these letters), `type?`, `set?`, `rarity?`, `format?` (legal in), `tag?`, `min_cmc?`, `max_cmc?`, `sort?` (`name`, `value`, `edhrec`, `added`), `limit?` (default 50, max 200), `offset?` | `{total, items: [card view + finish, condition, quantity, tags]}` grouped per entry |
| `search_cards` | same filters minus `tag`, plus `owned_only?` | `{total, items: [card view]}`, one per oracle_id unless `unique="prints"` |
| `get_card` | one of `card_id`, `name` (exact or fuzzy via local catalogue), `set_code` + `collector_number` | full card view, `oracle_text`, `printings_owned: [{id, set_code, collector_number, finish, condition, quantity}]`, `price_history_30d` |
| `collection_summary` | `group_by?` list of `color_identity`, `type`, `set`, `rarity`, `finish`, `condition` (default all) | counts, totals in USD, EUR and AUD with the rate used, breakdowns, top 20 by value |
| `list_decks` | `include_archived?` | `[{id, name, format, cards, owned_percent, updated_at}]` |
| `get_deck` | `deck_id` | deck, cards by role with owned quantity, totals, last validation summary |
| `create_deck` | `name`, `format`, `description?`, `commander?` (card id or name), `cards?` (list of `{card: id or name, quantity, role?}`) | the deck as `get_deck` returns it |
| `update_deck` | `deck_id`, `name?`, `format?`, `description?`, `archived?` | deck |
| `add_cards_to_deck` | `deck_id`, `cards: [{card: id or name, quantity?, role?}]`, `prefer_owned?` (default true: when given a name, pick a printing you own) | deck, plus `unresolved: [names]` |
| `remove_cards_from_deck` | `deck_id`, `cards: [{card: id or name, quantity?, role?}]` (no quantity removes all) | deck |
| `validate_deck` | `deck_id` | section 7 report |
| `suggest_from_collection` | `commander?` (id or name) or `color_identity?`, `theme?` (free text), `format?` (default commander when a commander is given), `exclude_deck_id?` (skip cards already in that deck), `limit?` (default 40) | `{candidates: [{card, category, why, edhrec_rank}], categories: {ramp, draw, removal, wipes, counters, creatures, other}}` |
| `delete_deck` | `deck_id` | `{deleted: true}` |
| `deck_conflicts` | `include_acknowledged?` (default false) | same shape as the API conflicts report: cards needed by more decks than you own copies of, with the decks involved and the shortfall |

Tool annotations: read tools carry `readOnlyHint`; `delete_deck` carries
`destructiveHint`.

Authentication on `/mcp`: an ASGI middleware ahead of the transport accepts either a
verified Cloudflare Access JWT (user or service token) or `Authorization: Bearer
<MCP_BEARER_TOKEN>` compared in constant time. Anything else is 401 with a JSON body. The
middleware also serves the RFC 9728 `/.well-known/oauth-protected-resource/mcp` document
only if a future OAuth setup needs it; for now it is not registered.

## 7. Format rules and validation

`rules.FORMATS` is a dict keyed by Scryfall legality key, each with: `min_main`,
`max_main` (null for none), `exact_main`, `max_sideboard`, `max_copies`, `singleton`,
`commander` (none, required, optional), `commander_types` (regex over type_line),
`color_identity` (bool), `rarity_limit` (for pauper formats: `common`). Covered: standard,
pioneer, modern, legacy, vintage, pauper, commander, paupercommander, brawl, standardbrawl,
oathbreaker, duel. Values are set from the official format pages during Phase 2 and
each is cited in a comment.

Checks: legality per card from `cards.legalities` (`restricted` allowed once in vintage),
copy limits (basic lands unlimited; oracle text "A deck can have any number of cards
named" or "up to seven" and "up to nine" handled by regex), deck size, sideboard size,
commander presence and type, colour identity containment, pauper rarity (any printing at
common counts, checked across oracle_id).

Report:

```json
{"format": "commander", "legal": false,
 "problems": [{"code": "not_legal", "card": "...", "message": "..."}],
 "counts": {"main": 99, "commander": 1, "sideboard": 0},
 "ownership": {"owned": 92, "missing": 8, "percent": 92.0,
   "missing_cards": [{"card": {...}, "need": 1, "have": 0, "estimated_usd": "3.40", "estimated_aud": "4.90"}],
   "also_in_decks": [{"card": "...", "decks": ["..."]}]},
 "missing_value": {"usd": "27.10", "aud": "39.03"}}
```

Ownership matches on oracle_id across all finishes and conditions. Cards used in other
decks are listed under `also_in_decks` for information; the cross-deck shortfall itself
is the `deck_conflicts` report (section 3), surfaced on the decks page and as an MCP tool.

## 8. Suggestions

`suggest_from_collection` is heuristic, not a model. Steps: take the commander's colour
identity (or the given one); select owned, paper, format-legal cards whose identity is a
subset; drop the commander and cards in `exclude_deck_id`; if `theme` is given, score
cards by FTS rank on oracle text, type line and keywords plus token overlap with the
commander's oracle text; otherwise rank by `edhrec_rank` ascending; classify each by regex
on oracle text (ramp: "add {" or "search your library for a .* land"; draw: "draw a card";
removal: "destroy target" or "exile target"; wipes: "destroy all" or "each creature"
destruction; counters: "counter target"); return the top `limit` with the matched reason.
Documented in the tool description so the model knows what it is getting.

## 9. Sync and pricing job

`scheduler.run_forever()` is an asyncio task started in the app lifespan. It wakes every
minute and runs the daily job once per Sydney day at `SYNC_HOUR` (default 06:00), and on
startup if the catalogue is empty or older than 36 hours. APScheduler would add a
dependency for one daily job; a loop with a persisted "last ran" marker is enough and is
easy to test. The job, each step recorded in `sync_runs`:

1. `GET /bulk-data`, find `default_cards`; skip if `updated_at` matches the last run.
2. Stream `jsonl_download_uri` (gzip, from data.scryfall.io, no rate limit) line by line;
   upsert `sets` and `cards` in batches of 2,000 inside transactions; refresh `cards_fts`.
   Expected 118k rows, a few minutes on arm64. Memory stays flat.
3. Price snapshot for every card id in `collection_entries` or `deck_cards`, into
   `price_snapshots` for today's Sydney date.
4. FX: Frankfurter `v1/latest?base=USD&symbols=AUD` and `base=EUR`; on failure reuse the
   latest stored rate; manual overrides in `settings` win. Written to `fx_rates`.
5. Rollup into `collection_value_daily`.
6. CollectorVision catalog update check (its own incremental feed), recorded as `cv_catalog`.

API calls to api.scryfall.com happen only for `/bulk-data`, set icons (downloaded once
into `/data/icons`), and the rare `/cards/named?fuzzy=` fallback when an import name is
not in the local catalogue. The client enforces the documented limits with a token
bucket and sleeps 30 seconds on a 429.

## 10. Authentication

Web and API: `Cf-Access-Jwt-Assertion` verified with PyJWT's `PyJWKClient` against
`https://$CF_ACCESS_TEAM_DOMAIN/cdn-cgi/access/certs` (RS256, key chosen by `kid`, JWKS
cached in memory with a one hour lifetime and refreshed on an unknown `kid`), `aud` must
equal `CF_ACCESS_AUD`, `iss` must be `https://$CF_ACCESS_TEAM_DOMAIN`, `exp` enforced.
This is the verification the Cloudflare docs describe, written small and tested with a
generated key pair; no code is copied from elsewhere. Email claim becomes the acting user. With
Access unconfigured and `DEV_MODE` off the app refuses every request except `/healthz`
and logs why.

MCP: the decision from Phase 0. Two Access applications on the same hostname: the main one
(path `/`) with the OAuth identity provider policy, and a second one (path `mcp`) with
two policies, Service Auth for a `claude-code` service token and, only if a client cannot
send headers through Access, a Bypass policy. The app verifies the resulting JWT exactly
as for the web (service tokens carry `common_name`, no email) or, on the bypassed path,
insists on `MCP_BEARER_TOKEN`. Trade-off: service tokens are checked at the edge, rotate
in the Cloudflare dashboard and never reach the origin as secrets, so they are the
default for Claude Code; the bearer token exists because claude.ai and Claude Desktop
connectors only offer a fixed Request headers field and connect from Anthropic's cloud,
and sending the Access client secret through that field would work but puts a
Cloudflare-wide credential in a third-party UI, whereas the app token can only reach this
app's MCP endpoint. Both are verified by the app, never inferred from the tunnel.

## 11. Configuration

```
DATA_DIR=/data
TZ=Australia/Sydney
CF_ACCESS_TEAM_DOMAIN=example.cloudflareaccess.com
CF_ACCESS_AUD=<aud tag>
MCP_BEARER_TOKEN=<64 hex, optional>
SCRYFALL_USER_AGENT=MtgCollection/<version> (+https://github.com/<you>/mtg-collection)
SYNC_HOUR=06:00
FX_PROVIDER=frankfurter | manual
FX_USD_AUD= FX_EUR_AUD=              (manual values, also settable in the UI)
SCAN_GAP=0.10 SCAN_MIN_SCORE=0.55 SCAN_KEEP_IMAGES=200
CV_CATALOG=mtg                       (CollectorVision game key) CV_OFFLINE=false
DEV_MODE=false
TUNNEL_TOKEN=<cloudflared>
```

## 12. Deployment

`docker-compose.yml`: `app` from `ghcr.io/<owner>/mtg-collection:latest` with
`mtg-data:/data` named volume, `env_file: .env`, no published ports, healthcheck on
`/healthz`; `cloudflared` with `TUNNEL_TOKEN`. Image: python:3.13-slim, uv-built venv,
CollectorVision from git at a pinned commit, RapidOCR, onnxruntime, opencv-headless.
Expected size 450 to 550 MB. Multi-arch build via buildx and QEMU on tag push `v*`, tags
`vX.Y.Z`, `vX.Y`, `latest`, plus `sha`. Backup: stop the app or use `sqlite3 .backup` to
copy `/data/collection.db`; the CV catalog cache and icons are re-downloadable. The UI
also offers a one-click download of the database and a CSV.

## 13. Tests

- Identification: pure unit tests for the OCR line parser (fixture strings), the resolver
  (fake embedder returning canned hits, fake OCR, gap gate on and off, shared-art groups),
  and the pipeline wiring; an opt-in integration test that runs the real models over
  `tests/fixtures/photos/<scryfall_id>.jpg` and reports top-1 and exact-printing rates,
  failing below a threshold set once a baseline exists.
- MCP: the mcp SDK client over an in-memory ASGI transport against the real app in
  `DEV_MODE` with a seeded catalogue of a few hundred cards: every tool, structured output
  shape, validation cases (commander identity, copy limits, pauper), suggestions, and 401
  without a token.
- API: importers (one fixture CSV per format), collection merge semantics, summary maths,
  value rollup with FX, Access JWT verification with a generated RSA key pair.
- Ruff and pytest in GitHub Actions on every push.

## 14. Phase plan

- Phase 2: migrations, config, db, Scryfall client and bulk sync, pricing and FX,
  scheduler, catalogue and collection modules, decks and rules, importers, JSON API,
  tests. Runs locally with `DEV_MODE=true`.
- Phase 3: PWA (scan screen with camera and confirm flow, collection browser, card detail
  with price chart, decks, import, settings) and the identification pipeline with real
  models.
- Phase 4: MCP server, tools, auth middleware, tests.
- Phase 5: Dockerfile, compose, Access verification wiring, Actions release pipeline,
  README with release process and backup notes, CHANGELOG.
