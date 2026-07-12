"""
Entity extraction — Phase 2 (NER + geocoding + Wikidata linking).

Three independent, resumable steps:
  1. extract_entities — spaCy multilingual NER (xx_ent_wiki_sm) on docs with
     embedded=1, is_duplicate=0, ner_done=0 → entities + document_entities
  2. geocode_events   — Nominatim lookup for events with location_name and
     lat NULL; 1 req/s, hits AND misses cached in geocode_cache
  3. link_wikidata    — wbsearchentities QID lookup for entities not yet
     checked → wikidata_qid + canonical_name

Memory: xx_ent_wiki_sm is ~30 MB; loaded only inside this phase, unloaded after.
"""

import html
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import bleach
import httpx
from loguru import logger

from pathosphere.semantic.embedder import NON_PROSE_ORIGINS

NER_MODEL_NAME = "xx_ent_wiki_sm"
MAX_NER_CHARS = 2000  # title + body head; bounds CPU per doc

# spaCy label → entities.entity_type
LABEL_MAP = {
    "PER": "person",
    "ORG": "company",
    "LOC": "location",
    "MISC": "other",
}

# Demonyms/adjectival country forms spaCy tags NORP/MISC (→ "other") — they
# are unambiguous references to a place and read better reclassified as
# location with the country as canonical_name. Matched case-insensitively,
# exact full-name match only (avoids swallowing unrelated "other" entities).
DEMONYM_TO_COUNTRY: dict[str, str] = {
    "israeli": "Israel", "russian": "Russia", "chinese": "China",
    "american": "United States", "british": "United Kingdom",
    "french": "France", "german": "Germany", "italian": "Italy",
    "spanish": "Spain", "japanese": "Japan", "korean": "South Korea",
    "indian": "India", "pakistani": "Pakistan", "iranian": "Iran",
    "iraqi": "Iraq", "syrian": "Syria", "turkish": "Turkey",
    "ukrainian": "Ukraine", "polish": "Poland", "canadian": "Canada",
    "mexican": "Mexico", "brazilian": "Brazil", "australian": "Australia",
    "egyptian": "Egypt", "saudi": "Saudi Arabia", "emirati": "United Arab Emirates",
    "lebanese": "Lebanon", "jordanian": "Jordan", "afghan": "Afghanistan",
    "vietnamese": "Vietnam", "taiwanese": "Taiwan", "thai": "Thailand",
    "indonesian": "Indonesia", "nigerian": "Nigeria", "kenyan": "Kenya",
    "ethiopian": "Ethiopia", "south african": "South Africa",
    "colombian": "Colombia", "argentine": "Argentina", "venezuelan": "Venezuela",
    "dutch": "Netherlands", "swedish": "Sweden", "norwegian": "Norway",
    "finnish": "Finland", "danish": "Denmark", "swiss": "Switzerland",
    "greek": "Greece", "portuguese": "Portugal", "irish": "Ireland",
    "scottish": "United Kingdom", "welsh": "United Kingdom",
    "yemeni": "Yemen", "qatari": "Qatar", "kuwaiti": "Kuwait",
    "libyan": "Libya", "algerian": "Algeria", "moroccan": "Morocco",
    "sudanese": "Sudan", "somali": "Somalia", "north korean": "North Korea",
}


def _classify(name: str, spacy_label: str) -> tuple[str, str | None]:
    """(entity_type, canonical_name) for a cleaned entity name.

    Falls back to the spaCy label mapping; overrides to location+country for
    known demonyms regardless of what spaCy tagged them as.
    """
    country = DEMONYM_TO_COUNTRY.get(name.lower())
    if country is not None:
        return "location", country
    return LABEL_MAP.get(spacy_label), None

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_S = 1.1  # usage policy: max 1 req/s
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_DELAY_S = 1.0  # Wikimedia anonymous rate limit: stay ~1 req/s

