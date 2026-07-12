"""Pathosphere main CLI — entry point: `pathos`."""

import sqlite3
from pathlib import Path

import click
from loguru import logger

from pathosphere.agent.predictions import (
    VALID_DOMAINS,
    VALID_MACRO_AREAS,
    VALID_PREDICTION_TYPES,
    VALID_SCOPES,
)
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


@cli.command()
@click.option(
    "--max-retries",
    type=int,
    default=3,
    help="Max retry attempts per phase before pausing.",
)
@click.option(
    "--sleep-hours",
    type=float,
    default=1.0,
    help="Hours to sleep between complete cycles.",
)
@click.option(
    "--state-file",
    type=click.Path(),
    default=None,
    help="Path to cycle state JSON (default: data/cycle_state.json).",
)
def loop(max_retries: int, sleep_hours: float, state_file: str | None) -> None:
    """Run autonomous nightly loop forever (resumable on failure).

    State persisted to JSON. Run with:
      caffeinate -i pathos loop

    Interrupt: Ctrl+C saves state and exits cleanly.
    """
    from pathosphere.cycle.loop import run_autonomous_loop

    state_path = Path(state_file) if state_file else None
    sleep_seconds = int(sleep_hours * 3600)

    try:
        run_autonomous_loop(
            max_retries=max_retries,
            sleep_between_cycles=sleep_seconds,
            state_file=state_path,
        )
    except KeyboardInterrupt:
        logger.info("Loop stopped by user.")
        click.echo("State saved. Resume with: pathos loop")


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


@ingest.command("gdelt-anomalies")
@click.option("--baseline-days", default=30, show_default=True,
              help="Trailing window for the Goldstein anomaly baseline.")
@click.option("--z-threshold", default=2.0, show_default=True,
              help="|z-score| above which a country/day Goldstein deviation becomes an event.")
@click.option("--min-events-per-day", default=3, show_default=True,
              help="Minimum raw GDELT events in a country/day/quad_class cell to consider it.")
@click.option("--full", is_flag=True, default=False,
              help="Sweep the whole stored history instead of just the latest day per series.")
@click.option("--backfill-country", is_flag=True, default=False,
              help="Repair action_geo_country on gdelt_events rows stored before "
                   "this column existed (recovered from events.title), then run.")
def ingest_gdelt_anomalies(
    baseline_days: int, z_threshold: float, min_events_per_day: int, full: bool,
    backfill_country: bool,
) -> None:
    """Aggregate gdelt_events (goldstein/tone) and promote anomalies to events.

    Numeric path for GDELT (CP-016): reads the goldstein/avg_tone signal
    already stored by `pathos ingest gdelt` per country+quad_class+day and
    flags trailing-baseline deviations directly as events — no NER/embed/
    cluster involved.
    """
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt_anomaly import (
        backfill_action_geo_country,
        detect_gdelt_anomalies,
    )

    settings = get_settings()
    _require_db(settings)

    conn = get_connection(settings.db_path)

    if backfill_country:
        recovered = backfill_action_geo_country(conn)
        click.echo(f"Backfilled action_geo_country on {recovered} rows.\n")

    result = detect_gdelt_anomalies(
        conn,
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        min_events_per_day=min_events_per_day,
        whole_history=full,
    )
    conn.close()

    click.echo(
        f"\nGDELT anomalies result:\n"
        f"  Series checked: {result.series_checked}\n"
        f"  Events created: {result.events_created}"
    )


@ingest.command("gdelt-reset")
@click.option("--yes", is_flag=True, default=False,
              help="Actually delete. Without this flag, only previews counts.")
