"""
Tests for story linking (semantic/story.py): grouping complete-linkage
micro-events into a macro-story via shared canonical person entities +
a time-window bound + an embedding-similarity floor.
"""

import hashlib
import math
import sqlite3
import struct

import numpy as np

from pathosphere.semantic.story import link_related_events

DIM = 384


def _insert_doc(conn: sqlite3.Connection, *, url: str) -> int:
    h = hashlib.sha256(url.encode()).hexdigest()
    conn.execute(
        "INSERT INTO raw_documents (url, title, body, content_hash, embedded) "
        "VALUES (?, 'Title', 'Body', ?, 1)",
        (url, h),
    )
    conn.commit()
    return conn.execute("SELECT id FROM raw_documents WHERE url = ?", (url,)).fetchone()["id"]


def _insert_embedding(conn: sqlite3.Connection, doc_id: int, vec: list[float]) -> None:
    blob = struct.pack(f"{DIM}f", *[float(x) for x in vec])
    conn.execute(
        "INSERT INTO vec_documents(document_id, embedding) VALUES (?, ?)", (doc_id, blob)
    )
    conn.commit()


def _unit_vec(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _insert_event(conn: sqlite3.Connection, *, first_seen: str, last_seen: str | None = None) -> int:
    conn.execute(
        "INSERT INTO events (title, first_seen, last_seen) VALUES ('Event', ?, ?)",
        (first_seen, last_seen or first_seen),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]


def _link_doc_to_event(conn: sqlite3.Connection, doc_id: int, event_id: int) -> None:
    conn.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)", (event_id, doc_id)
    )
    conn.commit()


def _insert_person_entity(conn: sqlite3.Connection, name: str, canonical_entity_id: int | None = None) -> int:
    conn.execute(
        "INSERT INTO entities (name, entity_type, canonical_entity_id) VALUES (?, 'person', ?)",
        (name, canonical_entity_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = 'person'", (name,)
    ).fetchone()["id"]


def _mention(conn: sqlite3.Connection, doc_id: int, entity_id: int) -> None:
    conn.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
        (doc_id, entity_id),
    )
    conn.commit()


def test_link_empty_db(tmp_db):
    result = link_related_events(tmp_db)
    assert result.stories_formed == 0
    assert result.events_linked == 0


def test_links_events_sharing_person_within_window(tmp_db):
    """Two micro-events mentioning the same person, 3 days apart, well within
    a 10-day window, with similar embeddings — must merge into one story."""
    person = _insert_person_entity(tmp_db, "Ali Khamenei")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-06-04T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)
    vec = _unit_vec(1)
    _insert_embedding(tmp_db, doc_a, vec)
    _insert_embedding(tmp_db, doc_b, vec)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    assert result.events_linked == 1
    story_ids = {
        r["id"]: r["story_id"]
        for r in tmp_db.execute("SELECT id, story_id FROM events").fetchall()
    }
    # Both events resolve (via COALESCE) to the same canonical event
    resolved_a = story_ids[event_a] or event_a
    resolved_b = story_ids[event_b] or event_b
    assert resolved_a == resolved_b


def test_handles_mixed_naive_and_aware_timestamps(tmp_db):
    """Production data has a mix of naive ("2026-06-01T00:00:00") and
    timezone-aware ("2026-06-04T00:00:00+00:00") first_seen values depending
    on ingest path — comparing them must not raise."""
    person = _insert_person_entity(tmp_db, "Ali Khamenei")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")  # naive
    event_b = _insert_event(tmp_db, first_seen="2026-06-04T00:00:00+00:00")  # aware

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)
    vec = _unit_vec(2)
    _insert_embedding(tmp_db, doc_a, vec)
    _insert_embedding(tmp_db, doc_b, vec)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    assert result.events_linked == 1


def test_does_not_link_events_outside_window(tmp_db):
    """Same person mentioned 30 days apart, window=10 — must NOT merge
    (rejected by the time check before embeddings are even considered)."""
    person = _insert_person_entity(tmp_db, "Ali Khamenei")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-07-01T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 0
    assert result.events_linked == 0


def test_rejects_merge_when_embeddings_dissimilar_despite_shared_entity(tmp_db):
    """The bug found on real data: a globally prominent person (e.g. a head
    of state) gets a passing one-line mention in genuinely unrelated stories
    within the same news cycle. Entity+time alone linked 244 unrelated
    events into one mega-story through such hub mentions. Dissimilar
    embeddings must block the merge even though entity+time both pass."""
    person = _insert_person_entity(tmp_db, "Trump")

    event_a = _insert_event(tmp_db, first_seen="2026-07-04T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-07-05T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")  # e.g. World Cup recap
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")  # e.g. NATO summit report
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)
    # Orthogonal vectors -> cosine similarity 0, well below the 0.82 floor
    v1 = [0.0] * DIM
    v1[0] = 1.0
    v2 = [0.0] * DIM
    v2[1] = 1.0
    _insert_embedding(tmp_db, doc_a, v1)
    _insert_embedding(tmp_db, doc_b, v2)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 0
    assert result.events_linked == 0


