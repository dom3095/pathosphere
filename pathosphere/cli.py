"""Pathosphere main CLI — entry point: `pathos`."""

import click
from loguru import logger

from pathosphere.config import get_settings
from pathosphere.logging_setup import setup_logging


@click.group()
@click.option("--log-level", default=None, help="Override LOG_LEVEL (.env)")
def cli(log_level: str | None) -> None:
    """Pathosphere — OSINT intelligence on global critical events."""
    if log_level:
        import os
        os.environ["LOG_LEVEL"] = log_level.upper()
    setup_logging()


# ─── db ───────────────────────────────────────────────────────────────────────

@cli.group()
def db() -> None:
    """Database management."""


@db.command("init")
def db_init() -> None:
    """Initialize the SQLite database (creates tables and vec0)."""
    from pathosphere.db.schema import init_db
    settings = get_settings()
    logger.info(f"Initializing DB: {settings.db_path}")
    init_db(settings.db_path)
    logger.success(f"Database ready: {settings.db_path}")


@db.command("info")
def db_info() -> None:
    """Show info and row counts for main tables."""
    from pathosphere.db.schema import get_connection
    settings = get_settings()
    if not settings.db_path.exists():
        click.echo("Database not found. Run: pathos db init")
        return
    conn = get_connection(settings.db_path)
    tables = [
        "sources", "raw_documents", "events", "entities",
        "entity_links", "theses", "trades", "portfolios", "predictions",
    ]
    click.echo(f"\nDatabase: {settings.db_path}")
    click.echo(f"{'Table':<25} {'Rows':>8}")
    click.echo("─" * 35)
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            count = row[0] if row else 0
        except Exception:
            count = "N/A"
        click.echo(f"{table:<25} {count:>8}")
    conn.close()


# ─── cycle ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--from-phase",
    type=click.Choice(["ingest", "embed", "extract", "cluster", "brief"]),
    default=None,
    help="Resume the cycle from this phase.",
)
@click.option("--dry-run", is_flag=True, help="Simulate the cycle without running anything.")
def cycle(from_phase: str | None, dry_run: bool) -> None:
    """Run the full nightly cycle (download → brief)."""
    from pathosphere.cycle.orchestrator import Phase, run_cycle

    start = None
    if from_phase:
        start = Phase[from_phase.upper()]

    if dry_run:
        logger.info("Dry-run mode active")

    state = run_cycle(start_from=start, dry_run=dry_run)

    if state.errors:
        click.echo(f"\nCycle aborted. Errors: {list(state.errors.keys())}")
        click.echo(f"Resume with: pathos cycle --from-phase {list(state.errors.keys())[0].name.lower()}")
    else:
        click.echo(f"\nCycle complete. Phases: {[p.name for p in state.completed]}")


# ─── config ───────────────────────────────────────────────────────────────────

@cli.command()
def config() -> None:
    """Show the active configuration."""
    settings = get_settings()
    click.echo("\nActive configuration:")
    for field_name, value in settings.model_dump().items():
        click.echo(f"  {field_name:<30} = {value}")


# ─── sources ──────────────────────────────────────────────────────────────────

@cli.group()
def sources() -> None:
    """Source catalogue management."""


@sources.command("list")
def sources_list() -> None:
    """List configured sources."""
    from pathosphere.db.schema import get_connection
    settings = get_settings()
    if not settings.db_path.exists():
        click.echo("Database not found. Run: pathos db init")
        return
    conn = get_connection(settings.db_path)
    rows = conn.execute(
        "SELECT id, name, country, geopolitical_block, state_control, active FROM sources ORDER BY geopolitical_block, name"
    ).fetchall()
    if not rows:
        click.echo("No sources configured. Use: pathos sources seed")
        return
    click.echo(f"\n{'ID':>4} {'Name':<30} {'Country':<8} {'Block':<12} {'Ctrl':>4} {'Active':>6}")
    click.echo("─" * 70)
    for r in rows:
        click.echo(f"{r['id']:>4} {r['name']:<30} {r['country']:<8} {r['geopolitical_block']:<12} {r['state_control']:>4} {'yes' if r['active'] else 'no':>6}")
    conn.close()


