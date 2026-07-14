"""
Tests for SQLite schema: init, tables, sqlite-vec.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pathosphere.db.schema import get_connection, init_db


EXPECTED_TABLES = {
    "sources",
    "raw_documents",
    "events",
    "event_documents",
    "narrative_divergences",
    "entities",
    "entity_links",
    "watchlist_items",
    "theses",
    "portfolios",
    "trades",
    "predictions",
    "gdelt_file_log",
    "briefs",
}


def get_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}


# ─────────────────────────────────────────────────────────────
# init_db
# ─────────────────────────────────────────────────────────────

def test_init_db_creates_all_tables(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    tables = get_table_names(conn)
    conn.close()
    assert EXPECTED_TABLES.issubset(tables)


def test_init_db_is_idempotent(tmp_path: Path):
    """Calling init_db twice raises no errors (CREATE TABLE IF NOT EXISTS)."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    init_db(db_path)  # second call: no error


def test_init_db_creates_db_file(tmp_path: Path):
    db_path = tmp_path / "sub" / "nested" / "test.db"
    init_db(db_path)
    assert db_path.exists()


# ─────────────────────────────────────────────────────────────
# get_connection
# ─────────────────────────────────────────────────────────────

def test_get_connection_row_factory(tmp_db):
    """Rows must be accessible by column name."""
    conn = tmp_db
    conn.execute(
        "INSERT INTO sources (name, country, geopolitical_block, state_control) "
        "VALUES ('TestSource', 'US', 'western', 0)"
    )
    conn.commit()
    row = conn.execute("SELECT name, country FROM sources").fetchone()
    assert row["name"] == "TestSource"
    assert row["country"] == "US"


def test_get_connection_foreign_keys_on(tmp_db):
    result = tmp_db.execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1


def test_get_connection_wal_mode(tmp_db):
    result = tmp_db.execute("PRAGMA journal_mode").fetchone()
    assert result[0] == "wal"


def test_get_connection_auto_migrates_pre_v2_db(tmp_path: Path):
    """CP-010: a DB created with only a bare CREATE TABLE (pre-v2, missing
    columns added later via _MIGRATIONS) must be auto-migrated by
    get_connection alone — no explicit init_db()/migrate_db() call needed.
    Otherwise a pulled DB with new code but an old schema crashes with
    'no such column' on the first query touching a migrated column."""
    db_path = tmp_path / "legacy.db"

    # Bare schema, deliberately missing columns added later by _MIGRATIONS
    # (e.g. entities.canonical_entity_id, CP-018).
    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)")
    raw_conn.commit()
    raw_conn.close()

    conn = get_connection(db_path)  # no init_db()/migrate_db() called explicitly
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(entities)")}
        assert "canonical_entity_id" in cols
    finally:
        conn.close()


def test_get_connection_only_migrates_once_per_process_per_path(tmp_path: Path):
    """Efficiency fix: migrate_db()'s full statement sweep must not re-run on
    every get_connection() call for an already-migrated path in this
    process — cycle/orchestrator.py opens 6 connections per `pathos cycle`
    run, and the Streamlit dashboard opens one per page rerun."""
    db_path = tmp_path / "repeat.db"
    init_db(db_path)

    with patch("pathosphere.db.schema.migrate_db") as mock_migrate:
        conn1 = get_connection(db_path)
        conn2 = get_connection(db_path)
        conn1.close()
        conn2.close()

    mock_migrate.assert_not_called()  # already in _migrated_paths from init_db()


def test_get_connection_migrates_each_new_path_once(tmp_path: Path):
    """A different db_path not yet seen this process must still be migrated
    on its first connection — the cache is per-path, not global."""
    import pathosphere.db.schema as schema_module

    db_path = tmp_path / "fresh_path.db"

    with patch.object(schema_module, "migrate_db", wraps=schema_module.migrate_db) as mock_migrate:
        conn1 = get_connection(db_path)
        conn2 = get_connection(db_path)
        conn1.close()
        conn2.close()

    assert mock_migrate.call_count == 1


# ─────────────────────────────────────────────────────────────
# sqlite-vec
# ─────────────────────────────────────────────────────────────

def test_vec_documents_virtual_table_exists(tmp_db):
    """vec_documents must exist as a virtual table."""
    rows = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_documents'"
    ).fetchall()
    assert len(rows) == 1


def test_vec_documents_selectable(tmp_db):
    """SELECT on virtual table must not raise errors."""
    rows = tmp_db.execute("SELECT * FROM vec_documents LIMIT 1").fetchall()
    assert rows == []


# ─────────────────────────────────────────────────────────────
# Schema integrity
# ─────────────────────────────────────────────────────────────

def test_raw_documents_content_hash_unique(tmp_db):
    """content_hash UNIQUE: second insert with same hash must fail."""
    tmp_db.execute(
        "INSERT INTO raw_documents (url, content_hash) VALUES ('http://a.com', 'abc123')"
    )
    tmp_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO raw_documents (url, content_hash) VALUES ('http://b.com', 'abc123')"
        )


def test_raw_documents_url_unique(tmp_db):
    """url UNIQUE: second insert with same URL must fail."""
    tmp_db.execute(
        "INSERT INTO raw_documents (url, content_hash) VALUES ('http://dup.com', 'hash1')"
    )
    tmp_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO raw_documents (url, content_hash) VALUES ('http://dup.com', 'hash2')"
        )


def test_portfolios_name_unique(tmp_db):
    tmp_db.execute(
        "INSERT INTO portfolios (name, portfolio_type) VALUES ('agent', 'agent')"
    )
    tmp_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO portfolios (name, portfolio_type) VALUES ('agent', 'agent')"
        )


def test_event_documents_insert_or_ignore(tmp_db):
    """INSERT OR IGNORE on event_documents must not fail on duplicate."""
    tmp_db.execute(
        "INSERT INTO events (title, first_seen, last_seen) VALUES ('E', '2026-01-01', '2026-01-01')"
    )
    tmp_db.execute(
        "INSERT INTO raw_documents (url, content_hash) VALUES ('http://x.com', 'h1')"
    )
    tmp_db.commit()
    event_id = tmp_db.execute("SELECT id FROM events").fetchone()["id"]
    doc_id = tmp_db.execute("SELECT id FROM raw_documents").fetchone()["id"]

    tmp_db.execute(
        "INSERT OR IGNORE INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, doc_id),
    )
    tmp_db.execute(
        "INSERT OR IGNORE INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, doc_id),
    )
    tmp_db.commit()
    count = tmp_db.execute("SELECT COUNT(*) FROM event_documents").fetchone()[0]
    assert count == 1
