"""
Tests for Phase 2 extraction: NER, geocoding, Wikidata linking.

NER uses a MockNer (no spaCy model load); network steps use mock httpx
transports — no real requests at test time.
"""

import hashlib
import json
import sqlite3

import httpx
import pytest

from pathosphere.semantic.extract import (
    backfill_demonym_entities,
    extract_entities,
    geocode_events,
    link_wikidata,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


class FakeEnt:
    def __init__(self, text: str, label: str):
        self.text = text
        self.label_ = label


class FakeDoc:
    def __init__(self, ents: list[FakeEnt]):
        self.ents = ents


class MockNer:
    """Maps exact text → list of (text, label). Default: empty."""

    def __init__(self, mapping: dict[str, list[tuple[str, str]]] | None = None):
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def __call__(self, text: str) -> FakeDoc:
        self.calls.append(text)
        for key, ents in self.mapping.items():
            if key in text:
                return FakeDoc([FakeEnt(t, l) for t, l in ents])
        return FakeDoc([])


def _insert_doc(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str = "Title",
    body: str = "Body",
    embedded: int = 1,
    is_duplicate: int = 0,
    origin: str | None = None,
) -> int:
    h = hashlib.sha256((url + body).encode()).hexdigest()
    cur = conn.execute(
        "INSERT INTO raw_documents (url, title, body, content_hash, embedded, is_duplicate, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (url, title, body, h, embedded, is_duplicate, origin),
    )
    conn.commit()
    return cur.lastrowid


def _insert_event(
    conn: sqlite3.Connection,
    *,
    title: str = "Event",
    location_name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO events (title, first_seen, last_seen, location_name, lat, lon) "
        "VALUES (?, '2026-06-01', '2026-06-01', ?, ?, ?)",
        (title, location_name, lat, lon),
    )
    conn.commit()
    return cur.lastrowid


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ─── extract_entities ────────────────────────────────────────────────────────


def test_ner_creates_entities_and_mentions(tmp_db):
    doc_id = _insert_doc(
        tmp_db, url="https://x.com/1", title="TSMC halts Huawei orders",
        body="TSMC confirmed the halt.",
    )
    ner = MockNer({"TSMC": [("TSMC", "ORG"), ("TSMC", "ORG"), ("Huawei", "ORG")]})

    result = extract_entities(tmp_db, model=ner)

    assert result.docs_processed == 1
    assert result.entities_created == 2
    assert result.mentions_recorded == 3

    row = tmp_db.execute(
        """SELECT de.mentions FROM document_entities de
           JOIN entities e ON e.id = de.entity_id
           WHERE e.name = 'TSMC' AND de.document_id = ?""",
        (doc_id,),
    ).fetchone()
    assert row["mentions"] == 2


def test_ner_label_mapping(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="mix", body="mix")
    ner = MockNer(
        {"mix": [("Xi Jinping", "PER"), ("Iran", "LOC"), ("Semiconductors", "MISC")]}
    )

    extract_entities(tmp_db, model=ner)

    types = {
        r["name"]: r["entity_type"]
        for r in tmp_db.execute("SELECT name, entity_type FROM entities")
    }
    assert types == {
        "Xi Jinping": "person",
        "Iran": "location",
        "Semiconductors": "other",
    }


def test_ner_demonym_reclassified_to_location(tmp_db):
    """spaCy tags 'Israeli' MISC (other) — demonym override reclassifies it
    to location with the country as canonical_name."""
    _insert_doc(tmp_db, url="https://x.com/1", title="x", body="x")
    ner = MockNer({"x": [("Israeli", "MISC"), ("Russian", "MISC")]})

    extract_entities(tmp_db, model=ner)

    rows = {
        r["name"]: (r["entity_type"], r["canonical_name"])
        for r in tmp_db.execute("SELECT name, entity_type, canonical_name FROM entities")
    }
    assert rows == {
        "Israeli": ("location", "Israel"),
        "Russian": ("location", "Russia"),
    }


def test_backfill_demonym_entities_reclassifies_existing(tmp_db):
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type) VALUES ('Israeli', 'other')"
    )
    tmp_db.commit()

    updated = backfill_demonym_entities(tmp_db)

    assert updated == 1
    row = tmp_db.execute(
        "SELECT entity_type, canonical_name FROM entities WHERE name = 'Israeli'"
    ).fetchone()
    assert row["entity_type"] == "location"
    assert row["canonical_name"] == "Israel"


def test_backfill_demonym_entities_is_idempotent(tmp_db):
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type) VALUES ('Russian', 'other')"
    )
    tmp_db.commit()

    first = backfill_demonym_entities(tmp_db)
    second = backfill_demonym_entities(tmp_db)

    assert first == 1
    assert second == 0


