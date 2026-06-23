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
    type=click.Choice(["ingest", "embed", "extract", "cluster", "graph", "brief"]),
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
    """Populate the catalogue with the project's default sources (52 sources, 7 blocks)."""
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
@click.option("--full", is_flag=True, default=False,
              help="Backfill the full history (~2019→now), paginated. "
                   "Overrides --days.")
@click.option("--baseline-days", default=30, show_default=True,
              help="Trailing window for the anomaly baseline.")
@click.option("--z-threshold", default=2.0, show_default=True,
              help="|z-score| above which a transit anomaly becomes an event.")
@click.option("--portids", default=None,
              help="Comma-separated chokepoint ids (default: strategic set).")
def ingest_portwatch(
    days: int, full: bool, baseline_days: int, z_threshold: float,
    portids: str | None
) -> None:
    """Fetch IMF PortWatch chokepoint transits; flag anomalies as events."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.portwatch import (
        FULL_HISTORY,
        ingest_portwatch as _ingest_portwatch,
    )

    settings = get_settings()
    _require_db(settings)

    ids = [x.strip() for x in portids.split(",")] if portids else None

    conn = get_connection(settings.db_path)
    result = _ingest_portwatch(
        conn,
        portids=ids,
        days=FULL_HISTORY if full else days,
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        backfill_anomalies=full,
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
@click.option("--start", default=None,
              help="Backfill from this YYYYMM to most recent (e.g. 201801). "
                   "Overrides --periods.")
@click.option("--end", default=None,
              help="End YYYYMM for --start backfill (default: most recent).")
@click.option("--delay", default=None, type=float,
              help="Seconds between period calls (default: 6; raise if 429).")
@click.option("--reporters", default=None,
              help="Comma-separated ISO numeric reporter codes (default: pilot set).")
def ingest_comtrade(periods: str | None, start: str | None, end: str | None,
                    delay: float | None, reporters: str | None) -> None:
    """Fetch monthly semiconductor trade flows (HS 8541/8542/8486) as documents."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.comtrade import (
        DEFAULT_REQUEST_DELAY,
        ingest_comtrade as _ingest_comtrade,
        month_range,
    )

    settings = get_settings()
    _require_db(settings)

    if start:
        p = month_range(start, end)
    elif periods:
        p = [x.strip() for x in periods.split(",")]
    else:
        p = None
    r = [int(x.strip()) for x in reporters.split(",")] if reporters else None
    req_delay = delay if delay is not None else DEFAULT_REQUEST_DELAY

    conn = get_connection(settings.db_path)
    result = _ingest_comtrade(conn, periods=p, reporters=r, request_delay=req_delay)
    conn.close()

    click.echo(
        f"\nComtrade result:\n"
        f"  Periods: {', '.join(result.periods)}\n"
        f"  Records: {result.records_fetched} fetched\n"
        f"  Docs:    +{result.docs_inserted} inserted | {result.docs_skipped} skipped\n"
        f"  Flows:   {result.flows_upserted} upserted | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:5]}")


@ingest.command("usgs")
@click.option("--min-magnitude", default=5.0, show_default=True,
              help="Minimum earthquake magnitude to keep.")
@click.option("--days", default=1, show_default=True,
              help="Days back when no prior quakes exist (incremental fallback).")
@click.option("--start", default=None,
              help="Historical backfill anchor (YYYY-MM-DD). Overrides --days. "
                   "When omitted, resume from the last stored quake.")
@click.option("--end", default=None,
              help="Range end (YYYY-MM-DD) for --start backfill.")
