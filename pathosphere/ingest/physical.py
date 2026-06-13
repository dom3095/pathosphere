"""
Physical-signal ingestors — USGS earthquakes + NASA FIRMS fires.

Both emit `events` with event_type='hazard' (lat/lon already known, so the
extract phase's geocoder leaves them untouched).

USGS earthquakes (no key):
  FDSNWS GeoJSON, significant quakes only (min magnitude filter) — one event
  per quake, deduped by (title, first_seen).

NASA FIRMS fires (free MAP_KEY required):
  Active-fire CSV per named area of interest. Raw detections are numerous, so
  only a per-area summary event is emitted when the detection count exceeds a
  threshold (principle: the LLM sees signals, not thousands of pixels).
  Skips gracefully when no MAP_KEY is configured.

Tables updated: events
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

DEFAULT_MIN_MAGNITUDE = 5.0
DEFAULT_QUAKE_DAYS = 1

# FIRMS strategic areas of interest: name → "west,south,east,north" bbox.
FIRMS_AREAS: dict[str, str] = {
    "Taiwan & Strait": "118,21,123,26",
    "Eastern Mediterranean": "32,30,40,38",
    "Red Sea & Bab el-Mandeb": "38,11,45,20",
    "Persian Gulf & Hormuz": "47,23,60,31",
}
DEFAULT_FIRMS_SOURCE = "VIIRS_SNPP_NRT"
DEFAULT_FIRMS_DAYS = 1
DEFAULT_FIRE_THRESHOLD = 50   # detections per area to warrant an event


@dataclass
class USGSResult:
    quakes_fetched: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class FIRMSResult:
    areas_checked: int = 0
    detections_total: int = 0
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


def ingest_usgs(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    min_magnitude: float = DEFAULT_MIN_MAGNITUDE,
    days: int = DEFAULT_QUAKE_DAYS,
    client: httpx.Client | None = None,
) -> USGSResult:
    """Fetch significant earthquakes and store each as a hazard event."""
    result = USGSResult()
    starttime = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(f"USGS: quakes since {starttime}, M>={min_magnitude}")

    try:
        resp = client.get(
            USGS_URL,
            params={
                "format": "geojson",
                "starttime": starttime,
                "minmagnitude": min_magnitude,
                "orderby": "time",
            },
            timeout=30,
        )
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
                   (title, summary, first_seen, last_seen, event_type,
                    severity, location_name, lat, lon)
                   VALUES (?, ?, ?, ?, 'hazard', ?, ?, ?, ?)""",
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


def _firms_centroid(rows: list[dict]) -> tuple[float, float]:
    lats = [float(r["latitude"]) for r in rows if r.get("latitude")]
    lons = [float(r["longitude"]) for r in rows if r.get("longitude")]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def ingest_firms(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    map_key: str | None,
    areas: dict[str, str] | None = None,
    source: str = DEFAULT_FIRMS_SOURCE,
    days: int = DEFAULT_FIRMS_DAYS,
    threshold: int = DEFAULT_FIRE_THRESHOLD,
    client: httpx.Client | None = None,
) -> FIRMSResult:
    """Summarize NASA FIRMS active-fire detections per area into hazard events.

    map_key: free FIRMS MAP_KEY; when None/empty the ingest is skipped.
    """
    result = FIRMSResult()
    if not map_key:
        result.skipped_no_key = True
        logger.info("FIRMS: no MAP_KEY configured — skipping")
        return result

    areas = areas or FIRMS_AREAS
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(f"FIRMS: {len(areas)} areas, source={source}, {days}d")

    try:
        for name, bbox in areas.items():
            result.areas_checked += 1
            url = f"{FIRMS_URL}/{map_key}/{source}/{bbox}/{days}/{today}"
            try:
                resp = client.get(url, timeout=30)
                resp.raise_for_status()
                rows = list(csv.DictReader(io.StringIO(resp.text)))
            except Exception as exc:
                msg = f"{name}: {exc}"
                result.errors.append(msg)
                logger.warning(f"FIRMS fetch error {msg}")
                continue

            result.detections_total += len(rows)
            if len(rows) < threshold:
                continue

            lat, lon = _firms_centroid(rows)
            frps = [float(r["frp"]) for r in rows if r.get("frp")]
            mean_frp = sum(frps) / len(frps) if frps else 0.0
            title = f"Fire activity cluster — {name} ({today})"

            exists = conn.execute(
                "SELECT 1 FROM events WHERE title = ?", (title,)
            ).fetchone()
            if exists:
                continue

            severity = max(1, min(5, len(rows) // threshold + 1))
            summary = (
                f"{len(rows)} active-fire detections in {name} on {today} "
                f"({source}), mean FRP {mean_frp:.1f} MW. Centroid "
                f"{lat:.2f}, {lon:.2f}."
            )
            with conn:
                conn.execute(
                    """INSERT INTO events
                       (title, summary, first_seen, last_seen, event_type,
                        severity, location_name, lat, lon)
                       VALUES (?, ?, ?, ?, 'hazard', ?, ?, ?, ?)""",
                    (title, summary, today, today, severity, name, lat, lon),
                )
            result.events_created += 1
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"FIRMS complete: {result.areas_checked} areas, "
        f"{result.detections_total} detections, +{result.events_created} events"
    )
    return result