def ingest_gdelt_reset(yes: bool) -> None:
    """Wipe everything derived from origin='gdelt' for a clean re-ingest.

    Scope: raw_documents/gdelt_events/events with origin='gdelt', their
    vec_documents/document_entities, entities left with zero remaining
    mentions (elsewhere), affected entity_links, and gdelt_file_log (so
    `gdelt-history` re-downloads instead of skipping). RSS/Comtrade/PortWatch
    and everything derived from them are untouched. Defaults to a read-only
    preview — pass --yes to actually delete.
    """
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt_reset import preview_gdelt_reset, reset_gdelt

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if not yes:
        c = preview_gdelt_reset(conn)
        conn.close()
        click.echo(
            f"\nGDELT reset PREVIEW (nothing deleted — pass --yes to execute):\n"
            f"  raw_documents:      -{c.raw_documents:,}\n"
            f"  gdelt_events:       -{c.gdelt_events:,}\n"
            f"  events:             -{c.events:,}\n"
            f"  vec_documents:      -{c.vec_documents:,}\n"
            f"  document_entities:  -{c.document_entities:,}\n"
            f"  entities (orphaned):-{c.entities_orphaned:,}\n"
            f"  entity_links:       -{c.entity_links:,}\n"
            f"  gdelt_file_log:     -{c.file_log:,}"
        )
        return

    c = reset_gdelt(conn)
    conn.close()
    click.echo(
        f"\nGDELT reset complete:\n"
        f"  raw_documents:      -{c.raw_documents:,}\n"
        f"  gdelt_events:       -{c.gdelt_events:,}\n"
        f"  events:             -{c.events:,}\n"
        f"  vec_documents:      -{c.vec_documents:,}\n"
        f"  document_entities:  -{c.document_entities:,}\n"
        f"  entities (orphaned):-{c.entities_orphaned:,}\n"
        f"  entity_links:       -{c.entity_links:,}\n"
        f"  gdelt_file_log:     -{c.file_log:,}"
    )


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


# ─── cluster ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--time-window-hours",
    default=72,
    show_default=True,
    help="Cluster only docs published within N hours of each other.",
)
def cluster(time_window_hours: int) -> None:
    """Group deduped docs into events by semantic similarity."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.cluster import cluster_documents

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    result = cluster_documents(conn, time_window_hours=time_window_hours)
    click.echo(
        f"Cluster: {result.events_created} events created | "
        f"{result.docs_assigned} docs assigned"
    )
    conn.close()


# ─── extract ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--limit", default=None, type=int, help="Max docs to run NER on.")
@click.option("--max-lookups", default=50, show_default=True,
              help="Network lookup budget for geocoding and Wikidata (each).")
@click.option("--skip-geocode", is_flag=True, help="Skip Nominatim geocoding.")
@click.option("--skip-wikidata", is_flag=True, help="Skip Wikidata entity linking.")
@click.option("--backfill-demonyms", is_flag=True, default=False,
              help="Reclassify existing demonym entities (Israeli, Russian...) "
                   "to location+country before running NER.")
def extract(
    limit: int | None, max_lookups: int, skip_geocode: bool, skip_wikidata: bool,
    backfill_demonyms: bool,
) -> None:
    """Run NER, geocode events, link entities to Wikidata."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.extract import (
        backfill_demonym_entities,
        canonicalize_person_entities,
        extract_entities,
        geocode_events,
        link_wikidata,
    )

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if backfill_demonyms:
        reclassified = backfill_demonym_entities(conn)
        click.echo(f"Demonym backfill: {reclassified} entities reclassified to location\n")

    ner = extract_entities(conn, limit=limit)
    click.echo(
        f"\nNER: {ner.docs_processed} docs | +{ner.entities_created} entities | "
        f"{ner.mentions_recorded} mentions | {ner.docs_skipped} skipped"
    )

    canon = canonicalize_person_entities(conn)
    click.echo(
        f"Person canonicalization: {canon.exact_groups_merged} exact groups | "
        f"{canon.bare_surname_merged} bare surnames merged | "
        f"{canon.bare_surname_skipped} ambiguous (skipped)"
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
            f"{wd.conflicts} conflicts | {wd.stoplisted} stoplisted"
            + (" | RATE LIMITED" if wd.rate_limited else "")
        )

    conn.close()


