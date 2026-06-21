"""
IODA internet disruption ingestor.

IODA (Internet Outage Detection and Analysis, Georgia Tech) rileva blackout
internet via tre segnali indipendenti:
  - BGP: visibilità dei prefissi di routing
  - active: probing ICMP/ping per rete /24
  - merit-nt: traffico telescopio darknet Merit

Endpoint usato: GET /signals/raw/country/{code}?from=&until=&datasource=bgp

Flusso:
  1. Fetch segnale BGP giornaliero per ogni paese monitorato
  2. Aggrega valori 5-min → medie giornaliere → upsert in internet_metrics
  3. Rileva drop anomali vs baseline 30d (direction="drop") con find_anomalies
  4. Promuove anomalie a events (event_type='infrastructure', origin='ioda')

Nessuna chiave API richiesta (dati pubblici). Rate limit: 1 req/s circa.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from loguru import logger

from pathosphere.ingest.anomaly import find_anomalies

IODA_BASE = "https://ioda.inetintel.cc.gatech.edu/api/v2"
IODA_SIGNALS_URL = IODA_BASE + "/signals/raw/country/{code}"

IODA_REQUEST_DELAY = 1.0   # seconds between country requests (be polite)
DEFAULT_IODA_DAYS = 1
DEFAULT_BASELINE_DAYS = 30
DEFAULT_Z_THRESHOLD = 2.5  # stricter than fire/portwatch: internet drops are rarer

# Countries with highest internet shutdown risk or geopolitical significance.
# North Korea (KP) has no internet connectivity data — included for completeness,
# requests will fail gracefully.
MONITORED_COUNTRIES: dict[str, str] = {
    "AF": "Afghanistan",
    "AZ": "Azerbaijan",
    "BD": "Bangladesh",
    "BY": "Belarus",
    "CN": "China",
    "CU": "Cuba",
    "ET": "Ethiopia",
    "IQ": "Iraq",
    "IR": "Iran",
    "KZ": "Kazakhstan",
    "LY": "Libya",
    "MM": "Myanmar",
    "NG": "Nigeria",
    "PK": "Pakistan",
    "PS": "Palestine",
    "RU": "Russia",
    "SD": "Sudan",
    "SY": "Syria",
    "TJ": "Tajikistan",
    "UA": "Ukraine",
    "UZ": "Uzbekistan",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "YE": "Yemen",
}


@dataclass
class IODAResult:
    countries_checked: int = 0
    metrics_upserted: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


def _last_ioda_date(conn, country_code: str) -> str | None:
    row = conn.execute(
        "SELECT max(date) FROM internet_metrics WHERE country_code = ?",
        (country_code,),
    ).fetchone()
    return row[0] if row and row[0] else None


def _aggregate_daily(from_ts: int, step: int, values: list) -> dict[str, float]:
    """Aggregate sub-hourly IODA timeseries to per-day averages."""
    by_date: dict[str, list[float]] = {}
    for i, v in enumerate(values):
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        ts = from_ts + i * step
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        by_date.setdefault(date, []).append(v)
    return {d: sum(vs) / len(vs) for d, vs in by_date.items()}


def _fetch_signals(
    client: httpx.Client,
    code: str,
    from_ts: int,
    until_ts: int,
    datasource: str = "bgp",
) -> dict[str, float]:
    """Fetch IODA signals for a country; return daily averages keyed by date string."""
    url = IODA_SIGNALS_URL.format(code=code)
    params = {"from": from_ts, "until": until_ts, "datasource": datasource}
    try:
        resp = client.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    body = resp.json()

    # IODA v2 response: {"data": {"signals": [{"from":..., "step":..., "values":[...]}]}}
    # or flat: {"data": [...]} — handle both shapes defensively
    data = body.get("data", {})
    if isinstance(data, dict):
        signals = data.get("signals", [])
    elif isinstance(data, list):
        signals = data
    else:
        return {}

    if not signals:
        return {}

    sig = signals[0]
    from_sig = sig.get("from") or from_ts
    step = sig.get("step") or sig.get("nativeStep") or 300
    raw_values = sig.get("values") or []

    return _aggregate_daily(int(from_sig), int(step), raw_values)


def _upsert_metrics(conn, country_code: str, daily: dict[str, float],
                    datasource: str) -> int:
    col = "signal_bgp" if datasource == "bgp" else "signal_active"
    upserted = 0
    with conn:
        for date, value in daily.items():
            conn.execute(
                f"""INSERT INTO internet_metrics (country_code, date, {col})
                    VALUES (?, ?, ?)
                    ON CONFLICT(country_code, date) DO UPDATE SET
                        {col} = excluded.{col},
                        fetched_at = datetime('now')""",
                (country_code, date, value),
            )
            upserted += 1
    return upserted


def _emit_outage_events(
    conn,
    country_code: str,
    country_name: str,
    *,
    baseline_days: int,
    z_threshold: float,
    whole_history: bool,
) -> int:
    rows = conn.execute(
        """SELECT date, signal_bgp FROM internet_metrics
           WHERE country_code = ? AND signal_bgp IS NOT NULL
           ORDER BY date ASC""",
        (country_code,),
    ).fetchall()

    if not rows:
        return 0

    anomalies = find_anomalies(
        [dict(r) for r in rows],
        value_key="signal_bgp",
        baseline_days=baseline_days,
        z_threshold=z_threshold,
        direction="drop",
        whole_history=whole_history,
    )

    created = 0
    for a in anomalies:
        date = a.point["date"]
        title = f"Internet disruption — {country_name} ({date})"
        exists = conn.execute(
            "SELECT 1 FROM events WHERE title = ?", (title,)
        ).fetchone()
        if exists:
            continue

        severity = max(1, min(5, round(abs(a.z) / z_threshold)))
        summary = (
            f"{country_name}: internet BGP signal {a.value:.0f} on {date}, "
            f"{a.pct:+.0f}% vs {baseline_days}d baseline "
            f"({a.mean:.0f}±{a.stdev:.0f}, z={a.z:+.1f}) — connectivity disruption."
        )

        with conn:
            conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type, origin, severity)
                   VALUES (?, ?, ?, ?, 'infrastructure', 'ioda', ?)""",
                (title, summary, date, date, severity),
            )
        logger.info(f"IODA outage: {summary}")
        created += 1

    return created


