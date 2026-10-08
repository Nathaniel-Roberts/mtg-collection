# Research notes: self-hosted MTG collection manager

Phase 0 output. Research done 09/10/2026 against live repos, the Scryfall API docs, the
MCP specification and the mcp Python SDK. Everything below was read from the actual
source, LICENSE file or doc page on that date; nothing is from memory. Where a number is
an estimate it says so.

## 1. Summary and recommendations

| Component | Recommendation | Source | Licence |
| --- | --- | --- | --- |
| Card detection, dewarp and "which card is this" | Use CollectorVision (cornelius corner detector + milo 128-d embedder + hosted Scryfall catalog). Does 80% of the identification job, runs under 100 ms on CPU, bundles the ONNX weights in the wheel. | github.com/HanClinto/CollectorVision | AGPL-3.0 (free noncommercial licence offered, commercial available) |
| Pinning the exact printing | Build fresh: RapidOCR on the bottom-left collector line of the dewarped crop ("0123/0281 R" and "MOM EN"), then `set + collector_number` lookup in the local catalogue. Fall back to a printings picker in the UI. | Our code; RapidOCR | Apache-2.0 |
| Perceptual hash fallback | Optional. The MIT art hash index (one 1024-bit dHash per `illustration_id`, published daily) is a drop-in if CollectorVision ever disappears. Not recommended as the primary path, see section 6. | github.com/neotoxicfr/mtg-scanner-art-index | MIT |
| Card catalogue | Scryfall `default_cards` bulk (gzipped JSONL, ~79 MB) loaded into SQLite daily. | Scryfall | Scryfall data use terms; WotC Fan Content Policy |
| Prices and history | Scryfall `prices` from the same daily bulk pull, snapshotted into our own history table. MTGJSON `AllPricesToday.json` is a possible second source but not needed. | Scryfall, MTGJSON | MTGJSON code is MIT |
| AUD conversion | Frankfurter v1 (ECB reference rates, no key) with a configurable manual override. | api.frankfurter.dev | MIT (self-hostable) |
| Cloudflare Access JWT verification | Write fresh with PyJWT's JWKS client per the Cloudflare docs. outfit-planner has a working example of the same checks to compare against, but it is still in development and is not a code base to copy. | Cloudflare docs | n/a |
| MCP server mount, bearer and service-token auth | Follow the mcp SDK 2.3.x docs for mounting `streamable_http_app()` into FastAPI, with a small ASGI auth middleware in front. outfit-planner shows the same shape working, which is useful as a comparison only. | mcp SDK docs | MIT |
| Deck validation rules, deck tools shape | Borrow the `FORMAT_RULES` table and the `get_card` input shape from as3k/scryfall-mcp. Re-verify every per-format value. | github.com/as3k/scryfall-mcp | README says MIT, no LICENSE file |
| CSV import mapping | Header-sniffing importer modelled on MtgCsvHelper's mappings; every modern export carries Scryfall ID or set code + collector number. | github.com/StepKie/MtgCsvHelper | Apache-2.0 |
| Docker, multi-arch GHCR build, compose with cloudflared | Same layout as the stacks already on the Home Assistant host: app plus cloudflared, named volume, buildx multi-arch to GHCR. | Your existing deploy pattern | n/a |

Language: Python with FastAPI. Reasons: CollectorVision and RapidOCR are Python, the mcp
SDK 2.x is Python and current with the 2026-07-28 spec, and PyJWT covers the Access JWT
check. One language across API, scanner and MCP. PWA in plain HTML
and JavaScript, no build step, so the image stays one container.

Licence consequence to decide: CollectorVision is AGPL-3.0. If we bake it into the GHCR
image, the published image is a combined work and the repo should be AGPL-3.0-or-later
(compatible with every other dependency listed here). Alternatives: keep the repo MIT and
fetch CollectorVision into the data volume at first run, as bindarr does; or ask the
author for the free noncommercial licence (offered in COMMERCIAL_LICENSE.md, but the
formal text "may be published separately after review", so it is not yet a document you
can rely on). Recommendation: AGPL-3.0-or-later for the repo. It costs a personal project
nothing and keeps the image honest.

## 2. The four projects you named

### aecyx/Binderbase

- https://github.com/aecyx/Binderbase. AGPL-3.0 (LICENSE file is the full text). 0 stars.
  Created 18/04/2026, last human commit 20/04/2026 ("promote 1.0.0"). Later pushes are
  dependabot only. No GitHub Releases despite the README pointing at them.
- Tauri 2 desktop app: Rust backend (rusqlite, reqwest, `image`), React/TypeScript front
  end. Desktop only. Phone photos are "planned".
- Identification: 256-bit dHash of the whole image (resize to 17x16, compare horizontal
  neighbours), brute-force Hamming scan, `MIN_CONFIDENCE = 0.70` which is distance 76 or
  less, top 5 returned. No card detection, no rectification, no OCR. README admits it
  "works best with cleanly cropped card images".
