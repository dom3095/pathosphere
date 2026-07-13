"""
Physical-signal ingestors — USGS earthquakes + NASA FIRMS fires.

Both emit `events` with event_type='hazard' (lat/lon already known, so the
extract phase's geocoder leaves them untouched).

USGS earthquakes (no key):
  FDSNWS GeoJSON, significant quakes only (min magnitude filter) — one event
  per quake, deduped by (title, first_seen). Supports historical backfill
  (--start) and incremental resume from the last stored quake.

NASA FIRMS fires (free MAP_KEY required):
  Active-fire CSV per chokepoint-aligned area. Raw detections are numerous, so
  daily per-area counts land in `fire_metrics` (timeseries) and only z-score
  anomalies vs the trailing baseline surface as hazard events (principle: the
  LLM sees signals, not thousands of pixels). Mirrors PortWatch. Supports
  historical backfill (--start, archive source VIIRS_NOAA20_SP, NRT fallback) in ≤10-day
  windows and incremental resume from the last stored date per area.
  Skips gracefully when no MAP_KEY is configured.

Tables updated: events, fire_metrics
"""

import csv
import io
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta, timezone

import httpx
from loguru import logger

from pathosphere.ingest.anomaly import find_anomalies

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

DEFAULT_MIN_MAGNITUDE = 5.0
DEFAULT_QUAKE_DAYS = 1

# FIRMS thermal monitoring aligned with PortWatch's 28 strategic chokepoints:
# a fire/heat spike near a strait can signal a burning tanker, a port/refinery
# hit, or coastal conflict. Centres (lat, lon) → bbox generated with a margin.
FIRMS_BBOX_MARGIN = 2.0   # degrees around each centre

CHOKEPOINT_COORDS: dict[str, tuple[float, float]] = {
    "Suez Canal": (30.5, 32.35),
    "Panama Canal": (9.1, -79.7),
    "Bosporus Strait": (41.1, 29.05),
    "Bab el-Mandeb Strait": (12.6, 43.4),
    "Malacca Strait": (2.5, 101.0),
    "Strait of Hormuz": (26.6, 56.3),
    "Cape of Good Hope": (-34.35, 18.5),
    "Gibraltar Strait": (35.95, -5.5),
    "Dover Strait": (51.0, 1.5),
    "Oresund Strait": (55.7, 12.7),
    "Taiwan Strait": (24.5, 119.5),
    "Korea Strait": (34.5, 129.0),
    "Tsugaru Strait": (41.5, 140.7),
    "Luzon Strait": (20.5, 121.0),
    "Lombok Strait": (-8.7, 115.8),
    "Ombai Strait": (-8.5, 125.0),
    "Bohai Strait": (38.0, 120.9),
    "Torres Strait": (-10.0, 142.5),
    "Sunda Strait": (-6.0, 105.8),
    "Makassar Strait": (-2.0, 118.0),
    "Magellan Strait": (-53.5, -70.5),
    "Yucatan Channel": (21.5, -85.5),
    "Windward Passage": (20.0, -73.7),
    "Mona Passage": (18.5, -67.9),
    "Balabac Strait": (7.8, 117.0),
    "Bering Strait": (65.8, -169.0),
    "Mindoro Strait": (12.5, 120.5),
    "Kerch Strait": (45.3, 36.5),
}


def _bbox(lat: float, lon: float, margin: float = FIRMS_BBOX_MARGIN) -> str:
    """FIRMS 'west,south,east,north' box around a centre."""
    return (f"{lon - margin:.2f},{lat - margin:.2f},"
            f"{lon + margin:.2f},{lat + margin:.2f}")


# name → bbox, one per chokepoint (same names as PortWatch STRATEGIC_CHOKEPOINTS).
FIRMS_AREAS: dict[str, str] = {
    name: _bbox(lat, lon) for name, (lat, lon) in CHOKEPOINT_COORDS.items()
}