def test_group_vs_group_complete_linkage_not_just_trigger_pair(tmp_db):
    """A first fix attempt only checked the two TRIGGERING events' similarity
    (average-linkage's blind spot) and still produced a 206-event mega-story
    on real data. Same geometry as cluster.py's bridging-doc regression test:
    cos(B,A)=cos(B,C)=0.90 (each pair individually clears 0.82), but
    cos(A,C)=0.62 (fails). A-B merge via person X (passes). Then B-C
    considered via person Y: checking ONLY sim(B,C)=0.90 would wrongly merge
    C into the {A,B} group. True complete-linkage checks {A,B} docs against
    C's docs — min(sim(A,C), sim(B,C)) = 0.62 — correctly rejects it."""
    person_x = _insert_person_entity(tmp_db, "Person X")
    person_y = _insert_person_entity(tmp_db, "Person Y")

    # B-C gap (12h) deliberately smaller than A-B gap (24h) so pairs process
    # in a fixed, deterministic order (tightest gap first): (B,C) merges
    # first, then (A,B) is checked against the {B,C} group as a whole.
    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-06-02T00:00:00")
    event_c = _insert_event(tmp_db, first_seen="2026-06-02T12:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    doc_c = _insert_doc(tmp_db, url="https://x.com/c")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _link_doc_to_event(tmp_db, doc_c, event_c)

    _mention(tmp_db, doc_a, person_x)
    _mention(tmp_db, doc_b, person_x)  # A-B linkable via person X
    _mention(tmp_db, doc_b, person_y)
    _mention(tmp_db, doc_c, person_y)  # B-C linkable via person Y

    vec_a = [0.0] * DIM
    vec_a[0] = 1.0
    vec_c = [0.0] * DIM
    vec_c[0] = 0.62
    vec_c[1] = 0.7846  # cos(A,C) = 0.62 < 0.82 threshold
    vec_b = [0.0] * DIM
    vec_b[0] = 0.90
    vec_b[1] = 0.4359  # cos(B,A) = cos(B,C) = 0.90 >= 0.82 threshold
    _insert_embedding(tmp_db, doc_a, vec_a)
    _insert_embedding(tmp_db, doc_b, vec_b)
    _insert_embedding(tmp_db, doc_c, vec_c)

    result = link_related_events(tmp_db, time_window_days=10)

    story_ids = {
        r["id"]: r["story_id"]
        for r in tmp_db.execute("SELECT id, story_id FROM events").fetchall()
    }
    resolved = {eid: (story_ids[eid] or eid) for eid in (event_a, event_b, event_c)}

    # B-C merge first (tightest gap, direct pair passes: sim=0.90)
    assert resolved[event_b] == resolved[event_c]
    # A stays separate: {B,C} group vs A uses min(sim(A,B), sim(A,C)) = 0.62 — fails
    assert resolved[event_a] != resolved[event_b]


def test_resolves_canonical_entity_id_before_matching(tmp_db):
    """'Khamenei' (bare) canonicalized to 'Ali Khamenei' (canonical_entity_id
    set) must still count as the same person for linking purposes."""
    canonical = _insert_person_entity(tmp_db, "Ali Khamenei")
    variant = _insert_person_entity(tmp_db, "Khamenei", canonical_entity_id=canonical)

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-06-03T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, canonical)  # doc_a mentions the canonical form
    _mention(tmp_db, doc_b, variant)    # doc_b mentions the bare variant
    vec = _unit_vec(3)
    _insert_embedding(tmp_db, doc_a, vec)
    _insert_embedding(tmp_db, doc_b, vec)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    assert result.events_linked == 1