def ingest_ioda(
    conn,
    *,
    countries: dict[str, str] | None = None,
    days: int = DEFAULT_IODA_DAYS,
    start: str | None = None,
    end: str | None = None,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    datasource: str = "bgp",
    client: httpx.Client | None = None,
) -> IODAResult:
    """Fetch IODA internet-signal timeseries; flag outages as infrastructure events.

    start: YYYY-MM-DD historical backfill anchor. When None, each country resumes
           from its last stored date (incremental); if none, falls back to `days`.
    baseline_days: trailing window used for anomaly z-score baseline.
    """
    result = IODAResult()
    countries = countries or MONITORED_COUNTRIES

    today = datetime.now(timezone.utc).date()
    end_date_str = end or today.strftime("%Y-%m-%d")
    end_dt = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
    until_ts = int(end_dt.timestamp())

    fixed_start: str | None = start
    whole_history = start is not None

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"},
            follow_redirects=True,
        )

    mode = f"history from {start}" if start else f"incremental (≤{days}d)"
    logger.info(
        f"IODA: {len(countries)} countries, datasource={datasource}, {mode}"
    )

    try:
        for code, name in countries.items():
            result.countries_checked += 1

            if fixed_start:
                start_str = fixed_start
            else:
                last = _last_ioda_date(conn, code)
                if last:
                    start_str = last  # re-fetch last stored day (late arrivals)
                else:
                    start_str = (today - timedelta(days=days + baseline_days - 1)).strftime(
                        "%Y-%m-%d"
                    )

            from_dt = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
            from_ts = int(from_dt.timestamp())

            try:
                daily = _fetch_signals(client, code, from_ts, until_ts, datasource)
            except RuntimeError as exc:
                msg = f"{code} ({name}): {exc}"
                result.errors.append(msg)
                logger.warning(f"IODA fetch error — {msg}")
                time.sleep(IODA_REQUEST_DELAY)
                continue

            upserted = _upsert_metrics(conn, code, daily, datasource)
            result.metrics_upserted += upserted

            result.events_created += _emit_outage_events(
                conn, code, name,
                baseline_days=baseline_days,
                z_threshold=z_threshold,
                whole_history=whole_history,
            )

            time.sleep(IODA_REQUEST_DELAY)

    finally:
        if _own_client:
            client.close()

    logger.info(
        f"IODA complete: {result.countries_checked} countries | "
        f"{result.metrics_upserted} metrics upserted | "
        f"+{result.events_created} outage events | {len(result.errors)} errors"
    )
    return result