# NRT = near-real-time (~last 60 days); SP = standard processing archive.
# NOAA-20 (J1) is the primary operational VIIRS satellite; Suomi NPP (SNPP) is
# the legacy predecessor — its SP archive may lag or be unavailable for recent
# dates (2025+), so NOAA-20 variants are now the defaults.
DEFAULT_FIRMS_SOURCE = "VIIRS_NOAA20_NRT"
ARCHIVE_FIRMS_SOURCE = "VIIRS_NOAA20_SP"
# NRT fallback used when SP returns 400 (data not yet processed / not available).
_NRT_FALLBACK: dict[str, str] = {
    "VIIRS_NOAA20_SP": "VIIRS_NOAA20_NRT",
    "VIIRS_SNPP_SP": "VIIRS_SNPP_NRT",
    "MODIS_SP": "MODIS_NRT",
}
DEFAULT_FIRMS_DAYS = 1          # incremental window (most-recent days) when no history
FIRMS_MAX_SPAN = 5             # FIRMS area API hard cap: ≤5 days per request (both NRT and SP)
FIRMS_REQUEST_DELAY = 0.12    # seconds between area-API requests to avoid rate-limiting

# Anomaly detection (mirror of PortWatch): z-score the latest daily detection
# count against the trailing baseline; an absolute floor avoids firing on tiny
# baselines (0→3 detections is noise, not signal).
DEFAULT_FIRE_BASELINE_DAYS = 30
DEFAULT_FIRE_Z_THRESHOLD = 2.0
DEFAULT_FIRE_MIN_DETECTIONS = 50   # latest count must exceed this to surface


@dataclass
class USGSResult:
    quakes_fetched: int = 0
    events_created: int = 0
    starttime: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class FIRMSResult:
    areas_checked: int = 0
    windows_fetched: int = 0
    detections_total: int = 0
    metrics_upserted: int = 0
    events_created: int = 0
    skipped_no_key: bool = False
    errors: list[str] = field(default_factory=list)


def _epoch_ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    except (ValueError, TypeError, OverflowError):
        return None


def _quake_severity(mag: float) -> int:
    return max(1, min(5, int(mag) - 2))


def _last_event_date(conn, origin: str) -> str | None:
    """Latest first_seen (YYYY-MM-DD) among events from a given ingestor."""
    row = conn.execute(
        "SELECT max(first_seen) FROM events WHERE origin = ?", (origin,)
    ).fetchone()
    return row[0][:10] if row and row[0] else None


# ──────────────────────────────────────────────────────────────────────────
# USGS earthquakes
# ──────────────────────────────────────────────────────────────────────────

def ingest_usgs(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    min_magnitude: float = DEFAULT_MIN_MAGNITUDE,
    days: int = DEFAULT_QUAKE_DAYS,
    start: str | None = None,
    end: str | None = None,
    client: httpx.Client | None = None,
) -> USGSResult:
    """Fetch significant earthquakes and store each as a hazard event.

    start: explicit YYYY-MM-DD historical backfill anchor (overrides --days).
    When None, resume from the last stored USGS quake (incremental); if none
    exists, fall back to `days` back from now.
    """
    result = USGSResult()

    if start:
        starttime = start
    else:
        last = _last_event_date(conn, "usgs")
        starttime = last or (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
    result.starttime = starttime

    params = {
        "format": "geojson",
        "starttime": starttime,
        "minmagnitude": min_magnitude,
        "orderby": "time",
    }
    if end:
        params["endtime"] = end

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(f"USGS: quakes since {starttime}, M>={min_magnitude}")

    try:
        resp = client.get(USGS_URL, params=params, timeout=60)
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as exc:
        result.errors.append(str(exc))
        logger.warning(f"USGS fetch error: {exc}")
        if _own_client:
            client.close()
        return result

    result.quakes_fetched = len(features)

    with conn:
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None, None]
            mag = props.get("mag")
            place = props.get("place") or "unknown location"
            iso_time = _epoch_ms_to_iso(props.get("time"))
            if mag is None or iso_time is None:
                continue

            lon, lat = coords[0], coords[1]
            depth = coords[2] if len(coords) > 2 else None
            title = f"M{mag:.1f} earthquake — {place}"

            exists = conn.execute(
                "SELECT 1 FROM events WHERE title = ? AND first_seen = ?",
                (title, iso_time),
            ).fetchone()
            if exists:
                continue

            summary = (
                f"Magnitude {mag} earthquake, {place}, at {iso_time} UTC"
                + (f", depth {depth} km." if depth is not None else ".")
            )
            conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type, origin,
                    severity, location_name, lat, lon)
                   VALUES (?, ?, ?, ?, 'hazard', 'usgs', ?, ?, ?, ?)""",
                (title, summary, iso_time, iso_time,
                 _quake_severity(mag), place, lat, lon),
            )
            result.events_created += 1

    if _own_client:
        client.close()

    logger.info(
        f"USGS complete: {result.quakes_fetched} quakes, "
        f"+{result.events_created} events"
    )
    return result


# ──────────────────────────────────────────────────────────────────────────
# NASA FIRMS fires
# ──────────────────────────────────────────────────────────────────────────

def _windows(
    start: date_cls, end: date_cls, max_span: int = FIRMS_MAX_SPAN
) -> list[tuple[str, int]]:
    """Split [start, end] into (start_date_iso, span_days) chunks of ≤max_span.

    FIRMS area API caps each request at 10 days; a window's start date is the
    range anchor and span the number of days fetched from it (inclusive).
    """
    out: list[tuple[str, int]] = []
    cur = start
    while cur <= end:
        span = min(max_span, (end - cur).days + 1)
        out.append((cur.strftime("%Y-%m-%d"), span))
        cur += timedelta(days=span)
    return out


def _last_fire_date(conn, area: str) -> str | None:
    row = conn.execute(
        "SELECT max(date) FROM fire_metrics WHERE area = ?", (area,)
    ).fetchone()
    return row[0] if row and row[0] else None


def _group_by_date(rows: list[dict]) -> dict[str, dict]:
    """Aggregate raw FIRMS detections into per-acq_date metrics."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = (r.get("acq_date") or "").strip()
        if d:
            by_date[d].append(r)

    metrics: dict[str, dict] = {}
    for d, drows in by_date.items():
        lats = [float(r["latitude"]) for r in drows if r.get("latitude")]
        lons = [float(r["longitude"]) for r in drows if r.get("longitude")]
        frps = [float(r["frp"]) for r in drows if r.get("frp")]
        metrics[d] = {
            "n_detections": len(drows),
            "frp_sum": sum(frps) if frps else 0.0,
            "frp_max": max(frps) if frps else 0.0,
            "lat": (sum(lats) / len(lats)) if lats else None,
            "lon": (sum(lons) / len(lons)) if lons else None,
        }
    return metrics