@sources.command("seed")
def sources_seed() -> None:
    """Populate the catalogue with the project's default sources (49 sources, 7 blocks)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.sources_seed import seed_sources
    settings = get_settings()
    conn = get_connection(settings.db_path)
    inserted = seed_sources(conn)
    conn.close()
    logger.success(f"Sources seed complete: {inserted} new rows inserted.")


# ─── ingest ───────────────────────────────────────────────────────────────────

@cli.group()
def ingest() -> None:
    """Data ingestion from sources."""


def _require_db(settings):
    if not settings.db_path.exists():
        click.echo("Database not found. Run first: pathos db init")
        raise SystemExit(1)


@ingest.command("gdelt")
@click.option("--days", default=1, show_default=True, help="How many days back.")
@click.option(
    "--quad",
    default="conflict",
    type=click.Choice(["conflict", "all"]),
    show_default=True,
    help="conflict=QuadClass 3-4 | all=1-4",
)
@click.option("--min-mentions", default=10, show_default=True, help="Minimum NumMentions filter.")
@click.option("--max-goldstein", default=None, type=float, help="Keep only GoldsteinScale ≤ value.")
@click.option("--countries", default=None, help="Comma-separated ISO-2 codes (e.g. CN,US,TW).")
@click.option("--max-files", default=None, type=int, help="Limit number of files (useful for testing).")
@click.option("--no-skip", is_flag=True, help="Re-download files already present in the log.")
def ingest_gdelt(
    days: int,
    quad: str,
    min_mentions: int,
    max_goldstein: float | None,
    countries: str | None,
    max_files: int | None,
    no_skip: bool,
) -> None:
    """Download GDELT 2.0 events for the last N days (incremental cycle)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt import QUAD_ALL, QUAD_CONFLICT, ingest_gdelt

    settings = get_settings()
    _require_db(settings)

    qc = QUAD_CONFLICT if quad == "conflict" else QUAD_ALL
    ctry = set(c.strip().upper() for c in countries.split(",")) if countries else None

    conn = get_connection(settings.db_path)
    result = ingest_gdelt(
        conn,
        n_days=days,
        quad_classes=qc,
        min_mentions=min_mentions,
        min_goldstein=max_goldstein,
        countries=ctry,
        max_files=max_files,
        skip_existing=not no_skip,
    )
    conn.close()

    click.echo(
        f"\nGDELT result:\n"
        f"  Files:   {result.files_ok} ok | {result.files_skipped} skipped | {result.files_error} errors\n"
        f"  Rows:    {result.rows_raw:,} raw → {result.rows_filtered:,} filtered\n"
        f"  Insert:  {result.events_inserted:,} events | {result.docs_inserted:,} documents"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:3]}")


