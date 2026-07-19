"""
Tests for Phase 2 extraction: NER, geocoding, Wikidata linking.

NER uses a MockNer (no spaCy model load); network steps use mock httpx
transports — no real requests at test time.
"""

import asyncio
import hashlib
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pathosphere.semantic.extract import (
    _build_text,
    _classify_heuristic,
    compute_major_powers,
    backfill_demonym_entities,
    backfill_organization_entities,
    canonicalize_location_entities,
    canonicalize_person_entities,
    extract_entities,
    geocode_events,
    geolocate_ambiguous_events_qwen,
    geolocate_rss_events,
    link_wikidata,
    purge_noise_entities,
    repair_wikidata_type_conflicts,
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
                return FakeDoc([FakeEnt(t, lbl) for t, lbl in ents])
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


def _insert_person(conn, name, mentions=1, canonical_entity_id=None):
    conn.execute(
        "INSERT INTO entities (name, entity_type, canonical_entity_id) VALUES (?, 'person', ?)",
        (name, canonical_entity_id),
    )
    eid = conn.execute(
        "SELECT id FROM entities WHERE name=? AND entity_type='person'", (name,)
    ).fetchone()["id"]
    if mentions:
        doc_id = _insert_doc(conn, url=f"https://x.com/{name}-{eid}")
        conn.execute(
            "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, ?)",
            (doc_id, eid, mentions),
        )
    return eid


def test_canonicalize_merges_honorific_variants(tmp_db):
    """'Ali Khamenei' / 'Ayatollah Ali Khamenei' strip to the same 2-token key
    ("ali khamenei") — exact-match merge, most-mentioned wins as canonical.
    'Ayatollah Khamenei' strips to the bare 1-token surname "khamenei" and is
    merged separately in pass 2 (unambiguous — only one 2-token candidate)."""
    id_ali = _insert_person(tmp_db, "Ali Khamenei", mentions=8)
    id_ayat_ali = _insert_person(tmp_db, "Ayatollah Ali Khamenei", mentions=9)
    id_ayatollah = _insert_person(tmp_db, "Ayatollah Khamenei", mentions=15)
    tmp_db.commit()

    result = canonicalize_person_entities(tmp_db)

    assert result.exact_groups_merged == 1
    assert result.bare_surname_merged == 1
    canon_ali = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_ali,)
    ).fetchone()["canonical_entity_id"]
    canon_ayatollah = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_ayatollah,)
    ).fetchone()["canonical_entity_id"]
    # "Ali Khamenei" (8) merges into "Ayatollah Ali Khamenei" (9, more mentions)
    assert canon_ali == id_ayat_ali
    # Bare "Ayatollah Khamenei" -> "Khamenei" attaches to the (only) 2-token group
    assert canon_ayatollah == id_ayat_ali


def test_canonicalize_does_not_merge_different_people_same_surname(tmp_db):
    """'Mojtaba Khamenei' (the son) must NOT merge with 'Ali Khamenei' (the
    father) — different given names never collapse to the same stripped key."""
    id_ali = _insert_person(tmp_db, "Ali Khamenei", mentions=8)
    id_mojtaba = _insert_person(tmp_db, "Mojtaba Khamenei", mentions=3)
    tmp_db.commit()

    result = canonicalize_person_entities(tmp_db)

    assert result.exact_groups_merged == 0  # each key has only 1 member
    assert tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_ali,)
    ).fetchone()["canonical_entity_id"] is None
    assert tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_mojtaba,)
    ).fetchone()["canonical_entity_id"] is None


def test_canonicalize_bare_surname_merges_when_dominant(tmp_db):
    """Bare 'Khamenei' (honorific-stripped from some mention) merges into the
    dominant full-name candidate when it clears the dominance ratio."""
    id_ali = _insert_person(tmp_db, "Ali Khamenei", mentions=20)
    _insert_person(tmp_db, "Mojtaba Khamenei", mentions=2)
    id_bare = _insert_person(tmp_db, "Khamenei", mentions=5)
    tmp_db.commit()

    result = canonicalize_person_entities(tmp_db)

    assert result.bare_surname_merged == 1
    assert result.bare_surname_skipped == 0
    canon_bare = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_bare,)
    ).fetchone()["canonical_entity_id"]
    assert canon_bare == id_ali  # dominant candidate (20 vs 2 mentions)


def test_canonicalize_bare_surname_skipped_when_ambiguous(tmp_db):
    """Bare 'Khamenei' stays unmerged when no candidate dominates (mentions
    too close to call) — a missed merge beats a wrong one."""
    _insert_person(tmp_db, "Ali Khamenei", mentions=10)
    _insert_person(tmp_db, "Mojtaba Khamenei", mentions=9)
    id_bare = _insert_person(tmp_db, "Khamenei", mentions=5)
    tmp_db.commit()

    result = canonicalize_person_entities(tmp_db)

    assert result.bare_surname_merged == 0
    assert result.bare_surname_skipped == 1
    canon_bare = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (id_bare,)
    ).fetchone()["canonical_entity_id"]
    assert canon_bare is None


