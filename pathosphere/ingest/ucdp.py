"""
UCDP GED ingestor — historical armed-conflict events (1989→today).

Georeferenced Event Dataset (Uppsala Conflict Data Program): one row per
violent event, precise lat/lon, real textual description (parties, place,
sources) — unlike GDELT's synthetic CAMEO docs it is usable on the map as-is.

Source: one-off CSV zip download (open, no token — the REST API requires an
access token, the download endpoint does not). ~380k rows; a min-deaths
filter keeps only significant events (default 25 → ~16k events).

Events land directly in `events` (event_type='conflict', origin='ucdp') with
lat/lon already set, so the extract phase's geocoder leaves them untouched.
No raw_documents: history is static anchor data for the map and future
"situations", not clustering input.

Tables updated: events
"""

import csv
import io
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from loguru import logger

UCDP_GED_ZIP_URL = "https://ucdp.uu.se/downloads/ged/ged251-csv.zip"

DEFAULT_MIN_DEATHS = 25

# type_of_violence codes (UCDP codebook)
_VIOLENCE_TYPE = {
    "1": "state-based conflict",
    "2": "non-state conflict",
    "3": "one-sided violence",
}


@dataclass
class UCDPResult:
    rows_read: int = 0
    rows_kept: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


def _severity(deaths: int) -> int:
    if deaths >= 1000:
        return 5
    if deaths >= 250:
        return 4
    if deaths >= 100:
        return 3
    if deaths >= 25:
        return 2
    return 1


def _date(raw: str) -> str:
    """'2017-07-31 00:00:00.000' → '2017-07-31'."""
    return (raw or "")[:10]


def _fetch_csv_lines(url: str, client: httpx.Client):
    """Download the GED zip and yield CSV text lines from the single member."""
    resp = client.get(url, timeout=600, follow_redirects=True)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise ValueError("no CSV member in UCDP zip")
    with zf.open(names[0]) as fh:
        yield from io.TextIOWrapper(fh, encoding="utf-8", newline="")


def ingest_ucdp(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    min_deaths: int = DEFAULT_MIN_DEATHS,
    start: str | None = None,
    end: str | None = None,
    csv_path: Path | None = None,
    url: str = UCDP_GED_ZIP_URL,
    client: httpx.Client | None = None,
) -> UCDPResult:
    """Load UCDP GED events with at least `min_deaths` best-estimate deaths.

    start/end: optional YYYY-MM-DD bounds on date_start.
    csv_path: use an already-downloaded CSV instead of fetching the zip
    (the download is ~29 MB compressed / 250 MB raw — reusable across runs).
    Idempotent: dedup by (title, first_seen), streaming read, batch commit.
    """
    result = UCDPResult()

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(
        f"UCDP GED: min_deaths={min_deaths}"
        + (f", from {start}" if start else "")
        + (f", to {end}" if end else "")
        + (f", local CSV {csv_path}" if csv_path else "")
    )

    try:
        if csv_path:
            fh = open(csv_path, newline="", encoding="utf-8")
            lines = fh
        else:
            fh = None
            lines = _fetch_csv_lines(url, client)

        try:
            reader = csv.DictReader(lines)
            with conn:
                for row in reader:
                    result.rows_read += 1
                    try:
                        deaths = int(row.get("best") or 0)
                    except ValueError:
                        continue
                    if deaths < min_deaths:
                        continue

                    date_start = _date(row.get("date_start", ""))
                    date_end = _date(row.get("date_end", "")) or date_start
                    if not date_start:
                        continue
                    if start and date_start < start:
                        continue
                    if end and date_start > end:
                        continue
                    result.rows_kept += 1

                    conflict = (row.get("conflict_name") or "").strip()
                    country = (row.get("country") or "").strip()
                    where = (
                        (row.get("where_description") or "").strip()
                        or (row.get("adm_1") or "").strip()
                        or country
                    )
                    title = f"{conflict} — {where}"

                    exists = conn.execute(
                        "SELECT 1 FROM events WHERE title = ? AND first_seen = ?",
                        (title, date_start),
                    ).fetchone()
                    if exists:
                        continue

                    side_a = (row.get("side_a") or "?").strip()
                    side_b = (row.get("side_b") or "?").strip()
                    civilians = row.get("deaths_civilians") or "0"
                    vtype = _VIOLENCE_TYPE.get(
                        (row.get("type_of_violence") or "").strip(), "violence"
                    )
                    span = (
                        f"on {date_start}" if date_end == date_start
                        else f"{date_start} to {date_end}"
                    )
                    summary = (
                        f"{vtype.capitalize()}: {side_a} vs {side_b}, "
                        f"{where}, {country} ({row.get('region', '')}), {span}. "
                        f"{deaths} deaths (best estimate, {civilians} civilians). "
                        f"UCDP GED id {row.get('id', '')}."
                    )

                    lat = float(row["latitude"]) if row.get("latitude") else None
                    lon = float(row["longitude"]) if row.get("longitude") else None

                    conn.execute(
                        """INSERT INTO events
                           (title, summary, first_seen, last_seen, event_type,
                            origin, severity, location_name, lat, lon)
                           VALUES (?, ?, ?, ?, 'conflict', 'ucdp', ?, ?, ?, ?)""",
                        (title, summary, date_start, date_end,
                         _severity(deaths), f"{where}, {country}", lat, lon),
                    )
                    result.events_created += 1
        finally:
            if fh:
                fh.close()
    except Exception as exc:
        result.errors.append(str(exc))
        logger.warning(f"UCDP ingest error: {exc}")
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"UCDP complete: {result.rows_read:,} rows read | "
        f"{result.rows_kept:,} kept (≥{min_deaths} deaths) | "
        f"+{result.events_created:,} events | {len(result.errors)} errors"
    )
    return result
