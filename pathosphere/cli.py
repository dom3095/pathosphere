"""CLI principale di Pathosphere — entry point: `pathos`."""

import click
from loguru import logger

from pathosphere.config import get_settings
from pathosphere.logging_setup import setup_logging


@click.group()
@click.option("--log-level", default=None, help="Override LOG_LEVEL (.env)")
def cli(log_level: str | None) -> None:
    """Pathosphere — OSINT intelligence su eventi critici globali."""
    if log_level:
        import os
        os.environ["LOG_LEVEL"] = log_level.upper()
    setup_logging()


# ─── db ───────────────────────────────────────────────────────────────────────

@cli.group()
def db() -> None:
    """Gestione database."""


@db.command("init")
def db_init() -> None:
    """Inizializza il database SQLite (crea tabelle e vec0)."""
    from pathosphere.db.schema import init_db
    settings = get_settings()
    logger.info(f"Inizializzazione DB: {settings.db_path}")
    init_db(settings.db_path)
    logger.success(f"Database pronto: {settings.db_path}")


@db.command("info")
def db_info() -> None:
    """Mostra info e conteggi delle tabelle principali."""
    from pathosphere.db.schema import get_connection
    settings = get_settings()
    if not settings.db_path.exists():
        click.echo("Database non trovato. Esegui: pathos db init")
        return
    conn = get_connection(settings.db_path)
    tables = [
        "sources", "raw_documents", "events", "entities",
        "entity_links", "theses", "trades", "portfolios", "predictions",
    ]
    click.echo(f"\nDatabase: {settings.db_path}")
    click.echo(f"{'Tabella':<25} {'Righe':>8}")
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
    help="Riprendi dal ciclo da questa fase.",
)
@click.option("--dry-run", is_flag=True, help="Simula il ciclo senza eseguire nulla.")
def cycle(from_phase: str | None, dry_run: bool) -> None:
    """Esegui il ciclo notturno completo (download → brief)."""
    from pathosphere.cycle.orchestrator import Phase, run_cycle

    start = None
    if from_phase:
        start = Phase[from_phase.upper()]

    if dry_run:
        logger.info("Modalità dry-run attiva")

    state = run_cycle(start_from=start, dry_run=dry_run)

    if state.errors:
        click.echo(f"\nCiclo interrotto. Errori: {list(state.errors.keys())}")
        click.echo(f"Riprendi con: pathos cycle --from-phase {list(state.errors.keys())[0].name.lower()}")
    else:
        click.echo(f"\nCiclo completato. Fasi: {[p.name for p in state.completed]}")


# ─── config ───────────────────────────────────────────────────────────────────

@cli.command()
def config() -> None:
    """Mostra la configurazione attiva."""
    settings = get_settings()
    click.echo("\nConfigurazione attiva:")
    for field_name, value in settings.model_dump().items():
        click.echo(f"  {field_name:<30} = {value}")


# ─── sources ──────────────────────────────────────────────────────────────────

@cli.group()
def sources() -> None:
    """Gestione catalogo fonti."""


@sources.command("list")
def sources_list() -> None:
    """Lista le fonti configurate."""
    from pathosphere.db.schema import get_connection
    settings = get_settings()
    if not settings.db_path.exists():
        click.echo("Database non trovato. Esegui: pathos db init")
        return
    conn = get_connection(settings.db_path)
    rows = conn.execute(
        "SELECT id, name, country, geopolitical_block, state_control, active FROM sources ORDER BY geopolitical_block, name"
    ).fetchall()
    if not rows:
        click.echo("Nessuna fonte configurata. Usa: pathos sources seed")
        return
    click.echo(f"\n{'ID':>4} {'Nome':<30} {'Paese':<8} {'Blocco':<12} {'Ctrl':>4} {'Attiva':>6}")
    click.echo("─" * 70)
    for r in rows:
        click.echo(f"{r['id']:>4} {r['name']:<30} {r['country']:<8} {r['geopolitical_block']:<12} {r['state_control']:>4} {'sì' if r['active'] else 'no':>6}")
    conn.close()