def test_canonicalize_is_idempotent(tmp_db):
    _insert_person(tmp_db, "Ali Khamenei", mentions=8)
    _insert_person(tmp_db, "Ayatollah Ali Khamenei", mentions=9)
    tmp_db.commit()

    first = canonicalize_person_entities(tmp_db)
    second = canonicalize_person_entities(tmp_db)

    assert first.exact_groups_merged == 1
    assert second.exact_groups_merged == 0  # already canonical_entity_id set, excluded from re-scan
    assert second.bare_surname_merged == 0
    assert second.bare_surname_skipped == 0


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


def test_wikidata_skips_curated_alias_without_network_call(tmp_db):
    """CP-019: 'UK' must never reach wbsearchentities — found empirically
    that it fuzzy-matches Q8798 (the Ukrainian *language*, via ISO code "uk"),
    not the United Kingdom. Curated alias/demonym/org names are stoplisted
    the same way as GENERIC_ENTITY_STOPLIST, but keep their known-correct
    label instead of being wiped to NULL."""
    uk = _insert_entity(tmp_db, "UK", entity_type="location")
    nato = _insert_entity(tmp_db, "NATO", entity_type="organization")

    def explode(request):
        raise AssertionError("network call not expected for curated alias names")

    result = link_wikidata(tmp_db, client=_mock_client(explode), delay_s=0)

    assert result.stoplisted == 2
    rows = {
        r["id"]: (r["wikidata_checked"], r["wikidata_qid"], r["canonical_name"])
        for r in tmp_db.execute("SELECT id, wikidata_checked, wikidata_qid, canonical_name FROM entities")
    }
    assert rows[uk] == (1, None, "United Kingdom")
    assert rows[nato] == (1, None, "NATO")