- Scryfall: `default_cards` bulk, hashes `image_uris.small` (146x204) with `normal` as a
  fallback. No `card_faces` handling, so double-faced cards get no image.
- Storage: SQLite with `cards`, `collection_entries`, `prices`, `scan_events`,
  `card_hashes`. Pricing: usd/usd_foil/eur/eur_foil as integer cents, refreshed per card
  with 100 ms spacing. No AUD.
- Verdict: well-engineered process (CI, fuzzing, input guards) wrapped around a naive
  matcher. Worth borrowing: the collection and hash table shapes in `schema_v1.sql` and
  `schema_v3.sql`, the 10 MB and 50 MP input guards. Not worth porting the matcher.
  Licence means take the ideas, not the code, unless the repo is AGPL anyway.

### bitdagger/mtg-scanner

- https://github.com/bitdagger/mtg-scanner. MIT. 6 stars. Last commit 08/03/2016.
- Python 2 only, OpenCV, libphash bindings, shells out to ImageMagick, two SQLite files.
  Card data from MTGJSON `AllSets.json` and Gatherer images by multiverseid, both URLs now
  stale. Not runnable as-is.
- Identification: 64-bit DCT pHash, Hamming threshold 15, ties broken by pHash radial
  digest cross-correlation. Rectification in `transformer.py`: Canny(100, 300), HoughLines,
  split lines into near-horizontal and near-vertical, intersect extremes for four corners,
  `getPerspectiveTransform` + `warpPerspective`.
- Verdict: `transformer.py` is a compact, MIT-licensed Hough quad finder that ports to
  Python 3 almost unchanged. CollectorVision's neural corner detector makes it unnecessary
  for the primary path, but it is a sensible zero-ML fallback. Nothing else to keep.

### JackTheTripperr/MTG-Bulk-Scan

- https://github.com/JackTheTripperr/MTG-Bulk-Scan. LICENSE file is GPL-3.0; README
  claims MIT. The LICENSE file governs. 5 stars. Created 02/07/2024, last commit
  02/08/2024.
- Single 476-line `main.py`. No hashing, no OCR, no detection: each photo is base64
  encoded and sent to OpenAI gpt-4o with a prompt asking for `card_name`, `set_code`,
  `collector_number` as JSON. Verifies with Scryfall `GET /cards/{set}/{number}`, name
  compared case-insensitively, falls back to `cards/named?fuzzy=` then `prints_search_uri`
  and a second GPT call to pick the printing. Output is a deckbox.org CSV. No rate limiting
  or retries on Scryfall. No tests.
- Verdict: not adaptable code, and the cloud-LLM approach is the opposite of what you
  asked for. The one good idea is the verification order (exact set + number, then name
  check, then fuzzy, then printings list), which we will use after OCR instead.

### as3k/scryfall-mcp, dccoder/mtg-mcp, pato/mtg-mcp

- as3k/scryfall-mcp: https://github.com/as3k/scryfall-mcp. Python, `mcp>=1.0.0`
  (FastMCP 1.x API), httpx. No LICENSE file; README says MIT. 0 stars, last commit
  07/07/2026. Transport: stdio, or SSE (deprecated transport). No auth. 28 tools: Scryfall
  search, named lookup (`exact | fuzzy | set_code + collector_number | id`), autocomplete,
  `/cards/collection` batch (75 ids), rulings, sets, symbols, catalogs, bulk data list,
  plus deck CRUD, `add_card_to_deck`, `remove_card_from_deck`, `analyze_deck`,
  `validate_deck`, `get_format_rules`, MTGA text import and export. Decks are one JSON file
  each under `~/.config/scryfall-mcp/decks/`. `owner` is free text, not an identity. A
  `FORMAT_RULES` dict covers 13 formats (min/max main, sideboard, max copies, singleton,
  commander requirement, colour identity, Pauper rarity, Tiny Leaders CMC). No collection
  concept. Scryfall client: 9 req/s spacing, one retry on 429, 24 h JSON cache, sets
  `User-Agent: ScryfallMCP/1.0` but no `Accept` header.
- dccoder/mtg-mcp: the URL 404s. The intended repo is https://github.com/DCCoder90/mtg-mcp.
  Go, official go-sdk v1.0.0, MIT (LICENSE.md). 0 stars, 10 commits, last 31/10/2025.
  stdio or SSE. "This server does not have built-in authentication." Five thin search
  tools; `search_card_by_text` is wired to the name handler (bug). No decks, no storage.
  Only idea worth taking: typed output schemas on every tool.
- pato/mtg-mcp: https://github.com/pato/mtg-mcp. Rust, third-party `mcp-core` crate on
  protocol 2024-11-05, stdio only. No LICENSE file; Cargo.toml says MIT. 5 stars, 5
  commits, last 21/07/2025. Two tools; `search_cards` calls the exact-name endpoint, so
  Scryfall query syntax does not work and `limit` is ignored.