@sources.command("seed")
def sources_seed() -> None:
    """Popola il catalogo con le fonti predefinite del progetto."""
    from pathosphere.db.schema import get_connection
    settings = get_settings()
    conn = get_connection(settings.db_path)
    _seed_sources(conn)
    conn.close()
    logger.success("Fonti predefinite inserite.")


def _seed_sources(conn: "sqlite3.Connection") -> None:  # type: ignore[name-defined]
    import sqlite3
    DEFAULT_SOURCES = [
        # name, url, country, block, orientation, state_control, language
        ("Reuters", "https://feeds.reuters.com/reuters/worldNews", "GB", "western", "independent", 0, "en"),
        ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml", "GB", "western", "public", 1, "en"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "QA", "arab", "state", 2, "en"),
        ("Xinhua", "http://www.xinhuanet.com/english/rss/worldrss.xml", "CN", "china", "state", 3, "en"),
        ("Global Times", "https://www.globaltimes.cn/rss/outbrain.xml", "CN", "china", "state", 3, "en"),
        ("TASS", "https://tass.com/rss/v2.xml", "RU", "russia", "state", 3, "en"),
        ("RT", "https://www.rt.com/rss/news/", "RU", "russia", "state", 3, "en"),
        ("Press TV", "https://www.presstv.ir/homepageVideos.xml", "IR", "arab", "state", 3, "en"),
        ("Anadolu Agency", "https://www.aa.com.tr/en/rss/default?cat=world", "TR", "arab", "state", 1, "en"),
        ("The Hindu", "https://www.thehindu.com/news/international/?service=rss", "IN", "india", "independent", 0, "en"),
        ("Folha de São Paulo", "https://feeds.folha.uol.com.br/mundo/rss091.xml", "BR", "latam", "independent", 0, "pt"),
        ("AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "ZA", "africa", "independent", 0, "en"),
        ("AP News", "https://rsshub.app/apnews/topics/world-news", "US", "western", "independent", 0, "en"),
        ("France 24", "https://www.france24.com/en/rss", "FR", "western", "public", 1, "en"),
        ("DW World", "https://rss.dw.com/xml/rss-en-world", "DE", "western", "public", 1, "en"),
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO sources
           (name, url, country, geopolitical_block, orientation, state_control, language)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        DEFAULT_SOURCES,
    )
    conn.commit()


# ─── ingest ───────────────────────────────────────────────────────────────────

@cli.group()
def ingest() -> None:
    """Ingestione dati dalle fonti."""


def _require_db(settings):
    if not settings.db_path.exists():
        click.echo("Database non trovato. Esegui prima: pathos db init")
        raise SystemExit(1)


@ingest.command("gdelt")
@click.option("--days", default=1, show_default=True, help="Quanti giorni indietro.")
@click.option(
    "--quad",
    default="conflict",
    type=click.Choice(["conflict", "all"]),
    show_default=True,
    help="conflict=QuadClass 3-4 | all=1-4",
)
@click.option("--min-mentions", default=10, show_default=True, help="Filtro NumMentions minimo.")
@click.option("--max-goldstein", default=None, type=float, help="Mantieni solo GoldsteinScale ≤ valore.")
@click.option("--countries", default=None, help="ISO-2 separati da virgola (es. CN,US,TW).")
@click.option("--max-files", default=None, type=int, help="Limita n. file (utile per test).")
@click.option("--no-skip", is_flag=True, help="Riscaricare file già presenti nel log.")
def ingest_gdelt(
    days: int,
    quad: str,
    min_mentions: int,
    max_goldstein: float | None,
    countries: str | None,
    max_files: int | None,
    no_skip: bool,
) -> None:
    """Scarica eventi GDELT 2.0 per gli ultimi N giorni (ciclo incrementale)."""
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
        f"\nRisultato GDELT:\n"
        f"  File:    {result.files_ok} ok | {result.files_skipped} saltati | {result.files_error} errori\n"
        f"  Righe:   {result.rows_raw:,} raw → {result.rows_filtered:,} filtrate\n"
        f"  Insert:  {result.events_inserted:,} eventi | {result.docs_inserted:,} documenti"
    )
    if result.errors:
        click.echo(f"\nPrimi errori: {result.errors[:3]}")