def test_backfill_demonym_entities_merges_into_existing_location(tmp_db):
    """If NER already created a fresh 'Israeli'/location (post-fix) alongside
    the legacy 'Israeli'/other, merge document_entities into the survivor."""
    doc_a = _insert_doc(tmp_db, url="https://x.com/1")
    doc_b = _insert_doc(tmp_db, url="https://x.com/2")

    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('Israeli', 'other')")
    old_id = tmp_db.execute(
        "SELECT id FROM entities WHERE name='Israeli' AND entity_type='other'"
    ).fetchone()["id"]
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type, canonical_name) VALUES ('Israeli', 'location', 'Israel')"
    )
    new_id = tmp_db.execute(
        "SELECT id FROM entities WHERE name='Israeli' AND entity_type='location'"
    ).fetchone()["id"]
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 3)",
        (doc_a, old_id),
    )
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 2)",
        (doc_b, new_id),
    )
    tmp_db.commit()

    updated = backfill_demonym_entities(tmp_db)

    assert updated == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM entities WHERE name='Israeli'"
    ).fetchone()[0] == 1
    mentions = {
        r["document_id"]: r["mentions"]
        for r in tmp_db.execute(
            "SELECT document_id, mentions FROM document_entities WHERE entity_id = ?",
            (new_id,),
        )
    }
    assert mentions == {doc_a: 3, doc_b: 2}


