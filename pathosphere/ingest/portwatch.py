"""
IMF PortWatch ingestor — daily chokepoint transit calls.

Source: ArcGIS FeatureServer (Daily_Chokepoints_Data), satellite AIS-derived
daily vessel transit counts for 28 maritime chokepoints, ~4-day processing lag.

Two stages:
  1. fetch + upsert daily counts into `chokepoint_metrics` (raw timeseries)
  2. anomaly detection: for the latest date per chokepoint, z-score n_total
     against the trailing baseline (the prior window, latest point excluded —
     no lookahead). |z| >= threshold → one `event` (event_type=infrastructure).

The event carries location_name=portname only; the extract phase's
geocode_events fills lat/lon. Daily counts stay out of the LLM's view; only
the anomaly events surface (principle: "the LLM sees only the best").

Tables updated: chokepoint_metrics, events
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from loguru import logger

from pathosphere.ingest.anomaly import find_anomalies

FEATURESERVER_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)

# All 28 chokepoints published by IMF PortWatch. portid → name is stable
# upstream; portname from the API overrides this on store. The full set is
# cheap to fetch; the z-score anomaly detection downstream decides what
# surfaces as an event ("the LLM sees only the best").
STRATEGIC_CHOKEPOINTS: dict[str, str] = {
    "chokepoint1": "Suez Canal",
    "chokepoint2": "Panama Canal",
    "chokepoint3": "Bosporus Strait",
    "chokepoint4": "Bab el-Mandeb Strait",
    "chokepoint5": "Malacca Strait",
    "chokepoint6": "Strait of Hormuz",
    "chokepoint7": "Cape of Good Hope",
    "chokepoint8": "Gibraltar Strait",
    "chokepoint9": "Dover Strait",
    "chokepoint10": "Oresund Strait",
    "chokepoint11": "Taiwan Strait",
    "chokepoint12": "Korea Strait",
    "chokepoint13": "Tsugaru Strait",
    "chokepoint14": "Luzon Strait",
    "chokepoint15": "Lombok Strait",
    "chokepoint16": "Ombai Strait",
    "chokepoint17": "Bohai Strait",
    "chokepoint18": "Torres Strait",
    "chokepoint19": "Sunda Strait",
    "chokepoint20": "Makassar Strait",
    "chokepoint21": "Magellan Strait",
    "chokepoint22": "Yucatan Channel",
    "chokepoint23": "Windward Passage",
    "chokepoint24": "Mona Passage",
    "chokepoint25": "Balabac Strait",
    "chokepoint26": "Bering Strait",
    "chokepoint27": "Mindoro Strait",
    "chokepoint28": "Kerch Strait",
}

_OUT_FIELDS = (
    "date,portid,portname,n_total,n_tanker,n_container,n_dry_bulk,n_cargo,capacity"
)

DEFAULT_DAYS = 90
DEFAULT_BASELINE_DAYS = 30
DEFAULT_Z_THRESHOLD = 2.0
PAGE_SIZE = 1000          # ArcGIS FeatureServer maxRecordCount
FULL_HISTORY = 10**9      # sentinel for --full: fetch every record


@dataclass
class PortWatchResult:
    chokepoints_fetched: int = 0
    metrics_upserted: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


def _iso_date(value) -> str | None:
    """ArcGIS date-only comes back as 'YYYY-MM-DD' (f=json) or epoch ms."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10]
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return None