- Verdict: none models an owned collection, none has auth, none sends both headers
  Scryfall requires. From as3k keep the `get_card` input shape, the `/cards/collection`
  batch resolver, the `FORMAT_RULES` table (re-verify values), MTGA text import/export
  format (`4 Lightning Bolt (M10) 213`), and the server `instructions` string that tells
  the model what each tool costs. Build the collection, deck storage and auth fresh.

## 3. Other projects found (your follow-up ask)

### Collection managers

- thenotoriousJeremy/bindarr, MIT, 67 stars, pushed 30/09/2026, and its MTG-focused fork
  Brenttime/scrybox (MIT, pushed 07/10/2026). JavaScript: React + Vite, Express, sqlite3,
  onnxruntime-node and onnxruntime-web. The closest existing thing to this brief: phone
  camera scanning, binder page and slot tracking, deck builder with checkout, 7 and 30 day
  value trends, `cards/:id/price-history`, ManaBox import, TCGplayer CSV export,
  multi-user with session tokens, GHCR image. Scan pipeline is CollectorVision (browser
  runs cornelius for the live outline and dewarps on the phone; server runs milo and a
  cosine sweep). Models and catalog are fetched at install time, not shipped, because
  they are AGPL and bindarr is MIT. No MCP server. Measured on its own 100-card noisy
  sample (`backend/src/cvScan.js`): cornelius + milo 76% exact printing, 90% right card,
  310 ms; the old hash + bag-of-words + ORB stack 78% exact, 88% right card, 1187 ms. Its
  "gap above ranks 2 to 11" confidence gate (default 0.10) is a good idea to copy.
  Not Python and multi-game, so not a base to fork, but the best reference for UX and
  the scan gate.
- McDandle/local-mtg-scanner, MIT, Python, pushed 19/08/2026. Phone-as-scanner with
  live OCR to exact printing by set code + collector number, SQLite, price snapshots with
  charts, condition-adjusted valuation, deck builder, CSV in and out. Closest in shape to
  this brief in Python. Worth reading its price-history schema and condition multipliers.
- daveSoupy/MTG-Card-Library, MIT, TypeScript (Fastify, SQLite), pushed 28/09/2026. Clean
  REST API and Scryfall bulk ingestion with offline search. No scanning or price history.
- JanAelbr/MTG-Collection-Tracker, MIT, Python FastAPI + Vue + SQLite, pushed 29/09/2026.
  Purchase CSV import, Cardmarket EUR sync, storage locations, profit and loss.
- Swallowtail23/mtgc, custom licence (read before reuse), PHP 8 + MySQL, pushed
  19/09/2026. Mature features including localised currency conversion. Wrong stack.
- nicho92/MtgDesktopCompanion, Apache-2.0, Java, 246 stars, pushed 04/10/2026. The most
  established project; heavy, desktop-centric. Price provider abstraction and alerting
  are worth a look.