@ingest.command("gdelt-history")
@click.option(
    "--start", required=True,
    help="Start date (YYYY-MM-DD).",
)
@click.option(
    "--end", default=None,
    help="End date excl. (YYYY-MM-DD). Default: yesterday.",
)
@click.option(
    "--sample-hours", default=1, show_default=True,
    help="Download 1 file every N hours. Default=1 (every hour, good coverage). "
         "Use 6 for a quick bootstrap, 0 for all (every 15 min, ~7 nights).",
)
@click.option("--min-mentions", default=10, show_default=True, help="NumMentions threshold.")
@click.option(
    "--quad",
    default="conflict",
    type=click.Choice(["conflict", "all"]),
    show_default=True,
)
@click.option("--countries", default=None, help="Comma-separated ISO-2 codes.")
def ingest_gdelt_history(
    start: str,
    end: str | None,
    sample_hours: int,
    min_mentions: int,
    quad: str,
    countries: str | None,
) -> None:
    """
    Bulk historical GDELT download for a date range (one-time, resumable).

    Samples 1 file every sample-hours hours to reduce volume. Events with
    significant coverage (min-mentions ≥ 10) appear in many consecutive windows,
    so hourly sampling (default 1h) captures everything that matters.

    Estimates for 5 years of history:
      --sample-hours 1  → ~43k files, ~2 nights (recommended)
      --sample-hours 2  → ~22k files, ~1 night
      --sample-hours 6  → ~7k files,  ~2.5h  (quick bootstrap)
      --sample-hours 0  → all files every 15min, ~7 nights

    Resumable: Ctrl+C and relaunch — automatically skips already-downloaded files.

    Example:
      pathos ingest gdelt-history --start 2021-01-01
    """
    from datetime import date, timedelta

    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt import (
        GDELT_BASE,
        QUAD_ALL,
        QUAD_CONFLICT,
        _extract_csv,
        _fetch_zip,
        _parse_rows,
        build_lookup_caches,
        filter_rows,
        store_rows,
    )

    settings = get_settings()
    _require_db(settings)

    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        click.echo(f"Invalid date format: {start} (use YYYY-MM-DD)")
        raise SystemExit(1)

    end_date = date.fromisoformat(end) if end else date.today()
    qc = QUAD_CONFLICT if quad == "conflict" else QUAD_ALL
    ctry = set(c.strip().upper() for c in countries.split(",")) if countries else None

    # Generate sampled URLs
    # sample_hours=0 → all files every 15 minutes
    # sample_hours=N → 1 file every N hours (at minute :00 of the chosen hour)
    urls: list[tuple[str, str]] = []
    cursor = start_date
    if sample_hours == 0:
        # full download: every 15 minutes
        slot_minutes = list(range(0, 24 * 60, 15))
    else:
        slot_minutes = [h * 60 for h in range(0, 24, sample_hours)]

    while cursor < end_date:
        day_str = cursor.strftime("%Y%m%d")
        for total_min in slot_minutes:
            h, m = divmod(total_min, 60)
            fname = f"{day_str}{h:02d}{m:02d}00.export.CSV.zip"
            urls.append((fname, f"{GDELT_BASE}/{fname}"))
        cursor += timedelta(days=1)

    total = len(urls)
    click.echo(
        f"GDELT historical: {start_date} → {end_date} | "
        f"{total} files "
        f"({'every 15min' if sample_hours == 0 else f'every {sample_hours}h'}) | "
        f"min_mentions={min_mentions}"
    )
    est_hours = total * 1.2 / 3600
    click.echo(f"Estimate: ~{est_hours:.1f}h ({est_hours/8:.1f} 8h-nights). Resumable with Ctrl+C.\n")

    import httpx

    conn = get_connection(settings.db_path)
    url_to_id, event_key_to_id = build_lookup_caches(conn)
    click.echo(f"Lookup caches: {len(url_to_id):,} urls, {len(event_key_to_id):,} event keys")

    files_ok = files_skip = files_err = 0
    rows_raw_total = rows_filt_total = ev_total = doc_total = 0

    with httpx.Client(
        headers={"User-Agent": "pathosphere/0.1 OSINT research"},
        timeout=30,
    ) as client:
        for i, (fname, url) in enumerate(urls, 1):
            already = conn.execute(
                "SELECT id FROM gdelt_file_log WHERE filename = ?", (fname,)
            ).fetchone()
            if already:
                files_skip += 1
                continue

            try:
                zip_bytes = _fetch_zip(url, client)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    files_skip += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO gdelt_file_log (filename, url, rows_raw, rows_stored, status) VALUES (?,?,0,0,'skipped')",
                        (fname, url),
                    )
                    conn.commit()
                    continue
                files_err += 1
                logger.warning(f"HTTP {exc.response.status_code}: {fname}")
                continue
            except Exception as exc:
                files_err += 1
                logger.warning(f"Error {fname}: {exc}")
                continue

            try:
                csv_text = _extract_csv(zip_bytes)
            except Exception as exc:
                files_err += 1
                logger.warning(f"Zip error {fname}: {exc}")
                continue

            raw_rows = list(_parse_rows(csv_text))
            filtered = filter_rows(
                iter(raw_rows),
                quad_classes=qc,
                min_mentions=min_mentions,
                min_goldstein=None,
                countries=ctry,
            )

            with conn:
                ev_ins, doc_ins = store_rows(conn, filtered, url_to_id, event_key_to_id)
                conn.execute(
                    "INSERT OR IGNORE INTO gdelt_file_log (filename, url, rows_raw, rows_stored, status) VALUES (?,?,?,?,'ok')",
                    (fname, url, len(raw_rows), len(filtered)),
                )

            files_ok += 1
            rows_raw_total += len(raw_rows)
            rows_filt_total += len(filtered)
            ev_total += ev_ins
            doc_total += doc_ins

            if i % 20 == 0 or i == total:
                from datetime import datetime as _dt
                pct = i / total * 100
                ts = _dt.now().strftime("%H:%M:%S")
                click.echo(
                    f"[{ts}] [{pct:5.1f}%] {i}/{total} | "
                    f"ok={files_ok} skip={files_skip} err={files_err} | "
                    f"events={ev_total:,} docs={doc_total:,}"
                )

    conn.close()
    click.echo(
        f"\nGDELT historical complete:\n"
        f"  Files:  {files_ok} ok | {files_skip} skipped | {files_err} errors\n"
        f"  Rows:   {rows_raw_total:,} raw → {rows_filt_total:,} filtered\n"
        f"  Insert: {ev_total:,} events | {doc_total:,} documents"
    )


