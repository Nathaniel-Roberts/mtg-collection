-- Initial schema. See ARCHITECTURE.md section 3.

CREATE TABLE sets (
  code TEXT PRIMARY KEY,
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
  id TEXT PRIMARY KEY,
  oracle_id TEXT,
  name TEXT NOT NULL,
  printed_name TEXT,
  lang TEXT NOT NULL,
  set_code TEXT NOT NULL REFERENCES sets(code),
  collector_number TEXT NOT NULL,
  released_at TEXT,
  rarity TEXT NOT NULL,
  layout TEXT NOT NULL,
  type_line TEXT,
  oracle_text TEXT,
  mana_cost TEXT,
  cmc REAL,
  colors TEXT NOT NULL DEFAULT '',
  color_identity TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '[]',
  power TEXT,
  toughness TEXT,
  loyalty TEXT,
  legalities TEXT NOT NULL DEFAULT '{}',
  finishes TEXT NOT NULL DEFAULT '[]',
  promo INTEGER NOT NULL DEFAULT 0,
  digital INTEGER NOT NULL DEFAULT 0,
  paper INTEGER NOT NULL DEFAULT 1,
  reprint INTEGER NOT NULL DEFAULT 0,
  full_art INTEGER NOT NULL DEFAULT 0,
  frame TEXT,
  border_color TEXT,
  artist TEXT,
  illustration_id TEXT,
  edhrec_rank INTEGER,
  image_small TEXT,
  image_normal TEXT,
  image_large TEXT,
  image_art_crop TEXT,
  image_back_normal TEXT,
  card_faces TEXT,
  prices TEXT NOT NULL DEFAULT '{}',
  price_usd REAL,                   -- usd, else usd_foil, else usd_etched; for sorting
  usd REAL, usd_foil REAL, usd_etched REAL, eur REAL, eur_foil REAL, eur_etched REAL, tix REAL,
  legal_formats TEXT NOT NULL DEFAULT ' ',  -- ' commander modern ' for legal or restricted formats
  is_canonical INTEGER NOT NULL DEFAULT 0,  -- one printing per oracle_id, shown in "cards" searches
  scryfall_uri TEXT,
  updated_at TEXT NOT NULL,
  seen_in_sync INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX cards_oracle ON cards(oracle_id);
CREATE INDEX cards_set_number ON cards(set_code, collector_number);
CREATE INDEX cards_name ON cards(name COLLATE NOCASE);
CREATE INDEX cards_illustration ON cards(illustration_id);
CREATE INDEX cards_canonical_name ON cards(is_canonical, name COLLATE NOCASE);
CREATE INDEX cards_canonical_released ON cards(is_canonical, released_at);
CREATE INDEX cards_price ON cards(price_usd);
CREATE INDEX cards_edhrec ON cards(edhrec_rank);
CREATE INDEX cards_cmc ON cards(cmc);
-- Covering index for joins from collection_entries and deck_cards by card id: the
-- columns summaries, ownership checks and value sorts need, so those joins never read
-- the wide rows (oracle text, JSON).
CREATE INDEX cards_by_id ON cards(
  id, oracle_id, name, set_code, collector_number, lang, rarity, type_line, color_identity, colors,
  cmc, usd, usd_foil, usd_etched, eur, eur_foil, eur_etched, price_usd, released_at, edhrec_rank
);
-- Covering index for filter-only catalogue searches: every column those queries touch,
-- so the planner never has to read the wide rows (oracle text, JSON) for 37k candidates.
CREATE INDEX cards_search ON cards(
  is_canonical, paper, name COLLATE NOCASE, released_at, color_identity, colors, type_line,
  legal_formats, rarity, cmc, edhrec_rank, price_usd, oracle_id, set_code, digital
);

CREATE VIRTUAL TABLE cards_fts USING fts5(
  name, printed_name, type_line, oracle_text,
  content='cards', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER cards_ai AFTER INSERT ON cards BEGIN
  INSERT INTO cards_fts(rowid, name, printed_name, type_line, oracle_text)
  VALUES (new.rowid, new.name, new.printed_name, new.type_line, new.oracle_text);
END;
CREATE TRIGGER cards_ad AFTER DELETE ON cards BEGIN
  INSERT INTO cards_fts(cards_fts, rowid, name, printed_name, type_line, oracle_text)
  VALUES ('delete', old.rowid, old.name, old.printed_name, old.type_line, old.oracle_text);
END;
CREATE TRIGGER cards_au AFTER UPDATE ON cards
WHEN old.name IS NOT new.name OR old.printed_name IS NOT new.printed_name
  OR old.type_line IS NOT new.type_line OR old.oracle_text IS NOT new.oracle_text
BEGIN
  INSERT INTO cards_fts(cards_fts, rowid, name, printed_name, type_line, oracle_text)
  VALUES ('delete', old.rowid, old.name, old.printed_name, old.type_line, old.oracle_text);
  INSERT INTO cards_fts(rowid, name, printed_name, type_line, oracle_text)
  VALUES (new.rowid, new.name, new.printed_name, new.type_line, new.oracle_text);
END;

CREATE TABLE collection_entries (
  id INTEGER PRIMARY KEY,
  card_id TEXT NOT NULL REFERENCES cards(id),
  finish TEXT NOT NULL CHECK (finish IN ('nonfoil','foil','etched')),
  condition TEXT NOT NULL CHECK (condition IN ('NM','LP','MP','HP','DMG')),
  language TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  notes TEXT,
  source TEXT NOT NULL,
  added_by TEXT,
  added_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (card_id, finish, condition, language)
);
CREATE INDEX entries_card ON collection_entries(card_id);

CREATE TABLE tags (name TEXT PRIMARY KEY);
CREATE TABLE entry_tags (
  entry_id INTEGER NOT NULL REFERENCES collection_entries(id) ON DELETE CASCADE,
  tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
  PRIMARY KEY (entry_id, tag)
);

CREATE TABLE price_snapshots (
  card_id TEXT NOT NULL REFERENCES cards(id),
  day TEXT NOT NULL,
  usd TEXT, usd_foil TEXT, usd_etched TEXT,
  eur TEXT, eur_foil TEXT, eur_etched TEXT,
  tix TEXT,
  PRIMARY KEY (card_id, day)
);
CREATE INDEX price_snapshots_day ON price_snapshots(day);

CREATE TABLE fx_rates (
  day TEXT NOT NULL,
  base TEXT NOT NULL,
  quote TEXT NOT NULL DEFAULT 'AUD',
  rate REAL NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (day, base, quote)
);

CREATE TABLE collection_value_daily (
  day TEXT PRIMARY KEY,
  cards INTEGER NOT NULL,
  entries INTEGER NOT NULL,
  usd REAL NOT NULL,
  eur REAL NOT NULL,
  aud REAL NOT NULL,
  usd_aud REAL NOT NULL,
  eur_aud REAL NOT NULL,
  priced_entries INTEGER NOT NULL
);

CREATE TABLE decks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  format TEXT NOT NULL,
  description TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE deck_cards (
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES cards(id),
  role TEXT NOT NULL CHECK (role IN ('main','commander','companion','sideboard','maybeboard')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (deck_id, card_id, role)
);
CREATE TABLE deck_conflict_acks (
  oracle_id TEXT PRIMARY KEY,
  shortfall INTEGER NOT NULL,
  acknowledged_at TEXT NOT NULL,
  acknowledged_by TEXT
);

CREATE TABLE scans (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  result TEXT NOT NULL,
  chosen_card_id TEXT REFERENCES cards(id),
  outcome TEXT,
  image_path TEXT
);

CREATE TABLE sync_runs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