def test_wikidata_curated_alias_strips_bad_legacy_qid_keeps_label(tmp_db):
    """A curated-alias entity that already picked up a wrong QID from a past
    run (before this fix) gets that QID cleared but its canonical_name
    forced to the curated value, not wiped like GENERIC_ENTITY_STOPLIST."""
    eid = _insert_entity(tmp_db, "Ukrainian", entity_type="location")
    tmp_db.execute(
        """UPDATE entities SET wikidata_qid = 'Q8798', canonical_name = 'Ukrainian',
           wikidata_checked = 1 WHERE id = ?""",
        (eid,),
    )
    tmp_db.commit()

    result = link_wikidata(tmp_db, client=_mock_client(lambda r: (_ for _ in ()).throw(
        AssertionError("no network expected")
    )), delay_s=0)

    assert result.stoplisted == 1
    row = tmp_db.execute(
        "SELECT wikidata_qid, canonical_name FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["wikidata_qid"] is None
    assert row["canonical_name"] == "Ukraine"


def test_wikidata_ambiguous_name_rejects_wrong_type_match(tmp_db):
    """'Turkey' (location, the country) must not accept a Wikidata hit whose
    P31 says something else (e.g. Q10817602, an animal taxon — the bird) —
    classic disambiguation trap for common-word country names."""
    eid = _insert_entity(tmp_db, "Turkey", entity_type="location")
    hit = [{"id": "Q10817602", "label": "turkey", "aliases": []}]
    # P31 hint disagrees with the row's entity_type (location) — Q5 (human)
    # stands in for "recognisably not a place", same as any non-location hint.
    client = _mock_client(_combined_wikidata_handler(
        {"Turkey": hit}, {"Q10817602": [_p31_claim("Q5")]},
    ))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.qids_found == 0
    assert result.entities_checked == 1
    row = tmp_db.execute(
        "SELECT wikidata_qid, wikidata_checked FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["wikidata_qid"] is None
    assert row["wikidata_checked"] == 1


def test_wikidata_ambiguous_name_accepts_when_type_matches(tmp_db):
    """'Turkey' correctly resolving to the country (P31=Q6256, country) must
    still be accepted."""
    eid = _insert_entity(tmp_db, "Turkey", entity_type="location")
    hit = [{"id": "Q43", "label": "Turkey", "aliases": []}]
    client = _mock_client(_combined_wikidata_handler(
        {"Turkey": hit}, {"Q43": [_p31_claim("Q6256")]},
    ))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.qids_found == 1
    row = tmp_db.execute(
        "SELECT wikidata_qid FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["wikidata_qid"] == "Q43"


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
    _insert_entity(tmp_db, "Minor Corp")
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


def test_extract_strips_html_from_body(tmp_db):
    """NER input should have HTML tags stripped before processing."""
    _insert_doc(
        tmp_db,
        url="https://example.com/article",
        title="Article Title",
        body="<p>Reuters reported <strong>bold</strong> text.</p><br/><p>Second paragraph.</p>",
        embedded=1,
    )

    model = MockNer(
        {
            "Article Title Reuters reported bold text. Second paragraph.": [
                ("Reuters", "ORG"),
                ("bold text", "MISC"),
            ],
        }
    )

    result = extract_entities(tmp_db, model=model)

    assert result.docs_processed == 1
    assert result.entities_created == 2  # Reuters + "bold text"
    rows = tmp_db.execute("SELECT name FROM entities ORDER BY name").fetchall()
    assert len(rows) == 2
    assert rows[0]["name"] == "Reuters"
    assert rows[1]["name"] == "bold text"


def test_build_text_decodes_html_entities():
    """
    bleach.clean strips TAGS but leaves HTML entity references (&nbsp;,
    &ldquo;, &rdquo;) literal — found leaking into extracted entity names
    (e.g. 'stabbed&nbsp;in') on freshly re-processed text. _build_text must
    also html.unescape after stripping tags.
    """
    body = "Netanyahu said &ldquo;very bad&rdquo; things.&nbsp;Officials reacted."

    text = _build_text("Title", body)

    assert text is not None
    assert "&ldquo;" not in text
    assert "&rdquo;" not in text
    assert "&nbsp;" not in text
    assert "very bad" in text


# ─── CP-018 #1: type-aware Wikidata QID conflict resolution ─────────────────


def _combined_wikidata_handler(
    search_responses: dict[str, list[dict]],
    claims_responses: dict[str, list[dict]] | None = None,
):
    """Mock transport handling both wbsearchentities (search=...) and
    wbgetclaims (entity=QID, property=P31) actions."""
    claims_responses = claims_responses or {}

    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params.get("action")
        if action == "wbgetclaims":
            qid = request.url.params["entity"]
            return httpx.Response(200, json={"claims": {"P31": claims_responses.get(qid, [])}})
        q = request.url.params["search"]
        return httpx.Response(200, json={"search": search_responses.get(q, [])})

    return handler


def _p31_claim(qid: str) -> dict:
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def test_wikidata_conflict_swaps_canonical_when_type_disagrees(tmp_db):
    """CP-018 #1: ALL-CAPS 'FRANCE' (mistyped company) got the QID first;
    correctly-typed 'France' (location) must become canonical instead, via
    Wikidata's P31 (country, Q6256) breaking the tie — not "first arrival
    wins"."""
    wrong = _insert_entity(tmp_db, "FRANCE", entity_type="company")
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid='Q142', canonical_name='France', wikidata_checked=1 WHERE id=?",
        (wrong,),
    )
    tmp_db.commit()
    correct = _insert_entity(tmp_db, "France", entity_type="location")

    hit = [{"id": "Q142", "label": "France", "aliases": []}]
    client = _mock_client(_combined_wikidata_handler(
        {"France": hit}, {"Q142": [_p31_claim("Q6256")]},
    ))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.conflicts == 1
    rows = {
        r["id"]: (r["entity_type"], r["wikidata_qid"], r["canonical_entity_id"])
        for r in tmp_db.execute(
            "SELECT id, entity_type, wikidata_qid, canonical_entity_id FROM entities"
        )
    }
    assert rows[correct] == ("location", "Q142", None)
    assert rows[wrong] == ("company", None, correct)


def test_wikidata_conflict_keeps_first_when_type_hint_unavailable(tmp_db):
    """No P31 hint (empty claims) → falls back to old first-arrival behavior
    rather than guessing which side is right."""
    first = _insert_entity(tmp_db, "TSMC", entity_type="company")
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid='Q713418', wikidata_checked=1 WHERE id=?",
        (first,),
    )
    tmp_db.commit()
    second = _insert_entity(tmp_db, "台積電", entity_type="other")

    hit = [{"id": "Q713418", "label": "TSMC", "aliases": []}]
    client = _mock_client(_combined_wikidata_handler({"台積電": hit}, {}))

    result = link_wikidata(tmp_db, client=client, delay_s=0)

    assert result.conflicts == 1
    row = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (second,)
    ).fetchone()
    assert row["canonical_entity_id"] == first


def test_repair_wikidata_type_conflicts_swaps_when_hint_disagrees(tmp_db):
    wrong = _insert_entity(tmp_db, "FRANCE", entity_type="company")
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid='Q142', canonical_name='France', wikidata_checked=1 WHERE id=?",
        (wrong,),
    )
    correct = _insert_entity(tmp_db, "France", entity_type="location")
    tmp_db.execute("UPDATE entities SET canonical_entity_id=? WHERE id=?", (wrong, correct))
    tmp_db.commit()

    client = _mock_client(_combined_wikidata_handler({}, {"Q142": [_p31_claim("Q6256")]}))

    fixed = repair_wikidata_type_conflicts(tmp_db, client=client, delay_s=0)

    assert fixed == 1
    rows = {
        r["id"]: (r["entity_type"], r["wikidata_qid"], r["canonical_entity_id"])
        for r in tmp_db.execute(
            "SELECT id, entity_type, wikidata_qid, canonical_entity_id FROM entities"
        )
    }
    assert rows[correct] == ("location", "Q142", None)
    assert rows[wrong] == ("company", None, correct)