# ─── story ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--time-window-days", default=10.0, show_default=True,
    help="Max span (days) a merged story can cover.",
)
def story(time_window_days: float) -> None:
    """Group micro-events into macro-stories via shared canonical person entities."""
    from pathosphere.db.schema import get_connection
    from pathosphere.semantic.story import link_related_events

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    result = link_related_events(conn, time_window_days=time_window_days)
    click.echo(
        f"Story linking: {result.stories_formed} stories formed | "
        f"{result.events_linked} events linked"
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


@thesis.command("list")
@click.option(
    "--status",
    default="pending",
    type=click.Choice(["pending", "approved", "rejected", "closed", "all"]),
    show_default=True,
    help="Filter by thesis status.",
)
def thesis_list(status: str) -> None:
    """List theses (default: pending)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.approval import list_theses

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if status == "all":
        rows = conn.execute(
            """
            SELECT id, title, instrument, direction, price_snapshot,
                   horizon_days, confidence, status, debate_id, created_at
            FROM theses ORDER BY id DESC
            """
        ).fetchall()
    else:
        rows = list_theses(conn, status=status)
    conn.close()

    if not rows:
        click.echo(f"No {status} theses.")
        return

    click.echo(
        f"\n{'ID':>4}  {'Title':<40}  {'Inst':<6}  {'Dir':<7}  "
        f"{'Price':>8}  {'Hor':>4}  {'Conf':>5}  {'Src':>5}  {'Status'}"
    )
    click.echo("─" * 100)
    for r in rows:
        src = "debate" if r["debate_id"] else "fast"
        price = f"{r['price_snapshot']:.2f}" if r["price_snapshot"] else "N/A"
        conf = f"{r['confidence']:.0%}" if r["confidence"] else "N/A"
        title = (r["title"] or "")[:40]
        click.echo(
            f"{r['id']:>4}  {title:<40}  {(r['instrument'] or ''):<6}  "
            f"{(r['direction'] or ''):<7}  {price:>8}  {(r['horizon_days'] or 0):>4}  "
            f"{conf:>5}  {src:>5}  {r['status']}"
        )


@thesis.command("show")
@click.argument("thesis_id", type=int)
def thesis_show(thesis_id: int) -> None:
    """Show full detail for a thesis (causal chain, debate context, watchlist)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.approval import format_causal_chain, get_thesis, get_watchlist_items

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    thesis = get_thesis(conn, thesis_id)
    if thesis is None:
        conn.close()
        click.echo(f"Thesis {thesis_id} not found.")
        raise SystemExit(1)

    watchlist = get_watchlist_items(conn, thesis_id)
    conn.close()

    chain = format_causal_chain(thesis["causal_chain"])

    click.echo(f"\n{'═' * 70}")
    click.echo(f"THESIS #{thesis_id}  [{thesis['status'].upper()}]")
    click.echo(f"{'═' * 70}")
    click.echo(f"Title       : {thesis['title']}")
    click.echo(f"Instrument  : {thesis['instrument'] or 'N/A'}  ({thesis['direction'] or 'N/A'})")
    click.echo(f"Price snap  : {thesis['price_snapshot']:.2f}" if thesis["price_snapshot"] else "Price snap  : N/A")
    click.echo(f"Horizon     : {thesis['horizon_days']}d" if thesis["horizon_days"] else "Horizon     : N/A")
    click.echo(f"Confidence  : {thesis['confidence']:.0%}" if thesis["confidence"] else "Confidence  : N/A")
    click.echo(f"Debate id   : {thesis['debate_id'] or 'N/A (fast path)'}")
    click.echo(f"Created     : {thesis['created_at']}")
    if thesis["approved_at"]:
        click.echo(f"Approved    : {thesis['approved_at']}")
    if thesis["rejected_at"]:
        click.echo(f"Rejected    : {thesis['rejected_at']}")
    if thesis["rejection_reason"]:
        click.echo(f"Rej. reason : {thesis['rejection_reason']}")

    click.echo(f"\n── Trigger ──────────────────────────────────────────────")
    click.echo(chain.get("trigger_summary") or "(none)")

    click.echo(f"\n── Causal chain ─────────────────────────────────────────")
    for i, step in enumerate(chain.get("steps", []), 1):
        click.echo(f"  {i}. {step}")

    click.echo(f"\n── Invalidation ─────────────────────────────────────────")
    click.echo(thesis["invalidation"] or "(none)")

    persona_notes = chain.get("persona_notes") or {}
    if persona_notes:
        click.echo(f"\n── Persona notes ────────────────────────────────────────")
        for persona, note in persona_notes.items():
            click.echo(f"  [{persona}] {note}")

    debate_ctx = chain.get("debate_context") or {}
    if debate_ctx:
        click.echo(f"\n── Debate context ───────────────────────────────────────")
        supporters = debate_ctx.get("supporters", [])
        opponents = debate_ctx.get("opponents", [])
        if supporters:
            click.echo(f"  Supporters: {', '.join(supporters)}")
        if opponents:
            click.echo(f"  Opponents : {', '.join(opponents)}")
        summary = debate_ctx.get("summary")
        if summary:
            click.echo(f"  Summary   : {summary}")

    if watchlist:
        click.echo(f"\n── Watchlist items ({len(watchlist)}) ──────────────────────────────")
        for w in watchlist:
            click.echo(f"  [{w['status']}] {w['label']}")
            if w["indicator_query"]:
                click.echo(f"           query: {w['indicator_query']}")

    click.echo(f"{'═' * 70}\n")


@thesis.command("approve")
@click.argument("thesis_id", type=int)
def thesis_approve(thesis_id: int) -> None:
    """Approve a pending thesis (status → approved). Validates ticker via yfinance."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.approval import approve_thesis, get_thesis, validate_ticker

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    # Pre-fetch thesis to run ticker validation before mutating
    thesis = get_thesis(conn, thesis_id)
    if thesis is None:
        conn.close()
        click.echo(f"Thesis {thesis_id} not found.")
        raise SystemExit(1)

    ticker = thesis["instrument"]
    if ticker:
        click.echo(f"Validating ticker {ticker}...", nl=False)
        ok = validate_ticker(ticker)
        if ok:
            click.echo(" OK")
        else:
            click.echo(f" WARNING: {ticker} not found on yfinance. Check the ticker before trading.")

    from pathosphere.agent.predictions import create_thesis_prediction

    try:
        updated = approve_thesis(conn, thesis_id)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)

    # v2: every approved thesis gets an auto economic prediction so the
    # geopolitical→thesis→trade→economic chain is measurable end to end.
    # Approval is already committed: a prediction failure must not mask it.
    pred_line = ""
    try:
        pred = create_thesis_prediction(conn, updated)
        pred_line = (f"\n  Economic prediction #{pred['id']} auto-created "
                     f"(p={pred['probability']:.0%}, horizon {pred['horizon_date']})")
    except (ValueError, sqlite3.Error) as exc:
        pred_line = (f"\n  WARNING: thesis approved but economic prediction "
                     f"NOT created: {exc}")
    conn.close()

    click.echo(
        f"\nThesis {thesis_id} approved.\n"
        f"  Title    : {updated['title']}\n"
        f"  Ticker   : {updated['instrument']} {updated['direction']}\n"
        f"  Approved : {updated['approved_at']}"
        + pred_line
    )


@thesis.command("reject")
@click.argument("thesis_id", type=int)
@click.option("--reason", required=True, help="Rejection reason (logged for calibration).")
def thesis_reject(thesis_id: int, reason: str) -> None:
    """Reject a pending thesis with a reason (status → rejected)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.approval import reject_thesis

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        updated = reject_thesis(conn, thesis_id, reason)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)
    conn.close()

    click.echo(
        f"\nThesis {thesis_id} rejected.\n"
        f"  Title   : {updated['title']}\n"
        f"  Reason  : {updated['rejection_reason']}\n"
        f"  Logged  : {updated['rejected_at']}"
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


# ─── portfolio ────────────────────────────────────────────────────────────────

@cli.group()
def portfolio() -> None:
    """Virtual portfolio management (paper trading)."""


@portfolio.command("init")
def portfolio_init() -> None:
    """Create agent / random / benchmark portfolios ($100k each).

    Benchmark opens a buy-and-hold SPY trade immediately.
    Safe to re-run: idempotent.
    """
    from pathosphere.db.schema import get_connection
    from pathosphere.market.trading import init_portfolios

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    result = init_portfolios(conn)
    conn.close()

    if result.portfolios_created:
        click.echo(f"Created  : {', '.join(result.portfolios_created)}")
    if result.portfolios_existing:
        click.echo(f"Existing : {', '.join(result.portfolios_existing)}")
    if result.benchmark_price:
        click.echo(f"Benchmark: SPY opened @ {result.benchmark_price:.2f}")
    elif not result.portfolios_existing or "benchmark" not in result.portfolios_existing:
        click.echo("Benchmark: SPY price unavailable — trade not opened (retry later)")


@portfolio.command("status")
def portfolio_status() -> None:
    """Show realized + unrealized P&L for all portfolios (fetches live prices)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.market.trading import INITIAL_CASH, get_portfolio_status

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)
    statuses = get_portfolio_status(conn)
    conn.close()

    if not statuses:
        click.echo("No portfolios found. Run: pathos portfolio init")
        return

    click.echo(f"\n{'Portfolio':<12} {'Type':<10} {'Realized':>10} {'Unreal.':>10} {'Total P&L':>10} {'Return':>8} {'Open':>5} {'Closed':>6}")
    click.echo("─" * 75)
    for s in statuses:
        click.echo(
            f"{s.name:<12} {s.portfolio_type:<10} "
            f"{s.realized_pnl:>+10.2f} {s.unrealized_pnl:>+10.2f} "
            f"{s.total_pnl:>+10.2f} {s.return_pct:>+7.2f}% "
            f"{s.open_trades:>5} {s.closed_trades:>6}"
        )

    click.echo(f"\nBase capital: ${INITIAL_CASH:,.0f} per portfolio")


# ─── trade ────────────────────────────────────────────────────────────────────

@cli.group()
def trade() -> None:
    """Paper trade management."""


@trade.command("open")
@click.argument("thesis_id", type=int)
def trade_open(thesis_id: int) -> None:
    """Open an agent trade + random control trade from an approved thesis.

    price_open = yfinance fetch at decision time (no-lookahead bias).
    Also opens a matching random trade (same qty/direction, random ticker).
    """
    from pathosphere.db.schema import get_connection
    from pathosphere.market.trading import open_agent_trade

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        result = open_agent_trade(conn, thesis_id)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)

    # v2: link the thesis's auto economic prediction to the opened agent trade
    from pathosphere.agent.predictions import link_thesis_prediction_to_trade

    linked = link_thesis_prediction_to_trade(conn, thesis_id, result.agent_trade_id)
    conn.close()

    click.echo(
        f"\nTrade opened:\n"
        f"  Agent  #{result.agent_trade_id:>4}  {result.ticker:<6}  {result.direction}  "
        f"qty={result.quantity:.4f}  @ {result.price_open:.2f}\n"
        f"  Random #{result.random_trade_id:>4}  {result.random_ticker:<6}  {result.direction}  "
        f"(control, same thesis_id={thesis_id})"
        + (f"\n  Economic prediction linked to trade #{result.agent_trade_id}" if linked else "")
    )


@trade.command("close")
@click.argument("trade_id", type=int)
def trade_close(trade_id: int) -> None:
    """Close a trade: fetch current price, compute P&L, persist."""
    from pathosphere.db.schema import get_connection
    from pathosphere.market.trading import close_trade

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        result = close_trade(conn, trade_id)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)
    conn.close()

    click.echo(
        f"\nTrade #{trade_id} closed:\n"
        f"  Ticker    : {result.ticker}  ({result.direction})\n"
        f"  Price open: {result.price_open:.2f}\n"
        f"  Price close: {result.price_close:.2f}\n"
        f"  Quantity  : {result.quantity:.4f}\n"
        f"  P&L       : {result.pnl:+.2f}"
    )


@trade.command("list")
@click.option("--portfolio", "portfolio_name", default=None,
              type=click.Choice(["agent", "random", "benchmark"]),
              help="Filter by portfolio [default: all].")
@click.option("--closed", is_flag=True, default=False,
              help="Show closed trades instead of open ones.")
def trade_list(portfolio_name: str | None, closed: bool) -> None:
    """List open (or closed) trades."""
    from pathosphere.db.schema import get_connection
    from pathosphere.market.trading import list_open_trades

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    if closed:
        query = """
            SELECT t.*, p.name AS portfolio_name
            FROM trades t JOIN portfolios p ON t.portfolio_id = p.id
            WHERE t.closed_at IS NOT NULL
        """
        params: tuple = ()
        if portfolio_name:
            query += " AND p.name = ?"
            params = (portfolio_name,)
        query += " ORDER BY t.closed_at DESC"
        rows = conn.execute(query, params).fetchall()
    else:
        rows = list_open_trades(conn, portfolio_name=portfolio_name)
    conn.close()

    if not rows:
        state = "closed" if closed else "open"
        click.echo(f"No {state} trades{' in ' + portfolio_name if portfolio_name else ''}.")
        return

    state_label = "closed" if closed else "open"
    click.echo(f"\n{'ID':>4}  {'Port':<10}  {'Ticker':<7}  {'Dir':<5}  {'Qty':>10}  {'Open':>8}  {'Close':>8}  {'P&L':>9}")
    click.echo("─" * 80)
    for r in rows:
        close_str = f"{r['price_close']:.2f}" if r["price_close"] else "—"
        pnl_str = f"{r['pnl']:+.2f}" if r["pnl"] is not None else "—"
        click.echo(
            f"{r['id']:>4}  {r['portfolio_name']:<10}  {r['ticker']:<7}  "
            f"{r['direction']:<5}  {r['quantity']:>10.4f}  "
            f"{r['price_open']:>8.2f}  {close_str:>8}  {pnl_str:>9}"
        )


# ─── predict ──────────────────────────────────────────────────────────────────

@cli.group()
def predict() -> None:
    """Non-financial predictions with Tetlock calibration."""


@predict.command("add")
@click.argument("description")
@click.option("--macro-area", required=True, type=click.Choice(VALID_MACRO_AREAS),
              help="Track: world (geopolitical/political/social) or economic.")
@click.option("--prediction-type", required=True,
              type=click.Choice(VALID_PREDICTION_TYPES),
              help="Granularity; must be coherent with --macro-area.")
@click.option("--probability", required=True, type=float,
              help="Subjective probability 0.0–1.0 (e.g. 0.65).")
@click.option("--horizon", "horizon_date", required=True,
              help="Deadline ISO YYYY-MM-DD (e.g. 2026-07-10).")
@click.option("--domain", "domains", multiple=True, required=True,
              type=click.Choice(VALID_DOMAINS),
              help="Domain from taxonomy (repeatable, at least one).")
@click.option("--primary-domain", default=None, type=click.Choice(VALID_DOMAINS),
              help="Primary domain [default: first --domain].")
@click.option("--origin-scope", default=None, type=click.Choice(VALID_SCOPES),
              help="Scope of the origin (required for world).")
@click.option("--impact-scope", default=None, type=click.Choice(VALID_SCOPES),
              help="Scope of the impact (required for world).")
@click.option("--thesis-id", default=None, type=int,
              help="Linked thesis id (required for economic).")
@click.option("--trade-id", default=None, type=int,
              help="Linked trade id (economic only).")
def predict_add(description: str, macro_area: str, prediction_type: str,
                probability: float, horizon_date: str, domains: tuple[str, ...],
                primary_domain: str | None, origin_scope: str | None,
                impact_scope: str | None, thesis_id: int | None,
                trade_id: int | None) -> None:
    """Add a prediction (world or economic track) for later resolution."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.predictions import add_prediction, get_prediction_domains

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        row = add_prediction(
            conn, description, probability, horizon_date,
            macro_area=macro_area, prediction_type=prediction_type,
            domains=list(domains), primary_domain=primary_domain,
            origin_scope=origin_scope, impact_scope=impact_scope,
            thesis_id=thesis_id, trade_id=trade_id,
        )
    except sqlite3.IntegrityError as exc:
        conn.close()
        click.echo(f"Error: invalid --thesis-id or --trade-id reference ({exc})")
        raise SystemExit(1)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)
    dom_rows = get_prediction_domains(conn, row["id"])
    conn.close()

    dom_str = ", ".join(
        f"{d['domain']}{'*' if d['is_primary'] else ''}" for d in dom_rows
    )
    click.echo(
        f"\nPrediction #{row['id']} added.\n"
        f"  Description: {row['description']}\n"
        f"  Track/type : {row['macro_area']} / {row['prediction_type']}\n"
        f"  Probability: {row['probability']:.0%}\n"
        f"  Horizon    : {row['horizon_date']} ({row['time_horizon_class']})\n"
        f"  Domains    : {dom_str}\n"
        f"  Scope      : {row['origin_scope'] or '—'} → {row['impact_scope'] or '—'}\n"
        f"  Thesis id  : {row['thesis_id'] or 'N/A'}"
    )


