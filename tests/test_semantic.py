"""
Tests for Phase 2 semantic pipeline: embed, dedup, cluster.

All tests use a MockModel that returns deterministic unit vectors — no
sentence-transformers download required at test time.
"""

import hashlib
import struct
import sqlite3

import numpy as np
import pytest

from pathosphere.semantic.embedder import EmbedResult, embed_documents, serialize
from pathosphere.semantic.dedup import DedupResult, dedup_documents
from pathosphere.semantic.cluster import ClusterResult, cluster_documents


# ─── helpers ─────────────────────────────────────────────────────────────────

DIM = 384


class MockModel:
    """Returns unit vectors seeded by text content (deterministic, fast)."""

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> np.ndarray:
        vecs = np.array(
            [self._vec(t) for t in texts], dtype=np.float32
        )
        return vecs

    def _vec(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) % (2**31)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(DIM).astype(np.float32)
        return v / np.linalg.norm(v)


def _unit_vec(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def _blob(vec: list[float]) -> bytes:
    return struct.pack(f"{DIM}f", *[float(x) for x in vec])


def _insert_doc(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str = "Title",
    body: str = "Body",
    published_at: str = "2026-06-01T00:00:00",
    embedded: int = 0,
) -> int:
    h = hashlib.sha256((url + body).encode()).hexdigest()
    conn.execute(
        "INSERT INTO raw_documents (url, title, body, published_at, content_hash, embedded) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (url, title, body, published_at, h, embedded),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM raw_documents WHERE url = ?", (url,)
    ).fetchone()["id"]


def _insert_vec(conn: sqlite3.Connection, doc_id: int, vec: list[float]) -> None:
    conn.execute(
        "INSERT INTO vec_documents (document_id, embedding) VALUES (?, ?)",
        (doc_id, _blob(vec)),
    )
    conn.execute(
        "UPDATE raw_documents SET embedded = 1 WHERE id = ?", (doc_id,)
    )
    conn.commit()


# ─── embedder ────────────────────────────────────────────────────────────────

def test_embed_empty_db(tmp_db):
    result = embed_documents(tmp_db, model=MockModel())
    assert isinstance(result, EmbedResult)
    assert result.docs_processed == 0
    assert result.docs_skipped == 0
    assert result.errors == 0


def test_embed_processes_docs(tmp_db):
    _insert_doc(tmp_db, url="http://a.com", title="A", body="Body A")
    _insert_doc(tmp_db, url="http://b.com", title="B", body="Body B")

    result = embed_documents(tmp_db, model=MockModel())

    assert result.docs_processed == 2
    assert result.docs_skipped == 0
    assert result.errors == 0


def test_embed_inserts_to_vec_documents(tmp_db):
    _insert_doc(tmp_db, url="http://a.com", body="Some content")

    embed_documents(tmp_db, model=MockModel())

    row = tmp_db.execute("SELECT COUNT(*) FROM vec_documents").fetchone()[0]
    assert row == 1


def test_embed_marks_embedded_flag(tmp_db):
    _insert_doc(tmp_db, url="http://a.com")

    embed_documents(tmp_db, model=MockModel())

    row = tmp_db.execute(
        "SELECT embedded FROM raw_documents WHERE url = 'http://a.com'"
    ).fetchone()
    assert row["embedded"] == 1


def test_embed_skips_already_embedded(tmp_db):
    _insert_doc(tmp_db, url="http://a.com", embedded=1)

    result = embed_documents(tmp_db, model=MockModel())

    assert result.docs_processed == 0


def test_embed_skips_doc_without_text(tmp_db):
    tmp_db.execute(
        "INSERT INTO raw_documents (url, title, body, content_hash, embedded) "
        "VALUES ('http://empty.com', NULL, NULL, 'nohash', 0)"
    )
    tmp_db.commit()

    result = embed_documents(tmp_db, model=MockModel())

    assert result.docs_skipped == 1
    assert result.docs_processed == 0
    # Should still be marked embedded=1 so it's not re-processed
    row = tmp_db.execute(
        "SELECT embedded FROM raw_documents WHERE url = 'http://empty.com'"
    ).fetchone()
    assert row["embedded"] == 1


# ─── serialize ───────────────────────────────────────────────────────────────

def test_serialize_produces_correct_bytesize():
    vec = [0.0] * DIM
    blob = serialize(vec)
    assert len(blob) == DIM * 4  # 4 bytes per float32


# ─── dedup ───────────────────────────────────────────────────────────────────

def test_dedup_empty_db(tmp_db):
    result = dedup_documents(tmp_db)
    assert isinstance(result, DedupResult)
    assert result.docs_checked == 0
    assert result.duplicates_found == 0


def test_dedup_marks_duplicate(tmp_db):
    # Two docs with identical vectors → second is duplicate of first
    vec = _unit_vec(42)
    id1 = _insert_doc(tmp_db, url="http://orig.com", published_at="2026-06-01T00:00:00")
    id2 = _insert_doc(tmp_db, url="http://dup.com", published_at="2026-06-01T01:00:00")
    _insert_vec(tmp_db, id1, vec)
    _insert_vec(tmp_db, id2, vec)  # same vector → cosine distance = 0

    result = dedup_documents(tmp_db)

    assert result.duplicates_found == 1
    row = tmp_db.execute(
        "SELECT is_duplicate, duplicate_of FROM raw_documents WHERE id = ?", (id2,)
    ).fetchone()
    assert row["is_duplicate"] == 1
    assert row["duplicate_of"] == id1


def test_dedup_canonical_has_lower_id(tmp_db):
    vec = _unit_vec(7)
    id1 = _insert_doc(tmp_db, url="http://first.com", published_at="2026-06-01T00:00:00")
    id2 = _insert_doc(tmp_db, url="http://second.com", published_at="2026-06-01T02:00:00")
    _insert_vec(tmp_db, id1, vec)
    _insert_vec(tmp_db, id2, vec)

    dedup_documents(tmp_db)

    row1 = tmp_db.execute(
        "SELECT is_duplicate FROM raw_documents WHERE id = ?", (id1,)
    ).fetchone()
    row2 = tmp_db.execute(
        "SELECT is_duplicate FROM raw_documents WHERE id = ?", (id2,)
    ).fetchone()
    assert row1["is_duplicate"] == 0   # canonical — not a duplicate
    assert row2["is_duplicate"] == 1   # newer → marked duplicate


def test_dedup_no_dup_for_dissimilar_docs(tmp_db):
    # seed 1 and seed 99 produce orthogonal-ish vectors (cos_sim ≈ 0)
    id1 = _insert_doc(tmp_db, url="http://a.com", published_at="2026-06-01T00:00:00")
    id2 = _insert_doc(tmp_db, url="http://b.com", published_at="2026-06-01T01:00:00")
    _insert_vec(tmp_db, id1, _unit_vec(1))
    _insert_vec(tmp_db, id2, _unit_vec(99))

    result = dedup_documents(tmp_db)

    assert result.duplicates_found == 0
    for doc_id in (id1, id2):
        row = tmp_db.execute(
            "SELECT is_duplicate FROM raw_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert row["is_duplicate"] == 0


def test_dedup_sets_dedup_checked(tmp_db):
    vec = _unit_vec(5)
    doc_id = _insert_doc(tmp_db, url="http://x.com")
    _insert_vec(tmp_db, doc_id, vec)

    dedup_documents(tmp_db)

    row = tmp_db.execute(
        "SELECT dedup_checked FROM raw_documents WHERE id = ?", (doc_id,)
    ).fetchone()
    assert row["dedup_checked"] == 1


# ─── cluster ─────────────────────────────────────────────────────────────────

def test_cluster_empty_db(tmp_db):
    result = cluster_documents(tmp_db)
    assert isinstance(result, ClusterResult)
    assert result.events_created == 0
    assert result.docs_assigned == 0


def test_cluster_creates_event_for_similar_docs(tmp_db):
    vec = _unit_vec(42)
    id1 = _insert_doc(tmp_db, url="http://a.com", title="Taiwan tensions")
    id2 = _insert_doc(tmp_db, url="http://b.com", title="Taiwan tensions update")
    _insert_vec(tmp_db, id1, vec)
    _insert_vec(tmp_db, id2, vec)  # same vector → will be clustered together

    # Mark both as dedup_checked=1, is_duplicate=0
    tmp_db.execute(
        "UPDATE raw_documents SET dedup_checked = 1 WHERE id IN (?, ?)", (id1, id2)
    )
    tmp_db.commit()

    result = cluster_documents(tmp_db, time_window_hours=9999)

    assert result.events_created == 1
    assert result.docs_assigned == 2
    count = tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1


def test_cluster_separate_events_for_dissimilar(tmp_db):
    id1 = _insert_doc(tmp_db, url="http://a.com", title="Taiwan")
    id2 = _insert_doc(tmp_db, url="http://b.com", title="Oil price")
    _insert_vec(tmp_db, id1, _unit_vec(1))
    _insert_vec(tmp_db, id2, _unit_vec(99))

    tmp_db.execute(
        "UPDATE raw_documents SET dedup_checked = 1 WHERE id IN (?, ?)", (id1, id2)
    )
    tmp_db.commit()

    result = cluster_documents(tmp_db, time_window_hours=9999)

    assert result.events_created == 2
    assert result.docs_assigned == 2


def test_cluster_skips_already_in_event(tmp_db):
    vec = _unit_vec(3)
    doc_id = _insert_doc(tmp_db, url="http://a.com")
    _insert_vec(tmp_db, doc_id, vec)
    tmp_db.execute(
        "UPDATE raw_documents SET dedup_checked = 1 WHERE id = ?", (doc_id,)
    )
    # Pre-assign to an event
    tmp_db.execute(
        "INSERT INTO events (title, first_seen, last_seen) VALUES ('Existing', '2026-06-01', '2026-06-01')"
    )
    event_id = tmp_db.execute("SELECT id FROM events").fetchone()["id"]
    tmp_db.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, doc_id),
    )
    tmp_db.commit()

    result = cluster_documents(tmp_db, time_window_hours=9999)

    assert result.docs_assigned == 0
    # Still only 1 event (the pre-existing one)
    count = tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1
