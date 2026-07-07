"""
GDELT numeric anomaly path (CP-016).

`pathos ingest gdelt` stores one `gdelt_events` row per raw GDELT record with
full numeric fidelity (goldstein, avg_tone, quad_class...) but that signal was
never read back — only used as an upstream filter. Meanwhile every raw
document synthesized from GDELT metadata (`"GDELT: ACTOR1 → ACTOR2 [CODE]"`)
was flowing through the same NLP pipeline (embed→extract→cluster→graph) built
for real prose, polluting entities/clusters/graph with generic CAMEO role
codes (`POLICE`, `MILITARY`...).

This module closes the loop the other way: aggregate `gdelt_events` per
day + country + quad_class, and promote trailing-baseline deviations in
Goldstein scale straight to `events` — same pattern as PortWatch transit
anomalies (see `ingest/anomaly.py`, `ingest/portwatch.py:175-235`), no
NER/embed/cluster involved. GDELT's raw documents themselves are excluded
from the prose pipeline separately (`semantic/embedder.py:NON_PROSE_ORIGINS`).

Tables read: gdelt_events. Tables written: events.
"""

from dataclasses import dataclass, field

from loguru import logger

from pathosphere.ingest.anomaly import find_anomalies

DEFAULT_BASELINE_DAYS = 30
DEFAULT_Z_THRESHOLD = 2.0
DEFAULT_MIN_EVENTS_PER_DAY = 3  # noise guard: ignore country/quad/day cells with too few raw events

QUAD_LABELS: dict[int, str] = {
    1: "verbal cooperation",
    2: "material cooperation",
    3: "verbal conflict",
    4: "material conflict",
}


@dataclass
class GdeltAnomalyResult:
    series_checked: int = 0
    events_created: int = 0


def _aggregate_series(conn, min_events_per_day: int) -> dict[tuple[str, int], list[dict]]:
    """Daily (country, quad_class) Goldstein/tone series, oldest→newest per key."""
    rows = conn.execute(
        """
        SELECT action_geo_country AS country,
               quad_class,
               substr(date_added, 1, 10) AS day,
               AVG(goldstein) AS goldstein,
               AVG(avg_tone) AS avg_tone,
               COUNT(*) AS n
        FROM gdelt_events
        WHERE action_geo_country IS NOT NULL
          AND action_geo_country != ''
          AND quad_class IS NOT NULL
          AND date_added IS NOT NULL
        GROUP BY country, quad_class, day
        HAVING n >= ?
        ORDER BY day ASC
        """,
        (min_events_per_day,),
    ).fetchall()

    series: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        key = (r["country"], r["quad_class"])
        series.setdefault(key, []).append(dict(r))
    return series


def detect_gdelt_anomalies(
    conn,
    *,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    min_events_per_day: int = DEFAULT_MIN_EVENTS_PER_DAY,
    whole_history: bool = False,
) -> GdeltAnomalyResult:
    """Promote Goldstein-scale deviations vs trailing baseline to events.

    whole_history=False checks only the latest day per (country, quad_class)
    series (incremental nightly run); True sweeps the whole stored history
    (backfill after `gdelt-history`). No lookahead: baseline excludes the
    point itself.
    """
    result = GdeltAnomalyResult()
    series = _aggregate_series(conn, min_events_per_day)

    with conn:
        for (country, quad_class), points in series.items():
            result.series_checked += 1
            anomalies = find_anomalies(
                points,
                value_key="goldstein",
                baseline_days=baseline_days,
                z_threshold=z_threshold,
                direction="both",
                # Goldstein ranges -10..+10 — the default min_value=0.0 floor
                # (sane for non-negative metrics like vessel counts) would
                # silently drop every negative value, exactly the
                # destabilizing ones this detector exists to catch.
                min_value=-10.0,
                whole_history=whole_history,
            )

            quad_label = QUAD_LABELS.get(quad_class, f"quad{quad_class}")
            for a in anomalies:
                p = a.point
                title = f"{country} {quad_label} anomaly {p['day']}"

                exists = conn.execute(
                    "SELECT 1 FROM events WHERE title = ?", (title,)
                ).fetchone()
                if exists:
                    continue

                direction = "escalation" if a.z < 0 else "de-escalation"
                severity = max(1, min(5, round(abs(a.z))))
                summary = (
                    f"{country} {quad_label}: Goldstein {p['goldstein']:.2f} on "
                    f"{p['day']} ({p['n']} events), {a.pct:+.0f}% vs "
                    f"{baseline_days}d baseline ({a.mean:.2f}±{a.stdev:.2f}, "
                    f"z={a.z:+.1f}) — {direction}. Avg tone {p['avg_tone']:.1f}."
                )

                conn.execute(
                    """INSERT INTO events
                       (title, summary, first_seen, last_seen, event_type,
                        origin, severity, location_name)
                       VALUES (?, ?, ?, ?, 'gdelt_anomaly', 'gdelt', ?, ?)""",
                    (title, summary, p["day"], p["day"], severity, country),
                )
                logger.info(f"GDELT anomaly: {summary}")
                result.events_created += 1

    logger.info(
        f"GDELT anomaly detect complete: {result.series_checked} series checked | "
        f"{result.events_created} anomaly events"
    )
    return result