- Arinlir/HoardKeeper (MIT, browser SPA, thin persistence), morosanmihail/hometg (no
  licence, C#, unmaintained), jumpinjackie/mtg-collection-tracker (MIT, C# desktop),
  NicoVIII/tcg-card-collector (MIT, Gleam). awesome-selfhosted has no MTG entries.

### Scanner and recognition projects

- HanClinto/CollectorVision, AGPL-3.0, Python with a JS port, 51 stars, pushed
  05/10/2026. Alpha, not on PyPI (install from git). Dependencies: Pillow, numpy,
  opencv-python-headless, onnxruntime (CPU) and optionally huggingface_hub. Bundled
  weights: `cornelius.onnx` 4.4 MB (corner detection, 384x384 input), `milo.onnx` 5.2 MB
  (128-d ArcFace-style embedding of a 448x448 dewarped crop). Catalog v2 for MTG is a
  hosted feed keyed by Scryfall ID (~108k printings in the README; bindarr's v1 npz was
  56 MB, snapshot 09/07/2026), downloaded once and updated incrementally into a local
  cache, with names, finishes, sets, languages and `layout` metadata. `search_records()`
  returns identifiers and metadata. You can also build your own catalog from a folder of
  images (`catalog/build_catalog_from_images.py`), so a locally built catalog from Scryfall
  `normal` images is possible if the hosted feed ever lags a set release. Known gaps:
  embeddings are sensitive to 180 degree rotation (embed both orientations), and identical
  artwork across printings embeds near-identically, which is the "exact printing" ceiling
  bindarr measured.
- neotoxicfr/mtg-scanner-art-index, MIT, Python, pushed 04/10/2026. Publishes
  `art_hashes.sqlite` (~37 MB) daily: one 1024-bit hybrid dHash (grey + B, G, R planes of an
  interior art crop, 17x16 each) per Scryfall `illustration_id`, ~50k entries, plus an
  optional DINOv2 embedding. Reference matcher is pure OpenCV + NumPy 2 and takes a few
  milliseconds. Deliberately per-artwork: a match returns a group of printings to narrow by
  collector-line OCR or a user pick. Author's calibration on 4K webcam captures: true group
  at Hamming 180 to 340 of 1024, best impostor 46 to 150 bits further away. Front faces
  only. Solves the cold-start problem for a hash approach with a permissive licence.
- zluo01/mtg-scanner, GPL-3.0, pushed 03/09/2026. Installable PWA: YOLO oriented-box
  detection in the browser (onnxruntime-web) including whole binder pages, homography on
  the phone, SigLIP2 embedding on the server, cosine over ~112k printings, claims ~99% top-1
  at ~270 ms. Author states it is entirely LLM-generated. Architecture is the
  browser-detect, server-identify split we want; GPL limits code reuse, and SigLIP2 is far
  heavier than milo on CPU.
- juthrbog/weatherlight, no licence file, Python FastAPI, active 2026. OpenCV + RapidOCR
  name reading with pHash as a secondary signal, Docker, Scryfall bulk sync. Reports name
  OCR identified 21 of 25 webcam captures with zero wrong names at ~300 ms, and that pHash
  never ranked the right card first on those captures. Image ~660 MB.
- DarylNo/CardScanner (MIT per README, Kotlin + FastAPI, on-device pHash over ~50k
  artworks plus collector-line OCR to confirm printing), GrimbiXcode/mtgscan (GPL-3.0, JS,
  OCR on collector number then Scryfall exact lookup, foil detection), jimmyly89/
  mtg-card-scanner (no licence, Express + CollectorVision, AUD pricing),
  jlelectroterra1-jpg/card-scanner (no licence, RapidOCR name bar + fuzzy match over ~34k
  names), IsaacAGz/Card-Scanner (no licence, YOLO11 + DINOv2 + FAISS), hj3yoo/
  mtg_card_detector (no licence, 138 stars, 2022, contour detection + pHash 16/32),
  fortierq/mtgscan (MIT, 2022, Azure OCR + SymSpell), Mr-Azrack/Moss-Machines (CC
  BY-NC-SA, 256-bit RGB pHash + OCR fallback, sorter hardware), MeIsGaggy/
  MTG-Card-Scanner-Sorter (custom non-commercial licence).

### Other MCP servers

Around 40 repos named scryfall-mcp or mtg-mcp exist, mostly single-author and 0 stars.
With substance: j4th/mtg-mcp-server (MIT, Python, FastMCP 3.x, 20 stars, 69 tools over
Scryfall, EDHREC, 17Lands, Moxfield and others, on PyPI), nathanmartins/mtg-mcp (MIT, Go,
20 stars, Commander-focused with deck validation and Moxfield import, 93% test coverage),
bmurdock/scryfall-mcp (MIT, TypeScript, stdio and Streamable HTTP, 15 tools, local
comprehensive-rules search), haksanlulz/mcp-scryfall (MIT, TypeScript, small and careful
with proper User-Agent). fkadriver/mtga-mcp (no licence) queries an Arena collection in
SQLite and is the nearest in shape to "expose my collection", but is Arena-only. None
exposes a self-hosted paper collection with auth.

## 4. Scryfall API facts (quoted from docs, 09/10/2026)

- Base: `https://api.scryfall.com`, HTTPS only.
- Headers: "All HTTP requests to api.scryfall.com must include a User-Agent header and an
  Accept header." The User-Agent "should be the name of your application, such as
  MTGExampleApp/1.0". `Accept: */*` or `application/json` is fine.
- Rate limits (own page, /docs/api/rate-limits): `/cards/search`, `/cards/named`,
  `/cards/random`, `/cards/collection` are 2 per second (500 ms); `/cards/manifest` is
  10 per minute; "All other methods 10/second (100ms)". "The direct file origins located at
  *.scryfall.io do not have rate limits." A 429 "will result in your access being limited
  for 30 seconds"; repeat offenders can be banned. "It is not acceptable to ignore HTTP 429
  responses."
- Caching guidance: cache at least 24 hours; prices update once a day; "If you need to
  rapidly look up card names, prices, or resolve a large number of card images, you must
  use the bulk data files."
- Bulk data has changed format: "Each bulk file is a gzipped JSONL (JSON Lines) archive",
  not a JSON array. The object field is `jsonl_download_uri` (the old `download_uri` is not
  in the live response), pointing at `https://data.scryfall.io/...`, with
  `compressed_size` and `updated_at`. Types: `oracle_cards` (23.5 MB), `unique_artwork`
  (36.1 MB), `default_cards` (75.1 MB on the page, 78.8 MB measured today, "every card
  object on Scryfall in English or the printed language if the card is only available in
  one language"), `all_cards` (377 MB, every language), `rulings`, `art_tags`,
  `oracle_tags`. "Bulk data is only collected once every 12-24 hours." Prices "should be
  considered dangerously stale after 24 hours." New `GET /cards/manifest` (15,000 entries
  per page) exists for comparing against a downstream sync.
- Counts measured today from `default_cards`: 118,602 card objects; 109,466 playable on
  paper; 51,248 distinct `illustration_id`s, of which 25,616 are shared by more than one
  printing (covering 88,021 printings, max 44 printings on one illustration).
- Card object: `prices` is "daily price information for this card, including usd,
  usd_foil, usd_etched, eur, eur_foil, eur_etched, and tix prices, as strings". Values are
  decimal strings or null; `eur_etched` was absent on a sampled card, so treat every key
  as optional. `finishes` is an array of `foil`, `nonfoil`, `etched` (the old `foil` and
  `nonfoil` booleans still appear in live responses but are no longer in the field table).
  `legalities` values are `legal`, `not_legal`, `restricted`, `banned`; live keys today:
  standard, future, historic, timeless, gladiator, pioneer, modern, legacy, pauper, vintage,
  penny, commander, oathbreaker, standardbrawl, brawl, competitivebrawl, alchemy,
  paupercommander, duel, oldschool, premodern, predh, tlr. `set` is the set code (there is
  no `set_code` on cards; `set_id`, `set_name`, `set_type` exist). `collector_number` "can
  contain non-numeric characters". `lang` codes include en, es, fr, de, it, pt, ja, ko, ru,
  zhs, zht, he, la, grc, ar, sa, ph, qya, dw, tlh. `oracle_id` is "always present except
  for the reversible_card layout where it will be absent; oracle_id will be found on each
  face instead." `rarity` is common, uncommon, rare, special, mythic or bonus. `games`
  contains paper, arena, mtgo, astral, sega. `image_status` is missing, placeholder,
  lowres or highres_scan. `card_faces` carries `image_uris` for split, flip, transform
  and double_faced_token layouts ("If this card is not double-sided, then the image_uris
  property will be part of the parent object instead").
- Images (/docs/api/images): `png` 744x1040, `small` 146x204 JPG, `normal` 488x680 JPG,
  `large` 672x936 JPG, `border_crop` 480x680, `art_crop` varies; new WEBP variants `thumb`,
  `grid`, `display`, `crop`, `art`. CDN host is `cards.scryfall.io`. Measured today:
  `small` averages 12.9 KB, `normal` 101.5 KB, so ~120k images is roughly 1.5 GB at small
  or 12 GB at normal (estimate). Display rules: no cropping off copyright or artist, no
  distortion or colour shifting of displayed images, and when showing `art_crop` list the
  artist and copyright nearby. Set `icon_svg_uri`: "Hotlinking this image isn't
  recommended... You should download it and use it locally".
- Lookups: `GET /cards/named?exact=` or `?fuzzy=` (404 on zero or more than one fuzzy
  match; optional `set`); `GET /cards/:code/:number(/:lang)`; `POST /cards/collection`
  with up to 75 identifiers (`id`, `oracle_id`, `name`, `name + set`, `collector_number +
  set`, and others), unknowns returned in `not_found`; `GET /cards/search` paginated at
  175 with `unique=cards|art|prints`, `order`, `dir`, `has_more`, `next_page`.
- Data use: "You may not 'paywall' access to Scryfall data" and "You may not simply
  repackage, republish, or proxy Scryfall data. Your software must create additional value
  for end-users." A personal collection manager is fine.

## 5. MCP specification and Python SDK (09/10/2026)

- Current spec revision is 2026-07-28 (https://modelcontextprotocol.io/specification/).
  It is a structural break: "Modern: protocol versions that convey version, identity, and
  capabilities as per-request metadata (revision 2026-07-28 and later). Legacy: protocol
  versions that establish a session with an initialize handshake (2025-11-25 and
  earlier)." "There is no negotiation handshake. Every request carries its protocol
  version."
- Streamable HTTP in 2026-07-28: "The server MUST provide a single HTTP endpoint path...
  that supports POST." The GET stream and protocol-level sessions are removed; a
  modern-only server answers GET or DELETE with 405. Clients send
  `Accept: application/json, text/event-stream`; servers answer a request with either a
  single JSON body or an SSE stream, 202 for notifications. Every POST "MUST include an
  MCP-Protocol-Version header" matching the `_meta` field, plus `Mcp-Method` and, for
  tools/call, `Mcp-Name`. Closing the SSE stream is cancellation. Resumable streams via
  Last-Event-ID are "not supported". Unchanged: "Servers MUST validate the Origin header on
  all incoming connections"; "Servers SHOULD implement proper authentication for all
  connections." The old HTTP+SSE transport is deprecated and "eligible for removal". Legacy
  clients (2025-11-25 and earlier) still use `Mcp-Session-Id`, GET streams and DELETE; the
  SDK handles both legs.
- Authorization: "Authorization is OPTIONAL for MCP implementations." When used over HTTP
  the server is an OAuth 2.1 resource server, "MCP servers MUST implement OAuth 2.0
  Protected Resource Metadata (RFC9728)", 401 with `WWW-Authenticate` carrying
  `resource_metadata`, resource indicators (RFC 8707) required of clients, tokens only in
  `Authorization: Bearer`, never in the query string, and "MCP servers MUST NOT accept or
  transit any other tokens." The spec says nothing about API keys or static bearer tokens;
  they are outside the spec but not forbidden, and the SDK's own auth tutorial implements
  a static-token `TokenVerifier`.
- Client support for fixed headers: Claude Code supports
  `claude mcp add --transport http <name> <url> --header "Authorization: Bearer ..."` and a
  `headers` map in `.mcp.json` with `${VAR}` expansion. claude.ai and Claude Desktop custom
  connectors now have a "Request headers" field for "fixed credentials such as API keys
  that Claude sends on every request" (support.claude.com article 11175166), and
  connections originate from Anthropic's cloud. This is new since March 2026, and it is
  what makes a non-OAuth path workable from every Claude client.
- Python SDK: latest is mcp v2.3.0 (02/10/2026), Python 3.10+, "v2 speaks the 2026-07-28
  revision". Breaking renames from 1.x: `FastMCP` is now `MCPServer` from `mcp.server`,
  `mcp.server.fastmcp.*` is `mcp.server.mcpserver.*`, camelCase fields are snake_case
  (`input_schema`, `structured_content`), `streamablehttp_client` is gone, transport
  options moved to `run()` and `streamable_http_app()`. Mounting: `mcp.streamable_http_app()`
  returns a Starlette app; "Mounting disables the built-in lifespan. The host app's lifespan
  must enter `mcp.session_manager.run()`, or the first request fails." Options
  `json_response=True` and `stateless_http=True` (the latter only affects the legacy leg;
  "the modern path is sessionless by construction"). Gotcha: by default the app answers
  only requests addressed to localhost and returns 421 otherwise; pass
  `TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])`. Auth hooks:
  `TokenVerifier.verify_token()` with `AuthSettings`, which also serves
  `/.well-known/oauth-protected-resource`. Tools support `outputSchema` and
  `structuredContent`; annotations exist but "clients MUST consider tool annotations to be
  untrusted unless they come from trusted servers."
- A working example of this exact mount (MCPServer, `StreamableHTTPASGIApp`,
  `stateless_http=True`, `json_response=True`, ASGI auth middleware accepting an Access JWT
  or a bearer token) exists in the outfit-planner repo on this machine. It is a useful
  comparison when debugging, not a source to copy; that project is still in development.

## 6. Identification method: analysis and recommendation

Evidence gathered:

| Method | Measured result | Source |
| --- | --- | --- |
| Full-card pHash, webcam captures vs Scryfall renders | Right card never ranked first across 26 captures; even a hand-cropped perfect warp ranked 5,367th of 111,154 | juthrbog/weatherlight README |
| Art-crop 1024-bit dHash index | True illustration group at Hamming 180 to 340, best impostor 46 to 150 bits further | neotoxicfr/mtg-scanner-art-index calibration |
| Hash + BoVW + ORB verify | 78% exact printing, 88% right card, 1187 ms | bindarr `cvScan.js` header, 100 noisy phone photos |
| CollectorVision cornelius + milo | 76% exact printing, 90% right card, 310 ms (server) | same |
| RapidOCR title line | 21 of 25 webcam captures named, zero wrong names, ~300 ms | weatherlight README |
| SigLIP2 server embedding + YOLO | Claims ~99% top-1 at ~270 ms (unverified, author-generated) | zluo01/mtg-scanner |

What the numbers say: whole-card hashing of phone photos is not reliable enough to be the
primary path. Both the learned embedding and the hash stack top out around 76 to 78%
"exact printing" for the same reason: 74% of printings share their artwork with another
printing, so nothing that looks only at the picture can tell them apart. "Right card" is
the achievable target for the image stage (90% for CollectorVision at a third of the
time). Pinning the printing needs the text that is unique to the printing: the collector
number and set code line on the bottom-left of every card since the M15 frame (2014),
"0123/0281 R" over "MOM EN". Cards from Exodus (1998) to 2014 carry a collector number but
no printed set code, and pre-1998 cards carry neither, so those fall back to a printings
picker scoped by the name and frame era.

Recommendation, a combination:

1. Detect and dewarp with CollectorVision's cornelius (works on a card held in hand, in a
   sleeve, on a table). The browser can run the same model via onnxruntime-web to show a
   live outline and send a pre-warped 448x448 crop, which keeps the upload small and the
   server fast; bindarr proves this works. Phase 3 decides whether to do the warp on the
   phone or server; server-only is simpler and still ~300 ms.
2. Embed with milo and sweep the hosted Scryfall catalog. Keep the top hits, group them by
   `oracle_id` (same card, different printings). Apply bindarr's gap gate (winner must beat
   ranks 2 to 11 by at least 0.10) to decide between auto-fill and "pick one".
3. Read the collector line with RapidOCR on the bottom-left strip of the dewarped crop,
   upscaled and thresholded. Parse `(\d{1,4})/(\d{1,4})` and a 3 to 5 letter set code.
   If a printing in the candidate group matches `set + collector_number`, that is the
   answer. If not, show the candidate printings for the top oracle group with the most
   recent first, and let the user tap one.
4. Manual search (name autocomplete from the local catalogue, then printings) and CSV
   import stay as independent paths.

Why not OCR-first: title OCR alone cannot resolve the printing either, and it fails on
foil glare, non-English cards and stylised frames where the embedding still works.
Why not pHash-first: the measured accuracy is worse and the speed is not better once
rectification is included. The art hash index remains a good MIT fallback and is cheap
to wire in later as a second opinion, so the pipeline should be written as pluggable
stages with a test harness of real phone photos rather than hard-coded to one library.

Speed through a stack: CollectorVision's end-to-end is under 100 ms on a laptop CPU;
bindarr measures 310 ms server-side including hydration; RapidOCR adds roughly 200 to
300 ms on the small strip. Budget about half a second per card on the Home Assistant host
(arm64 or amd64, CPU only), with the confirm step being the real bottleneck. The UI
should queue: capture, show the best match immediately, allow a one-tap confirm with the
previous card's foil and condition defaults, and move on.

## 7. CPU tooling on amd64 and arm64

- opencv-python-headless 5.0.0.93, Apache-2.0, ships manylinux aarch64 abi3 wheels
  (~37 to 40 MB).
- onnxruntime 1.30.0, MIT, manylinux_2_28 aarch64 wheels (~21 MB). Needed by both
  CollectorVision and RapidOCR, so it is paid for once.
- RapidOCR: `rapidocr` 3.10.0, Apache-2.0, pure-Python wheel (27 MB, PP-OCRv4 models
  bundled: detector 4.5 MB, English recogniser 7.3 MB). Runs identically on both
  architectures. Chosen over Tesseract (weak on small low-contrast text), EasyOCR (pulls
  torch) and PaddleOCR (no Linux aarch64 wheels; reported ARM64 segfaults).
- imagehash 4.3.2 (BSD-2-Clause) and numpy 2 `bitwise_count` cover the optional hash path.
  Brute-force Hamming over 120k 256-bit hashes measured at 2.4 ms in numpy; faiss is not
  needed.
- Image size estimate: python slim + OpenCV + onnxruntime + RapidOCR + CollectorVision
  comes to roughly 400 to 500 MB; weatherlight reports 660 MB with a similar stack.

## 8. Pricing and AUD

- Scryfall prices arrive with the daily `default_cards` pull, so the price job and the
  catalogue sync are the same job: download once a day, upsert the catalogue, snapshot
  `usd`, `usd_foil`, `usd_etched`, `eur`, `eur_foil`, `tix` into a history table keyed by
  Scryfall ID and date. No per-card API calls at all.
- MTGJSON (MIT, mtgjson.com) publishes `AllPrices.json` with a rolling 90 days of
  TCGplayer, Cardmarket, Card Kingdom, Cardsphere and Cardhoarder retail and buylist
  prices per finish, and `AllPricesToday.json` for just the current day; builds daily
  around 09:00 US Eastern. Useful if you later want TCGplayer buylist or 90 days of
  backfill on first install; not needed for the brief.
- FX: Frankfurter v1 (`https://api.frankfurter.dev/v1/latest?base=USD&symbols=AUD`,
  returned 1.4402 on 08/10/2026; `base=EUR` returned 1.611) is ECB reference data updated
  around 16:00 CET on working days, no key, MIT, self-hostable. The old
  `api.frankfurter.app` host redirects to it. open.er-api.com also works without a key
  (`/v6/latest/USD`, 24 h cadence, attribution required). exchangerate.host now requires an
  APILayer key with a 100 request per month USD-only free tier; skip it. Store the rate
  used alongside each day's snapshot so AUD history is reproducible, and allow a manual
  override rate in settings.

## 9. CSV import formats (actual headers)

From MtgCsvHelper's mappings (Apache-2.0) and vendor docs:

- ManaBox: Name, Set code, Set name, Collector number, Foil (normal/foil/etched), Rarity,
  Quantity, ManaBox ID, Scryfall ID, Purchase price, Misprint, Altered, Condition,
  Language, Purchase price currency. Whole-collection exports add a Binder Name column.
- Deckbox: Count, Tradelist Count, Name, Edition, Edition Code, Card Number, Condition,
  Language, Foil ("" or foil), Signed, Artist Proof, Altered Art, Misprint, Promo,
  Textless, Printing Id, Printing Note, Tags, My Price, Cost, Rarity, Price, TcgPlayer
  ID, Scryfall ID. Deckbox edition names need an alias table.
- Moxfield: Count, Tradelist Count, Name, Edition, Condition, Language, Foil, Tags, Last
  Modified, Collector Number, Alter, Proxy, Purchase Price. Conditions are Mint, Near
  Mint, Good (Lightly Played), Played, Heavily Played, Damaged.
- Archidekt: Quantity, Name, Finish, Condition, Date Added, Language, Purchase Price,
  Tags, Edition Name, Edition Code, Multiverse Id, Scryfall ID, Collector Number.
- TCGplayer app: Quantity, Name, Simple Name, Set, Card Number, Set Code, Printing,
  Condition, Language, Rarity, Product ID, SKU.
- Dragon Shield: Folder Name, Quantity, Trade Quantity, Card Name, Set Code, Set Name,
  Card Number, Condition, Printing, Language, Price Bought, Date Bought, LOW, MID, MARKET.
- Delver Lens: user-configurable columns; treat as any subset of Scryfall ID, Name,
  Quantity, Condition, Foil, Language.

Every modern format carries a Scryfall ID or set code + collector number, so one importer
keyed on those, with name-only fallback through the local catalogue, covers all of them.
Detect the format by header set.

## 10. Cloudflare Access facts

From developers.cloudflare.com (validating-json and service-tokens pages):

- Access signs an application token "with a key pair unique to your account" and sends it
  to the origin as the `Cf-Access-Jwt-Assertion` request header; browser requests also
  carry it as the `CF_Authorization` cookie. "We recommend validating the
  Cf-Access-Jwt-Assertion header" rather than the cookie.
- Public keys: `https://<team-name>.cloudflareaccess.com/cdn-cgi/access/certs`, a JWKS
  with RS256 keys identified by `kid`. "Validate tokens using the external endpoint rather
  than saving the public key as a hard-coded value" and match by `kid` rather than reading
  `public_cert`, to survive rotation.
- The `aud` claim "specifies which application the JWT is valid for"; the AUD tag is in
  the application's Configure page under Additional settings. Issuer is the team domain.
  User tokens carry `email`; service-token requests carry `common_name` and no email.
- Service tokens: client sends `CF-Access-Client-Id` and `CF-Access-Client-Secret`
  headers; the policy action must be Service Auth "otherwise, Access will prompt for an
  identity provider login". Tokens have a set duration (for example 8760h), can be rotated
  with a grace period of one hour to 30 days during which both secrets work, and renewed.
- PyJWT's `PyJWKClient` does the JWKS fetch, `kid` selection and caching; `jwt.decode`
  with `audience`, `issuer` and `algorithms=["RS256"]` does the rest. A few dozen lines.

## 11. Licence table

| Project | Licence (from LICENSE file unless noted) | Use |
| --- | --- | --- |
| CollectorVision (code and ONNX weights) | AGPL-3.0-or-later; free noncommercial and commercial licences offered, formal texts not yet published | Adopt |
| neotoxicfr/mtg-scanner-art-index | MIT | Optional fallback |
| RapidOCR, onnxruntime, opencv-python-headless | Apache-2.0, MIT, Apache-2.0 | Adopt |
| imagehash | BSD-2-Clause | Optional |
| mcp Python SDK | MIT | Adopt |
| FastAPI, uvicorn, httpx, PyJWT | MIT, BSD, BSD, MIT | Adopt |
| as3k/scryfall-mcp | No LICENSE file; README says MIT | Borrow ideas only |
| bindarr, scrybox | MIT | Reference for UX, scan gate, ManaBox sync |
| bitdagger/mtg-scanner | MIT | Hough rectifier as zero-ML fallback |
| Binderbase | AGPL-3.0 | Reference only |
| MTG-Bulk-Scan | GPL-3.0 (README wrongly says MIT) | Nothing |
| DCCoder90/mtg-mcp | MIT | Nothing |
| pato/mtg-mcp | No LICENSE file; Cargo.toml says MIT | Nothing |
| MtgCsvHelper | Apache-2.0 | CSV mapping reference |
| McDandle/local-mtg-scanner | MIT | Price-history and condition schema reference |
| Frankfurter | MIT | Adopt (hosted) |
| MTGJSON | MIT | Optional |
| Scryfall data and images | Scryfall terms; WotC Fan Content Policy | Adopt |

## 12. Decisions carried into Phase 1

1. Repo licence: AGPL-3.0-or-later (recommended) so CollectorVision can be baked into the
   image, versus MIT with a first-run fetch into the volume.
2. Identification pipeline as in section 6, written as pluggable stages with a photo test
   set.
3. Python 3.13, FastAPI, SQLite, single container plus cloudflared. Docker and
   Cloudflare layout as already deployed on the host; everything else chosen on merit.
4. Pricing and catalogue sync are one daily job over the Scryfall bulk file; AUD from
   Frankfurter with manual override.
5. MCP auth: service tokens on a path-scoped Access application for Claude Code, plus an
   app-issued bearer token for claude.ai and Claude Desktop connectors via their Request
   headers field. Both verified by the app, never trusted from the tunnel. Trade-off
   written up in ARCHITECTURE.md.