@ingest.command("rss")
@click.option(
    "--max-age-days", default=2, show_default=True,
    help="Skip articles older than N days (0 = no limit).",
)
@click.option(
    "--source-ids", default=None,
    help="Comma-separated source IDs to fetch (default: all active).",
)
def ingest_rss(max_age_days: int, source_ids: str | None) -> None:
    """Fetch RSS feeds from all active sources and insert into raw_documents."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.rss import ingest_rss as _ingest_rss

    settings = get_settings()
    _require_db(settings)

    ids: list[int] | None = None
    if source_ids:
        ids = [int(x.strip()) for x in source_ids.split(",")]

    conn = get_connection(settings.db_path)
    result = _ingest_rss(conn, source_ids=ids, max_age_days=max_age_days)
    conn.close()

    click.echo(
        f"\nRSS result:\n"
        f"  Sources: {result.sources_ok} ok | {result.sources_error} errors "
        f"(of {result.sources_attempted} attempted)\n"
        f"  Docs:    +{result.docs_inserted:,} inserted | {result.docs_skipped:,} skipped"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:5]}")


@ingest.command("portwatch")
@click.option("--days", default=90, show_default=True,
              help="Daily records to fetch per chokepoint.")
@click.option("--baseline-days", default=30, show_default=True,
              help="Trailing window for the anomaly baseline.")
@click.option("--z-threshold", default=2.0, show_default=True,
              help="|z-score| above which a transit anomaly becomes an event.")
@click.option("--portids", default=None,
              help="Comma-separated chokepoint ids (default: strategic set).")
def ingest_portwatch(
    days: int, baseline_days: int, z_threshold: float, portids: str | None
) -> None:
    """Fetch IMF PortWatch chokepoint transits; flag anomalies as events."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.portwatch import ingest_portwatch as _ingest_portwatch

    settings = get_settings()
    _require_db(settings)

    ids = [x.strip() for x in portids.split(",")] if portids else None

    conn = get_connection(settings.db_path)
    result = _ingest_portwatch(
        conn,
        portids=ids,
        days=days,
        baseline_days=baseline_days,
        z_threshold=z_threshold,
    )
    conn.close()

    click.echo(
        f"\nPortWatch result:\n"
        f"  Chokepoints: {result.chokepoints_fetched} fetched\n"
        f"  Metrics:     {result.metrics_upserted:,} upserted\n"
        f"  Events:      {result.events_created} anomalies | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:5]}")


@ingest.command("comtrade")
@click.option("--periods", default=None,
              help="Comma-separated YYYYMM (default: 3 recent months, ~2mo lag).")
@click.option("--reporters", default=None,
              help="Comma-separated ISO numeric reporter codes (default: pilot set).")
def ingest_comtrade(periods: str | None, reporters: str | None) -> None:
    """Fetch monthly semiconductor trade flows (HS 8541/8542/8486) as documents."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.comtrade import ingest_comtrade as _ingest_comtrade

    settings = get_settings()
    _require_db(settings)

    p = [x.strip() for x in periods.split(",")] if periods else None
    r = [int(x.strip()) for x in reporters.split(",")] if reporters else None

    conn = get_connection(settings.db_path)
    result = _ingest_comtrade(conn, periods=p, reporters=r)
    conn.close()

    click.echo(
        f"\nComtrade result:\n"
        f"  Periods: {', '.join(result.periods)}\n"
        f"  Records: {result.records_fetched} fetched\n"
        f"  Docs:    +{result.docs_inserted} inserted | {result.docs_skipped} skipped | "
        f"{len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:5]}")


@ingest.command("usgs")
@click.option("--min-magnitude", default=5.0, show_default=True,
              help="Minimum earthquake magnitude to keep.")
@click.option("--days", default=1, show_default=True, help="How many days back.")
def ingest_usgs(min_magnitude: float, days: int) -> None:
    """Fetch significant USGS earthquakes as hazard events."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.physical import ingest_usgs as _ingest_usgs

    settings = get_settings()
    _require_db(settings)

    conn = get_connection(settings.db_path)
    result = _ingest_usgs(conn, min_magnitude=min_magnitude, days=days)
    conn.close()

    click.echo(
        f"\nUSGS result:\n"
        f"  Quakes: {result.quakes_fetched} fetched\n"
        f"  Events: +{result.events_created} | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nErrors: {result.errors[:5]}")