def ingest_usgs(min_magnitude: float, days: int, start: str | None,
                end: str | None) -> None:
    """Fetch significant USGS earthquakes as hazard events (historical + resume)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.physical import ingest_usgs as _ingest_usgs

    settings = get_settings()
    _require_db(settings)

    conn = get_connection(settings.db_path)
    result = _ingest_usgs(
        conn, min_magnitude=min_magnitude, days=days, start=start, end=end
    )
    conn.close()

    click.echo(
        f"\nUSGS result (since {result.starttime}):\n"
        f"  Quakes: {result.quakes_fetched} fetched\n"
        f"  Events: +{result.events_created} | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nErrors: {result.errors[:5]}")


@ingest.command("firms")
@click.option("--days", default=1, show_default=True,
              help="Incremental window (days back) when an area has no history.")
@click.option("--start", default=None,
              help="Historical backfill anchor (YYYY-MM-DD). Overrides --days; "
                   "auto-selects VIIRS_NOAA20_SP (falls back to NRT on 400). "
                   "When omitted, each area resumes from its last stored date.")
@click.option("--end", default=None, help="Range end (YYYY-MM-DD), default today.")
@click.option("--source", default=None,
              help="FIRMS sensor product (default VIIRS_NOAA20_NRT; "
                   "auto VIIRS_NOAA20_SP when --start is given; "
                   "SP falls back to NRT per-window on 400).")
@click.option("--baseline-days", default=30, show_default=True,
              help="Trailing window for the anomaly baseline.")
@click.option("--z-threshold", default=2.0, show_default=True,
              help="z-score above which a fire surge becomes an event.")
@click.option("--min-detections", default=50, show_default=True,
              help="Latest daily count must exceed this to surface an anomaly.")
def ingest_firms(days: int, start: str | None, end: str | None,
                 source: str | None, baseline_days: int, z_threshold: float,
                 min_detections: int) -> None:
    """Store daily NASA FIRMS detections per area; flag fire surges (needs FIRMS_MAP_KEY)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.physical import (
        ARCHIVE_FIRMS_SOURCE,
        DEFAULT_FIRMS_SOURCE,
        ingest_firms as _ingest_firms,
    )

    settings = get_settings()
    _require_db(settings)

    if source is None:
        source = ARCHIVE_FIRMS_SOURCE if start else DEFAULT_FIRMS_SOURCE

    conn = get_connection(settings.db_path)
    result = _ingest_firms(
        conn, map_key=settings.firms_map_key, source=source, days=days,
        start=start, end=end, baseline_days=baseline_days,
        z_threshold=z_threshold, min_detections=min_detections,
    )
    conn.close()

    if result.skipped_no_key:
        click.echo("FIRMS skipped: set FIRMS_MAP_KEY in .env (free registration).")
        return
    click.echo(
        f"\nFIRMS result (source={source}):\n"
        f"  Areas:      {result.areas_checked} checked | {result.windows_fetched} windows\n"
        f"  Detections: {result.detections_total} | {result.metrics_upserted} metrics upserted\n"
        f"  Events:     +{result.events_created} anomalies | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nErrors: {result.errors[:5]}")


@ingest.command("ioda")
@click.option("--days", default=1, show_default=True,
              help="Incremental window (days back) when a country has no history.")
@click.option("--start", default=None,
              help="Historical backfill anchor (YYYY-MM-DD). Overrides --days.")
@click.option("--end", default=None, help="Range end (YYYY-MM-DD), default today.")
@click.option("--countries", default=None,
              help="Comma-separated ISO-2 codes (default: all monitored countries).")
@click.option("--baseline-days", default=30, show_default=True,
              help="Trailing window for the anomaly baseline.")
@click.option("--z-threshold", default=2.5, show_default=True,
              help="|z-score| below which a connectivity drop becomes an event.")
@click.option("--datasource", default="bgp", show_default=True,
              type=click.Choice(["bgp", "ping-slash24", "merit-nt"]),
              help="IODA signal datasource.")
def ingest_ioda(
    days: int, start: str | None, end: str | None, countries: str | None,
    baseline_days: int, z_threshold: float, datasource: str,
) -> None:
    """Fetch IODA internet-signal data; flag outages as infrastructure events."""
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.ioda import MONITORED_COUNTRIES, ingest_ioda as _ingest_ioda

    settings = get_settings()
    _require_db(settings)

    country_map = MONITORED_COUNTRIES
    if countries:
        codes = [c.strip().upper() for c in countries.split(",")]
        country_map = {c: MONITORED_COUNTRIES.get(c, c) for c in codes}

    conn = get_connection(settings.db_path)
    result = _ingest_ioda(
        conn,
        countries=country_map,
        days=days,
        start=start,
        end=end,
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        datasource=datasource,
    )
    conn.close()

    click.echo(
        f"\nIODA result:\n"
        f"  Countries: {result.countries_checked} checked\n"
        f"  Metrics:   {result.metrics_upserted} upserted\n"
        f"  Events:    +{result.events_created} outages | {len(result.errors)} errors"
    )
    if result.errors:
        click.echo(f"\nFirst errors: {result.errors[:5]}")


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