@predict.command("revise")
@click.argument("prediction_id", type=int)
@click.option("--probability", required=True, type=float,
              help="New probability 0.0–1.0.")
@click.option("--rationale", default=None,
              help="Reason for the revision (logged).")
def predict_revise(prediction_id: int, probability: float,
                   rationale: str | None) -> None:
    """Revise probability of an open prediction (revision history logged)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.predictions import revise_prediction, get_prediction_revisions

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        row = revise_prediction(conn, prediction_id, probability, rationale)
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)
    n_revisions = len(get_prediction_revisions(conn, prediction_id))
    conn.close()

    click.echo(
        f"\nPrediction #{prediction_id} revised.\n"
        f"  Description: {row['description']}\n"
        f"  Probability: {row['probability']:.0%}\n"
        f"  Revisions  : {n_revisions}"
    )


@predict.command("list")
@click.option("--open", "only_open", is_flag=True, default=False,
              help="Show only open (unresolved) predictions.")
@click.option("--resolved", "only_resolved", is_flag=True, default=False,
              help="Show only resolved predictions.")
@click.option("--macro-area", default=None, type=click.Choice(VALID_MACRO_AREAS),
              help="Filter by track.")
@click.option("--prediction-type", default=None,
              type=click.Choice(VALID_PREDICTION_TYPES),
              help="Filter by type.")
@click.option("--domain", default=None, type=click.Choice(VALID_DOMAINS),
              help="Filter by domain (taxonomy value).")
def predict_list(only_open: bool, only_resolved: bool, macro_area: str | None,
                 prediction_type: str | None, domain: str | None) -> None:
    """List predictions (default: all)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.predictions import list_predictions

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)
    rows = list_predictions(
        conn, only_open=only_open, only_resolved=only_resolved,
        macro_area=macro_area, prediction_type=prediction_type, domain=domain,
    )
    conn.close()

    if not rows:
        click.echo("No predictions found.")
        return

    click.echo(
        f"\n{'ID':>4}  {'Area':<8}  {'Type':<12}  {'Prob':>5}  {'Horizon':<12}  "
        f"{'St':<4}  {'Out':<5}  {'TAS':>6}  Description"
    )
    click.echo("─" * 110)
    for r in rows:
        status = "open" if not r["resolved"] else "done"
        # pre-v2 rows only have legacy `outcome`
        out = r["outcome_eventual"] if r["outcome_eventual"] is not None else r["outcome"]
        out_str = "true" if out == 1 else ("false" if out == 0 else "—")
        tas_str = (f"{r['time_adjusted_score']:.3f}"
                   if r["time_adjusted_score"] is not None else "—")
        desc = (r["description"] or "")[:45]
        click.echo(
            f"{r['id']:>4}  {r['macro_area']:<8}  {r['prediction_type']:<12}  "
            f"{r['probability']:>4.0%}  {r['horizon_date']:<12}  "
            f"{status:<4}  {out_str:<5}  {tas_str:>6}  {desc}"
        )