def _upsert_fire_metrics(
    conn, area: str, metrics: dict[str, dict], source: str
) -> int:
    upserted = 0
    with conn:
        for d, m in metrics.items():
            conn.execute(
                """INSERT INTO fire_metrics
                   (area, date, n_detections, frp_sum, frp_max, lat, lon, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(area, date) DO UPDATE SET
                     n_detections = excluded.n_detections,
                     frp_sum = excluded.frp_sum,
                     frp_max = excluded.frp_max,
                     lat = excluded.lat,
                     lon = excluded.lon,
                     source = excluded.source,
                     fetched_at = datetime('now')""",
                (area, d, m["n_detections"], m["frp_sum"], m["frp_max"],
                 m["lat"], m["lon"], source),
            )
            upserted += 1
    return upserted


def _emit_fire_anomalies(
    conn,
    area: str,
    *,
    baseline_days: int,
    z_threshold: float,
    min_detections: int,
    whole_history: bool,
) -> int:
    """Promote fire surges vs the trailing baseline to hazard events.

    Surges only (a quiet day is not signal) and the count must exceed
    `min_detections` so noise on near-empty baselines doesn't surface.
    whole_history=False checks only the latest day (incremental); True sweeps
    the entire stored timeseries (backfill). Baseline excludes the point itself
    (no lookahead). Returns the number of events created.
    """
    rows = conn.execute(
        """SELECT date, n_detections, lat, lon, frp_max FROM fire_metrics
           WHERE area = ? AND n_detections IS NOT NULL
           ORDER BY date ASC""",
        (area,),
    ).fetchall()

    anomalies = find_anomalies(
        [dict(r) for r in rows],
        value_key="n_detections",
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        direction="surge",
        min_value=min_detections,
        whole_history=whole_history,
    )

    created = 0
    for a in anomalies:
        p = a.point
        date = p["date"]
        title = f"{area} fire anomaly {date}"
        exists = conn.execute(
            "SELECT 1 FROM events WHERE title = ?", (title,)
        ).fetchone()
        if exists:
            continue

        severity = max(1, min(5, round(a.z)))
        frp_max = p["frp_max"] or 0.0
        summary = (
            f"{area}: {p['n_detections']} active-fire detections on {date}, "
            f"{a.pct:+.0f}% vs {baseline_days}d baseline "
            f"({a.mean:.0f}±{a.stdev:.0f}, z={a.z:+.1f}), peak FRP "
            f"{frp_max:.0f} MW — fire surge."
        )

        with conn:
            conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type, origin,
                    severity, location_name, lat, lon)
                   VALUES (?, ?, ?, ?, 'hazard', 'firms', ?, ?, ?, ?)""",
                (title, summary, date, date, severity, area, p["lat"], p["lon"]),
            )
        logger.info(f"FIRMS anomaly: {summary}")
        created += 1
    return created


def ingest_firms(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    map_key: str | None,
    areas: dict[str, str] | None = None,
    source: str = DEFAULT_FIRMS_SOURCE,
    days: int = DEFAULT_FIRMS_DAYS,
    start: str | None = None,
    end: str | None = None,
    baseline_days: int = DEFAULT_FIRE_BASELINE_DAYS,
    z_threshold: float = DEFAULT_FIRE_Z_THRESHOLD,
    min_detections: int = DEFAULT_FIRE_MIN_DETECTIONS,
    backfill_anomalies: bool | None = None,
    client: httpx.Client | None = None,
) -> FIRMSResult:
    """Store daily FIRMS detections per area in fire_metrics; flag fire surges.

    map_key: free FIRMS MAP_KEY; when None/empty the ingest is skipped.
    start:   YYYY-MM-DD historical backfill anchor (use archive source, e.g.
             VIIRS_SNPP_SP). When None, each area resumes from its last stored
             date (incremental); if an area has none, the last `days` days.
    end:     YYYY-MM-DD range end (default: today).
    backfill_anomalies: sweep the whole stored timeseries for anomalies;
             defaults to True in historical mode (start given), else latest-only.
    """
    result = FIRMSResult()
    if not map_key:
        result.skipped_no_key = True
        logger.info("FIRMS: no MAP_KEY configured — skipping")
        return result

    if backfill_anomalies is None:
        backfill_anomalies = start is not None

    areas = areas or FIRMS_AREAS
    today = datetime.now(timezone.utc).date()
    end_date = date_cls.fromisoformat(end) if end else today
    fixed_start = date_cls.fromisoformat(start) if start else None

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    mode = f"history from {start}" if start else f"incremental (≤{days}d)"
    logger.info(f"FIRMS: {len(areas)} areas, source={source}, {mode}")

    try:
        for name, bbox in areas.items():
            result.areas_checked += 1

            if fixed_start is not None:
                area_start = fixed_start
            else:
                last = _last_fire_date(conn, name)
                # re-fetch the last stored day to catch late-arriving detections
                area_start = (
                    date_cls.fromisoformat(last) if last
                    else end_date - timedelta(days=days - 1)
                )
            if area_start > end_date:
                continue

            area_rows: list[dict] = []
            nrt_fallback = _NRT_FALLBACK.get(source)
            for win_start, span in _windows(area_start, end_date):
                url = f"{FIRMS_URL}/{map_key}/{source}/{bbox}/{span}/{win_start}"
                try:
                    resp = client.get(url, timeout=60)
                    if resp.status_code == 400 and nrt_fallback:
                        time.sleep(FIRMS_REQUEST_DELAY)
                        url = f"{FIRMS_URL}/{map_key}/{nrt_fallback}/{bbox}/{span}/{win_start}"
                        resp = client.get(url, timeout=60)
                    resp.raise_for_status()
                    rows = list(csv.DictReader(io.StringIO(resp.text)))
                except Exception as exc:
                    msg = f"{name} [{win_start}+{span}d]: {exc}"
                    result.errors.append(msg)
                    logger.warning(f"FIRMS fetch error {msg}")
                    continue
                result.windows_fetched += 1
                area_rows.extend(rows)
                time.sleep(FIRMS_REQUEST_DELAY)

            result.detections_total += len(area_rows)
            metrics = _group_by_date(area_rows)
            result.metrics_upserted += _upsert_fire_metrics(
                conn, name, metrics, source
            )
            result.events_created += _emit_fire_anomalies(
                conn, name,
                baseline_days=baseline_days,
                z_threshold=z_threshold,
                min_detections=min_detections,
                whole_history=backfill_anomalies,
            )
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"FIRMS complete: {result.areas_checked} areas | "
        f"{result.windows_fetched} windows | "
        f"{result.detections_total} detections | "
        f"{result.metrics_upserted} metrics upserted | "
        f"+{result.events_created} anomaly events | {len(result.errors)} errors"
    )
    return result