# Generic common nouns / roles / demonyms that GDELT-derived NER surfaces in
# ALL CAPS with huge mention counts. Linking them wastes the lookup budget and
# produces wrong QIDs ("MALE" → Malé). Matched case-insensitively.
GENERIC_ENTITY_STOPLIST = frozenset({
    "activist", "actor", "administration", "agency", "airline", "airport",
    "ambassador", "american", "analyst", "armed", "army", "attorney",
    "authorities", "authority", "bank", "border", "business", "businesses",
    "cabinet", "candidate", "chairman", "chief", "citizen", "citizens",
    "civilian", "civilians", "coalition", "commander", "commission",
    "committee", "community", "companies", "company", "congress", "council",
    "court", "criminal", "criminals", "customer", "defence", "defense",
    "deputy", "director", "doctor", "economy", "editor", "election",
    "embassy", "employee", "employees", "expert", "family", "farmer",
    "federal", "female", "firefighter", "general", "goverment", "government",
    "governor", "hospital", "industry", "insurgent", "intelligence",
    "investor", "israeli", "journalist", "judge", "lawmaker", "lawyer", "leader",
    "leaders", "male", "manager", "mayor", "media", "migrant", "military",
    "minister", "ministry", "minority", "official", "officials", "opposition",
    "parliament", "party", "patient", "police", "politician", "president",
    "prime minister", "prisoner", "professor", "prosecutor", "protester",
    "protesters", "rebel", "refugee", "researcher", "resident", "residents",
    "school", "scientist",
    "secretary", "security", "senate", "senator", "soldier", "spokesman",
    "spokesperson", "student", "supreme", "teacher", "terrorist", "tourist",
    "union", "university", "victim", "villager", "voter", "voters", "witness",
    "worker", "workers",
})


@dataclass
class ExtractResult:
    docs_processed: int = 0
    docs_skipped: int = 0   # no usable text
    entities_created: int = 0
    mentions_recorded: int = 0


@dataclass
class GeocodeResult:
    events_geocoded: int = 0
    cache_hits: int = 0
    lookups: int = 0
    misses: int = 0


@dataclass
class WikidataResult:
    entities_checked: int = 0
    qids_found: int = 0
    conflicts: int = 0      # QID already taken by another entity row
    stoplisted: int = 0     # generic names marked checked without lookup
    rate_limited: bool = False  # run aborted on 429; rest retried next cycle


@runtime_checkable
class NerModel(Protocol):
    def __call__(self, text: str): ...  # returns object with .ents


def load_ner_model() -> NerModel:
    import spacy

    logger.info(f"Loading NER model: {NER_MODEL_NAME}")
    try:
        return spacy.load(NER_MODEL_NAME)
    except OSError as exc:
        raise RuntimeError(
            f"spaCy model {NER_MODEL_NAME} not installed — run: "
            f"uv run python -m spacy download {NER_MODEL_NAME}"
        ) from exc


def _build_text(title: str | None, body: str | None) -> str | None:
    parts = []
    if title:
        parts.append(title.strip())
    if body:
        # Strip HTML tags from body before NER (common in RSS feeds).
        # bleach.clean with tags=[] removes all markup but leaves entity
        # references (&nbsp;, &ldquo;...) literal — html.unescape decodes
        # those. Collapse internal whitespace (including newlines from
        # block tags) to single spaces.
        clean_body = bleach.clean(body, tags=[], strip=True)
        clean_body = html.unescape(clean_body)
        clean_body = " ".join(clean_body.split())
        parts.append(clean_body)
    if not parts:
        return None
    return " ".join(parts)[:MAX_NER_CHARS]


def _clean_entity(text: str) -> str | None:
    name = " ".join(text.split())
    if len(name) < 2 or name.isdigit():
        return None
    return name


def _get_or_create_entity(
    conn: sqlite3.Connection,
    cache: dict[tuple[str, str], int],
    name: str,
    entity_type: str,
    canonical_name: str | None = None,
) -> tuple[int, bool]:
    """Return (entity_id, created). Cache avoids per-mention SELECTs."""
    key = (name, entity_type)
    if key in cache:
        return cache[key], False

    row = conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
        (name, entity_type),
    ).fetchone()
    if row:
        cache[key] = row["id"]
        return row["id"], False

    cur = conn.execute(
        "INSERT INTO entities (name, entity_type, canonical_name) VALUES (?, ?, ?)",
        (name, entity_type, canonical_name),
    )
    cache[key] = cur.lastrowid
    return cur.lastrowid, True