def test_repair_wikidata_type_conflicts_noop_when_types_already_agree(tmp_db):
    canonical = _insert_entity(tmp_db, "Donald Trump", entity_type="person")
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid='Q22686', wikidata_checked=1 WHERE id=?", (canonical,)
    )
    alias = _insert_entity(tmp_db, "Trump", entity_type="person")
    tmp_db.execute("UPDATE entities SET canonical_entity_id=? WHERE id=?", (canonical, alias))
    tmp_db.commit()

    def explode(request):
        raise AssertionError("network call not expected when types agree")

    fixed = repair_wikidata_type_conflicts(tmp_db, client=_mock_client(explode), delay_s=0)
    assert fixed == 0


def test_repair_wikidata_type_conflicts_skips_when_hint_unavailable(tmp_db):
    wrong = _insert_entity(tmp_db, "Weird Co", entity_type="company")
    tmp_db.execute(
        "UPDATE entities SET wikidata_qid='Q999', wikidata_checked=1 WHERE id=?", (wrong,)
    )
    alias = _insert_entity(tmp_db, "Weird", entity_type="location")
    tmp_db.execute("UPDATE entities SET canonical_entity_id=? WHERE id=?", (wrong, alias))
    tmp_db.commit()

    client = _mock_client(_combined_wikidata_handler({}, {}))
    fixed = repair_wikidata_type_conflicts(tmp_db, client=client, delay_s=0)

    assert fixed == 0
    row = tmp_db.execute("SELECT entity_type FROM entities WHERE id=?", (wrong,)).fetchone()
    assert row["entity_type"] == "company"


# ─── CP-018 #3: location canonicalization ────────────────────────────────────


def _insert_location(conn, name, mentions=1):
    conn.execute("INSERT INTO entities (name, entity_type) VALUES (?, 'location')", (name,))
    eid = conn.execute(
        "SELECT id FROM entities WHERE name=? AND entity_type='location'", (name,)
    ).fetchone()["id"]
    if mentions:
        doc_id = _insert_doc(conn, url=f"https://x.com/loc-{name}-{eid}")
        conn.execute(
            "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, ?)",
            (doc_id, eid, mentions),
        )
    return eid


def test_canonicalize_location_merges_uk_aliases(tmp_db):
    """England/British/Britain/UK all refer to the same country (CP-018 #3)
    — most-mentioned (UK) wins as canonical."""
    uk = _insert_location(tmp_db, "UK", mentions=58)
    britain = _insert_location(tmp_db, "Britain", mentions=32)
    british = _insert_location(tmp_db, "British", mentions=37)
    england = _insert_location(tmp_db, "England", mentions=20)
    tmp_db.commit()

    result = canonicalize_location_entities(tmp_db)

    assert result.groups_merged == 1
    assert result.entities_merged == 3
    rows = {
        r["id"]: r["canonical_entity_id"]
        for r in tmp_db.execute(
            "SELECT id, canonical_entity_id FROM entities WHERE entity_type='location'"
        )
    }
    assert rows[uk] is None
    assert rows[britain] == uk
    assert rows[british] == uk
    assert rows[england] == uk
    canonical_row = tmp_db.execute(
        "SELECT canonical_name FROM entities WHERE id=?", (uk,)
    ).fetchone()
    assert canonical_row["canonical_name"] == "United Kingdom"


def test_canonicalize_location_does_not_merge_unrelated_places(tmp_db):
    _insert_location(tmp_db, "Paris")
    _insert_location(tmp_db, "Tokyo")

    result = canonicalize_location_entities(tmp_db)

    assert result.groups_merged == 0
    assert result.entities_merged == 0


def test_canonicalize_location_is_idempotent(tmp_db):
    _insert_location(tmp_db, "UK", mentions=10)
    _insert_location(tmp_db, "Britain", mentions=5)
    tmp_db.commit()

    first = canonicalize_location_entities(tmp_db)
    second = canonicalize_location_entities(tmp_db)

    assert first.groups_merged == 1
    assert second.groups_merged == 0