@ingest.command("firms")
@click.option("--days", default=1, show_default=True, help="How many days back.")
@click.option("--threshold", default=50, show_default=True,
              help="Detections per area to warrant a hazard event.")
def ingest_firms(days: int, threshold: int) -> None:
    """Summarize NASA FIRMS active-fire detections per area (needs FIRMS_MAP_KEY)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.physical import ingest_firms as _ingest_firms

    settings = get_settings()
    _require_db(settings)

    conn = get_connection(settings.db_path)
    result = _ingest_firms(
        conn, map_key=settings.firms_map_key, days=days, threshold=threshold
    )
    conn.close()

    if result.skipped_no_key:
        click.echo("FIRMS skipped: set FIRMS_MAP_KEY in .env (free registration).")
        return
    click.echo(
        f"\nFIRMS result:\n"
        f"  Areas:      {result.areas_checked} checked\n"
        f"  Detections: {result.detections_total}\n"
        f"  Events:     +{result.events_created} | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nErrors: {result.errors[:5]}")


# ─── embed ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--batch-size", default=32, show_default=True, help="Docs per encode() call.")
@click.option("--skip-dedup", is_flag=True, help="Only embed; skip near-duplicate detection.")
@click.option("--skip-cluster", is_flag=True, help="Skip event clustering.")
def embed(batch_size: int, skip_dedup: bool, skip_cluster: bool) -> None:
    """Embed unprocessed docs, dedup near-duplicates, cluster into events."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.embedder import embed_documents
    from pathosphere.semantic.dedup import dedup_documents
    from pathosphere.semantic.cluster import cluster_documents

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    embed_result = embed_documents(conn, batch_size=batch_size)
    click.echo(
        f"\nEmbed: {embed_result.docs_processed} embedded | "
        f"{embed_result.docs_skipped} skipped (no text) | "
        f"{embed_result.errors} errors"
    )

    if not skip_dedup:
        dedup_result = dedup_documents(conn)
        click.echo(
            f"Dedup: {dedup_result.docs_checked} checked | "
            f"{dedup_result.duplicates_found} duplicates marked"
        )

    if not skip_cluster:
        cluster_result = cluster_documents(conn)
        click.echo(
            f"Cluster: {cluster_result.events_created} events created | "
            f"{cluster_result.docs_assigned} docs assigned"
        )

    conn.close()


# ─── extract ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--limit", default=None, type=int, help="Max docs to run NER on.")
@click.option("--max-lookups", default=50, show_default=True,
              help="Network lookup budget for geocoding and Wikidata (each).")
@click.option("--skip-geocode", is_flag=True, help="Skip Nominatim geocoding.")
@click.option("--skip-wikidata", is_flag=True, help="Skip Wikidata entity linking.")
def extract(
    limit: int | None, max_lookups: int, skip_geocode: bool, skip_wikidata: bool
) -> None:
    """Run NER, geocode events, link entities to Wikidata."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.extract import (
        extract_entities,
        geocode_events,
        link_wikidata,
    )

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    ner = extract_entities(conn, limit=limit)
    click.echo(
        f"\nNER: {ner.docs_processed} docs | +{ner.entities_created} entities | "
        f"{ner.mentions_recorded} mentions | {ner.docs_skipped} skipped"
    )

    if not skip_geocode:
        geo = geocode_events(
            conn,
            user_agent=settings.nominatim_user_agent,
            max_lookups=max_lookups,
        )
        click.echo(
            f"Geocode: {geo.events_geocoded} events | {geo.lookups} lookups | "
            f"{geo.cache_hits} cache hits | {geo.misses} misses"
        )

    if not skip_wikidata:
        wd = link_wikidata(
            conn,
            user_agent=settings.nominatim_user_agent,
            max_lookups=max_lookups,
        )
        click.echo(
            f"Wikidata: {wd.qids_found} QIDs | {wd.entities_checked} checked | "
            f"{wd.conflicts} conflicts"
        )

    conn.close()
