"""
GDELT 2.0 Events ingestion.

GDELT publishes compressed CSV files every 15 minutes:
  http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.export.CSV.zip

Each file is TSV (tab-separated, no header) with 61 columns.
We filter by QuadClass (conflicts) and NumMentions (relevance) before
storing, so the LLM sees only significant events.

Tables updated: raw_documents, events, event_documents, gdelt_file_log.
"""

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

GDELT_BASE = "http://data.gdeltproject.org/gdeltv2"

# 61 columns GDELT 2.0 Events (tab-separated, no header in file)
GDELT_COLS = [
    "GlobalEventID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode",
    "QuadClass", "GoldsteinScale", "NumMentions", "NumSources", "NumArticles",
    "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code", "Actor1Geo_Lat", "Actor1Geo_Long",
    "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code", "Actor2Geo_Lat", "Actor2Geo_Long",
    "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_ADM2Code", "ActionGeo_Lat", "ActionGeo_Long",
    "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL",
]

# QuadClass: 1=Verbal Cooperation, 2=Material Cooperation,
#            3=Verbal Conflict, 4=Material Conflict
QUAD_CONFLICT = {3, 4}
QUAD_ALL = {1, 2, 3, 4}

# EventRootCode CAMEO → human-readable label
EVENT_TYPE_MAP: dict[str, str] = {
    "01": "statement", "02": "appeal", "03": "cooperate_intent",
    "04": "consult", "05": "diplomatic", "06": "material_coop",
    "07": "aid", "08": "yield", "09": "investigate",
    "10": "demand", "11": "disapprove", "12": "reject",
    "13": "threaten", "14": "protest", "15": "force_posture",
    "16": "reduce_relations", "17": "coerce", "18": "assault",
    "19": "fight", "20": "mass_violence",
}


# ──────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    files_attempted: int = 0
    files_ok: int = 0
    files_skipped: int = 0
    files_error: int = 0
    rows_raw: int = 0
    rows_filtered: int = 0
    events_inserted: int = 0
    docs_inserted: int = 0
    errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# URL generation
# ──────────────────────────────────────────────────────────────────────────────

def generate_file_urls(n_days: int) -> list[tuple[str, str]]:
    """
    Generate list of (filename, url) for the last n_days complete days.
    GDELT publishes files every 15 minutes: HH:00, HH:15, HH:30, HH:45.
    """
    urls = []
    today = datetime.now(timezone.utc).date()
    for delta in range(1, n_days + 1):
        day = today - timedelta(days=delta)
        day_str = day.strftime("%Y%m%d")
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                fname = f"{day_str}{hour:02d}{minute:02d}00.export.CSV.zip"
                urls.append((fname, f"{GDELT_BASE}/{fname}"))
    return urls


# ──────────────────────────────────────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _fetch_zip(url: str, client: httpx.Client) -> bytes:
    resp = client.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.content


