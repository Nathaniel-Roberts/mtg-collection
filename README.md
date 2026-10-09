# MTG Collection

Self-hosted Magic: The Gathering collection manager. Scan cards with a phone camera,
keep a database of everything you own with daily Scryfall prices and an AUD conversion,
build and validate decks, and let an AI assistant see the collection through an MCP
server. One container, SQLite, behind a Cloudflare Tunnel and Cloudflare Access.

See ARCHITECTURE.md for the design and RESEARCH.md for the research behind it.

## What it does

- Scan: open the camera in the installable web app, capture a card, confirm the match
  with finish, condition and quantity, move to the next one. Manual search and CSV import
  (ManaBox, Moxfield, Deckbox, Archidekt, TCGplayer, Dragon Shield, MTGGoldfish, generic)
  are the fallbacks.
- Collection: one row per printing, finish, condition and language with a quantity, tags
  and notes. List and grid views, filters, sort, value summary by colour, type, set, rarity,
  finish and condition, per-card price history, daily collection value.
- Prices: the Scryfall bulk file once a day (USD, EUR, tix), snapshots for cards you own
  or have in decks, AUD from the European Central Bank reference rate via Frankfurter or a
  manual override.
- Decks: roles (commander, companion, main, sideboard, maybeboard), format validation
  (deck size, copy limits, commander and colour identity, Scryfall legality), owned versus
  missing with the cost to buy, cross-deck conflict warnings, text import and export,
  suggestions from what you own.
- MCP: fourteen tools over the same database so Claude (or any MCP client) can search
  the collection, build decks from owned cards, validate them and save them back.

## How identification works

A photo goes through four stages: CollectorVision finds the card's corners and dewarps
it, its milo embedder matches the artwork against a catalogue of every Scryfall printing,
RapidOCR reads the collector line on the bottom edge to pin the exact printing, and a
resolver merges the two into a ranked list with a confidence gate. The first scan after a
start loads the models and downloads the image catalogue (about 35 MB) into the data
volume. On a CPU a scan takes roughly half a second.

## Deploy

Everything runs on one Docker host. The app has no login of its own: it trusts
Cloudflare Access to identify people and verifies the Access JWT on every request.
Never expose it without Access (or an equivalent authenticating proxy) in front.

### 1. Cloudflare Tunnel

1. Zero Trust > Networks > Tunnels > Create a tunnel (cloudflared). Copy the token.
2. Add a public hostname, for example `cards.example.com`, with service
   `http://app:8000` (the compose service name).

### 2. Start the containers

```sh
mkdir mtg-collection && cd mtg-collection
curl -fsSLO https://raw.githubusercontent.com/Nathaniel-Roberts/mtg-collection/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/Nathaniel-Roberts/mtg-collection/main/.env.example -o .env
# edit .env: TUNNEL_TOKEN now; CF_ACCESS_* after step 3; MCP_BEARER_TOKEN if you want it
docker compose up -d
docker compose logs -f app
```

The first start downloads the Scryfall bulk file (about 80 MB) and loads roughly 120k
printings, which takes one to two minutes. Until Access is configured the app refuses
every request except `/healthz` and logs why, so do step 3 straight away.

### 3. Cloudflare Access

Two applications on the same hostname: one for people, one for MCP clients.

**People.** Zero Trust > Access > Applications > Add an application > Self-hosted.
Domain `cards.example.com`, session duration to taste. Policy `Owner`, action Allow,
include your email (or your identity provider group). Pick your OAuth identity
provider under Login methods.

**MCP clients.** Add a second self-hosted application with domain `cards.example.com`
and path `mcp`. Access evaluates the more specific path first. Give it:

- a policy `Claude Code`, action Service Auth, include Service Token, selecting a token
  you create under Access > Service Auth > Service Tokens (copy the Client ID and Client
  Secret when you create it; the secret is shown once), and
- only if you use claude.ai or Claude Desktop connectors, a policy `Connectors`, action
  Bypass, include Everyone. The app then insists on `MCP_BEARER_TOKEN` for anything that
  reaches `/mcp` without an Access JWT, and returns 401 otherwise.

Then open the people application, Configure, Additional settings, and copy the
Application Audience (AUD) tag. Set in `.env`:

```
CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
CF_ACCESS_AUD=<the AUD tag>
MCP_BEARER_TOKEN=<openssl rand -hex 32, optional>
```

`docker compose up -d` again. Both Access applications sign tokens with the same team
key pair; the app checks the signature against the team's public keys, the audience,
issuer and expiry on every request. Service tokens carry no email, so the web API
refuses them and only `/mcp` accepts them.

### 4. Connect Claude

Claude Code sends custom headers, so use the service token:

```sh
claude mcp add --transport http cards https://cards.example.com/mcp \
  --header "CF-Access-Client-Id: <client id>.access" \
  --header "CF-Access-Client-Secret: <client secret>"
```

claude.ai and Claude Desktop custom connectors (Settings > Connectors > Add custom
connector) connect from Anthropic's cloud and offer a Request headers field. With the
Bypass policy on `/mcp`, add the connector with URL `https://cards.example.com/mcp` and
header `Authorization: Bearer <MCP_BEARER_TOKEN>`. Rotate the token by changing `.env`
and restarting.

The tools: search_collection, search_cards, get_card, collection_summary, list_decks,
get_deck, create_deck, update_deck, add_cards_to_deck, remove_cards_from_deck,
validate_deck, suggest_from_collection, deck_conflicts, delete_deck. Decks an assistant
builds appear in the web app immediately.

### Environment

Every setting is an environment variable; `.env.example` lists them with comments. The
important ones: `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` (Access verification),
`MCP_BEARER_TOKEN`, `SYNC_HOUR` (daily job time, Sydney by default), `FX_PROVIDER`
(frankfurter or manual with `FX_USD_AUD` and `FX_EUR_AUD`), `SCANNER_ENABLED`,
`SCRYFALL_USER_AGENT` (Scryfall asks for a descriptive one).

### Backup and restore

Everything lives in the `mtg-data` volume: `collection.db` (the only thing that matters),
`cv-cache` (re-downloaded), `scans` (recent crops), `icons`. Back up the database with the
online backup API so you get a consistent copy while the app is running:

```sh
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/collection.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose cp app:/data/backup.db ./collection-$(date +%F).db
```

Settings > Download database backup in the web app does the same thing in one click, and
Export CSV gives a ManaBox-compatible file. To restore, stop the app, copy the file to
`/data/collection.db` in the volume, start it again. A nightly cron doing the two
commands above into a folder your existing backups cover is enough.

## Releases

Images are built by GitHub Actions for linux/amd64 and linux/arm64 and pushed to
`ghcr.io/nathaniel-roberts/mtg-collection`. Every push to `main` updates `:latest`; a
tag `vX.Y.Z` publishes `:vX.Y.Z` and `:vX.Y`.

To release:

1. Update `version` in `pyproject.toml` and move the Unreleased notes in CHANGELOG.md
   under a new heading with today's date.
2. Commit, then tag and push: `git tag vX.Y.Z && git push origin main vX.Y.Z`.
3. Wait for the Docker image workflow, then on the host `docker compose pull && docker compose up -d`.

Schema changes ship as new files in `migrations/` and apply themselves at startup, so
upgrades need no manual step. Downgrades are not supported; restore a backup instead.

## Develop

```sh
cp .env.example .env            # set DEV_MODE=true and DATA_DIR=./data
uv sync
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Open http://127.0.0.1:8000. The camera only works over HTTPS or on localhost, so test the
scanner on the machine itself or through the tunnel. `DEV_MODE=true` makes every request
act as `DEV_USER_EMAIL` with no Access check; never run it anywhere reachable. The scan
tests use fake models; drop real phone photos named `<scryfall_id>.jpg` into
`tests/fixtures/photos/` to exercise the real pipeline. On NixOS the native wheels need
`LD_LIBRARY_PATH` pointing at `stdenv.cc.cc.lib`, `zlib`, `glib` and `libGL`.

## Licences and data

The code is AGPL-3.0-or-later (see LICENSE), chosen so that CollectorVision (AGPL-3.0)
can ship inside the image. Card data and images are from Scryfall under the Wizards of
the Coast Fan Content Policy; this app is unofficial and not endorsed by Wizards.
Prices are Scryfall's daily values. Exchange rates from Frankfurter (ECB). OCR by
RapidOCR (Apache-2.0). Interface built with Preact (MIT) and htm (Apache-2.0).