def _fetch_chokepoint(
    client: httpx.Client, portid: str, days: int
) -> list[dict]:
    """Most-recent `days` daily records for one chokepoint, newest first.

    Pages through ArcGIS's 1000-record cap via resultOffset, so `days` can
    exceed it (use FULL_HISTORY to pull the whole timeseries back to 2019).
    """
    collected: list[dict] = []
    offset = 0
    while len(collected) < days:
        page = min(PAGE_SIZE, days - len(collected))
        resp = client.get(
            FEATURESERVER_URL,
            params={
                "where": f"portid='{portid}'",
                "outFields": _OUT_FIELDS,
                "orderByFields": "date DESC",
                "resultOffset": offset,
                "resultRecordCount": page,
                "f": "json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error: {payload['error']}")
        rows = [f["attributes"] for f in payload.get("features", [])]
        collected.extend(rows)
        if len(rows) < page:
            break  # server exhausted — no more records
        offset += len(rows)
    return collected


def _upsert_metrics(conn, portid: str, rows: list[dict]) -> int:
    upserted = 0
    with conn:
        for attr in rows:
            date = _iso_date(attr.get("date"))
            if date is None:
                continue
            conn.execute(
                """INSERT INTO chokepoint_metrics
                   (portid, portname, date, n_total, n_tanker, n_container,
                    n_dry_bulk, n_cargo, capacity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(portid, date) DO UPDATE SET
                     portname = excluded.portname,
                     n_total = excluded.n_total,
                     n_tanker = excluded.n_tanker,
                     n_container = excluded.n_container,
                     n_dry_bulk = excluded.n_dry_bulk,
                     n_cargo = excluded.n_cargo,
                     capacity = excluded.capacity,
                     fetched_at = datetime('now')""",
                (
                    portid,
                    attr.get("portname"),
                    date,
                    attr.get("n_total"),
                    attr.get("n_tanker"),
                    attr.get("n_container"),
                    attr.get("n_dry_bulk"),
                    attr.get("n_cargo"),
                    attr.get("capacity"),
                ),
            )
            upserted += 1
    return upserted


def _emit_anomalies(
    conn,
    portid: str,
    *,
    baseline_days: int,
    z_threshold: float,
    whole_history: bool,
) -> int:
    """Promote transit anomalies vs the trailing baseline to events.

    whole_history=False checks only the latest day (incremental); True sweeps
    the entire stored timeseries (backfill) so historical anomalies — not just
    the most recent — become events. Baseline excludes the point itself (no
    lookahead). Returns the number of events created.
    """
    rows = conn.execute(
        """SELECT date, portname, n_total FROM chokepoint_metrics
           WHERE portid = ? AND n_total IS NOT NULL
           ORDER BY date ASC""",
        (portid,),
    ).fetchall()

    anomalies = find_anomalies(
        [dict(r) for r in rows],
        value_key="n_total",
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        direction="both",
        whole_history=whole_history,
    )

    created = 0
    for a in anomalies:
        p = a.point
        portname = p["portname"] or STRATEGIC_CHOKEPOINTS.get(portid, portid)
        date = p["date"]
        title = f"{portname} transit anomaly {date}"

        exists = conn.execute(
            "SELECT 1 FROM events WHERE title = ?", (title,)
        ).fetchone()
        if exists:
            continue

        direction = "drop" if a.z < 0 else "surge"
        severity = max(1, min(5, round(abs(a.z))))
        summary = (
            f"{portname}: {p['n_total']} vessel transits on {date}, "
            f"{a.pct:+.0f}% vs {baseline_days}d baseline "
            f"({a.mean:.0f}±{a.stdev:.0f}, z={a.z:+.1f}) — transit {direction}."
        )

        with conn:
            conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type, origin,
                    severity, location_name)
                   VALUES (?, ?, ?, ?, 'infrastructure', 'portwatch', ?, ?)""",
                (title, summary, date, date, severity, portname),
            )
        logger.info(f"PortWatch anomaly: {summary}")
        created += 1
    return created


def _detect_anomaly(
    conn,
    portid: str,
    *,
    baseline_days: int,
    z_threshold: float,
) -> int:
    """Latest-day anomaly check (back-compat wrapper over _emit_anomalies)."""
    return _emit_anomalies(
        conn, portid,
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        whole_history=False,
    )


def ingest_portwatch(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    portids: list[str] | None = None,
    days: int = DEFAULT_DAYS,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    backfill_anomalies: bool = False,
    client: httpx.Client | None = None,
) -> PortWatchResult:
    """Fetch daily chokepoint transits, upsert timeseries, flag anomalies.

    portids: restrict to these chokepoint ids; None = STRATEGIC_CHOKEPOINTS.
    backfill_anomalies: sweep the whole stored timeseries for anomalies (use
      with --full); default checks only the latest day (incremental).
    """
    result = PortWatchResult()
    targets = portids or list(STRATEGIC_CHOKEPOINTS)

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(f"PortWatch: {len(targets)} chokepoints (last {days}d)")

    try:
        for portid in targets:
            try:
                rows = _fetch_chokepoint(client, portid, days)
            except Exception as exc:
                msg = f"{portid}: {exc}"
                result.errors.append(msg)
                logger.warning(f"PortWatch fetch error {msg}")
                continue

            if not rows:
                continue

            result.chokepoints_fetched += 1
            result.metrics_upserted += _upsert_metrics(conn, portid, rows)
            result.events_created += _emit_anomalies(
                conn,
                portid,
                baseline_days=baseline_days,
                z_threshold=z_threshold,
                whole_history=backfill_anomalies,
            )
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"PortWatch complete: {result.chokepoints_fetched} chokepoints | "
        f"{result.metrics_upserted} metrics upserted | "
        f"{result.events_created} anomaly events | {len(result.errors)} errors"
    )
    return result
