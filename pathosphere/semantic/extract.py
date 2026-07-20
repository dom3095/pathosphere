"""
Entity extraction — Phase 2 (NER + geocoding + Wikidata linking).

Independent, resumable steps:
  1. extract_entities — spaCy multilingual NER (xx_ent_wiki_sm) on docs with
     embedded=1, is_duplicate=0, ner_done=0 → entities + document_entities
  2. geolocate_rss_events — CP-022 heuristic: derives events.location_name
     for origin='rss' events (only geo-native ingestors wrote it before).
     Free, instant, no network — always run before geocode_events.
  3. geocode_events   — Nominatim lookup for events with location_name and
     lat NULL; 1 req/s, hits AND misses cached in geocode_cache
  4. link_wikidata    — wbsearchentities QID lookup for entities not yet
     checked → wikidata_qid + canonical_name
  5. geolocate_ambiguous_events_qwen — CP-022 Qwen3 4B local fallback for the
     'ambiguous' cases step 2 can't resolve. Slow (network+LLM per event) —
     explicit opt-in batch, never run implicitly from extract_entities'ish flow.

Memory: xx_ent_wiki_sm is ~30 MB; loaded only inside this phase, unloaded after.
"""

import html
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import bleach
import httpx
from loguru import logger

from pathosphere.semantic.embedder import NON_PROSE_ORIGINS

if TYPE_CHECKING:
    from pathosphere.llm.client import LLMClient

NER_MODEL_NAME = "xx_ent_wiki_sm"
MAX_NER_CHARS = 2000  # title + body head; bounds CPU per doc

# spaCy label → entities.entity_type
LABEL_MAP = {
    "PER": "person",
    "ORG": "company",
    "LOC": "location",
    "MISC": "other",
}

