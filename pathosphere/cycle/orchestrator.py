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
    GRAPH = auto()
    BRIEF = auto()


PHASE_ORDER = [
    Phase.INGEST,
    Phase.EMBED,
    Phase.EXTRACT,
    Phase.CLUSTER,
    Phase.GRAPH,
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
        case Phase.GRAPH:
            _phase_graph()
        case Phase.BRIEF:
            _phase_brief()


def _phase_ingest() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.comtrade import ingest_comtrade
    from pathosphere.ingest.gdelt import QUAD_CONFLICT, ingest_gdelt
    from pathosphere.ingest.gdelt_anomaly import detect_gdelt_anomalies
    from pathosphere.ingest.physical import ingest_firms, ingest_usgs
    from pathosphere.ingest.portwatch import ingest_portwatch
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

    gdelt_anom = detect_gdelt_anomalies(conn)
    logger.info(
        f"INGEST/GDELT-ANOMALIES: {gdelt_anom.series_checked} series, "
        f"+{gdelt_anom.events_created} anomaly events"
    )

    rss = ingest_rss(conn, max_age_days=2)
    logger.info(
        f"INGEST/RSS: {rss.sources_ok} sources ok, +{rss.docs_inserted} docs "
        f"({rss.sources_error} errors)"
    )

    pw = ingest_portwatch(conn)
    logger.info(
        f"INGEST/PORTWATCH: {pw.chokepoints_fetched} chokepoints, "
        f"{pw.events_created} anomaly events ({len(pw.errors)} errors)"
    )

    ct = ingest_comtrade(conn)
    logger.info(
        f"INGEST/COMTRADE: {ct.records_fetched} records, +{ct.docs_inserted} docs "
        f"({len(ct.errors)} errors)"
    )

    usgs = ingest_usgs(conn)
    logger.info(
        f"INGEST/USGS: {usgs.quakes_fetched} quakes, +{usgs.events_created} events"
    )

    firms = ingest_firms(conn, map_key=settings.firms_map_key)
    if firms.skipped_no_key:
        logger.info("INGEST/FIRMS: skipped (no FIRMS_MAP_KEY)")
    else:
        logger.info(
            f"INGEST/FIRMS: {firms.detections_total} detections, "
            f"+{firms.events_created} events"
        )

    from pathosphere.ingest.ioda import ingest_ioda

    ioda = ingest_ioda(conn)
    logger.info(
        f"INGEST/IODA: {ioda.countries_checked} countries | "
        f"{ioda.metrics_upserted} metrics | +{ioda.events_created} events"
        + (f" | {len(ioda.errors)} errors" if ioda.errors else "")
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
        f"({wd.conflicts} conflicts, {wd.stoplisted} stoplisted"
        + (", rate limited" if wd.rate_limited else "") + ")"
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


def _phase_graph() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.graph import build_entity_links, compute_narrative_divergences

    settings = get_settings()
    conn = get_connection(settings.db_path)

    links = build_entity_links(conn)
    logger.info(
        f"GRAPH/LINKS: {links.links_written} links "
        f"({links.links_deleted} replaced), {links.pairs_evaluated} pairs"
    )

    divs = compute_narrative_divergences(conn)
    logger.info(
        f"GRAPH/DIVERGENCE: {divs.pairs_written} pairs, "
        f"{divs.events_processed} events processed, {divs.events_skipped} skipped"
    )

    conn.close()


def _phase_brief() -> None:
    import asyncio
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.llm.client import LLMClient
    from pathosphere.agent.brief import generate_brief

    settings = get_settings()
    conn = get_connection(settings.db_path)
    llm_client = LLMClient()

    result = asyncio.run(generate_brief(conn, llm_client))
    conn.close()

    logger.info(
        f"BRIEF: id={result.brief_id} | "
        f"events={result.event_count} | entities={result.entity_count} | "
        f"file={result.file_path}"
    )