@ingest.command("gdelt-history")
@click.option(
    "--start", required=True,
    help="Data inizio (YYYY-MM-DD).",
)
@click.option(
    "--end", default=None,
    help="Data fine escl. (YYYY-MM-DD). Default: ieri.",
)
@click.option(
    "--sample-hours", default=1, show_default=True,
    help="Scarica 1 file ogni N ore. Default=1 (ogni ora, buona copertura). "
         "Usa 6 per un bootstrap rapido, 0 per tutto (ogni 15 min, ~7 notti).",
)
@click.option("--min-mentions", default=10, show_default=True, help="Soglia NumMentions.")
@click.option(
    "--quad",
    default="conflict",
    type=click.Choice(["conflict", "all"]),
    show_default=True,
)
@click.option("--countries", default=None, help="ISO-2 separati da virgola.")
def ingest_gdelt_history(
    start: str,
    end: str | None,
    sample_hours: int,
    min_mentions: int,
    quad: str,
    countries: str | None,
) -> None:
    """
    Download bulk storico GDELT per un intervallo di date (operazione una-tantum, ripartibile).

    Campiona 1 file ogni sample-hours ore per ridurre il volume. Gli eventi con
    copertura significativa (min-mentions ≥ 10) appaiono in molte finestre
    consecutive, quindi l'orario (default 1h) cattura tutto ciò che conta.

    Stime per 5 anni di storico:
      --sample-hours 1  → ~43k file, ~2 notti (raccomandato)
      --sample-hours 2  → ~22k file, ~1 notte
      --sample-hours 6  → ~7k file,  ~2.5h  (bootstrap rapido)
      --sample-hours 0  → tutti i file ogni 15min, ~7 notti

    Ripartibile: Ctrl+C e rilancia — salta automaticamente i file già scaricati.

    Esempio:
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
        filter_rows,
        store_rows,
    )

    settings = get_settings()
    _require_db(settings)

    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        click.echo(f"Formato data non valido: {start} (usa YYYY-MM-DD)")
        raise SystemExit(1)

    end_date = date.fromisoformat(end) if end else date.today()
    qc = QUAD_CONFLICT if quad == "conflict" else QUAD_ALL
    ctry = set(c.strip().upper() for c in countries.split(",")) if countries else None

    # Genera URL campionati
    # sample_hours=0 → tutti i file ogni 15 minuti
    # sample_hours=N → 1 file per ogni N ore (al minuto :00 dell'ora scelta)
    urls: list[tuple[str, str]] = []
    cursor = start_date
    if sample_hours == 0:
        # download completo: ogni 15 minuti
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
        f"GDELT storico: {start_date} → {end_date} | "
        f"{total} file "
        f"({'ogni 15min' if sample_hours == 0 else f'ogni {sample_hours}h'}) | "
        f"min_mentions={min_mentions}"
    )
    est_hours = total * 1.2 / 3600
    click.echo(f"Stima: ~{est_hours:.1f}h ({est_hours/8:.1f} notti da 8h). Ripartibile con Ctrl+C.\n")

    import httpx
    from pathosphere.ingest.gdelt import _fetch_zip

    conn = get_connection(settings.db_path)
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
                logger.warning(f"Errore {fname}: {exc}")
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
                ev_ins, doc_ins = store_rows(conn, filtered)
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
                pct = i / total * 100
                click.echo(
                    f"[{pct:5.1f}%] {i}/{total} | "
                    f"ok={files_ok} skip={files_skip} err={files_err} | "
                    f"eventi={ev_total:,} doc={doc_total:,}"
                )

    conn.close()
    click.echo(
        f"\nStorico GDELT completato:\n"
        f"  File:   {files_ok} ok | {files_skip} saltati | {files_err} errori\n"
        f"  Righe:  {rows_raw_total:,} raw → {rows_filt_total:,} filtrate\n"
        f"  Insert: {ev_total:,} eventi | {doc_total:,} documenti"
    )
