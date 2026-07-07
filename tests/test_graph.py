"""
Tests for semantic/graph.py: build_entity_links + compute_narrative_divergences.

All tests use in-memory SQLite (tmp_db fixture from conftest.py).
No model loading — vectors injected directly as raw bytes.
"""

import hashlib
import struct
import sqlite3

import numpy as np
import pytest

from pathosphere.semantic.graph import (
    GraphResult,
    DivergenceResult,
    build_entity_links,
    compute_narrative_divergences,
    deserialize,
)
from pathosphere.semantic.embedder import EMBED_DIM

DIM = EMBED_DIM


# ─── helpers ─────────────────────────────────────────────────────────────────

def _unit_vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _blob(vec: np.ndarray) -> bytes:
    return struct.pack(f"{DIM}f", *[float(x) for x in vec])


def _insert_source(
    conn: sqlite3.Connection, *, name: str, block: str = "western", country: str = "GB"
) -> int:
    conn.execute(
        "INSERT INTO sources (name, url, country, geopolitical_block) VALUES (?, ?, ?, ?)",
        (name, f"http://{name}.com", country, block),
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()["id"]


def _insert_doc(
    conn: sqlite3.Connection,
    *,
    url: str,
    source_id: int | None = None,
    embedded: int = 1,
    is_duplicate: int = 0,
) -> int:
    h = hashlib.sha256(url.encode()).hexdigest()
    conn.execute(
        "INSERT INTO raw_documents (url, title, content_hash, embedded, is_duplicate, source_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (url, url, h, embedded, is_duplicate, source_id),
    )
    conn.commit()
    return conn.execute("SELECT id FROM raw_documents WHERE url = ?", (url,)).fetchone()["id"]


def _insert_vec(conn: sqlite3.Connection, doc_id: int, vec: np.ndarray) -> None:
    conn.execute(
        "INSERT INTO vec_documents (document_id, embedding) VALUES (?, ?)",
        (doc_id, _blob(vec)),
    )
    conn.commit()


def _insert_entity(conn: sqlite3.Connection, name: str = "Entity A") -> int:
    conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)", (name, "country")
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM entities WHERE name = ?", (name,)
    ).fetchone()["id"]


def _insert_event(conn: sqlite3.Connection, title: str = "Event") -> int:
    conn.execute(
        "INSERT INTO events (title, first_seen, last_seen) VALUES (?, '2026-06-01', '2026-06-01')",
        (title,),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM events WHERE title = ?", (title,)
    ).fetchone()["id"]


def _assign_doc_to_event(conn: sqlite3.Connection, event_id: int, doc_id: int) -> None:
    conn.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, doc_id),
    )
    conn.commit()


def _assign_entity_to_doc(
    conn: sqlite3.Connection, doc_id: int, entity_id: int, mentions: int = 1
) -> None:
    conn.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, ?)",
        (doc_id, entity_id, mentions),
    )
    conn.commit()


# ─── build_entity_links ───────────────────────────────────────────────────────

def test_links_empty_db(tmp_db):
    result = build_entity_links(tmp_db)
    assert isinstance(result, GraphResult)
    assert result.pairs_evaluated == 0
    assert result.links_written == 0
    assert result.links_deleted == 0


def test_links_basic_cooccurrence(tmp_db):
    doc = _insert_doc(tmp_db, url="http://a.com")
    ea = _insert_entity(tmp_db, "EntityA")
    eb = _insert_entity(tmp_db, "EntityB")
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc)
    _assign_entity_to_doc(tmp_db, doc, ea)
    _assign_entity_to_doc(tmp_db, doc, eb)

    result = build_entity_links(tmp_db)

    assert result.pairs_evaluated == 1
    assert result.links_written == 1
    row = tmp_db.execute("SELECT * FROM entity_links").fetchone()
    assert row["relation_type"] == "co-occurs"
    assert 0.0 <= row["strength"] <= 1.0
    assert row["entity_a"] < row["entity_b"]  # canonical ordering enforced by SQL WHERE