def test_ner_skips_duplicates_and_unembedded(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", embedded=0)
    _insert_doc(tmp_db, url="https://x.com/2", is_duplicate=1)
    ner = MockNer()

    result = extract_entities(tmp_db, model=ner)

    assert result.docs_processed == 0
    assert ner.calls == []


def test_ner_excludes_gdelt_and_comtrade_origin_even_if_already_embedded(tmp_db):
    """CP-016 follow-up: extract.py must skip gdelt/comtrade docs that were
    already embedded=1 before the embedder.py fix existed — otherwise NER
    keeps ingesting synthetic CAMEO metadata as if it were prose."""
    _insert_doc(tmp_db, url="https://x.com/1", origin="gdelt")
    _insert_doc(tmp_db, url="https://x.com/2", origin="comtrade")
    _insert_doc(tmp_db, url="https://x.com/3", origin="rss")
    _insert_doc(tmp_db, url="https://x.com/4", origin=None)
    ner = MockNer()

    result = extract_entities(tmp_db, model=ner)

    assert result.docs_processed == 2  # rss + legacy NULL origin only
    still_pending = {
        r["url"]
        for r in tmp_db.execute(
            "SELECT url FROM raw_documents WHERE ner_done = 0"
        ).fetchall()
    }
    assert still_pending == {"https://x.com/1", "https://x.com/2"}


def test_ner_marks_done_and_is_resumable(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="TSMC", body="TSMC")
    ner = MockNer({"TSMC": [("TSMC", "ORG")]})

    extract_entities(tmp_db, model=ner)
    second = extract_entities(tmp_db, model=ner)

    assert second.docs_processed == 0
    assert len(ner.calls) == 1


def test_ner_reuses_existing_entity(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="TSMC a", body="x")
    _insert_doc(tmp_db, url="https://x.com/2", title="TSMC b", body="y")
    ner = MockNer({"TSMC": [("TSMC", "ORG")]})

    result = extract_entities(tmp_db, model=ner)

    assert result.entities_created == 1
    n = tmp_db.execute("SELECT COUNT(*) AS c FROM entities").fetchone()["c"]
    assert n == 1


def test_ner_filters_junk_entities(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="junk", body="junk")
    ner = MockNer({"junk": [("X", "ORG"), ("2026", "ORG"), ("  OK Corp ", "ORG")]})

    result = extract_entities(tmp_db, model=ner)

    names = [r["name"] for r in tmp_db.execute("SELECT name FROM entities")]
    assert names == ["OK Corp"]
    assert result.entities_created == 1


def test_ner_empty_doc_skipped_but_marked(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="", body="")
    result = extract_entities(tmp_db, model=MockNer())

    assert result.docs_skipped == 1
    row = tmp_db.execute("SELECT ner_done FROM raw_documents").fetchone()
    assert row["ner_done"] == 1


def test_ner_respects_limit(tmp_db):
    for i in range(5):
        _insert_doc(tmp_db, url=f"https://x.com/{i}")
    result = extract_entities(tmp_db, model=MockNer(), limit=2)

    assert result.docs_processed == 2


# ─── geocode_events ──────────────────────────────────────────────────────────


def _nominatim_handler(responses: dict[str, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        return httpx.Response(200, json=responses.get(q, []))
    return handler


def test_geocode_sets_lat_lon_and_caches(tmp_db):
    ev = _insert_event(tmp_db, location_name="Taipei")
    client = _mock_client(_nominatim_handler(
        {"Taipei": [{"lat": "25.03", "lon": "121.56", "display_name": "Taipei, TW"}]}
    ))

    result = geocode_events(tmp_db, client=client, delay_s=0)

    assert result.events_geocoded == 1
    assert result.lookups == 1
    row = tmp_db.execute("SELECT lat, lon FROM events WHERE id = ?", (ev,)).fetchone()
    assert row["lat"] == pytest.approx(25.03)
    cached = tmp_db.execute(
        "SELECT lat FROM geocode_cache WHERE query = 'Taipei'"
    ).fetchone()
    assert cached["lat"] == pytest.approx(25.03)


def test_geocode_uses_cache_without_network(tmp_db):
    _insert_event(tmp_db, location_name="Taipei")
    tmp_db.execute(
        "INSERT INTO geocode_cache (query, lat, lon) VALUES ('Taipei', 25.03, 121.56)"
    )
    tmp_db.commit()

    def explode(request):
        raise AssertionError("network call not expected")

    result = geocode_events(tmp_db, client=_mock_client(explode), delay_s=0)

    assert result.events_geocoded == 1
    assert result.cache_hits == 1
    assert result.lookups == 0


def test_geocode_caches_misses(tmp_db):
    _insert_event(tmp_db, location_name="Nowhere Xyz")
    client = _mock_client(_nominatim_handler({}))

    result = geocode_events(tmp_db, client=client, delay_s=0)

    assert result.events_geocoded == 0
    assert result.misses == 1
    cached = tmp_db.execute(
        "SELECT lat FROM geocode_cache WHERE query = 'Nowhere Xyz'"
    ).fetchone()
    assert cached is not None and cached["lat"] is None


def test_geocode_respects_lookup_budget(tmp_db):
    for i in range(3):
        _insert_event(tmp_db, location_name=f"Place {i}")
    client = _mock_client(_nominatim_handler({}))

    result = geocode_events(tmp_db, client=client, max_lookups=2, delay_s=0)

    assert result.lookups == 2


def test_geocode_skips_already_geocoded(tmp_db):
    _insert_event(tmp_db, location_name="Taipei", lat=25.0, lon=121.5)

    def explode(request):
        raise AssertionError("network call not expected")

    result = geocode_events(tmp_db, client=_mock_client(explode), delay_s=0)
    assert result.events_geocoded == 0


# ─── link_wikidata ───────────────────────────────────────────────────────────


def _wikidata_handler(responses: dict[str, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["search"]
        return httpx.Response(200, json={"search": responses.get(q, [])})
    return handler


def _insert_entity(conn, name: str, entity_type: str = "company") -> int:
    cur = conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)", (name, entity_type)
    )
    conn.commit()
    return cur.lastrowid


def test_wikidata_sets_qid_and_canonical(tmp_db):
    eid = _insert_entity(tmp_db, "TSMC")
    client = _mock_client(_wikidata_handler(
        {"TSMC": [{"id": "Q713418",
                   "label": "Taiwan Semiconductor Manufacturing Company",
                   "aliases": ["台積電"]}]}
    ))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.qids_found == 1
    row = tmp_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    assert row["wikidata_qid"] == "Q713418"
    assert row["canonical_name"] == "Taiwan Semiconductor Manufacturing Company"
    assert json.loads(row["aliases"]) == ["台積電"]
    assert row["wikidata_checked"] == 1


def test_wikidata_marks_checked_on_miss(tmp_db):
    eid = _insert_entity(tmp_db, "Unknown Corp Xyz")
    client = _mock_client(_wikidata_handler({}))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.qids_found == 0
    assert result.entities_checked == 1
    row = tmp_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    assert row["wikidata_qid"] is None
    assert row["wikidata_checked"] == 1


def test_wikidata_qid_conflict_marks_checked(tmp_db):
    a = _insert_entity(tmp_db, "TSMC")
    b = _insert_entity(tmp_db, "台積電", entity_type="other")
    hit = [{"id": "Q713418", "label": "TSMC", "aliases": []}]
    client = _mock_client(_wikidata_handler({"TSMC": hit, "台積電": hit}))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.qids_found == 1
    assert result.conflicts == 1
    rows = tmp_db.execute(
        "SELECT wikidata_checked FROM entities WHERE id IN (?, ?)", (a, b)
    ).fetchall()
    assert all(r["wikidata_checked"] == 1 for r in rows)


def test_wikidata_skips_already_checked(tmp_db):
    eid = _insert_entity(tmp_db, "TSMC")
    tmp_db.execute("UPDATE entities SET wikidata_checked = 1 WHERE id = ?", (eid,))
    tmp_db.commit()

    def explode(request):
        raise AssertionError("network call not expected")

    result = link_wikidata(tmp_db, client=_mock_client(explode), delay_s=0)
    assert result.entities_checked == 0


def test_wikidata_stoplists_generic_names(tmp_db):
    junk = _insert_entity(tmp_db, "CRIMINAL", entity_type="other")
    good = _insert_entity(tmp_db, "TSMC")
    client = _mock_client(_wikidata_handler(
        {"TSMC": [{"id": "Q713418", "label": "TSMC", "aliases": []}]}
    ))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.stoplisted == 1
    assert result.qids_found == 1
    junk_row = tmp_db.execute(
        "SELECT * FROM entities WHERE id = ?", (junk,)
    ).fetchone()
    assert junk_row["wikidata_checked"] == 1
    assert junk_row["wikidata_qid"] is None
    good_row = tmp_db.execute(
        "SELECT wikidata_qid FROM entities WHERE id = ?", (good,)
    ).fetchone()
    assert good_row["wikidata_qid"] == "Q713418"


def test_wikidata_stoplist_strips_legacy_qid(tmp_db):
    eid = _insert_entity(tmp_db, "PRESIDENT", entity_type="other")
    tmp_db.execute(
        """UPDATE entities SET wikidata_qid = 'Q30461', canonical_name = 'president',
           wikidata_checked = 1 WHERE id = ?""",
        (eid,),
    )
    tmp_db.commit()
    client = _mock_client(_wikidata_handler({}))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.stoplisted == 1
    row = tmp_db.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
    assert row["wikidata_qid"] is None
    assert row["canonical_name"] is None
    assert row["wikidata_checked"] == 1


def test_wikidata_aborts_on_429(tmp_db):
    a = _insert_entity(tmp_db, "Alpha Corp")
    b = _insert_entity(tmp_db, "Beta Corp")
    doc = _insert_doc(tmp_db, url="https://x.com/429")
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 9)",
        (doc, a),
    )
    tmp_db.commit()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["search"])
        return httpx.Response(429, json={})

    result = link_wikidata(tmp_db, client=_mock_client(handler), delay_s=0)

    assert result.rate_limited is True
    assert calls == ["Alpha Corp"]  # stops at first 429, no hammering
    rows = tmp_db.execute(
        "SELECT wikidata_checked FROM entities WHERE id IN (?, ?)", (a, b)
    ).fetchall()
    # both stay unchecked → retried next cycle
    assert all(r["wikidata_checked"] == 0 for r in rows)


