"""
Nightly cycle orchestrator.

Phases (sequential, resumable):
  1. ingest   — download documents from sources
  2. embed    — compute embeddings + vector dedup
  3. extract  — NER, geocoding, entity linking
  4. cluster  — group articles into events
  5. brief    — generate morning brief with Qwen

Each phase is atomic: if interrupted, resumes from the last completed phase.
"""

from dataclasses import dataclass, field
from enum import Enum, auto

from loguru import logger


class Phase(Enum):
    INGEST = auto()
    EMBED = auto()
    EXTRACT = auto()
    CLUSTER = auto()
    BRIEF = auto()


PHASE_ORDER = [
    Phase.INGEST,
    Phase.EMBED,
    Phase.EXTRACT,
    Phase.CLUSTER,
    Phase.BRIEF,
]


@dataclass
class CycleState:
    completed: set[Phase] = field(default_factory=set)
    errors: dict[Phase, str] = field(default_factory=dict)


def run_cycle(
    *,
    start_from: Phase | None = None,
    dry_run: bool = False,
) -> CycleState:
    """Run the nightly cycle sequentially and resumably."""
    state = CycleState()
    skip = start_from is not None

    for phase in PHASE_ORDER:
        if skip:
            if phase == start_from:
                skip = False
            else:
                logger.info(f"Skipping phase {phase.name} (resuming from {start_from.name})")
                continue

        logger.info(f"→ Phase {phase.name}")

        if dry_run:
            logger.info(f"  [dry-run] {phase.name} simulated")
            state.completed.add(phase)
            continue

        try:
            _run_phase(phase)
            state.completed.add(phase)
            logger.success(f"✓ {phase.name} complete")
        except Exception as exc:
            state.errors[phase] = str(exc)
            logger.error(f"✗ {phase.name} failed: {exc}")
            break

    return state


def _run_phase(phase: Phase) -> None:
    match phase:
        case Phase.INGEST:
            _phase_ingest()
        case Phase.EMBED:
            _phase_embed()
        case Phase.EXTRACT:
            _phase_extract()
        case Phase.CLUSTER:
            _phase_cluster()
        case Phase.BRIEF:
            _phase_brief()


def _phase_ingest() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt import QUAD_CONFLICT, ingest_gdelt
    from pathosphere.ingest.rss import ingest_rss

    settings = get_settings()
    conn = get_connection(settings.db_path)

    gdelt = ingest_gdelt(
        conn,
        n_days=1,
        quad_classes=QUAD_CONFLICT,
        min_mentions=10,
        skip_existing=True,
    )
    logger.info(
        f"INGEST/GDELT: {gdelt.events_inserted} events, {gdelt.docs_inserted} docs"
    )

    rss = ingest_rss(conn, max_age_days=2)
    logger.info(
        f"INGEST/RSS: {rss.sources_ok} sources ok, +{rss.docs_inserted} docs "
        f"({rss.sources_error} errors)"
    )

    conn.close()


def _phase_embed() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.embedder import embed_documents
    from pathosphere.semantic.dedup import dedup_documents

    settings = get_settings()
    conn = get_connection(settings.db_path)

    embed = embed_documents(conn)
    logger.info(
        f"EMBED: {embed.docs_processed} embedded, {embed.docs_skipped} skipped, "
        f"{embed.errors} errors"
    )

    dedup = dedup_documents(conn)
    logger.info(
        f"DEDUP: {dedup.docs_checked} checked, {dedup.duplicates_found} duplicates"
    )

    conn.close()


def _phase_extract() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.extract import (
        extract_entities,
        geocode_events,
        link_wikidata,
    )

    settings = get_settings()
    conn = get_connection(settings.db_path)

    ner = extract_entities(conn)
    logger.info(
        f"EXTRACT/NER: {ner.docs_processed} docs, +{ner.entities_created} entities, "
        f"{ner.mentions_recorded} mentions"
    )

    geo = geocode_events(conn, user_agent=settings.nominatim_user_agent)
    logger.info(
        f"EXTRACT/GEO: {geo.events_geocoded} events geocoded "
        f"({geo.lookups} lookups, {geo.cache_hits} cache hits)"
    )

    wd = link_wikidata(conn, user_agent=settings.nominatim_user_agent)
    logger.info(
        f"EXTRACT/WIKIDATA: {wd.qids_found} QIDs on {wd.entities_checked} checked "
        f"({wd.conflicts} conflicts)"
    )

    conn.close()


def _phase_cluster() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.cluster import cluster_documents

    settings = get_settings()
    conn = get_connection(settings.db_path)

    cluster = cluster_documents(conn)
    logger.info(
        f"CLUSTER: {cluster.events_created} events, {cluster.docs_assigned} docs assigned"
    )

    conn.close()


def _phase_brief() -> None:
    logger.info("BRIEF phase not yet implemented (Phase 3) — skipping")