def extract_entities(
    conn: sqlite3.Connection,
    *,
    model: NerModel | None = None,
    limit: int | None = None,
) -> ExtractResult:
    """Run NER on unprocessed docs; populate entities + document_entities."""
    import gc

    result = ExtractResult()

    # origin exclusion mirrors semantic/embedder.py::NON_PROSE_ORIGINS — needed
    # here too because raw_documents already embedded=1 from before that fix
    # (e.g. legacy GDELT backfills) would otherwise still reach NER (CP-016).
    placeholders = ", ".join("?" for _ in NON_PROSE_ORIGINS)
    sql = f"""
        SELECT id, title, body FROM raw_documents
        WHERE embedded = 1 AND is_duplicate = 0 AND ner_done = 0
          AND (origin IS NULL OR origin NOT IN ({placeholders}))
        ORDER BY id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, NON_PROSE_ORIGINS).fetchall()
    if not rows:
        return result

    _own_model = model is None
    if _own_model:
        model = load_ner_model()

    logger.info(f"NER on {len(rows)} documents")
    entity_cache: dict[tuple[str, str], int] = {}

    for row in rows:
        text = _build_text(row["title"], row["body"])
        if text is None:
            with conn:
                conn.execute(
                    "UPDATE raw_documents SET ner_done = 1 WHERE id = ?",
                    (row["id"],),
                )
            result.docs_skipped += 1
            continue

        doc = model(text)
        counts: Counter[tuple[str, str]] = Counter()
        canonical_names: dict[tuple[str, str], str | None] = {}
        for ent in doc.ents:
            name = _clean_entity(ent.text)
            if name is None:
                continue
            entity_type, canonical_name = _classify(name, ent.label_)
            if entity_type is None:
                continue
            key = (name, entity_type)
            counts[key] += 1
            canonical_names[key] = canonical_name

        with conn:
            for (name, entity_type), n in counts.items():
                entity_id, created = _get_or_create_entity(
                    conn, entity_cache, name, entity_type,
                    canonical_name=canonical_names[(name, entity_type)],
                )
                if created:
                    result.entities_created += 1
                conn.execute(
                    """INSERT INTO document_entities (document_id, entity_id, mentions)
                       VALUES (?, ?, ?)
                       ON CONFLICT(document_id, entity_id)
                       DO UPDATE SET mentions = mentions + excluded.mentions""",
                    (row["id"], entity_id, n),
                )
                result.mentions_recorded += n
            conn.execute(
                "UPDATE raw_documents SET ner_done = 1 WHERE id = ?", (row["id"],)
            )
        result.docs_processed += 1

    if _own_model:
        del model
        gc.collect()

    logger.info(
        f"NER complete: {result.docs_processed} docs, "
        f"+{result.entities_created} entities, {result.mentions_recorded} mentions"
    )
    return result


def backfill_demonym_entities(conn: sqlite3.Connection) -> int:
    """One-time repair: reclassify existing demonym entities (`entity_type`
    != 'location', e.g. spaCy-tagged 'other') to location+canonical_name.

    If a 'location' entity with the same name already exists (created after
    this fix), merges document_entities/entity_links into it and drops the
    duplicate instead of violating the (name, entity_type) unique index.
    Idempotent — running twice updates 0 rows the second time.
    """
    updated = 0
    with conn:
        for demonym, country in DEMONYM_TO_COUNTRY.items():
            row = conn.execute(
                "SELECT id, name FROM entities WHERE lower(name) = ? AND entity_type != 'location'",
                (demonym,),
            ).fetchone()
            if row is None:
                continue
            old_id, name = row["id"], row["name"]

            dup = conn.execute(
                "SELECT id FROM entities WHERE name = ? AND entity_type = 'location'",
                (name,),
            ).fetchone()

            if dup is not None:
                new_id = dup["id"]
                conn.execute(
                    """INSERT INTO document_entities (document_id, entity_id, mentions)
                       SELECT document_id, ?, mentions FROM document_entities WHERE entity_id = ?
                       ON CONFLICT(document_id, entity_id)
                       DO UPDATE SET mentions = mentions + excluded.mentions""",
                    (new_id, old_id),
                )
                conn.execute("DELETE FROM document_entities WHERE entity_id = ?", (old_id,))
                conn.execute("UPDATE entity_links SET entity_a = ? WHERE entity_a = ?", (new_id, old_id))
                conn.execute("UPDATE entity_links SET entity_b = ? WHERE entity_b = ?", (new_id, old_id))
                conn.execute("DELETE FROM entities WHERE id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE entities SET entity_type = 'location', canonical_name = ? WHERE id = ?",
                    (country, old_id),
                )
            updated += 1

    logger.info(f"Demonym backfill: {updated} entities reclassified to location")
    return updated


def _nominatim_lookup(
    client: httpx.Client, query: str, user_agent: str
) -> tuple[float, float, str] | None:
    resp = client.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": user_agent},
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json()
    if not hits:
        return None
    h = hits[0]
    return float(h["lat"]), float(h["lon"]), h.get("display_name", "")


def geocode_events(
    conn: sqlite3.Connection,
    *,
    client: httpx.Client | None = None,
    user_agent: str = "pathosphere/0.1",
    max_lookups: int = 50,
    delay_s: float = NOMINATIM_DELAY_S,
) -> GeocodeResult:
    """Fill lat/lon on events from location_name via Nominatim (cached)."""
    result = GeocodeResult()

    rows = conn.execute(
        """SELECT id, location_name FROM events
           WHERE location_name IS NOT NULL AND location_name != '' AND lat IS NULL
           ORDER BY id"""
    ).fetchall()
    if not rows:
        return result

    _own_client = client is None
    if _own_client:
        client = httpx.Client()

    logger.info(f"Geocoding {len(rows)} events (max {max_lookups} network lookups)")

    try:
        for row in rows:
            query = row["location_name"].strip()

            cached = conn.execute(
                "SELECT lat, lon FROM geocode_cache WHERE query = ?", (query,)
            ).fetchone()

            if cached:
                result.cache_hits += 1
                lat, lon = cached["lat"], cached["lon"]
            else:
                if result.lookups >= max_lookups:
                    continue
                result.lookups += 1
                try:
                    hit = _nominatim_lookup(client, query, user_agent)
                except Exception as exc:
                    logger.warning(f"Nominatim failed for {query!r}: {exc}")
                    continue
                lat, lon, display = (None, None, None) if hit is None else hit
                with conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO geocode_cache
                           (query, lat, lon, display_name) VALUES (?, ?, ?, ?)""",
                        (query, lat, lon, display),
                    )
                if delay_s:
                    time.sleep(delay_s)

            if lat is None:
                result.misses += 1
                continue

            with conn:
                conn.execute(
                    "UPDATE events SET lat = ?, lon = ? WHERE id = ?",
                    (lat, lon, row["id"]),
                )
            result.events_geocoded += 1
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"Geocode complete: {result.events_geocoded} events, "
        f"{result.lookups} lookups, {result.cache_hits} cache hits, "
        f"{result.misses} misses"
    )
    return result