def test_wikidata_non_429_error_continues(tmp_db):
    doc = _insert_doc(tmp_db, url="https://x.com/err")
    a = _insert_entity(tmp_db, "Alpha Corp")
    _insert_entity(tmp_db, "Beta Corp")
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 9)",
        (doc, a),
    )
    tmp_db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["search"] == "Alpha Corp":
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"search": []})

    result = link_wikidata(tmp_db, client=_mock_client(handler), delay_s=0)

    assert result.rate_limited is False
    assert result.entities_checked == 1  # Beta checked despite Alpha 500


def test_wikidata_prioritises_most_mentioned(tmp_db):
    doc = _insert_doc(tmp_db, url="https://x.com/1")
    minor = _insert_entity(tmp_db, "Minor Corp")
    major = _insert_entity(tmp_db, "Major Corp")
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 9)",
        (doc, major),
    )
    tmp_db.commit()

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["search"])
        return httpx.Response(200, json={"search": []})

    link_wikidata(tmp_db, client=_mock_client(handler), max_lookups=1, delay_s=0)

    assert seen == ["Major Corp"]


def test_wikidata_marks_duplicate_as_alias(tmp_db):
    """On QID conflict, mark the new entity as canonical_entity_id alias."""
    # Insert canonical Trump first
    trump1 = _insert_entity(tmp_db, "Donald Trump", entity_type="person")
    # Manually assign QID as if first lookup succeeded
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid = 'Q22686', wikidata_checked = 1 WHERE id = ?",
        (trump1,),
    )
    tmp_db.commit()

    # Insert alternate form (should get same QID, trigger conflict)
    trump2 = _insert_entity(tmp_db, "Donald J. Trump", entity_type="person")

    # Mock handler returns same QID for both lookups
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "search": [
                    {"id": "Q22686", "label": "Donald Trump", "aliases": []}
                ]
            },
        )

    result = link_wikidata(tmp_db, client=_mock_client(handler), delay_s=0)

    # Second entity (trump2) should be marked as alias of trump1
    assert result.conflicts == 1
    row = tmp_db.execute(
        "SELECT canonical_entity_id, wikidata_checked FROM entities WHERE id = ?",
        (trump2,),
    ).fetchone()
    assert row["canonical_entity_id"] == trump1
    assert row["wikidata_checked"] == 1
