"""Tests for pathosphere/agent/brief.py (3b).

All LLM calls are mocked — no real model is invoked.
DB tests use the tmp_db fixture (full schema with briefs table).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pathosphere.agent.brief import (
    BriefResult,
    _build_prompt,
    _query_divergences,
    _query_hub_entities,
    _query_recent_anomalies,
    _query_recent_events,
    _save_brief_db,
    _save_brief_file,
    generate_brief,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _insert_event(conn: sqlite3.Connection, *, title="Test event", last_seen=None,
                  event_type="conflict", origin="rss", severity=3,
                  location_name=None) -> int:
    if last_seen is None:
        last_seen = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO events (title, first_seen, last_seen, event_type, origin, severity, location_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, last_seen, last_seen, event_type, origin, severity, location_name),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_divergence(conn: sqlite3.Connection, event_id: int, *,
                       block_a="western", block_b="china", score=0.7,
                       summary=None) -> None:
    conn.execute(
        "INSERT INTO narrative_divergences (event_id, block_a, block_b, divergence_score, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, block_a, block_b, score, summary),
    )
    conn.commit()


def _insert_entity(conn: sqlite3.Connection, name: str, entity_type="country") -> int:
    conn.execute(
        "INSERT OR IGNORE INTO entities (name, entity_type) VALUES (?, ?)",
        (name, entity_type),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = ?", (name, entity_type)
    ).fetchone()[0]


def _insert_link(conn: sqlite3.Connection, a: int, b: int, relation="ally") -> None:
    conn.execute(
        "INSERT INTO entity_links (entity_a, entity_b, relation_type) VALUES (?, ?, ?)",
        (a, b, relation),
    )
    conn.commit()


def _insert_doc(conn: sqlite3.Connection, url: str) -> int:
    conn.execute("INSERT INTO raw_documents (url, origin) VALUES (?, 'rss')", (url,))
    conn.commit()
    return conn.execute("SELECT id FROM raw_documents WHERE url = ?", (url,)).fetchone()[0]


def _link_event_doc(conn: sqlite3.Connection, event_id: int, document_id: int) -> None:
    conn.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, document_id),
    )
    conn.commit()


# ─── _query_divergences ───────────────────────────────────────────────────────

def test_query_divergences_empty(tmp_db):
    result = _query_divergences(tmp_db, lookback_days=7)
    assert result == []


def test_query_divergences_returns_above_threshold(tmp_db):
    eid = _insert_event(tmp_db)
    _insert_divergence(tmp_db, eid, score=0.75)

    result = _query_divergences(tmp_db, lookback_days=7)
    assert len(result) == 1
    assert result[0]["event_id"] == eid
    assert result[0]["divergence_score"] == pytest.approx(0.75)


def test_query_divergences_filters_below_threshold(tmp_db):
    eid = _insert_event(tmp_db)
    _insert_divergence(tmp_db, eid, score=0.4)  # below 0.5

    result = _query_divergences(tmp_db, lookback_days=7)
    assert result == []


def test_query_divergences_filters_at_threshold(tmp_db):
    eid = _insert_event(tmp_db)
    _insert_divergence(tmp_db, eid, score=0.5)  # exactly 0.5 = excluded (> not >=)

    result = _query_divergences(tmp_db, lookback_days=7)
    assert result == []


def test_query_divergences_filters_old_events(tmp_db):
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    eid = _insert_event(tmp_db, last_seen=old_date)
    _insert_divergence(tmp_db, eid, score=0.9)

    result = _query_divergences(tmp_db, lookback_days=7)
    assert result == []


def test_query_divergences_includes_summary(tmp_db):
    eid = _insert_event(tmp_db)
    _insert_divergence(tmp_db, eid, score=0.8, summary="Conflicting narratives on Taiwan.")

    result = _query_divergences(tmp_db, lookback_days=7)
    assert result[0]["divergence_summary"] == "Conflicting narratives on Taiwan."


def test_query_divergences_ordered_by_score_desc(tmp_db):
    eid1 = _insert_event(tmp_db, title="Low divergence event")
    eid2 = _insert_event(tmp_db, title="High divergence event")
    _insert_divergence(tmp_db, eid1, score=0.6)
    _insert_divergence(tmp_db, eid2, score=0.9)

    result = _query_divergences(tmp_db, lookback_days=7)
    assert len(result) == 2
    assert result[0]["divergence_score"] > result[1]["divergence_score"]


# ─── _query_hub_entities ──────────────────────────────────────────────────────

def test_query_hub_entities_empty(tmp_db):
    result = _query_hub_entities(tmp_db)
    assert result == []


def test_query_hub_entities_returns_degree(tmp_db):
    a = _insert_entity(tmp_db, "TSMC", "company")
    b = _insert_entity(tmp_db, "Taiwan", "country")
    c = _insert_entity(tmp_db, "ASML", "company")
    _insert_link(tmp_db, a, b)
    _insert_link(tmp_db, a, c)

    result = _query_hub_entities(tmp_db)
    assert len(result) >= 1
    top = result[0]
    assert top["name"] == "TSMC"
    assert top["degree"] == 2


def test_query_hub_entities_ordered_by_degree(tmp_db):
    a = _insert_entity(tmp_db, "HubA", "company")
    b = _insert_entity(tmp_db, "HubB", "company")
    c = _insert_entity(tmp_db, "Leaf", "company")
    # HubA appears in 3 links, HubB in 1
    _insert_link(tmp_db, a, b)
    _insert_link(tmp_db, a, c)
    _insert_link(tmp_db, b, c)

    result = _query_hub_entities(tmp_db)
    degrees = [r["degree"] for r in result]
    assert degrees == sorted(degrees, reverse=True)


# ─── _query_recent_anomalies ──────────────────────────────────────────────────

def test_query_recent_anomalies_empty(tmp_db):
    result = _query_recent_anomalies(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_anomalies_returns_sensor_origins(tmp_db):
    for origin in ("portwatch", "usgs", "firms", "ioda"):
        _insert_event(tmp_db, title=f"Anomaly from {origin}", origin=origin)

    result = _query_recent_anomalies(tmp_db, lookback_days=7)
    returned_origins = {r["origin"] for r in result}
    assert returned_origins == {"portwatch", "usgs", "firms", "ioda"}


def test_query_recent_anomalies_excludes_rss_origin(tmp_db):
    _insert_event(tmp_db, title="RSS article", origin="rss")

    result = _query_recent_anomalies(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_anomalies_filters_old_events(tmp_db):
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _insert_event(tmp_db, title="Old quake", origin="usgs", last_seen=old_date)

    result = _query_recent_anomalies(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_anomalies_ordered_by_severity(tmp_db):
    _insert_event(tmp_db, title="Low severity", origin="usgs", severity=1)
    _insert_event(tmp_db, title="High severity", origin="usgs", severity=5)

    result = _query_recent_anomalies(tmp_db, lookback_days=7)
    assert result[0]["severity"] >= result[-1]["severity"]


# ─── _query_recent_events (CP-025) ─────────────────────────────────────────────

def test_query_recent_events_empty(tmp_db):
    result = _query_recent_events(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_events_requires_at_least_one_document(tmp_db):
    """An RSS event with no linked documents (shouldn't happen via the real
    clustering pipeline, but the query is an INNER JOIN) is excluded."""
    _insert_event(tmp_db, title="Orphan event", origin="rss")
    result = _query_recent_events(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_events_returns_rss_with_docs(tmp_db):
    eid = _insert_event(tmp_db, title="Bulgaria coalition statement", origin="rss")
    doc = _insert_doc(tmp_db, "https://example.com/a")
    _link_event_doc(tmp_db, eid, doc)

    result = _query_recent_events(tmp_db, lookback_days=7)
    assert len(result) == 1
    assert result[0]["event_id"] == eid
    assert result[0]["doc_count"] == 1


def test_query_recent_events_excludes_non_rss_origin(tmp_db):
    eid = _insert_event(tmp_db, title="Earthquake", origin="usgs")
    doc = _insert_doc(tmp_db, "https://example.com/b")
    _link_event_doc(tmp_db, eid, doc)

    result = _query_recent_events(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_events_filters_old_events(tmp_db):
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    eid = _insert_event(tmp_db, title="Old story", origin="rss", last_seen=old_date)
    doc = _insert_doc(tmp_db, "https://example.com/c")
    _link_event_doc(tmp_db, eid, doc)

    result = _query_recent_events(tmp_db, lookback_days=7)
    assert result == []


def test_query_recent_events_ordered_by_doc_count_desc(tmp_db):
    e_small = _insert_event(tmp_db, title="Minor story", origin="rss")
    e_big = _insert_event(tmp_db, title="Major story", origin="rss")
    _link_event_doc(tmp_db, e_small, _insert_doc(tmp_db, "https://example.com/d1"))
    _link_event_doc(tmp_db, e_big, _insert_doc(tmp_db, "https://example.com/d2"))
    _link_event_doc(tmp_db, e_big, _insert_doc(tmp_db, "https://example.com/d3"))

    result = _query_recent_events(tmp_db, lookback_days=7)
    assert result[0]["event_id"] == e_big
    assert result[0]["doc_count"] == 2
    assert result[1]["doc_count"] == 1


def test_query_recent_events_independent_of_divergence(tmp_db):
    """The whole point of CP-025: an event with zero narrative divergence
    (the common case) still surfaces here."""
    eid = _insert_event(tmp_db, title="Undisputed story", origin="rss")
    _link_event_doc(tmp_db, eid, _insert_doc(tmp_db, "https://example.com/e"))

    assert _query_divergences(tmp_db, lookback_days=7) == []
    result = _query_recent_events(tmp_db, lookback_days=7)
    assert len(result) == 1


# ─── _build_prompt ────────────────────────────────────────────────────────────

def test_build_prompt_returns_two_messages():
    messages = _build_prompt([], [], [], [], "2026-06-22")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_prompt_includes_date():
    messages = _build_prompt([], [], [], [], "2026-06-22")
    assert "2026-06-22" in messages[1]["content"]


def test_build_prompt_empty_data_notes_absence():
    messages = _build_prompt([], [], [], [], "2026-06-22")
    user_content = messages[1]["content"]
    assert "No significant signals" in user_content


def test_build_prompt_with_recent_events():
    events = [{
        "event_id": 42,
        "title": "Bulgaria says no place in Ukraine coalition of willing",
        "event_type": None,
        "location_name": "Bulgaria",
        "last_seen": "2026-07-14T16:10:56+00:00",
        "doc_count": 3,
    }]
    messages = _build_prompt([], [], [], events, "2026-06-22")
    content = messages[1]["content"]
    assert "RECENT EVENTS" in content
    assert "Bulgaria says no place in Ukraine coalition of willing" in content
    assert "sources=3" in content


def test_build_prompt_recent_events_alone_avoids_absence_note():
    """CP-025: recent_events is the fallback signal — its presence alone
    should suppress the 'no significant signals' placeholder."""
    events = [{
        "event_id": 1, "title": "Some story", "event_type": None,
        "location_name": None, "last_seen": "2026-06-20", "doc_count": 1,
    }]
    messages = _build_prompt([], [], [], events, "2026-06-22")
    assert "No significant signals" not in messages[1]["content"]


def test_build_prompt_with_divergence():
    divs = [{
        "event_id": 1,
        "title": "Taiwan tensions",
        "event_type": "conflict",
        "location_name": "Taiwan",
        "last_seen": "2026-06-20",
        "block_a": "western",
        "block_b": "china",
        "divergence_score": 0.82,
        "divergence_summary": "Major narrative gap",
    }]
    messages = _build_prompt(divs, [], [], [], "2026-06-22")
    content = messages[1]["content"]
    assert "Taiwan tensions" in content
    assert "0.82" in content
    assert "western" in content
    assert "china" in content


def test_build_prompt_with_hub_entities():
    hubs = [{"id": 1, "name": "TSMC", "entity_type": "company", "canonical_name": None, "degree": 10}]
    messages = _build_prompt([], hubs, [], [], "2026-06-22")
    assert "TSMC" in messages[1]["content"]
    assert "degree=10" in messages[1]["content"]


def test_build_prompt_canonical_name_preferred():
    hubs = [{"id": 1, "name": "TSMC", "entity_type": "company",
              "canonical_name": "Taiwan Semiconductor Manufacturing Company", "degree": 5}]
    messages = _build_prompt([], hubs, [], [], "2026-06-22")
    assert "Taiwan Semiconductor Manufacturing Company" in messages[1]["content"]


def test_build_prompt_with_anomalies():
    anoms = [{
        "id": 1,
        "title": "Suez traffic drop",
        "event_type": "infrastructure",
        "origin": "portwatch",
        "severity": 4,
        "location_name": "Suez Canal",
        "last_seen": "2026-06-21",
        "summary": "20% drop in transits",
    }]
    messages = _build_prompt([], [], anoms, [], "2026-06-22")
    content = messages[1]["content"]
    assert "PORTWATCH" in content
    assert "Suez traffic drop" in content
    assert "20% drop" in content


# ─── _save_brief_db ───────────────────────────────────────────────────────────

def test_save_brief_db_inserts_row(tmp_db):
    brief_id = _save_brief_db(tmp_db, "2026-06-22", "# Brief content", 5, 3)
    row = tmp_db.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    assert row is not None
    assert row["date"] == "2026-06-22"
    assert row["content"] == "# Brief content"
    assert row["event_count"] == 5
    assert row["entity_count"] == 3


def test_save_brief_db_upserts_on_same_date(tmp_db):
    _save_brief_db(tmp_db, "2026-06-22", "First version", 1, 1)
    brief_id2 = _save_brief_db(tmp_db, "2026-06-22", "Updated version", 2, 2)

    count = tmp_db.execute("SELECT COUNT(*) FROM briefs WHERE date = '2026-06-22'").fetchone()[0]
    assert count == 1

    row = tmp_db.execute("SELECT content, event_count FROM briefs WHERE id = ?", (brief_id2,)).fetchone()
    assert row["content"] == "Updated version"
    assert row["event_count"] == 2


# ─── _save_brief_file ─────────────────────────────────────────────────────────

def test_save_brief_file_creates_file(tmp_path):
    briefs_dir = tmp_path / "briefs"
    path = _save_brief_file("# Hello world", "2026-06-22", briefs_dir)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Hello world"
    assert path.name == "2026-06-22.md"


def test_save_brief_file_creates_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "briefs"
    _save_brief_file("content", "2026-06-22", nested)
    assert nested.exists()


# ─── generate_brief (full pipeline) ─────────────────────────────────────────

def _make_mock_llm(response: str = "# Mock brief") -> MagicMock:
    mock = MagicMock()
    mock.complete = AsyncMock(return_value=response)
    return mock


def test_generate_brief_returns_result(tmp_db, tmp_path):
    llm = _make_mock_llm("# Brief for today")
    result = asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )

    assert isinstance(result, BriefResult)
    assert result.date == "2026-06-22"
    assert result.content == "# Brief for today"
    assert result.brief_id > 0
    assert result.file_path.exists()


def test_generate_brief_calls_llm(tmp_db, tmp_path):
    llm = _make_mock_llm()
    asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )
    llm.complete.assert_called_once()


def test_generate_brief_default_date(tmp_db, tmp_path):
    llm = _make_mock_llm()
    result = asyncio.run(
        generate_brief(tmp_db, llm, briefs_dir=tmp_path / "briefs")
    )
    assert result.date == date.today().isoformat()


def test_generate_brief_persists_to_db(tmp_db, tmp_path):
    llm = _make_mock_llm("Persisted content")
    asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )
    row = tmp_db.execute("SELECT content FROM briefs WHERE date = '2026-06-22'").fetchone()
    assert row is not None
    assert row["content"] == "Persisted content"


def test_generate_brief_with_data(tmp_db, tmp_path):
    """Verify signal counts flow through correctly when DB has data."""
    eid = _insert_event(tmp_db, title="Conflict", origin="portwatch", severity=4)
    eid2 = _insert_event(tmp_db, title="Narrative event")
    _insert_divergence(tmp_db, eid2, score=0.8)
    a = _insert_entity(tmp_db, "TSMC", "company")
    b = _insert_entity(tmp_db, "Taiwan", "country")
    _insert_link(tmp_db, a, b)

    llm = _make_mock_llm("# Brief with data")
    result = asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )

    assert result.event_count >= 1   # at least the portwatch anomaly + divergence event
    assert result.entity_count >= 1  # TSMC + Taiwan as hub entities


def test_generate_brief_event_count_dedups_overlap(tmp_db, tmp_path):
    """An RSS event with both a high divergence score AND enough source
    coverage to land in recent_events must be counted once, not twice."""
    eid = _insert_event(tmp_db, title="Overlapping event", origin="rss")
    _insert_divergence(tmp_db, eid, score=0.8)
    _link_event_doc(tmp_db, eid, _insert_doc(tmp_db, "https://example.com/overlap"))

    llm = _make_mock_llm("# Brief")
    result = asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )

    assert result.event_count == 1  # not 2, despite appearing in both lists


def test_generate_brief_idempotent(tmp_db, tmp_path):
    """Running twice on the same date updates rather than inserts a duplicate."""
    llm = _make_mock_llm("Version 1")
    asyncio.run(
        generate_brief(tmp_db, llm, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )
    llm2 = _make_mock_llm("Version 2")
    asyncio.run(
        generate_brief(tmp_db, llm2, brief_date="2026-06-22", briefs_dir=tmp_path / "briefs")
    )

    count = tmp_db.execute("SELECT COUNT(*) FROM briefs WHERE date = '2026-06-22'").fetchone()[0]
    assert count == 1
    row = tmp_db.execute("SELECT content FROM briefs WHERE date = '2026-06-22'").fetchone()
    assert row["content"] == "Version 2"


# ─── agent __init__ export ────────────────────────────────────────────────────

def test_agent_init_exports():
    from pathosphere.agent import BriefResult as _BR, generate_brief as _gb
    assert _BR is BriefResult
    assert _gb is generate_brief