def test_canonicalize_location_merges_country_with_its_own_demonym(tmp_db):
    """Class-of-error fix: 'China' (the country's own literal name) must join
    the same group as 'Chinese' (its demonym) — found empirically that only
    the demonym side ever resolved a group key, so China/Chinese stayed two
    separate nodes even though DEMONYM_TO_COUNTRY already maps 'chinese' ->
    'China'. Also covers the case where the country's canonical_name holds
    Wikidata's full official form ("People's Republic of China") rather than
    the short form used in our tables."""
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type, canonical_name) VALUES "
        "('China', 'location', \"People's Republic of China\")"
    )
    china = tmp_db.execute(
        "SELECT id FROM entities WHERE name='China'"
    ).fetchone()["id"]
    doc = _insert_doc(tmp_db, url="https://x.com/china")
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 50)",
        (doc, china),
    )
    chinese = _insert_location(tmp_db, "Chinese", mentions=5)
    tmp_db.commit()

    result = canonicalize_location_entities(tmp_db)

    assert result.groups_merged == 1
    assert result.entities_merged == 1
    row = tmp_db.execute(
        "SELECT canonical_entity_id FROM entities WHERE id=?", (chinese,)
    ).fetchone()
    assert row["canonical_entity_id"] == china
    canonical_row = tmp_db.execute(
        "SELECT canonical_name FROM entities WHERE id=?", (china,)
    ).fetchone()
    assert canonical_row["canonical_name"] == "China"


def test_backfill_demonym_entities_covers_location_alias_continents(tmp_db):
    """'European' (adjective for the continent) must be reclassified to
    location too, not just DEMONYM_TO_COUNTRY entries — found stuck as
    entity_type='other', invisible to canonicalize_location_entities."""
    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('European', 'other')")
    tmp_db.commit()

    updated = backfill_demonym_entities(tmp_db)

    assert updated == 1
    row = tmp_db.execute(
        "SELECT entity_type, canonical_name FROM entities WHERE name='European'"
    ).fetchone()
    assert row["entity_type"] == "location"
    assert row["canonical_name"] == "Europe"


def test_backfill_demonym_entities_forces_canonical_name_on_merge_survivor(tmp_db):
    """A pre-existing 'location' dup with a WRONG canonical_name (e.g. from a
    bad Wikidata match, CP-019-class) must have it corrected on merge, not
    just inherit mentions."""
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type) VALUES ('Europe', 'company')"
    )
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type, canonical_name) VALUES "
        "('Europe', 'location', 'Europe PubMed Central')"
    )
    tmp_db.commit()

    updated = backfill_demonym_entities(tmp_db)

    assert updated == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM entities WHERE name='Europe'"
    ).fetchone()[0] == 1
    row = tmp_db.execute(
        "SELECT entity_type, canonical_name FROM entities WHERE name='Europe'"
    ).fetchone()
    assert row["entity_type"] == "location"
    assert row["canonical_name"] == "Europe"


# ─── CP-018 #2: intergovernmental orgs → entity_type=organization ───────────


def test_ner_classifies_known_intergovernmental_orgs_as_organization(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="x", body="x")
    ner = MockNer({"x": [("NATO", "ORG"), ("EU", "ORG"), ("Boeing", "ORG")]})

    extract_entities(tmp_db, model=ner)

    rows = {
        r["name"]: (r["entity_type"], r["canonical_name"])
        for r in tmp_db.execute("SELECT name, entity_type, canonical_name FROM entities")
    }
    assert rows["NATO"] == ("organization", "NATO")
    assert rows["EU"] == ("organization", "European Union")
    assert rows["Boeing"] == ("company", None)


def test_backfill_organization_entities_reclassifies_existing(tmp_db):
    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('NATO', 'company')")
    tmp_db.commit()

    updated = backfill_organization_entities(tmp_db)

    assert updated == 1
    row = tmp_db.execute(
        "SELECT entity_type, canonical_name FROM entities WHERE name='NATO'"
    ).fetchone()
    assert row["entity_type"] == "organization"
    assert row["canonical_name"] == "NATO"


def test_backfill_organization_entities_is_idempotent(tmp_db):
    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('NATO', 'company')")
    tmp_db.commit()

    first = backfill_organization_entities(tmp_db)
    second = backfill_organization_entities(tmp_db)

    assert first == 1
    assert second == 0


# ─── CP-018 #4: NER boilerplate noise ────────────────────────────────────────


def test_ner_excludes_noise_boilerplate_entities(tmp_db):
    _insert_doc(tmp_db, url="https://x.com/1", title="x", body="x")
    ner = MockNer({"x": [("VIDEO", "ORG"), ("Reuters", "ORG")]})

    result = extract_entities(tmp_db, model=ner)

    assert result.entities_created == 1
    names = {r["name"] for r in tmp_db.execute("SELECT name FROM entities")}
    assert names == {"Reuters"}


def test_purge_noise_entities_deletes_legacy_rows(tmp_db):
    doc = _insert_doc(tmp_db, url="https://x.com/1")
    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('VIDEO', 'company')")
    eid = tmp_db.execute("SELECT id FROM entities WHERE name='VIDEO'").fetchone()["id"]
    tmp_db.execute(
        "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 22)",
        (doc, eid),
    )
    tmp_db.commit()

    deleted = purge_noise_entities(tmp_db)

    assert deleted == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM entities WHERE name='VIDEO'"
    ).fetchone()[0] == 0
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM document_entities WHERE entity_id=?", (eid,)
    ).fetchone()[0] == 0


