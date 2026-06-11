"""
Schema SQLite per Pathosphere.

Tabelle:
  sources          — catalogo fonti (paese, orientamento, controllo statale)
  raw_documents    — documenti grezzi ingestiti
  events           — eventi aggregati (cluster di articoli)
  entities         — entità estratte (paesi, aziende, commodity, infrastrutture)
  entity_links     — relazioni tra entità (grafo)
  theses           — tesi generate dall'agent
  trades           — operazioni paper trading
  portfolios       — portafogli virtuali (agent, random, benchmark)
  predictions      — anticipazioni non finanziarie con calibrazione Tetlock

sqlite-vec: tabella virtuale vec_documents per nearest-neighbour su embedding.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ──────────────────────────────────────────────
-- FONTI
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    url             TEXT,
    country         TEXT    NOT NULL,           -- ISO 3166-1 alpha-2
    geopolitical_block TEXT NOT NULL,           -- western, china, russia, arab, india, latam, africa, other
    orientation     TEXT,                       -- es. state, independent, opposition
    state_control   INTEGER NOT NULL DEFAULT 0, -- 0-3 (0=none, 3=full)
    language        TEXT,                       -- ISO 639-1
    active          INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- DOCUMENTI GREZZI
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_documents (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER REFERENCES sources(id),
    url             TEXT    UNIQUE,
    title           TEXT,
    body            TEXT,
    published_at    TEXT,                       -- ISO 8601
    fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    language        TEXT,
    content_hash    TEXT    UNIQUE,             -- SHA-256 del body per dedup esatto
    embedded        INTEGER NOT NULL DEFAULT 0  -- 0=non ancora; 1=embedding calcolato
);

CREATE INDEX IF NOT EXISTS idx_raw_doc_source    ON raw_documents(source_id);
CREATE INDEX IF NOT EXISTS idx_raw_doc_published ON raw_documents(published_at);
CREATE INDEX IF NOT EXISTS idx_raw_doc_embedded  ON raw_documents(embedded);

-- ──────────────────────────────────────────────
-- EVENTI (cluster di articoli)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    title           TEXT    NOT NULL,
    summary         TEXT,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    event_type      TEXT,                       -- conflict, epidemic, trade, infrastructure, political, other
    severity        INTEGER,                    -- 1-5
    location_name   TEXT,
    lat             REAL,
    lon             REAL,
    resolved_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_documents (
    event_id        INTEGER REFERENCES events(id) ON DELETE CASCADE,
    document_id     INTEGER REFERENCES raw_documents(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, document_id)
);

-- Divergenza narrativa per evento (confronto blocchi geopolitici)
CREATE TABLE IF NOT EXISTS narrative_divergences (
    id              INTEGER PRIMARY KEY,
    event_id        INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    block_a         TEXT    NOT NULL,
    block_b         TEXT    NOT NULL,
    divergence_score REAL,                      -- 0-1
    summary         TEXT,
    computed_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- ENTITÀ E GRAFO
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    canonical_name  TEXT,
    entity_type     TEXT    NOT NULL,           -- country, company, commodity, infrastructure, person, other
    wikidata_qid    TEXT    UNIQUE,
    aliases         TEXT,                       -- JSON array
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entity_qid  ON entities(wikidata_qid);

CREATE TABLE IF NOT EXISTS entity_links (
    id              INTEGER PRIMARY KEY,
    entity_a        INTEGER NOT NULL REFERENCES entities(id),
    entity_b        INTEGER NOT NULL REFERENCES entities(id),
    relation_type   TEXT    NOT NULL,           -- depends_on, supplies, sanctions, ally, adversary, ...
    strength        REAL,                       -- 0-1
    source_event    INTEGER REFERENCES events(id),
    valid_from      TEXT,
    valid_to        TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_elink_a ON entity_links(entity_a);
CREATE INDEX IF NOT EXISTS idx_elink_b ON entity_links(entity_b);

-- ──────────────────────────────────────────────
-- WATCHLIST (indicatori osservabili per scenario)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist_items (
    id              INTEGER PRIMARY KEY,
    label           TEXT    NOT NULL,
    description     TEXT,
    indicator_query TEXT,                       -- keyword/GDELT filter
    status          TEXT    NOT NULL DEFAULT 'active',  -- active, triggered, expired
    triggered_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- TESI DELL'AGENT
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS theses (
    id              INTEGER PRIMARY KEY,
    trigger_event   INTEGER REFERENCES events(id),
    title           TEXT    NOT NULL,
    causal_chain    TEXT    NOT NULL,           -- testo libero / JSON
    instrument      TEXT,                       -- es. "USO", "TSMC", "GLD"
    direction       TEXT,                       -- long / short / neutral
    horizon_days    INTEGER,
    invalidation    TEXT,
    confidence      REAL,                       -- 0-1 soggettivo
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending, approved, rejected, closed
    approved_at     TEXT,
    rejected_at     TEXT,
    rejection_reason TEXT,
    sources_json    TEXT,                       -- JSON array di URL
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────
-- PAPER TRADING
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolios (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,    -- agent, random, benchmark
    portfolio_type  TEXT    NOT NULL,
    cash            REAL    NOT NULL DEFAULT 100000.0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY,
    portfolio_id    INTEGER NOT NULL REFERENCES portfolios(id),
    thesis_id       INTEGER REFERENCES theses(id),
    ticker          TEXT    NOT NULL,
    direction       TEXT    NOT NULL,           -- buy / sell
    quantity        REAL    NOT NULL,
    price_open      REAL    NOT NULL,           -- prezzo al momento della DECISIONE (no lookahead)
    price_close     REAL,                       -- prezzo alla chiusura
    opened_at       TEXT    NOT NULL,
    closed_at       TEXT,
    transaction_cost REAL   NOT NULL DEFAULT 0.0,
    slippage        REAL    NOT NULL DEFAULT 0.0,
    pnl             REAL,                       -- calcolato alla chiusura
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_portfolio ON trades(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_trade_ticker    ON trades(ticker);

-- ──────────────────────────────────────────────
-- PREDIZIONI NON FINANZIARIE (calibrazione Tetlock)
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY,
    thesis_id       INTEGER REFERENCES theses(id),
    description     TEXT    NOT NULL,           -- "Escalation in X entro 2 settimane"
    probability     REAL    NOT NULL,           -- 0-1
    horizon_date    TEXT    NOT NULL,           -- scadenza ISO 8601
    resolved        INTEGER NOT NULL DEFAULT 0, -- 0=aperta, 1=risolta
    outcome         INTEGER,                    -- NULL=aperta, 1=vero, 0=falso
    resolved_at     TEXT,
    brier_score     REAL,                       -- calcolato alla risoluzione
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pred_horizon  ON predictions(horizon_date);
CREATE INDEX IF NOT EXISTS idx_pred_resolved ON predictions(resolved);
"""

DDL += """
-- ──────────────────────────────────────────────
-- TRACKING DOWNLOAD GDELT
-- ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gdelt_file_log (
    id              INTEGER PRIMARY KEY,
    filename        TEXT    NOT NULL UNIQUE,    -- es. 20260611153000.export.CSV.zip
    url             TEXT    NOT NULL,
    downloaded_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    rows_raw        INTEGER,                    -- righe nel CSV originale
    rows_stored     INTEGER,                    -- righe effettivamente inserite dopo filtro
    status          TEXT    NOT NULL DEFAULT 'ok'  -- ok, error, skipped
);
"""

SQLITE_VEC_VIRTUAL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents
USING vec0(
    document_id INTEGER PRIMARY KEY,
    embedding   FLOAT[384]          -- multilingual-e5-small output dim
);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_db(db_path: Path) -> None:
    conn = get_connection(db_path)
    with conn:
        conn.executescript(DDL)
        conn.executescript(SQLITE_VEC_VIRTUAL)
    conn.close()