def test_links_strength_scales_with_count(tmp_db):
    ea = _insert_entity(tmp_db, "EntityA")
    eb = _insert_entity(tmp_db, "EntityB")
    for i in range(5):
        doc = _insert_doc(tmp_db, url=f"http://doc{i}.com")
        ev = _insert_event(tmp_db, title=f"Event {i}")
        _assign_doc_to_event(tmp_db, ev, doc)
        _assign_entity_to_doc(tmp_db, doc, ea)
        _assign_entity_to_doc(tmp_db, doc, eb)

    build_entity_links(tmp_db)

    row = tmp_db.execute("SELECT strength FROM entity_links").fetchone()
    assert abs(row["strength"] - 0.5) < 1e-6  # min(1.0, 5/10.0)


def test_links_min_cooccurrences_filter(tmp_db):
    doc = _insert_doc(tmp_db, url="http://a.com")
    ea = _insert_entity(tmp_db, "EntityA")
    eb = _insert_entity(tmp_db, "EntityB")
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc)
    _assign_entity_to_doc(tmp_db, doc, ea)
    _assign_entity_to_doc(tmp_db, doc, eb)

    result = build_entity_links(tmp_db, min_cooccurrences=2)

    assert result.links_written == 0
    count = tmp_db.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0]
    assert count == 0


def test_links_idempotency(tmp_db):
    doc = _insert_doc(tmp_db, url="http://a.com")
    ea = _insert_entity(tmp_db, "EntityA")
    eb = _insert_entity(tmp_db, "EntityB")
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc)
    _assign_entity_to_doc(tmp_db, doc, ea)
    _assign_entity_to_doc(tmp_db, doc, eb)

    build_entity_links(tmp_db)
    count_after_first = tmp_db.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0]

    result2 = build_entity_links(tmp_db)
    count_after_second = tmp_db.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0]

    assert count_after_first == count_after_second
    assert result2.links_deleted > 0


# ─── compute_narrative_divergences ───────────────────────────────────────────

def test_divergence_empty_db(tmp_db):
    result = compute_narrative_divergences(tmp_db)
    assert isinstance(result, DivergenceResult)
    assert result.events_processed == 0
    assert result.events_skipped == 0
    assert result.pairs_written == 0


def test_divergence_single_block_skipped(tmp_db):
    src = _insert_source(tmp_db, name="BBC", block="western")
    doc1 = _insert_doc(tmp_db, url="http://a.com", source_id=src)
    doc2 = _insert_doc(tmp_db, url="http://b.com", source_id=src)
    _insert_vec(tmp_db, doc1, _unit_vec(1))
    _insert_vec(tmp_db, doc2, _unit_vec(2))
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc1)
    _assign_doc_to_event(tmp_db, ev, doc2)

    result = compute_narrative_divergences(tmp_db)

    assert result.events_skipped == 1
    assert result.events_processed == 0
    assert result.pairs_written == 0
    count = tmp_db.execute("SELECT COUNT(*) FROM narrative_divergences").fetchone()[0]
    assert count == 0


def test_divergence_two_blocks_creates_row(tmp_db):
    s_west = _insert_source(tmp_db, name="BBC", block="western")
    s_china = _insert_source(tmp_db, name="CCTV", block="china")
    doc1 = _insert_doc(tmp_db, url="http://a.com", source_id=s_west)
    doc2 = _insert_doc(tmp_db, url="http://b.com", source_id=s_china)
    _insert_vec(tmp_db, doc1, _unit_vec(1))
    _insert_vec(tmp_db, doc2, _unit_vec(99))
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc1)
    _assign_doc_to_event(tmp_db, ev, doc2)

    result = compute_narrative_divergences(tmp_db)

    assert result.events_processed == 1
    assert result.pairs_written == 1
    row = tmp_db.execute("SELECT * FROM narrative_divergences").fetchone()
    assert row["event_id"] == ev
    assert row["block_a"] == "china"    # alphabetically first
    assert row["block_b"] == "western"
    assert 0.0 <= row["divergence_score"] <= 1.0
    assert row["summary"] is None