def _wikidata_search(
    client: httpx.Client, name: str, user_agent: str
) -> tuple[str, str, list[str]] | None:
    """Return (qid, canonical_label, aliases) for best match, or None."""
    resp = client.get(
        WIKIDATA_URL,
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "uselang": "en",
            "format": "json",
            "limit": 1,
        },
        headers={"User-Agent": user_agent},
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("search", [])
    if not hits:
        return None
    h = hits[0]
    return h["id"], h.get("label", name), h.get("aliases", [])


def link_wikidata(
    conn: sqlite3.Connection,
    *,
    client: httpx.Client | None = None,
    user_agent: str = "pathosphere/0.1",
    max_lookups: int = 50,
    delay_s: float = WIKIDATA_DELAY_S,
) -> WikidataResult:
    """Resolve Wikidata QIDs for entities not yet checked.

    Prioritises most-mentioned entities so the lookup budget goes to
    entities that matter for the graph.
    """
    result = WikidataResult()

    # Retire generic names before spending the lookup budget on them; also
    # strips wrong QIDs assigned before the stoplist existed (PRESIDENT → …).
    placeholders = ",".join("?" * len(GENERIC_ENTITY_STOPLIST))
    with conn:
        cur = conn.execute(
            f"""UPDATE entities
                SET wikidata_checked = 1, wikidata_qid = NULL,
                    canonical_name = NULL, aliases = NULL
                WHERE (wikidata_checked = 0 OR wikidata_qid IS NOT NULL)
                  AND lower(name) IN ({placeholders})""",
            tuple(GENERIC_ENTITY_STOPLIST),
        )
    result.stoplisted = cur.rowcount
    if result.stoplisted:
        logger.info(f"Stoplisted {result.stoplisted} generic entities")

    rows = conn.execute(
        """SELECT e.id, e.name
           FROM entities e
           LEFT JOIN document_entities de ON de.entity_id = e.id
           WHERE e.wikidata_checked = 0 AND e.wikidata_qid IS NULL
           GROUP BY e.id
           ORDER BY COALESCE(SUM(de.mentions), 0) DESC
           LIMIT ?""",
        (max_lookups,),
    ).fetchall()
    if not rows:
        return result

    _own_client = client is None
    if _own_client:
        client = httpx.Client()

    logger.info(f"Wikidata linking for {len(rows)} entities")

    try:
        for i, row in enumerate(rows):
            if delay_s and i:
                time.sleep(delay_s)
            try:
                hit = _wikidata_search(client, row["name"], user_agent)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    # Hammering past a 429 only digs the hole deeper; the
                    # unchecked rows stay queued for the next cycle.
                    logger.warning(
                        f"Wikidata rate limited (429) at {row['name']!r} — "
                        f"aborting run, remaining entities retried next cycle"
                    )
                    result.rate_limited = True
                    break
                logger.warning(f"Wikidata failed for {row['name']!r}: {exc}")
                continue
            except Exception as exc:
                logger.warning(f"Wikidata failed for {row['name']!r}: {exc}")
                continue

            result.entities_checked += 1
            if hit is None:
                with conn:
                    conn.execute(
                        "UPDATE entities SET wikidata_checked = 1 WHERE id = ?",
                        (row["id"],),
                    )
            else:
                qid, label, aliases = hit
                try:
                    with conn:
                        conn.execute(
                            """UPDATE entities
                               SET wikidata_qid = ?, canonical_name = ?,
                                   aliases = ?, wikidata_checked = 1
                               WHERE id = ?""",
                            (qid, label, json.dumps(aliases), row["id"]),
                        )
                    result.qids_found += 1
                except sqlite3.IntegrityError:
                    # QID already owned by another entity row (e.g. "TSMC" vs
                    # "台積電"). Mark current entity as alias of the earlier one.
                    canonical = conn.execute(
                        "SELECT id FROM entities WHERE wikidata_qid = ?", (qid,)
                    ).fetchone()
                    if canonical is not None:
                        with conn:
                            conn.execute(
                                "UPDATE entities SET canonical_entity_id = ?, wikidata_checked = 1 WHERE id = ?",
                                (canonical["id"], row["id"]),
                            )
                    result.conflicts += 1
                    with conn:
                        conn.execute(
                            "UPDATE entities SET wikidata_checked = 1 WHERE id = ?",
                            (row["id"],),
                        )
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"Wikidata complete: {result.entities_checked} checked, "
        f"{result.qids_found} QIDs, {result.conflicts} conflicts, "
        f"{result.stoplisted} stoplisted"
        + (", rate limited" if result.rate_limited else "")
    )
    return result