def test_purge_noise_entities_is_idempotent(tmp_db):
    tmp_db.execute("INSERT INTO entities (name, entity_type) VALUES ('WATCH', 'company')")
    tmp_db.commit()

    first = purge_noise_entities(tmp_db)
    second = purge_noise_entities(tmp_db)

    assert first == 1
    assert second == 0


# ─── CP-022: RSS event geolocation (heuristic) ──────────────────────────────


def _insert_rss_event(conn, *, title: str = "Event", origin: str = "rss") -> int:
    cur = conn.execute(
        "INSERT INTO events (title, first_seen, last_seen, origin) "
        "VALUES (?, '2026-07-01', '2026-07-01', ?)",
        (title, origin),
    )
    conn.commit()
    return cur.lastrowid


def _get_or_create_location_entity(conn, name: str) -> int:
    """Unlike _insert_entity (plain INSERT, blows up on repeat names), country
    entities here are shared across many events/docs — need get-or-create."""
    row = conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = 'location'", (name,)
    ).fetchone()
    if row:
        return row["id"]
    return _insert_entity(conn, name, entity_type="location")


def _attach_event_countries(conn, event_id: int, countries: list[str]) -> None:
    """Create one document, link it into the event's cluster, tag it with
    the given location entities (reuses existing entity rows by name so
    global major-power document counts accumulate across calls)."""
    doc_id = _insert_doc(conn, url=f"https://x.com/ev{event_id}")
    conn.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (event_id, doc_id),
    )
    for name in countries:
        eid = _get_or_create_location_entity(conn, name)
        conn.execute(
            "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
            (doc_id, eid),
        )
    conn.commit()


def _bump_country_doc_count(conn, name: str, n_docs: int) -> None:
    """Inflate a country's global distinct-document mention count (used by
    compute_major_powers), independent of any event linkage."""
    eid = _get_or_create_location_entity(conn, name)
    for i in range(n_docs):
        doc_id = _insert_doc(conn, url=f"https://hub.example/{name}/{i}")
        conn.execute(
            "INSERT INTO document_entities (document_id, entity_id, mentions) VALUES (?, ?, 1)",
            (doc_id, eid),
        )
    conn.commit()


def test_classify_heuristic_no_countries_is_skip_none():
    r = _classify_heuristic([], {"United States"})
    assert r.decision == "skip_none"
    assert r.location_country is None


def test_classify_heuristic_single_country_is_located():
    r = _classify_heuristic(["Cuba"], {"United States"})
    assert r.decision == "located"
    assert r.location_country == "Cuba"


def test_classify_heuristic_dedups_repeated_country():
    r = _classify_heuristic(["Cuba", "Cuba", "Cuba"], {"United States"})
    assert r.decision == "located"
    assert r.location_country == "Cuba"


def test_classify_heuristic_all_majors_is_skip_bilateral():
    r = _classify_heuristic(
        ["United States", "Iran"], {"United States", "Iran", "China"}
    )
    assert r.decision == "skip_bilateral"
    assert r.location_country is None


def test_classify_heuristic_one_major_one_minor_targets_minor():
    r = _classify_heuristic(["United States", "Cuba"], {"United States", "China"})
    assert r.decision == "located"
    assert r.location_country == "Cuba"


def test_classify_heuristic_one_major_two_minors_is_ambiguous():
    """The Cuba/Venezuela case that motivated CP-022: actor-via-target
    pattern isn't distinguishable by count alone."""
    r = _classify_heuristic(
        ["United States", "Cuba", "Venezuela"], {"United States", "China"}
    )
    assert r.decision == "ambiguous"
    assert r.location_country is None


def test_classify_heuristic_two_majors_one_minor_is_ambiguous():
    r = _classify_heuristic(
        ["United States", "China", "Venezuela"], {"United States", "China"}
    )
    assert r.decision == "ambiguous"