def test_divergence_score_range(tmp_db):
    # Seeds 1 and 99 produce near-orthogonal vectors → divergence close to 1.0
    v1 = _unit_vec(1)
    v99 = _unit_vec(99)
    expected_cos = float(np.dot(v1, v99))
    expected_div = max(0.0, min(1.0, 1.0 - expected_cos))

    s_west = _insert_source(tmp_db, name="Reuters", block="western")
    s_russia = _insert_source(tmp_db, name="TASS", block="russia")
    doc1 = _insert_doc(tmp_db, url="http://reuters.com/1", source_id=s_west)
    doc2 = _insert_doc(tmp_db, url="http://tass.com/1", source_id=s_russia)
    _insert_vec(tmp_db, doc1, v1)
    _insert_vec(tmp_db, doc2, v99)
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc1)
    _assign_doc_to_event(tmp_db, ev, doc2)

    compute_narrative_divergences(tmp_db)

    row = tmp_db.execute("SELECT divergence_score FROM narrative_divergences").fetchone()
    assert abs(row["divergence_score"] - expected_div) < 1e-5


def test_divergence_idempotency(tmp_db):
    s_west = _insert_source(tmp_db, name="BBC", block="western")
    s_china = _insert_source(tmp_db, name="CCTV", block="china")
    doc1 = _insert_doc(tmp_db, url="http://a.com", source_id=s_west)
    doc2 = _insert_doc(tmp_db, url="http://b.com", source_id=s_china)
    _insert_vec(tmp_db, doc1, _unit_vec(3))
    _insert_vec(tmp_db, doc2, _unit_vec(7))
    ev = _insert_event(tmp_db)
    _assign_doc_to_event(tmp_db, ev, doc1)
    _assign_doc_to_event(tmp_db, ev, doc2)

    compute_narrative_divergences(tmp_db)
    count_first = tmp_db.execute(
        "SELECT COUNT(*) FROM narrative_divergences"
    ).fetchone()[0]

    compute_narrative_divergences(tmp_db)
    count_second = tmp_db.execute(
        "SELECT COUNT(*) FROM narrative_divergences"
    ).fetchone()[0]

    assert count_first == count_second == 1


def test_entity_links_collapses_canonical_aliases(tmp_db):
    """Entities marked as aliases (canonical_entity_id) should be collapsed
    in the co-occurrence graph, so Trump/Donald Trump→same node."""
    # Canonical Trump
    trump_canonical = _insert_entity(tmp_db, "Donald Trump")
    # Alias variants
    trump_variant1 = _insert_entity(tmp_db, "Donald J. Trump")
    trump_variant2 = _insert_entity(tmp_db, "Trump")
    # Unrelated
    clinton = _insert_entity(tmp_db, "Hillary Clinton")

    # Mark variants as aliases
    tmp_db.execute(
        "UPDATE entities SET canonical_entity_id = ? WHERE id = ?",
        (trump_canonical, trump_variant1),
    )
    tmp_db.execute(
        "UPDATE entities SET canonical_entity_id = ? WHERE id = ?",
        (trump_canonical, trump_variant2),
    )
    tmp_db.commit()

    # Create event with both canonical Trump and variants, plus Clinton
    ev = _insert_event(tmp_db)
    doc = _insert_doc(tmp_db, url="http://ex.com/1")
    _assign_doc_to_event(tmp_db, ev, doc)

    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 2)",
        (doc, trump_canonical),
    )
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
        (doc, trump_variant1),
    )
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
        (doc, trump_variant2),
    )
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
        (doc, clinton),
    )
    tmp_db.commit()

    build_entity_links(tmp_db)

    # Should have exactly 1 link: canonical Trump ↔ Clinton
    links = tmp_db.execute(
        "SELECT entity_a, entity_b FROM entity_links WHERE relation_type = 'co-occurs'"
    ).fetchall()
    assert len(links) == 1
    # Link should involve canonical Trump (lowest ID if canonical < clinton)
    link = links[0]
    assert trump_canonical in (link["entity_a"], link["entity_b"])
    assert clinton in (link["entity_a"], link["entity_b"])