def test_prevents_chain_collapse_across_time(tmp_db):
    """A-B share person X (gap 5d), B-C share person Y (gap 4d), window=8d.
    Merging (B,C) first (tightest pair, similar embeddings) is safe (span
    4d). Then merging the resulting {B,C} group with A would make total
    span 9d > window — must be REJECTED by the time check alone, regardless
    of A's embedding. This is the time-domain analogue of the embedding
    bridging-doc bug."""
    person_x = _insert_person_entity(tmp_db, "Person X")
    person_y = _insert_person_entity(tmp_db, "Person Y")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")  # day 0
    event_b = _insert_event(tmp_db, first_seen="2026-06-06T00:00:00")  # day 5
    event_c = _insert_event(tmp_db, first_seen="2026-06-10T00:00:00")  # day 9

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    doc_c = _insert_doc(tmp_db, url="https://x.com/c")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _link_doc_to_event(tmp_db, doc_c, event_c)

    _mention(tmp_db, doc_a, person_x)
    _mention(tmp_db, doc_b, person_x)  # A-B linked via person X (gap 5d)
    _mention(tmp_db, doc_b, person_y)
    _mention(tmp_db, doc_c, person_y)  # B-C linked via person Y (gap 4d)

    # B and C get matching (similar) embeddings so their pair clears the
    # embedding gate; A gets an unrelated embedding — irrelevant here since
    # the A-vs-{B,C} merge is rejected by the time check first regardless.
    vec_bc = _unit_vec(4)
    _insert_embedding(tmp_db, doc_b, vec_bc)
    _insert_embedding(tmp_db, doc_c, vec_bc)
    _insert_embedding(tmp_db, doc_a, _unit_vec(5))

    result = link_related_events(tmp_db, time_window_days=8)

    story_ids = {
        r["id"]: r["story_id"]
        for r in tmp_db.execute("SELECT id, story_id FROM events").fetchall()
    }
    resolved = {eid: (story_ids[eid] or eid) for eid in (event_a, event_b, event_c)}

    # B and C merge (span 4d <= 8d window)
    assert resolved[event_b] == resolved[event_c]
    # A stays separate — merging it with {B,C} would span 9d > 8d window
    assert resolved[event_a] != resolved[event_b]


def test_ties_on_time_gap_prefer_higher_similarity_pair(tmp_db):
    """CP-021: with a hub-ish person mentioned in many overlapping-time
    events, thousands of candidate pairs can tie on gap=0 — with no
    secondary sort key, which pair gets first crack at a group is arbitrary
    Python set-iteration order, and a weaker-but-still-passing match
    processed first can consume an event before its genuinely stronger match
    gets a turn. A, B, C all mention the same person with fully overlapping
    (gap=0) windows: sim(A,B)=0.90 (the real story), sim(A,C)=0.85 (weaker
    but still clears 0.82 in isolation), sim(B,C)=0.53 (unrelated). The
    strongest pair (A,B) must win regardless of tie order — verified here by
    asserting the outcome directly, not by relying on hash order."""
    person = _insert_person_entity(tmp_db, "Hub Person")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_c = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    doc_c = _insert_doc(tmp_db, url="https://x.com/c")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _link_doc_to_event(tmp_db, doc_c, event_c)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)
    _mention(tmp_db, doc_c, person)

    def vec_at(angle_deg: float) -> list[float]:
        v = [0.0] * DIM
        v[0] = math.cos(math.radians(angle_deg))
        v[1] = math.sin(math.radians(angle_deg))
        return v

    vec_a = vec_at(0.0)
    vec_b = vec_at(math.degrees(math.acos(0.90)))    # sim(A,B) = 0.90
    vec_c = vec_at(-math.degrees(math.acos(0.85)))   # sim(A,C) = 0.85, sim(B,C) ~= 0.53
    _insert_embedding(tmp_db, doc_a, vec_a)
    _insert_embedding(tmp_db, doc_b, vec_b)
    _insert_embedding(tmp_db, doc_c, vec_c)

    result = link_related_events(tmp_db, time_window_days=10)

    story_ids = {
        r["id"]: r["story_id"]
        for r in tmp_db.execute("SELECT id, story_id FROM events").fetchall()
    }
    resolved = {eid: (story_ids[eid] or eid) for eid in (event_a, event_b, event_c)}

    assert result.stories_formed == 1
    assert resolved[event_a] == resolved[event_b]
    assert resolved[event_c] != resolved[event_a]


def test_canonical_is_most_documents_tie_break_earliest(tmp_db):
    """When merging, the event with the most docs wins as canonical; ties
    break to the earlier first_seen."""
    person = _insert_person_entity(tmp_db, "Ali Khamenei")

    event_small = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_big = _insert_event(tmp_db, first_seen="2026-06-03T00:00:00")

    doc_1 = _insert_doc(tmp_db, url="https://x.com/1")
    doc_2 = _insert_doc(tmp_db, url="https://x.com/2")
    doc_3 = _insert_doc(tmp_db, url="https://x.com/3")
    _link_doc_to_event(tmp_db, doc_1, event_small)
    _link_doc_to_event(tmp_db, doc_2, event_big)
    _link_doc_to_event(tmp_db, doc_3, event_big)
    _mention(tmp_db, doc_1, person)
    _mention(tmp_db, doc_2, person)
    vec = _unit_vec(6)
    _insert_embedding(tmp_db, doc_1, vec)
    _insert_embedding(tmp_db, doc_2, vec)
    _insert_embedding(tmp_db, doc_3, vec)

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    row = tmp_db.execute(
        "SELECT story_id FROM events WHERE id = ?", (event_small,)
    ).fetchone()
    assert row["story_id"] == event_big  # event_big has 2 docs vs 1