def test_compute_major_powers_top_n(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    _bump_country_doc_count(tmp_db, "Cuba", 1)

    majors = compute_major_powers(tmp_db, top_n=2)

    assert majors == {"United States", "China"}


def test_geolocate_rss_events_accepts_precomputed_major_powers(tmp_db):
    """Efficiency fix: passing major_powers= skips the corpus-wide GROUP BY
    entirely — `pathos extract --geolocate-qwen` computes it once and feeds
    it to both geolocate_rss_events and geolocate_ambiguous_events_qwen
    instead of running the same query twice in one invocation."""
    ev = _insert_rss_event(tmp_db, title="US sanctions Cuba")
    _attach_event_countries(tmp_db, ev, ["United States", "Cuba"])

    with patch(
        "pathosphere.semantic.extract.compute_major_powers"
    ) as mock_compute:
        result = geolocate_rss_events(tmp_db, major_powers={"United States"})

    mock_compute.assert_not_called()
    assert result.major_powers == ["United States"]
    assert result.located == 1


def test_geolocate_ambiguous_events_qwen_accepts_precomputed_major_powers(tmp_db):
    ev = _insert_rss_event(tmp_db, title="US pressures Cuba via Venezuela")
    _attach_event_countries(tmp_db, ev, ["United States", "Cuba", "Venezuela"])
    qwen = _make_qwen_client(return_value=json.dumps({"location_country": "Cuba"}))

    with patch(
        "pathosphere.semantic.extract.compute_major_powers"
    ) as mock_compute:
        result = asyncio.run(
            geolocate_ambiguous_events_qwen(tmp_db, qwen, major_powers={"United States"})
        )

    mock_compute.assert_not_called()
    assert result.major_powers == ["United States"]


def test_geolocate_rss_events_classifies_and_writes_location(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)

    ev_single = _insert_rss_event(tmp_db, title="Cuba earthquake solidarity")
    _attach_event_countries(tmp_db, ev_single, ["Cuba"])

    ev_bilateral = _insert_rss_event(tmp_db, title="US-China trade talks")
    _attach_event_countries(tmp_db, ev_bilateral, ["United States", "China"])

    ev_major_minor = _insert_rss_event(tmp_db, title="US sanctions Cuba")
    _attach_event_countries(tmp_db, ev_major_minor, ["United States", "Cuba"])

    ev_ambiguous = _insert_rss_event(tmp_db, title="US pressures Cuba via Venezuela")
    _attach_event_countries(
        tmp_db, ev_ambiguous, ["United States", "China", "Venezuela"]
    )

    ev_no_entities = _insert_rss_event(tmp_db, title="No countries mentioned here")

    ev_non_rss = _insert_rss_event(tmp_db, title="USGS earthquake", origin="usgs")
    _attach_event_countries(tmp_db, ev_non_rss, ["Japan"])

    result = geolocate_rss_events(tmp_db, top_n_major_powers=2)

    assert result.events_evaluated == 5  # ev_non_rss excluded (origin != 'rss')
    assert result.located == 2
    assert result.skip_bilateral == 1
    assert result.ambiguous == 1
    assert result.skip_none == 1
    assert set(result.major_powers) == {"United States", "China"}

    rows = {
        r["id"]: r["location_name"]
        for r in tmp_db.execute("SELECT id, location_name FROM events")
    }
    assert rows[ev_single] == "Cuba"
    assert rows[ev_bilateral] is None
    assert rows[ev_major_minor] == "Cuba"
    assert rows[ev_ambiguous] is None
    assert rows[ev_no_entities] is None
    assert rows[ev_non_rss] is None  # untouched — not origin='rss'


def test_geolocate_rss_events_is_idempotent_on_already_located(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)

    ev = _insert_rss_event(tmp_db, title="Cuba earthquake solidarity")
    _attach_event_countries(tmp_db, ev, ["Cuba"])

    first = geolocate_rss_events(tmp_db, top_n_major_powers=2)
    second = geolocate_rss_events(tmp_db, top_n_major_powers=2)

    assert first.located == 1
    assert second.events_evaluated == 0  # nothing left with location_name IS NULL
    assert second.located == 0


def test_rss_event_countries_resolves_alias_to_canonical_major_power(tmp_db):
    """Regression: an RSS event mentioning only a lowercase alias entity
    ('turkey') of a major power ('Turkey', canonical_entity_id-linked) must
    still be recognized as mentioning that major power. Before the fix,
    _rss_event_countries read the alias row's own (unsynced) name/
    canonical_name instead of following canonical_entity_id, so 'turkey' !=
    'Turkey' (case-sensitive) and the event misclassified as 2 minors
    (ambiguous) instead of 1 major + 1 minor (located, target=minor).
    Found on real production data (entity id 9653, 'turkey')."""
    canon_id = _get_or_create_location_entity(tmp_db, "Turkey")
    _bump_country_doc_count(tmp_db, "Turkey", 10)  # sole top-1 major power
    tmp_db.execute(
        "INSERT INTO entities (name, entity_type, canonical_entity_id) VALUES ('turkey', 'location', ?)",
        (canon_id,),
    )
    tmp_db.commit()

    ev = _insert_rss_event(tmp_db, title="Turkey earthquake response reaches Syria")
    _attach_event_countries(tmp_db, ev, ["turkey", "Syria"])  # reuses the alias row by exact name

    result = geolocate_rss_events(tmp_db, top_n_major_powers=1)

    assert set(result.major_powers) == {"Turkey"}
    assert result.located == 1
    assert result.ambiguous == 0
    row = tmp_db.execute("SELECT location_name FROM events WHERE id = ?", (ev,)).fetchone()
    assert row["location_name"] == "Syria"


# ─── CP-022: RSS event geolocation (Qwen fallback) ──────────────────────────


def _make_qwen_client(*, side_effect=None, return_value=None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.complete = AsyncMock(side_effect=side_effect)
    else:
        client.complete = AsyncMock(return_value=return_value)
    return client


def test_qwen_fallback_resolves_ambiguous_location(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    ev = _insert_rss_event(tmp_db, title="US pressures Cuba via Venezuela")
    _attach_event_countries(tmp_db, ev, ["United States", "China", "Venezuela"])

    client = _make_qwen_client(
        return_value=json.dumps(
            {"location_country": "Cuba", "actor_countries": ["United States"], "via_countries": ["Venezuela"]}
        )
    )

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=5, top_n_major_powers=2)
    )

    assert result.qwen_calls == 1
    assert result.qwen_located == 1
    row = tmp_db.execute(
        "SELECT location_name, geoloc_checked FROM events WHERE id = ?", (ev,)
    ).fetchone()
    assert row["location_name"] == "Cuba"
    assert row["geoloc_checked"] == 1


def test_qwen_fallback_records_no_anchor_without_retry(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    ev = _insert_rss_event(tmp_db, title="No final agreement on deal with US – Iran")
    _attach_event_countries(tmp_db, ev, ["United States", "China", "Iran"])

    client = _make_qwen_client(
        return_value=json.dumps(
            {"location_country": None, "actor_countries": ["United States", "Iran"], "via_countries": []}
        )
    )

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=5, top_n_major_powers=2)
    )

    assert result.qwen_calls == 1
    assert result.qwen_no_location == 1
    row = tmp_db.execute(
        "SELECT location_name, geoloc_checked FROM events WHERE id = ?", (ev,)
    ).fetchone()
    assert row["location_name"] is None
    assert row["geoloc_checked"] == 1  # examined, no anchor — never retried