# ─── graph ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--skip-links", is_flag=True, help="Skip entity co-occurrence graph.")
@click.option("--skip-divergence", is_flag=True, help="Skip narrative divergence computation.")
@click.option(
    "--min-cooccurrences", default=1, show_default=True,
    help="Min shared events for an entity pair to create a link.",
)
def graph(skip_links: bool, skip_divergence: bool, min_cooccurrences: int) -> None:
    """Build entity co-occurrence graph and compute narrative divergences."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.graph import build_entity_links, compute_narrative_divergences

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if not skip_links:
        links = build_entity_links(conn, min_cooccurrences=min_cooccurrences)
        click.echo(
            f"\nGraph links: {links.links_written} written | "
            f"{links.links_deleted} replaced | "
            f"{links.pairs_evaluated} pairs evaluated"
        )

    if not skip_divergence:
        divs = compute_narrative_divergences(conn)
        click.echo(
            f"Narrative divergence: {divs.pairs_written} pairs | "
            f"{divs.events_processed} events processed | "
            f"{divs.events_skipped} skipped"
        )

    conn.close()


# ─── brief ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--date", "brief_date", default=None,
    help="ISO date for the brief (default: today UTC, e.g. 2026-06-22).",
)
@click.option(
    "--lookback-days", default=7, show_default=True,
    help="Days back to scan for divergences and anomalies.",
)
@click.option(
    "--model", default=None,
    type=click.Choice(["claude", "qwen-local"]),
    help="LLM backend override (default: from REASONING_MODEL in .env).",
)
@click.option("--dry-run", is_flag=True, help="Print context counts only; do not call LLM.")
def brief(
    brief_date: str | None,
    lookback_days: int,
    model: str | None,
    dry_run: bool,
) -> None:
    """Generate the morning intelligence brief and save it to data/briefs/."""
    import asyncio
    from pathosphere.db.schema import get_connection
    from pathosphere.llm.client import LLMClient
    from pathosphere.agent.brief import generate_brief
    from pathosphere.agent.brief import (
        _query_divergences,
        _query_hub_entities,
        _query_recent_anomalies,
    )

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if dry_run:
        from datetime import date as _date
        target = brief_date or _date.today().isoformat()
        divs = _query_divergences(conn, lookback_days)
        hubs = _query_hub_entities(conn)
        anoms = _query_recent_anomalies(conn, lookback_days)
        conn.close()
        click.echo(
            f"\nBrief dry-run for {target} (lookback={lookback_days}d):\n"
            f"  Divergences : {len(divs)}\n"
            f"  Hub entities: {len(hubs)}\n"
            f"  Anomalies   : {len(anoms)}\n"
            "(no LLM call made)"
        )
        return

    llm_client = LLMClient(backend=model)
    result = asyncio.run(
        generate_brief(conn, llm_client, brief_date=brief_date, lookback_days=lookback_days)
    )
    conn.close()

    click.echo(
        f"\nBrief generated:\n"
        f"  Date    : {result.date}\n"
        f"  File    : {result.file_path}\n"
        f"  DB id   : {result.brief_id}\n"
        f"  Signals : {result.event_count} events | {result.entity_count} entities"
    )
    click.echo(f"\n--- preview (first 500 chars) ---\n{result.content[:500]}")


# ─── thesis ───────────────────────────────────────────────────────────────────

@cli.group()
def thesis() -> None:
    """Thesis generation (direct or via multi-persona debate)."""


@thesis.command("generate")
@click.option("--date", "brief_date", default=None,
              help="ISO date of the brief to use (default: today UTC).")
@click.option("--n", default=3, show_default=True,
              help="Number of primary theses to generate.")
@click.option("--model", default=None, type=click.Choice(["claude", "qwen-local"]),
              help="LLM backend override (default: from REASONING_MODEL in .env).")
def thesis_generate(brief_date: str | None, n: int, model: str | None) -> None:
    """Generate theses from today's brief (fast, single LLM call)."""
    import asyncio
    from pathosphere.db.schema import get_connection
    from pathosphere.llm.client import LLMClient
    from pathosphere.agent.thesis import generate_theses

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)
    llm_client = LLMClient(backend=model)

    result = asyncio.run(generate_theses(conn, llm_client, brief_date=brief_date, n=n))
    conn.close()

    click.echo(
        f"\nTheses generated:\n"
        f"  Theses   : {result.theses_created} ({n} primary + {result.theses_created - n} alternatives)\n"
        f"  Watchlist: +{result.watchlist_created} items\n"
        f"  IDs      : {result.thesis_ids}"
    )