def _extract_csv(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".CSV"))
        return zf.read(csv_name).decode("utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# Parsing and filtering
# ──────────────────────────────────────────────────────────────────────────────

def _parse_rows(csv_text: str) -> Iterator[dict]:
    reader = csv.DictReader(
        io.StringIO(csv_text),
        fieldnames=GDELT_COLS,
        delimiter="\t",
    )
    for row in reader:
        yield row


def _safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _safe_float(v: str, default: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def filter_rows(
    rows: Iterator[dict],
    quad_classes: set[int],
    min_mentions: int,
    min_goldstein: float | None,
    countries: set[str] | None,
) -> list[dict]:
    """
    Filter GDELT rows by relevance.

    quad_classes  : set of allowed QuadClass values (1-4)
    min_mentions  : minimum NumMentions threshold
    min_goldstein : if set, discard events with GoldsteinScale > threshold
                    (negative values = destabilising)
    countries     : if set, keep only events where Actor1, Actor2,
                    or ActionGeo belongs to these countries (ISO 2)
    """
    kept = []
    for row in rows:
        qc = _safe_int(row.get("QuadClass", "0"))
        if qc not in quad_classes:
            continue

        nm = _safe_int(row.get("NumMentions", "0"))
        if nm < min_mentions:
            continue

        if min_goldstein is not None:
            gs = _safe_float(row.get("GoldsteinScale", "0"))
            if gs > min_goldstein:
                continue

        if countries:
            involved = {
                row.get("Actor1CountryCode", ""),
                row.get("Actor2CountryCode", ""),
                row.get("ActionGeo_CountryCode", ""),
            }
            if not involved.intersection(countries):
                continue

        kept.append(row)
    return kept


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

def _sqldate_to_iso(sqldate: str) -> str:
    """YYYYMMDD → YYYY-MM-DDT00:00:00 (full ISO for consistent datetime comparisons)"""
    if len(sqldate) == 8:
        return f"{sqldate[:4]}-{sqldate[4:6]}-{sqldate[6:]}T00:00:00"
    return sqldate


def build_lookup_caches(conn: "sqlite3.Connection") -> tuple[dict, dict]:  # type: ignore[name-defined]
    """
    Pre-load known URLs and event keys into dicts for O(1) in-memory dedup.
    Call once before the ingest loop; pass the dicts to store_rows.
    """
    url_to_id: dict[str, int] = {
        r[0]: r[1]
        for r in conn.execute("SELECT url, id FROM raw_documents WHERE url IS NOT NULL")
    }
    event_key_to_id: dict[str, int] = {
        r[0]: r[1]
        for r in conn.execute("SELECT title, id FROM events")
    }
    return url_to_id, event_key_to_id


def store_rows(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    rows: list[dict],
    url_to_id: dict[str, int] | None = None,
    event_key_to_id: dict[str, int] | None = None,
) -> tuple[int, int]:
    """
    Insert events and documents into the DB.
    Returns (events_inserted, docs_inserted).

    Pass url_to_id and event_key_to_id (from build_lookup_caches) to avoid
    per-row SELECT queries — critical for bulk historical ingestion.
    When None, falls back to SELECT-based dedup (backward compatible).
    """
    events_ins = 0
    docs_ins = 0

    for row in rows:
        source_url = row.get("SOURCEURL", "").strip()
        if not source_url:
            continue

        # ── raw_document (dedup by URL) ───────────────────────────────────
        if url_to_id is not None:
            doc_id = url_to_id.get(source_url)
        else:
            existing = conn.execute(
                "SELECT id FROM raw_documents WHERE url = ?", (source_url,)
            ).fetchone()
            doc_id = existing["id"] if existing else None

        if doc_id is None:
            url_hash = hashlib.sha256(source_url.encode()).hexdigest()
            cur = conn.execute(
                """INSERT INTO raw_documents
                   (url, title, published_at, content_hash, embedded)
                   VALUES (?, ?, ?, ?, 0)""",
                (
                    source_url,
                    f"GDELT: {row.get('Actor1Name','')} → {row.get('Actor2Name','')} [{row.get('EventCode','')}]",
                    _sqldate_to_iso(row.get("SQLDATE", "")),
                    url_hash,
                ),
            )
            doc_id = cur.lastrowid
            docs_ins += 1
            if url_to_id is not None:
                url_to_id[source_url] = doc_id

        # ── event (dedup by semantic key) ─────────────────────────────────
        event_key_str = "|".join((
            row.get("Actor1CountryCode", ""),
            row.get("Actor2CountryCode", ""),
            row.get("EventRootCode", ""),
            row.get("SQLDATE", ""),
            row.get("ActionGeo_CountryCode", ""),
        ))

        if event_key_to_id is not None:
            event_id = event_key_to_id.get(event_key_str)
        else:
            existing = conn.execute(
                "SELECT id FROM events WHERE title = ?", (event_key_str,)
            ).fetchone()
            event_id = existing["id"] if existing else None

        if event_id is None:
            event_type = EVENT_TYPE_MAP.get(row.get("EventRootCode", ""), "other")
            gs = _safe_float(row.get("GoldsteinScale", "0"))
            severity = max(1, min(5, int(abs(gs) / 2) + 1)) if gs != 0 else 1

            lat_str = row.get("ActionGeo_Lat", "")
            lon_str = row.get("ActionGeo_Long", "")
            lat = _safe_float(lat_str) if lat_str else None
            lon = _safe_float(lon_str) if lon_str else None

            iso_date = _sqldate_to_iso(row.get("SQLDATE", ""))
            cur = conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type,
                    severity, location_name, lat, lon)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_key_str,
                    (
                        f"{row.get('Actor1Name','')} [{row.get('Actor1CountryCode','')}]"
                        f" — {row.get('EventCode','')} —"
                        f" {row.get('Actor2Name','')} [{row.get('Actor2CountryCode','')}]"
                        f" | Goldstein={row.get('GoldsteinScale','')} Tone={row.get('AvgTone','')}"
                    ),
                    iso_date,
                    iso_date,
                    event_type,
                    severity,
                    row.get("ActionGeo_FullName") or None,
                    lat,
                    lon,
                ),
            )
            event_id = cur.lastrowid
            events_ins += 1
            if event_key_to_id is not None:
                event_key_to_id[event_key_str] = event_id

        # ── event ↔ document link ─────────────────────────────────────────
        conn.execute(
            "INSERT OR IGNORE INTO event_documents (event_id, document_id) VALUES (?, ?)",
            (event_id, doc_id),
        )

    return events_ins, docs_ins


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def ingest_gdelt(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    n_days: int = 1,
    quad_classes: set[int] = QUAD_CONFLICT,
    min_mentions: int = 10,
    min_goldstein: float | None = None,
    countries: set[str] | None = None,
    max_files: int | None = None,
    skip_existing: bool = True,
) -> IngestResult:
    """
    Download and insert GDELT events for the last n_days days.

    n_days        : how many days back (default 1)
    quad_classes  : QuadClass to keep ({3,4}=conflicts only, {1,2,3,4}=all)
    min_mentions  : minimum filter for NumMentions
    min_goldstein : keep only events with GoldsteinScale ≤ threshold (e.g. -1.0)
    countries     : filter by ISO-2 country code (None = all)
    max_files     : limit number of files (useful for testing)
    skip_existing : skip files already present in gdelt_file_log
    """
    result = IngestResult()
    file_list = generate_file_urls(n_days)
    if max_files:
        file_list = file_list[:max_files]

    logger.info(
        f"GDELT: {len(file_list)} files to download "
        f"(last {n_days} days, quad={quad_classes}, min_mentions={min_mentions})"
    )

    url_to_id, event_key_to_id = build_lookup_caches(conn)
    logger.debug(f"Lookup caches: {len(url_to_id)} urls, {len(event_key_to_id)} event keys")

    with httpx.Client(
        headers={"User-Agent": "pathosphere/0.1 OSINT research"},
        timeout=30,
    ) as client:
        for fname, url in file_list:
            result.files_attempted += 1

            # skip if already downloaded
            if skip_existing:
                already = conn.execute(
                    "SELECT id FROM gdelt_file_log WHERE filename = ?", (fname,)
                ).fetchone()
                if already:
                    result.files_skipped += 1
                    continue

            try:
                logger.debug(f"Downloading {fname}")
                zip_bytes = _fetch_zip(url, client)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # File not yet available (normal GDELT gap)
                    result.files_skipped += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO gdelt_file_log (filename, url, rows_raw, rows_stored, status) VALUES (?,?,0,0,'skipped')",
                        (fname, url),
                    )
                    conn.commit()
                    continue
                result.files_error += 1
                result.errors.append(f"{fname}: {exc}")
                logger.warning(f"HTTP error {exc.response.status_code} for {fname}")
                continue
            except Exception as exc:
                result.files_error += 1
                result.errors.append(f"{fname}: {exc}")
                logger.warning(f"Download error {fname}: {exc}")
                continue

            try:
                csv_text = _extract_csv(zip_bytes)
            except Exception as exc:
                result.files_error += 1
                result.errors.append(f"{fname} (zip): {exc}")
                logger.warning(f"Extraction error {fname}: {exc}")
                continue

            raw_rows = list(_parse_rows(csv_text))
            result.rows_raw += len(raw_rows)

            filtered = filter_rows(
                iter(raw_rows),
                quad_classes=quad_classes,
                min_mentions=min_mentions,
                min_goldstein=min_goldstein,
                countries=countries,
            )
            result.rows_filtered += len(filtered)

            with conn:
                ev_ins, doc_ins = store_rows(conn, filtered, url_to_id, event_key_to_id)
                result.events_inserted += ev_ins
                result.docs_inserted += doc_ins

                conn.execute(
                    """INSERT OR IGNORE INTO gdelt_file_log
                       (filename, url, rows_raw, rows_stored, status)
                       VALUES (?, ?, ?, ?, 'ok')""",
                    (fname, url, len(raw_rows), len(filtered)),
                )

            result.files_ok += 1
            logger.debug(
                f"  {fname}: {len(raw_rows)} rows → {len(filtered)} filtered "
                f"(+{ev_ins} events, +{doc_ins} docs)"
            )

    logger.info(
        f"GDELT complete: {result.files_ok} files ok, "
        f"{result.files_skipped} skipped, {result.files_error} errors | "
        f"{result.rows_raw} rows raw → {result.rows_filtered} filtered → "
        f"{result.events_inserted} events, {result.docs_inserted} docs inserted"
    )
    return result