# Intergovernmental organizations/alliances spaCy tags ORG (→ "company" via
# LABEL_MAP) — misleading in a graph/dashboard (NATO, EU shown as companies).
# Matched case-insensitively, exact full-name match only.
INTERGOVERNMENTAL_ORGS: dict[str, str] = {
    "eu": "European Union", "european union": "European Union",
    "nato": "NATO", "un": "United Nations", "united nations": "United Nations",
    "who": "World Health Organization",
    "world health organization": "World Health Organization",
    "imf": "International Monetary Fund",
    "international monetary fund": "International Monetary Fund",
    "world bank": "World Bank", "wto": "World Trade Organization",
    "world trade organization": "World Trade Organization",
    "opec": "OPEC", "g7": "G7", "g20": "G20", "asean": "ASEAN",
    "african union": "African Union", "au": "African Union",
    "arab league": "Arab League", "brics": "BRICS",
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


# Non-grammatical aliases/abbreviations for a country that aren't demonyms
# (so DEMONYM_TO_COUNTRY doesn't already catch them) — e.g. "UK"/"Britain"/
# "England" for the United Kingdom. Feeds both _classify (for NER labelled
# LOC/other) and canonicalize_location_entities (cross-entity dedup below).
LOCATION_ALIAS_TO_COUNTRY: dict[str, str] = {
    "uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
    "great britain": "United Kingdom",
    "us": "United States", "usa": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "america": "United States",
    "uae": "United Arab Emirates",
    "prc": "China", "mainland china": "China",
    "rok": "South Korea", "dprk": "North Korea", "roc": "Taiwan",
    # Continents — same class of bug as country demonyms (noun tagged one
    # way, adjective another, neither merged with the other): "European" was
    # found stuck as entity_type='other' (not a location at all, invisible to
    # canonicalize_location_entities) while "Europe" itself had picked up a
    # wrong Wikidata match ("Europe PubMed Central", a literature database —
    # the same fuzzy-search collision class as CP-019, just on a new word).
    "europe": "Europe", "european": "Europe",
    "asia": "Asia", "asian": "Asia",
    "africa": "Africa", "african": "Africa",
}

# Boilerplate/UI noise (video players, "read more" widgets...) that spaCy
# occasionally mistags as an entity (usually ORG from ALL-CAPS text) — never
# a meaningful entity, so excluded at creation time rather than merely
# skipped for Wikidata linking like GENERIC_ENTITY_STOPLIST above.
NOISE_ENTITY_STOPLIST = frozenset({
    "video", "videos", "watch", "photo", "photos", "gallery", "image",
    "images", "live", "click", "read more", "share", "subscribe",
    "breaking", "update", "updates", "exclusive",
})

# Common English words that are ALSO a country/place name — a classic
# Wikidata disambiguation trap where wbsearchentities can return the *other*
# sense as the top hit (found via audit after CP-019: Turkey/bird,
# Jordan/river or the basketball player, Chad/personal name, Guinea/rodent,
# Georgia/US state, Congo/river, Mali, Niger, Jersey/clothing or NJ). None
# were corrupted yet at audit time (all wikidata_qid IS NULL) but were next
# in the unchecked queue — link_wikidata verifies these against P31 (see
# WIKIDATA_TYPE_HINTS) before accepting a match, instead of trusting the
# search result blindly like it does for unambiguous names.
AMBIGUOUS_ENTITY_NAMES = frozenset({
    "turkey", "georgia", "jordan", "chad", "guinea", "niger", "congo",
    "mali", "jersey",
})


def _classify(name: str, spacy_label: str) -> tuple[str, str | None]:
    """(entity_type, canonical_name) for a cleaned entity name.

    Falls back to the spaCy label mapping; overrides to location+country for
    known demonyms/aliases and to organization for known intergovernmental
    bodies, regardless of what spaCy tagged them as.
    """
    lname = name.lower()
    country = DEMONYM_TO_COUNTRY.get(lname) or LOCATION_ALIAS_TO_COUNTRY.get(lname)
    if country is not None:
        return "location", country
    org = INTERGOVERNMENTAL_ORGS.get(lname)
    if org is not None:
        return "organization", org
    return LABEL_MAP.get(spacy_label), None

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_S = 1.1  # usage policy: max 1 req/s
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_DELAY_S = 1.0  # Wikimedia anonymous rate limit: stay ~1 req/s

# CP-022 (RSS event geolocation): how many top country/location entities
# (by distinct-document count) count as "major powers" for the heuristic in
# geolocate_rss_events(). Data-driven, not a hand-written list — recomputed
# every run from the live corpus (study_19: natural step-down in the
# distribution after the 8th country in the current corpus; India=88 docs to
# Europe=71 drops off a "continent/bloc, not a state" cliff). NOTE: this is
# not a stable list over time — as the corpus grows a country can enter or
# leave the top-N, which can flip a past `located`/`ambiguous` classification
# on re-run. See CP-022 in CRITICAL_POINTS.md.
MAJOR_POWERS_TOP_N = 8

# Qwen prompt for the ambiguous-case fallback (study_19, validated on the 2
# real cases that motivated CP-022). Title-only — no document body needed.
_GEOLOC_PROMPT_TMPL = """Analizza questo titolo di notizia geopolitica. Identifica:
- location_country: il SINGOLO paese che è il bersaglio/luogo dell'evento (dove succede la cosa
  principale). null se la notizia descrive una relazione tra grandi potenze senza un bersaglio
  geografico specifico (es. trattativa USA-Iran, tensione USA-Cina).
- actor_countries: paesi che agiscono/decidono/dichiarano (i soggetti attivi).
- via_countries: paesi che sono solo strumento/tramite/mediatore, non il bersaglio.

Esempio: "US pressures Cuba via Venezuela oil routes" ->
{{"location_country": "Cuba", "actor_countries": ["United States"], "via_countries": ["Venezuela"]}}

Esempio: "No final agreement on deal with US – Iran" ->
{{"location_country": null, "actor_countries": ["United States", "Iran"], "via_countries": []}}

Titolo: "{title}"

Rispondi SOLO con il JSON: {{"location_country": ..., "actor_countries": [...], "via_countries": [...]}}"""

_GEOLOC_SCHEMA = {
    "type": "object",
    "properties": {
        "location_country": {"type": ["string", "null"]},
        "actor_countries": {"type": "array", "items": {"type": "string"}},
        "via_countries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["location_country", "actor_countries", "via_countries"],
}

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

# Names already classified via a curated table (DEMONYM_TO_COUNTRY,
# LOCATION_ALIAS_TO_COUNTRY, INTERGOVERNMENTAL_ORGS) carry a higher-confidence
# canonical_name than a Wikidata search can offer, and looking them up is
# actively dangerous: found empirically on production data — wbsearchentities
# for the 2-letter query "UK" returned Q8798, the *Ukrainian language* entity
# (matched via its ISO 639 code "uk", unrelated to the United Kingdom),
# silently aliasing the "UK" location entity (58 mentions) to a language
# node. The same class of collision threatens any demonym that is also a
# language name (french/russian/german/... -> the language, not the country).
# Unlike GENERIC_ENTITY_STOPLIST (no meaningful canonical_name to begin with),
# these DO have a correct curated label — the lookup is skipped but the label
# is kept/forced, not wiped to NULL.
CURATED_ALIAS_TO_LABEL: dict[str, str] = {
    **DEMONYM_TO_COUNTRY,
    **LOCATION_ALIAS_TO_COUNTRY,
    **INTERGOVERNMENTAL_ORGS,
}


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
            if name is None or name.lower() in NOISE_ENTITY_STOPLIST:
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
    """One-time repair: reclassify existing demonym/alias entities
    (`entity_type` != 'location', e.g. spaCy-tagged 'other' or mistagged
    'company') to location+canonical_name. Covers both DEMONYM_TO_COUNTRY
    (adjectival forms: "Chinese") and LOCATION_ALIAS_TO_COUNTRY (acronyms/
    continents: "UK", "European") — same bug class, same fix, one pass.

    If a 'location' entity with the same name already exists (created after
    this fix), merges document_entities/entity_links into it and drops the
    duplicate instead of violating the (name, entity_type) unique index —
    and forces the survivor's canonical_name to the curated value (it may
    hold a stale/wrong one, e.g. from a bad Wikidata match). Idempotent —
    running twice updates 0 rows the second time.
    """
    updated = 0
    with conn:
        for demonym, country in {**DEMONYM_TO_COUNTRY, **LOCATION_ALIAS_TO_COUNTRY}.items():
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
                conn.execute("UPDATE entities SET canonical_name = ? WHERE id = ?", (country, new_id))
            else:
                conn.execute(
                    "UPDATE entities SET entity_type = 'location', canonical_name = ? WHERE id = ?",
                    (country, old_id),
                )
            updated += 1

    logger.info(f"Demonym backfill: {updated} entities reclassified to location")
    return updated


def backfill_organization_entities(conn: sqlite3.Connection) -> int:
    """One-time repair: reclassify existing intergovernmental-org entities
    (created before INTERGOVERNMENTAL_ORGS existed, typically entity_type=
    'company' via the ORG->company LABEL_MAP default) to entity_type=
    'organization' + canonical_name. Same merge-on-collision logic as
    backfill_demonym_entities. Idempotent."""
    updated = 0
    with conn:
        for name_key, canonical in INTERGOVERNMENTAL_ORGS.items():
            row = conn.execute(
                "SELECT id, name FROM entities WHERE lower(name) = ? AND entity_type != 'organization'",
                (name_key,),
            ).fetchone()
            if row is None:
                continue
            old_id, name = row["id"], row["name"]

            dup = conn.execute(
                "SELECT id FROM entities WHERE name = ? AND entity_type = 'organization'",
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
                    "UPDATE entities SET entity_type = 'organization', canonical_name = ? WHERE id = ?",
                    (canonical, old_id),
                )
            updated += 1

    logger.info(f"Organization backfill: {updated} entities reclassified to organization")
    return updated


def purge_noise_entities(conn: sqlite3.Connection) -> int:
    """One-time repair: delete entities matching NOISE_ENTITY_STOPLIST created
    before that stoplist existed (extract_entities now excludes them at
    creation time). Removes dependent document_entities/entity_links rows
    first. Idempotent."""
    placeholders = ",".join("?" * len(NOISE_ENTITY_STOPLIST))
    with conn:
        ids = [
            r["id"] for r in conn.execute(
                f"SELECT id FROM entities WHERE lower(name) IN ({placeholders})",
                tuple(NOISE_ENTITY_STOPLIST),
            ).fetchall()
        ]
        if not ids:
            return 0
        id_placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM document_entities WHERE entity_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM entity_links WHERE entity_a IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM entity_links WHERE entity_b IN ({id_placeholders})", ids)
        conn.execute(f"UPDATE entities SET canonical_entity_id = NULL WHERE canonical_entity_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM entities WHERE id IN ({id_placeholders})", ids)

    logger.info(f"Noise purge: {len(ids)} entities deleted")
    return len(ids)


# Honorifics/titles NER leaves attached to person names, causing the same
# real person to surface as several distinct entity rows ("Khamenei",
# "Ali Khamenei", "Ayatollah Ali Khamenei", "Ayatollah Khamenei"). Sorted
# longest-first so multi-word titles are matched before their sub-strings
# ("grand ayatollah" before "ayatollah").
PERSON_HONORIFICS: list[str] = sorted(
    {
        "grand ayatollah", "ayatollah", "prime minister", "field marshal",
        "grand mufti", "sheikh", "sheikha", "imam", "president", "general",
        "governor", "chancellor", "cardinal", "archbishop", "bishop",
        "colonel", "major", "captain", "admiral", "king", "queen", "prince",
        "princess", "sultan", "emir", "senator", "pope", "dr.", "dr", "mr.",
        "mr", "mrs.", "mrs", "ms.", "ms",
    },
    key=len,
    reverse=True,
)

# Ambiguous bare-surname merges only proceed if the top mention-count
# candidate beats the runner-up by at least this ratio (e.g. "Khamenei"
# overwhelmingly means Ali Khamenei in the news, not his son Mojtaba) —
# otherwise the merge is skipped as genuinely ambiguous.
BARE_SURNAME_DOMINANCE_RATIO = 3.0


def _strip_person_honorifics(name: str) -> str:
    """Repeatedly strip leading honorifics/titles from a person name."""
    working = name.strip()
    changed = True
    while changed:
        changed = False
        lower = working.lower()
        for honorific in PERSON_HONORIFICS:
            if lower.startswith(honorific + " "):
                working = working[len(honorific):].strip()
                changed = True
                break
    return working


@dataclass
class PersonCanonicalizeResult:
    exact_groups_merged: int = 0     # multi-token honorific-stripped matches
    bare_surname_merged: int = 0     # single-token surname, unambiguous or dominant
    bare_surname_skipped: int = 0    # single-token surname, genuinely ambiguous


def canonicalize_person_entities(conn: sqlite3.Connection) -> PersonCanonicalizeResult:
    """Point duplicate person-entity name variants at one canonical row.

    Non-destructive: sets canonical_entity_id (same pointer convention as
    Wikidata-QID alias resolution, resolved via COALESCE in graph.py) rather
    than deleting rows. Two passes:

    1. Honorific-stripped exact match on multi-token names ("Ali Khamenei" ==
       "Ayatollah Ali Khamenei" stripped == "Ayatollah Ali Khamenei" stripped)
       — safe, no cross-person ambiguity possible.
    2. Bare single-token surnames ("Khamenei" from "Ayatollah Khamenei") are
       merged into a multi-token group sharing that surname only if there is
       exactly one candidate, or one dominates the others by
       BARE_SURNAME_DOMINANCE_RATIO in total mentions — otherwise left
       unmerged (e.g. "Khamenei" alone is ambiguous between Ali Khamenei and
       his son Mojtaba Khamenei; a wrong merge is worse than a missed one).
    """
    result = PersonCanonicalizeResult()

    rows = conn.execute(
        """SELECT e.id, e.name,
                  COALESCE(SUM(de.mentions), 0) AS total_mentions
           FROM entities e
           LEFT JOIN document_entities de ON de.entity_id = e.id
           WHERE e.entity_type = 'person' AND e.canonical_entity_id IS NULL
           GROUP BY e.id"""
    ).fetchall()
    if not rows:
        return result

    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        stripped = _strip_person_honorifics(row["name"])
        key = stripped.lower()
        groups.setdefault(key, []).append(row)

    # Pass 1: multi-token exact matches (unambiguous — different given names
    # never collapse to the same stripped key).
    multi_token_canonical: dict[str, int] = {}  # key -> canonical entity id
    with conn:
        for key, members in groups.items():
            if len(key.split()) < 2 or len(members) < 2:
                continue
            canonical = max(members, key=lambda r: r["total_mentions"])
            multi_token_canonical[key] = canonical["id"]
            for m in members:
                if m["id"] == canonical["id"]:
                    continue
                conn.execute(
                    "UPDATE entities SET canonical_entity_id = ? WHERE id = ?",
                    (canonical["id"], m["id"]),
                )
            result.exact_groups_merged += 1

    # Also register single-member multi-token groups as merge targets for
    # pass 2 (a lone "Ali Khamenei" with no variant is still a valid target
    # for a bare "Khamenei" mention).
    for key, members in groups.items():
        if len(key.split()) >= 2 and key not in multi_token_canonical:
            multi_token_canonical[key] = members[0]["id"]

    # Pass 2: bare single-token surnames — merge only if unambiguous or the
    # dominant candidate clears BARE_SURNAME_DOMINANCE_RATIO.
    surname_candidates: dict[str, list[tuple[str, int]]] = {}  # surname -> [(key, canonical_id)]
    for key, canonical_id in multi_token_canonical.items():
        surname = key.split()[-1]
        surname_candidates.setdefault(surname, []).append((key, canonical_id))

    with conn:
        for key, members in groups.items():
            if len(key.split()) != 1:
                continue
            candidates = surname_candidates.get(key, [])
            if not candidates:
                continue

            if len(candidates) == 1:
                target_id = candidates[0][1]
            else:
                mentions_by_target = [
                    (cid, conn.execute(
                        "SELECT COALESCE(SUM(mentions),0) as m FROM document_entities WHERE entity_id = ?",
                        (cid,),
                    ).fetchone()["m"])
                    for _, cid in candidates
                ]
                mentions_by_target.sort(key=lambda t: t[1], reverse=True)
                top_id, top_m = mentions_by_target[0]
                runner_m = mentions_by_target[1][1] if len(mentions_by_target) > 1 else 0
                if runner_m > 0 and top_m < BARE_SURNAME_DOMINANCE_RATIO * runner_m:
                    result.bare_surname_skipped += len(members)
                    continue
                target_id = top_id

            for m in members:
                conn.execute(
                    "UPDATE entities SET canonical_entity_id = ? WHERE id = ?",
                    (target_id, m["id"]),
                )
            result.bare_surname_merged += len(members)

    logger.info(
        f"Person canonicalization: {result.exact_groups_merged} exact groups, "
        f"{result.bare_surname_merged} bare surnames merged, "
        f"{result.bare_surname_skipped} bare surnames skipped (ambiguous)"
    )
    return result


@dataclass
class LocationCanonicalizeResult:
    groups_merged: int = 0
    entities_merged: int = 0


# Lowercased lookup for the *targets* of the demonym/alias dicts (e.g.
# "china", "united kingdom", "europe") — lets a country/continent's own
# literal-name entity ("China") join the same group as its demonym variants
# ("Chinese") even when nothing has set its canonical_name yet, or when
# canonical_name holds Wikidata's full official form ("People's Republic of
# China") rather than the short form used here. Without this, only the
# demonym side of a pair ever resolved a key — found empirically: "China"
# and "Chinese" stayed two separate nodes because "China" alone never
# matched anything (class of bug, not specific to China/Chinese).
_KNOWN_PLACE_VALUES_LOWER: dict[str, str] = {
    v.lower(): v for v in set(DEMONYM_TO_COUNTRY.values()) | set(LOCATION_ALIAS_TO_COUNTRY.values())
}


def _location_country_key(name: str, canonical_name: str | None) -> str | None:
    """Country/continent string a location entity refers to, or None if it
    isn't a known demonym/alias (city/region names are left alone — only
    entities this module already knows how to map are grouped)."""
    lname = name.lower()
    key = LOCATION_ALIAS_TO_COUNTRY.get(lname) or DEMONYM_TO_COUNTRY.get(lname)
    if key is not None:
        return key
    if lname in _KNOWN_PLACE_VALUES_LOWER:
        return _KNOWN_PLACE_VALUES_LOWER[lname]
    if canonical_name and canonical_name.lower() in _KNOWN_PLACE_VALUES_LOWER:
        return _KNOWN_PLACE_VALUES_LOWER[canonical_name.lower()]
    return None


def canonicalize_location_entities(conn: sqlite3.Connection) -> LocationCanonicalizeResult:
    """Point duplicate location-entity variants referring to the same country
    at one canonical row (CP-018 #3) — e.g. England/English/British/Britain/
    UK all collapse to one "United Kingdom" node.

    Non-destructive: sets canonical_entity_id, same convention as
    canonicalize_person_entities. Only entities recognised as a demonym or
    known alias (LOCATION_ALIAS_TO_COUNTRY/DEMONYM_TO_COUNTRY) or already
    carrying a matching canonical_name are grouped — plain city/region names
    are left untouched.
    """
    result = LocationCanonicalizeResult()

    rows = conn.execute(
        """SELECT e.id, e.name, e.canonical_name,
                  COALESCE(SUM(de.mentions), 0) AS total_mentions
           FROM entities e
           LEFT JOIN document_entities de ON de.entity_id = e.id
           WHERE e.entity_type = 'location' AND e.canonical_entity_id IS NULL
           GROUP BY e.id"""
    ).fetchall()
    if not rows:
        return result

    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        key = _location_country_key(row["name"], row["canonical_name"])
        if key is None:
            continue
        groups.setdefault(key, []).append(row)

    with conn:
        for country, members in groups.items():
            if len(members) < 2:
                continue
            canonical = max(members, key=lambda r: r["total_mentions"])
            for m in members:
                if m["id"] == canonical["id"]:
                    continue
                conn.execute(
                    "UPDATE entities SET canonical_entity_id = ? WHERE id = ?",
                    (canonical["id"], m["id"]),
                )
                result.entities_merged += 1
            if canonical["canonical_name"] != country:
                conn.execute(
                    "UPDATE entities SET canonical_name = ? WHERE id = ?",
                    (country, canonical["id"]),
                )
            result.groups_merged += 1

    logger.info(
        f"Location canonicalization: {result.groups_merged} groups, "
        f"{result.entities_merged} entities merged"
    )
    return result


@dataclass
class HeuristicResult:
    decision: str  # 'located' | 'skip_bilateral' | 'skip_none' | 'ambiguous'
    location_country: str | None
    reason: str


@dataclass
class HeuristicGeolocateResult:
    events_evaluated: int = 0   # RSS events with >=1 location/country entity examined
    located: int = 0            # location_name written
    skip_bilateral: int = 0     # only major powers mentioned — no anchor
    skip_none: int = 0          # no location/country entity at all
    ambiguous: int = 0          # left for the Qwen fallback (geolocate_ambiguous_events_qwen)
    major_powers: list[str] = field(default_factory=list)  # computed set, for logging/audit


def compute_major_powers(conn: sqlite3.Connection, top_n: int = MAJOR_POWERS_TOP_N) -> set[str]:
    """Data-driven "major power" set — the top-N country/location entities by
    distinct-document count in the whole corpus (not just RSS). A country
    that dominates the corpus is almost always commentator/actor, not the
    specific target of any one story (study_19, section 3)."""
    rows = conn.execute(
        """SELECT COALESCE(en.canonical_name, en.name) AS country,
                  COUNT(DISTINCT de.document_id) AS n_docs
           FROM document_entities de
           JOIN entities en ON en.id = de.entity_id
           WHERE en.entity_type IN ('location', 'country') AND en.canonical_entity_id IS NULL
           GROUP BY country ORDER BY n_docs DESC LIMIT ?""",
        (top_n,),
    ).fetchall()
    return {row["country"] for row in rows}


def _classify_heuristic(countries: list[str], major_powers: set[str]) -> HeuristicResult:
    """Actor/target role heuristic for a single event's mentioned countries
    (study_19, section 4 — reference implementation, reused verbatim)."""
    uniq = list(dict.fromkeys(countries))  # preserve order, dedup
    majors = [c for c in uniq if c in major_powers]
    minors = [c for c in uniq if c not in major_powers]

    if not uniq:
        return HeuristicResult("skip_none", None, "nessuna entità location")
    if len(uniq) == 1:
        return HeuristicResult("located", uniq[0], "unico paese menzionato")
    if not minors:
        return HeuristicResult("skip_bilateral", None, f"solo grandi potenze: {majors}")
    if len(majors) <= 1 and len(minors) == 1:
        return HeuristicResult(
            "located", minors[0], f"1 major ({majors}) + 1 minor → minor = bersaglio"
        )
    return HeuristicResult(
        "ambiguous", None, f"majors={majors} minors={minors} — bersaglio vs mezzo non distinguibile"
    )


def _rss_event_countries(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """event_id → ordered list of country/location entity names mentioned in
    its cluster's documents (with repeats — classify_heuristic dedups), for
    RSS events still missing location_name (study_19, section 2).

    Resolves aliases to their canonical entity before reading the display
    name — same `COALESCE(canonical_entity_id, id)` idiom as
    `semantic/graph.py::build_entity_links`. Reading `en.canonical_name`
    directly on a possibly-aliased row (the previous approach) is wrong: only
    the canonical row's own `canonical_name`/`name` is kept in sync by
    `canonicalize_location_entities`, not every alias pointing to it — found
    on real data (entity 'turkey' lowercase, alias of canonical 'Turkey'),
    where the alias's own name diverges case-sensitively from the canonical
    display name `compute_major_powers` (which already filters to canonical
    rows only) would use for the same country.
    """
    rows = conn.execute(
        """SELECT e.id AS event_id,
                  COALESCE(canon.canonical_name, canon.name) AS country,
                  SUM(de.mentions) AS mentions
           FROM events e
           JOIN event_documents ed ON ed.event_id = e.id
           JOIN document_entities de ON de.document_id = ed.document_id
           JOIN entities en ON en.id = de.entity_id
           JOIN entities canon ON canon.id = COALESCE(en.canonical_entity_id, en.id)
           WHERE e.origin = 'rss' AND e.location_name IS NULL
             AND en.entity_type IN ('location', 'country')
           GROUP BY e.id, country
           ORDER BY e.id"""
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["event_id"], []).append(row["country"])
    return grouped


def geolocate_rss_events(
    conn: sqlite3.Connection,
    *,
    top_n_major_powers: int = MAJOR_POWERS_TOP_N,
    major_powers: set[str] | None = None,
) -> HeuristicGeolocateResult:
    """Step 1 (CP-022) — free, instant, no network. Derive `events.location_name`
    for `origin='rss'` events from the actor/target heuristic validated in
    `notebooks/study_19_rss_event_geolocation.ipynb`.

    Bilateral/multilateral relations between major powers (US-Iran, US-Israel)
    are intentionally left unlocated (skip_bilateral) — they aren't anchored
    to a place. Single-country mentions and "1 major + 1 minor" (major=actor,
    minor=target hypothesis) are written to location_name. Everything else
    (actor-via-target patterns like "US pressures Cuba via Venezuela", 2+
    majors, 2+ minors...) is left `ambiguous` for the Qwen fallback — this
    function never calls the network or an LLM.

    Only writes to events still missing location_name → safe to call on every
    `pathos extract` run (idempotent, re-evaluates nothing already resolved).
    Does not touch geoloc_checked — that column is owned by the Qwen fallback.

    `major_powers`: pass a precomputed set to skip the corpus-wide GROUP BY
    in `compute_major_powers()` — `pathos extract --geolocate-qwen` runs this
    function and `geolocate_ambiguous_events_qwen()` back-to-back in one
    invocation; computing the identical set twice for the same snapshot was
    wasted work on the module's most expensive query.
    """
    result = HeuristicGeolocateResult()
    major_powers = (
        major_powers if major_powers is not None
        else compute_major_powers(conn, top_n=top_n_major_powers)
    )
    result.major_powers = sorted(major_powers)

    all_ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM events WHERE origin = 'rss' AND location_name IS NULL"
        ).fetchall()
    ]
    result.events_evaluated = len(all_ids)
    if not all_ids:
        return result

    grouped = _rss_event_countries(conn)  # only ids with >=1 location/country entity

    with conn:
        for event_id in all_ids:
            r = _classify_heuristic(grouped.get(event_id, []), major_powers)
            if r.decision == "located":
                conn.execute(
                    "UPDATE events SET location_name = ? WHERE id = ?",
                    (r.location_country, event_id),
                )
                result.located += 1
            elif r.decision == "skip_bilateral":
                result.skip_bilateral += 1
            elif r.decision == "skip_none":
                result.skip_none += 1
            else:
                result.ambiguous += 1

    logger.info(
        f"RSS geolocation (heuristic): {result.events_evaluated} events evaluated | "
        f"{result.located} located | {result.ambiguous} ambiguous | "
        f"{result.skip_bilateral} skip_bilateral | {result.skip_none} skip_none | "
        f"major_powers={result.major_powers}"
    )
    return result


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


# Wikidata P31 (instance of) target QIDs mapped to the pathosphere entity_type
# they best correspond to — used only to break a QID conflict (CP-018 #1)
# between two rows disagreeing on entity_type, not for classification at
# extraction time (would need a lookup per entity, budget-prohibitive).
WIKIDATA_TYPE_HINTS: dict[str, str] = {
    "Q6256": "location",       # country
    "Q3624078": "location",    # sovereign state
    "Q515": "location",        # city
    "Q1549591": "location",    # big city
    "Q5119": "location",       # capital
    "Q7275": "location",       # state
    "Q5": "person",            # human
    "Q43229": "organization",  # organization
    "Q7278": "organization",   # political party
    "Q484652": "organization", # international organization
    "Q161726": "organization", # alliance
    "Q748019": "organization", # intergovernmental organization
    "Q4830453": "company",     # business
    "Q783794": "company",      # company
    "Q891723": "company",      # public company
    "Q6881511": "company",     # enterprise
}


def _wikidata_instance_of_hint(
    client: httpx.Client, qid: str, user_agent: str
) -> str | None:
    """Best-effort entity_type guess from a QID's P31 claims, via one of
    WIKIDATA_TYPE_HINTS. Returns None on no match or any lookup failure —
    callers must treat that as "no opinion", not an error."""
    try:
        resp = client.get(
            WIKIDATA_URL,
            params={"action": "wbgetclaims", "entity": qid, "property": "P31", "format": "json"},
            headers={"User-Agent": user_agent},
            timeout=15,
        )
        resp.raise_for_status()
        claims = resp.json().get("claims", {}).get("P31", [])
    except Exception:
        return None
    for claim in claims:
        try:
            target_qid = claim["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        hint = WIKIDATA_TYPE_HINTS.get(target_qid)
        if hint is not None:
            return hint
    return None


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

    # Curated demonym/alias/org names (CP-019): skip the lookup, force the
    # known-correct label, strip any QID a collision assigned before this
    # fix existed. Per-entry (not a single IN(...) query) since each name
    # forces a different label.
    with conn:
        for name_key, label in CURATED_ALIAS_TO_LABEL.items():
            cur2 = conn.execute(
                """UPDATE entities
                   SET wikidata_checked = 1, wikidata_qid = NULL,
                       canonical_name = ?, aliases = NULL
                   WHERE lower(name) = ?
                     AND (wikidata_checked = 0 OR wikidata_qid IS NOT NULL
                          OR canonical_name IS NOT ?)""",
                (label, name_key, label),
            )
            result.stoplisted += cur2.rowcount

    if result.stoplisted:
        logger.info(f"Stoplisted {result.stoplisted} generic/curated-alias entities")

    rows = conn.execute(
        """SELECT e.id, e.name, e.entity_type
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

                if row["name"].lower() in AMBIGUOUS_ENTITY_NAMES:
                    # Classic disambiguation trap (Turkey/bird, Jordan/river,
                    # Chad/personal name...) — verify P31 before trusting the
                    # search result, instead of accepting it blindly.
                    hint = _wikidata_instance_of_hint(client, qid, user_agent)
                    if hint is not None and hint != row["entity_type"]:
                        logger.warning(
                            f"Wikidata match for ambiguous name {row['name']!r} "
                            f"rejected: got {qid} ({hint}), expected {row['entity_type']}"
                        )
                        with conn:
                            conn.execute(
                                "UPDATE entities SET wikidata_checked = 1 WHERE id = ?",
                                (row["id"],),
                            )
                        continue

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
                    # "台積電"). Default: mark current entity as alias of the
                    # earlier one ("first to arrive wins"). But if the two
                    # disagree on entity_type (CP-018 #1 — e.g. ALL-CAPS
                    # "FRANCE" mistyped "company" got the QID before the
                    # correctly-typed "France" location), that default
                    # silently propagates the wrong type downstream. Ask
                    # Wikidata's P31 which type is actually correct and swap
                    # canonical ownership to whichever row already has it.
                    canonical = conn.execute(
                        "SELECT id, entity_type FROM entities WHERE wikidata_qid = ?", (qid,)
                    ).fetchone()
                    if canonical is not None:
                        winner_id, loser_id = canonical["id"], row["id"]
                        if canonical["entity_type"] != row["entity_type"]:
                            hint = _wikidata_instance_of_hint(client, qid, user_agent)
                            if hint == row["entity_type"] and hint != canonical["entity_type"]:
                                winner_id, loser_id = row["id"], canonical["id"]
                                with conn:
                                    conn.execute(
                                        """UPDATE entities SET wikidata_qid = NULL,
                                           canonical_name = NULL, aliases = NULL
                                           WHERE id = ?""",
                                        (loser_id,),
                                    )
                                    conn.execute(
                                        """UPDATE entities SET wikidata_qid = ?,
                                           canonical_name = ?, aliases = ?
                                           WHERE id = ?""",
                                        (qid, label, json.dumps(aliases), winner_id),
                                    )
                                    # any prior aliases of the old canonical
                                    # follow the QID to the new one.
                                    conn.execute(
                                        """UPDATE entities SET canonical_entity_id = ?
                                           WHERE canonical_entity_id = ? AND id != ?""",
                                        (winner_id, loser_id, winner_id),
                                    )
                        with conn:
                            conn.execute(
                                "UPDATE entities SET canonical_entity_id = ?, wikidata_checked = 1 WHERE id = ?",
                                (winner_id, loser_id),
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


def repair_wikidata_type_conflicts(
    conn: sqlite3.Connection,
    *,
    client: httpx.Client | None = None,
    user_agent: str = "pathosphere/0.1",
    delay_s: float = WIKIDATA_DELAY_S,
) -> int:
    """One-time repair for QID conflicts resolved before link_wikidata was
    type-aware (CP-018 #1) — e.g. "FRANCE" (entity_type=company) got QID Q142
    first and "France" (entity_type=location, correct) became its alias.
    Finds canonical entities whose aliases disagree on entity_type, asks
    Wikidata's P31 which type is right, and swaps canonical ownership to the
    correctly-typed row when it differs. Network-bound but rare (only actual
    conflicts) — safe to re-run, no-ops once every mismatch is settled."""
    fixed = 0
    rows = conn.execute(
        """SELECT DISTINCT c.id AS canonical_id, c.entity_type AS canonical_type,
                  c.wikidata_qid AS qid, c.canonical_name, c.aliases
           FROM entities c
           JOIN entities a ON a.canonical_entity_id = c.id
           WHERE c.wikidata_qid IS NOT NULL AND a.entity_type != c.entity_type"""
    ).fetchall()
    if not rows:
        return fixed

    _own_client = client is None
    if _own_client:
        client = httpx.Client()

    try:
        for i, row in enumerate(rows):
            if delay_s and i:
                time.sleep(delay_s)
            hint = _wikidata_instance_of_hint(client, row["qid"], user_agent)
            if hint is None or hint == row["canonical_type"]:
                continue
            alias = conn.execute(
                """SELECT id FROM entities WHERE canonical_entity_id = ? AND entity_type = ?
                   ORDER BY id LIMIT 1""",
                (row["canonical_id"], hint),
            ).fetchone()
            if alias is None:
                continue
            new_id, old_id = alias["id"], row["canonical_id"]
            with conn:
                conn.execute(
                    """UPDATE entities SET wikidata_qid = NULL, canonical_name = NULL,
                       aliases = NULL, canonical_entity_id = ? WHERE id = ?""",
                    (new_id, old_id),
                )
                conn.execute(
                    """UPDATE entities SET wikidata_qid = ?, canonical_name = ?,
                       aliases = ?, canonical_entity_id = NULL WHERE id = ?""",
                    (row["qid"], row["canonical_name"], row["aliases"], new_id),
                )
                conn.execute(
                    """UPDATE entities SET canonical_entity_id = ?
                       WHERE canonical_entity_id = ? AND id != ?""",
                    (new_id, old_id, new_id),
                )
            fixed += 1
    finally:
        if _own_client:
            client.close()

    logger.info(f"Wikidata type-conflict repair: {fixed} canonical swaps")
    return fixed


@dataclass
class QwenGeolocateResult:
    events_scanned: int = 0        # candidate events (location_name NULL, geoloc_checked=0)
    resolved_by_heuristic: int = 0  # re-classified located/skip on this pass, no LLM call spent
    qwen_calls: int = 0            # LLM calls actually made (bounded by `limit`)
    qwen_located: int = 0          # LLM returned a location_country
    qwen_no_location: int = 0      # LLM confirmed no anchor (bilateral/relational)
    qwen_errors: int = 0           # call/parse failed — event left unchecked, retried next batch
    major_powers: list[str] = field(default_factory=list)


def _parse_qwen_geoloc_response(raw: str) -> str | None:
    """Parse the Qwen JSON response; return location_country or None.
    Raises ValueError on malformed JSON / unexpected shape (caller treats
    that the same as a network failure — retried next batch, not permanent)."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object, got {type(data).__name__}")
    loc = data.get("location_country")
    if loc is None:
        return None
    if not isinstance(loc, str) or not loc.strip():
        raise ValueError(f"unexpected location_country value: {loc!r}")
    return loc.strip()


async def geolocate_ambiguous_events_qwen(
    conn: sqlite3.Connection,
    llm_client: "LLMClient",
    *,
    limit: int = 20,
    top_n_major_powers: int = MAJOR_POWERS_TOP_N,
    major_powers: set[str] | None = None,
) -> QwenGeolocateResult:
    """Step 2 (CP-022) — Qwen3 4B local fallback for RSS events the free
    heuristic (geolocate_rss_events) left `ambiguous` (actor-via-target
    patterns like "US pressures Cuba via Venezuela", 2+ major powers plus
    minors...). NOT wired into `pathos extract`'s default flow: 46.7s/call
    measured on an idle M1 8GB (study_19 measured 90-113s under memory
    pressure from Jupyter+Ollama+IDE together — better idle, still slow).
    ~1000 ambiguous events on the current corpus would take hours in series.
    Explicit opt-in batch only — `pathos extract --geolocate-qwen [--limit N]`
    — small default limit, resumable across runs, same shape as the rest of
    this module's network-bound steps.

    Only the event title is sent to the model (study_19 prompt template,
    validated on the 2 real cases that motivated CP-022 — that's 2 samples,
    not a statistical validation, see CRITICAL_POINTS.md CP-022).

    Resumability: an event is marked geoloc_checked=1 once *examined*,
    whether or not a location was found — "bilateral, no anchor" is a real
    answer and must not be retried every night. A network/parse failure on a
    single call is logged and the loop continues to the next event
    (`--geolocate-qwen` never aborts on one bad call); that event's
    geoloc_checked stays 0 so it is retried on a future batch.

    `major_powers`: same precomputed-set passthrough as `geolocate_rss_events`
    — avoids recomputing the identical corpus-wide set within one
    `pathos extract --geolocate-qwen` invocation.
    """
    result = QwenGeolocateResult()
    major_powers = (
        major_powers if major_powers is not None
        else compute_major_powers(conn, top_n=top_n_major_powers)
    )
    result.major_powers = sorted(major_powers)

    rows = conn.execute(
        """SELECT id, title FROM events
           WHERE origin = 'rss' AND location_name IS NULL AND geoloc_checked = 0
           ORDER BY id"""
    ).fetchall()
    result.events_scanned = len(rows)
    if not rows:
        return result

    # Same country/location entities used by geolocate_rss_events, keyed by
    # event id — re-classifying here catches events whose classification
    # would now resolve for free (heuristic/corpus moved since the last
    # `pathos extract` run) before spending an LLM call on them.
    grouped = _rss_event_countries(conn)

    logger.info(
        f"RSS geolocation (Qwen fallback): {len(rows)} candidate events, "
        f"budget {limit} LLM calls"
    )

    qwen_calls_made = 0
    for row in rows:
        event_id, title = row["id"], row["title"]
        r = _classify_heuristic(grouped.get(event_id, []), major_powers)

        if r.decision == "located":
            with conn:
                conn.execute(
                    "UPDATE events SET location_name = ?, geoloc_checked = 1 WHERE id = ?",
                    (r.location_country, event_id),
                )
            result.resolved_by_heuristic += 1
            continue
        if r.decision in ("skip_bilateral", "skip_none"):
            with conn:
                conn.execute(
                    "UPDATE events SET geoloc_checked = 1 WHERE id = ?", (event_id,)
                )
            result.resolved_by_heuristic += 1
            continue

        # decision == "ambiguous" — the only case worth an LLM call
        if qwen_calls_made >= limit:
            continue  # budget exhausted this run; geoloc_checked stays 0, retried next batch

        qwen_calls_made += 1
        result.qwen_calls += 1
        try:
            raw = await llm_client.complete(
                [{"role": "user", "content": _GEOLOC_PROMPT_TMPL.format(title=title)}],
                json_mode=True,
                json_schema=_GEOLOC_SCHEMA,
            )
            location_country = _parse_qwen_geoloc_response(raw)
        except Exception as exc:
            logger.warning(f"RSS geoloc/Qwen failed for event {event_id} ({title!r}): {exc}")
            result.qwen_errors += 1
            continue  # geoloc_checked stays 0 — retried next batch

        with conn:
            if location_country:
                conn.execute(
                    "UPDATE events SET location_name = ?, geoloc_checked = 1 WHERE id = ?",
                    (location_country, event_id),
                )
                result.qwen_located += 1
            else:
                conn.execute(
                    "UPDATE events SET geoloc_checked = 1 WHERE id = ?", (event_id,)
                )
                result.qwen_no_location += 1

        logger.info(
            f"RSS geoloc/Qwen [{qwen_calls_made}/{limit}]: event {event_id} → "
            f"{location_country or 'no anchor'}"
        )

    logger.info(
        f"RSS geolocation (Qwen fallback) complete: {result.events_scanned} scanned | "
        f"{result.resolved_by_heuristic} resolved by heuristic re-check | "
        f"{result.qwen_calls} LLM calls ({result.qwen_located} located, "
        f"{result.qwen_no_location} no anchor, {result.qwen_errors} errors)"
    )
    return result