@thesis.command("debate")
@click.option("--date", "brief_date", default=None,
              help="ISO date of the brief to use (default: today UTC).")
@click.option("--n", default=3, show_default=True,
              help="Number of primary theses to generate.")
def thesis_debate(brief_date: str | None, n: int) -> None:
    """Generate theses via multi-persona debate (Qwen x13 + Claude x1).

    Pipeline:
      1. Research   — 6 personas independently analyse the brief (Qwen, parallel)
      2. Divergence — detect 2-3 key disagreement points (Qwen)
      3. Critique   — each persona responds to divergences (Qwen, parallel)
      4. Synthesis  — Claude generates theses informed by the debate (Claude)
    """
    import asyncio
    from pathosphere.db.schema import get_connection
    from pathosphere.llm.client import LLMClient
    from pathosphere.agent.debate import PERSONAS, run_debate

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    qwen_client = LLMClient(backend="qwen-local")
    claude_client = LLMClient(backend="claude")

    click.echo(
        f"\nStarting debate for {brief_date or 'today'} | "
        f"personas: {', '.join(PERSONAS)} | n={n}"
    )
    click.echo("Step 1/4 — Research (6 personas, parallel)...")

    result = asyncio.run(
        run_debate(conn, qwen_client, claude_client, brief_date=brief_date, n_theses=n)
    )
    conn.close()

    click.echo(
        f"\nDebate complete:\n"
        f"  Debate id  : {result.debate_id}\n"
        f"  Divergences: {len(result.divergence_points)}"
    )
    for dp in result.divergence_points:
        click.echo(f"    [{dp.get('id')}] {dp.get('title')}")
    click.echo(
        f"  Theses     : {result.thesis_result.theses_created} "
        f"({n} primary + {result.thesis_result.theses_created - n} alternatives)\n"
        f"  Watchlist  : +{result.thesis_result.watchlist_created} items\n"
        f"  IDs        : {result.thesis_result.thesis_ids}"
    )


# ─── export ───────────────────────────────────────────────────────────────────

@cli.group()
def export() -> None:
    """Export data to external formats (Parquet, CSV)."""


@export.command("parquet")
@click.option("--tables", default=None,
              help="Comma-separated table names (default: raw_documents,events,entities,entity_links).")
@click.option("--out-dir", default=None,
              help="Output directory (default: parquet_dir from config).")
def export_parquet(tables: str | None, out_dir: str | None) -> None:
    """Export SQLite tables to partitioned Parquet files (Snappy compressed).

    Partitioned by year/month for dated tables (raw_documents, events).
    Single file for undated tables (entities, entity_links).
    Idempotent — safe to re-run; overwrites existing partitions.

    Read back with DuckDB:
      duckdb.sql("SELECT * FROM 'data/parquet/raw_documents/**/*.parquet'")
    """
    from pathosphere.db.schema import get_connection
    from pathosphere.export.parquet import export_to_parquet

    settings = get_settings()
    _require_db(settings)

    target_tables: list[str] | None = (
        [t.strip() for t in tables.split(",")] if tables else None
    )
    parquet_dir = Path(out_dir) if out_dir else settings.parquet_dir
    parquet_dir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(settings.db_path)
    result = export_to_parquet(conn, parquet_dir, tables=target_tables)
    conn.close()

    click.echo(f"\nParquet export → {parquet_dir}")
    for tbl in result.tables_written:
        click.echo(f"  {tbl:<25} {result.rows_written[tbl]:>10,} rows")
    if result.errors:
        click.echo(f"\nErrors: {result.errors}")