def test_qwen_fallback_respects_limit_and_is_resumable(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    events = []
    for i in range(3):
        ev = _insert_rss_event(tmp_db, title=f"ambiguous event {i}")
        _attach_event_countries(tmp_db, ev, ["United States", "China", f"Minor{i}"])
        events.append(ev)

    client = _make_qwen_client(
        return_value=json.dumps({"location_country": "X", "actor_countries": [], "via_countries": []})
    )

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=1, top_n_major_powers=2)
    )

    assert result.qwen_calls == 1
    checked = [
        r["geoloc_checked"] for r in tmp_db.execute(
            "SELECT geoloc_checked FROM events WHERE id IN (?, ?, ?) ORDER BY id", events
        )
    ]
    assert sum(checked) == 1  # only 1 event examined this batch, other 2 left for next run


def test_qwen_fallback_call_failure_leaves_event_unchecked(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    ev = _insert_rss_event(tmp_db, title="ambiguous event")
    _attach_event_countries(tmp_db, ev, ["United States", "China", "Minor"])

    client = _make_qwen_client(side_effect=RuntimeError("Cannot reach Ollama"))

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=5, top_n_major_powers=2)
    )

    assert result.qwen_errors == 1
    row = tmp_db.execute(
        "SELECT location_name, geoloc_checked FROM events WHERE id = ?", (ev,)
    ).fetchone()
    assert row["location_name"] is None
    assert row["geoloc_checked"] == 0  # left for retry, not a permanent answer


def test_qwen_fallback_malformed_json_leaves_event_unchecked(tmp_db):
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    ev = _insert_rss_event(tmp_db, title="ambiguous event")
    _attach_event_countries(tmp_db, ev, ["United States", "China", "Minor"])

    client = _make_qwen_client(return_value="not json at all")

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=5, top_n_major_powers=2)
    )

    assert result.qwen_errors == 1
    row = tmp_db.execute(
        "SELECT geoloc_checked FROM events WHERE id = ?", (ev,)
    ).fetchone()
    assert row["geoloc_checked"] == 0


def test_qwen_fallback_skips_llm_call_when_heuristic_now_resolves(tmp_db):
    """Bilateral-only event sitting unchecked (e.g. from before geolocate_rss_events
    last ran) should be marked checked without ever calling the LLM."""
    _bump_country_doc_count(tmp_db, "United States", 10)
    _bump_country_doc_count(tmp_db, "China", 8)
    ev = _insert_rss_event(tmp_db, title="US-China trade talks")
    _attach_event_countries(tmp_db, ev, ["United States", "China"])

    client = _make_qwen_client(return_value=json.dumps({"location_country": None}))

    result = asyncio.run(
        geolocate_ambiguous_events_qwen(tmp_db, client, limit=5, top_n_major_powers=2)
    )

    client.complete.assert_not_called()
    assert result.resolved_by_heuristic == 1
    row = tmp_db.execute(
        "SELECT location_name, geoloc_checked FROM events WHERE id = ?", (ev,)
    ).fetchone()
    assert row["location_name"] is None
    assert row["geoloc_checked"] == 1
