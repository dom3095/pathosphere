"""
Tests for story linking (semantic/story.py): grouping complete-linkage
micro-events into a macro-story via shared canonical person entities +
a time-window bound.
"""

import hashlib
import sqlite3

from pathosphere.semantic.story import link_related_events


def _insert_doc(conn: sqlite3.Connection, *, url: str) -> int:
    h = hashlib.sha256(url.encode()).hexdigest()
    conn.execute(
        "INSERT INTO raw_documents (url, title, body, content_hash, embedded) "
        "VALUES (?, 'Title', 'Body', ?, 1)",
        (url, h),
    )
    conn.commit()
    return conn.execute("SELECT id FROM raw_documents WHERE url = ?", (url,)).fetchone()["id"]


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
    a 10-day window — must merge into one story."""
    person = _insert_person_entity(tmp_db, "Ali Khamenei")

    event_a = _insert_event(tmp_db, first_seen="2026-06-01T00:00:00")
    event_b = _insert_event(tmp_db, first_seen="2026-06-04T00:00:00")

    doc_a = _insert_doc(tmp_db, url="https://x.com/a")
    doc_b = _insert_doc(tmp_db, url="https://x.com/b")
    _link_doc_to_event(tmp_db, doc_a, event_a)
    _link_doc_to_event(tmp_db, doc_b, event_b)
    _mention(tmp_db, doc_a, person)
    _mention(tmp_db, doc_b, person)

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


def test_does_not_link_events_outside_window(tmp_db):
    """Same person mentioned 30 days apart, window=10 — must NOT merge."""
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

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    assert result.events_linked == 1


def test_prevents_chain_collapse_across_time(tmp_db):
    """A-B share person X (gap 5d), B-C share person Y (gap 4d), window=8d.
    Merging (B,C) first (tightest pair) is safe (span 4d). Then merging the
    resulting {B,C} group with A would make total span 9d > window — must be
    REJECTED, even though each individual pairwise gap looked mergeable.
    This is the time-domain analogue of the embedding bridging-doc bug."""
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

    result = link_related_events(tmp_db, time_window_days=10)

    assert result.stories_formed == 1
    row = tmp_db.execute(
        "SELECT story_id FROM events WHERE id = ?", (event_small,)
    ).fetchone()
    assert row["story_id"] == event_big  # event_big has 2 docs vs 1