@predict.command("resolve")
@click.argument("prediction_id", type=int)
@click.option("--outcome-eventual", required=True, type=click.Choice(["true", "false"]),
              help="Did the event ever happen (timing-independent).")
@click.option("--resolved-date", required=True,
              help="Actual event date, or evaluation date if it never happened (YYYY-MM-DD).")
def predict_resolve(prediction_id: int, outcome_eventual: str,
                    resolved_date: str) -> None:
    """Resolve a prediction: timing-aware score + Brier."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.predictions import resolve_prediction

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)

    try:
        row = resolve_prediction(
            conn, prediction_id, outcome_eventual == "true", resolved_date
        )
    except ValueError as exc:
        conn.close()
        click.echo(f"Error: {exc}")
        raise SystemExit(1)
    conn.close()

    on_time_str = "true" if row["outcome_on_time"] == 1 else "false"
    click.echo(
        f"\nPrediction #{prediction_id} resolved.\n"
        f"  Description  : {row['description']}\n"
        f"  Probability  : {row['probability']:.0%}\n"
        f"  Eventual     : {'true' if row['outcome_eventual'] == 1 else 'false'}\n"
        f"  On time      : {on_time_str}  (horizon {row['horizon_date']}, actual {row['resolved_date']})\n"
        f"  Brier score  : {row['brier_score']:.4f}\n"
        f"  Time-adj sc. : {row['time_adjusted_score']:.4f}\n"
        f"  Resolved at  : {row['resolved_at']}"
    )


@predict.command("calibration")
def predict_calibration() -> None:
    """Show Brier score + per-bucket calibration breakdown (Tetlock-style)."""
    from pathosphere.db.schema import get_connection
    from pathosphere.agent.predictions import get_calibration

    settings = get_settings()
    _require_db(settings)
    conn = get_connection(settings.db_path)
    cal = get_calibration(conn)
    conn.close()

    total = cal["total_resolved"]
    if total == 0:
        click.echo("No resolved predictions yet.")
        return

    mean_bs = cal["mean_brier_score"]
    mean_tas = cal["mean_time_adjusted_score"]
    tas_str = f"{mean_tas:.4f}" if mean_tas is not None else "— (no v2 rows)"
    click.echo(
        f"\nCalibration summary ({total} resolved predictions):\n"
        f"  Mean time-adjusted score: {tas_str}  (1=perfect, 0=worst — primary)\n"
        f"  Mean Brier score        : {mean_bs:.4f}  "
        f"(0=perfect, 0.25=random, 1=worst)\n"
    )
    click.echo(
        f"  {'Bucket':<10}  {'Count':>5}  {'Mean Brier':>10}  {'Accuracy':>9}"
    )
    click.echo("  " + "─" * 40)
    for b in cal["buckets"]:
        brier_str = f"{b['mean_brier']:.4f}" if b["mean_brier"] is not None else "—"
        acc_str = f"{b['accuracy']:.0%}" if b["accuracy"] is not None else "—"
        click.echo(
            f"  {b['label']:<10}  {b['count']:>5}  {brier_str:>10}  {acc_str:>9}"
        )

    for section, key in (("By macro area", "by_macro_area"),
                         ("By prediction type", "by_prediction_type")):
        groups = cal[key]
        if not groups:
            continue
        click.echo(f"\n  {section}:")
        click.echo(f"  {'Group':<14}  {'Count':>5}  {'Time-adj':>9}  {'Brier':>7}")
        click.echo("  " + "─" * 42)
        for name, agg in groups.items():
            g_tas = (f"{agg['mean_time_adjusted_score']:.4f}"
                     if agg["mean_time_adjusted_score"] is not None else "—")
            g_brier = (f"{agg['mean_brier_score']:.4f}"
                       if agg["mean_brier_score"] is not None else "—")
            click.echo(f"  {name:<14}  {agg['count']:>5}  {g_tas:>9}  {g_brier:>7}")
