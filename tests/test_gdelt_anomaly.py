"""
Tests for the GDELT numeric anomaly path (CP-016): aggregate gdelt_events
per country/quad_class/day and promote Goldstein-scale deviations to events,
bypassing NER/embed/cluster entirely.
"""

import sqlite3
from datetime import date, timedelta

from pathosphere.ingest.gdelt_anomaly import (
    GdeltAnomalyResult,
    backfill_action_geo_country,
    detect_gdelt_anomalies,
)


def _jittered_baseline(n: int, center: float = -2.0) -> list[float]:
    """Small deterministic variance around `center` (stdev>0, needed —
    find_anomalies skips zero-stdev baselines by design)."""
    jitter = [0.0, 0.2, -0.2, 0.1, -0.1, 0.0, 0.15, -0.15, 0.05, -0.05, 0.0, 0.1]
    return [center + jitter[i % len(jitter)] for i in range(n)]


def _seed_daily(
    conn: sqlite3.Connection,
    *,
    goldstein_values: list[float],
    country: str = "TW",
    quad_class: int = 4,
    events_per_day: int = 5,
    avg_tone: float = -3.0,
    end: date = date(2026, 6, 30),
    start_gid: int = 1,
) -> None:
    """Insert `events_per_day` gdelt_events rows per day, oldest first."""
    n = len(goldstein_values)
    gid = start_gid
    for i, gs in enumerate(goldstein_values):
        d = (end - timedelta(days=n - 1 - i)).isoformat()
        for _ in range(events_per_day):
            conn.execute(
                """INSERT INTO gdelt_events
                   (global_event_id, sqldate, date_added, quad_class, goldstein,
                    avg_tone, num_mentions, num_sources, num_articles,
                    action_geo_country)
                   VALUES (?, ?, ?, ?, ?, ?, 10, 2, 2, ?)""",
                (gid, d.replace("-", ""), d, quad_class, gs, avg_tone, country),
            )
            gid += 1
    conn.commit()


# ─── empty / no-signal ───────────────────────────────────────────────────────


def test_no_series_empty_db(tmp_db):
    result = detect_gdelt_anomalies(tmp_db)
    assert isinstance(result, GdeltAnomalyResult)
    assert result.series_checked == 0
    assert result.events_created == 0


def test_stable_baseline_no_anomaly(tmp_db):
    # 15 days flat at goldstein=-2 → no deviation, no event
    _seed_daily(tmp_db, goldstein_values=[-2.0] * 15)
    result = detect_gdelt_anomalies(tmp_db, baseline_days=10, whole_history=True)
    assert result.events_created == 0


# ─── anomaly detection ───────────────────────────────────────────────────────


def test_detects_goldstein_anomaly_latest_day(tmp_db):
    # 12 low-variance baseline days then one sharp drop on the latest day
    values = _jittered_baseline(12) + [-9.0]
    _seed_daily(tmp_db, goldstein_values=values)

    result = detect_gdelt_anomalies(
        tmp_db, baseline_days=12, z_threshold=2.0, whole_history=False
    )

    assert result.series_checked == 1
    assert result.events_created == 1

    row = tmp_db.execute(
        "SELECT title, event_type, origin, severity, location_name FROM events"
    ).fetchone()
    assert "TW" in row["title"]
    assert "material conflict" in row["title"]
    assert row["event_type"] == "gdelt_anomaly"
    assert row["origin"] == "gdelt"
    assert row["location_name"] == "TW"
    assert row["severity"] >= 1


def test_dedup_by_title_on_rerun(tmp_db):
    values = _jittered_baseline(12) + [-9.0]
    _seed_daily(tmp_db, goldstein_values=values)

    detect_gdelt_anomalies(tmp_db, baseline_days=12, whole_history=False)
    result2 = detect_gdelt_anomalies(tmp_db, baseline_days=12, whole_history=False)

    assert result2.events_created == 0
    count = tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1


def test_min_events_per_day_filters_thin_cells(tmp_db):
    # only 1 event/day, default min_events_per_day=3 → cell excluded entirely
    values = [-2.0] * 12 + [-9.0]
    _seed_daily(tmp_db, goldstein_values=values, events_per_day=1)

    result = detect_gdelt_anomalies(tmp_db, baseline_days=12, whole_history=False)

    assert result.series_checked == 0
    assert result.events_created == 0


def test_whole_history_sweeps_past_anomalies(tmp_db):
    # anomaly buried in the middle of the series, not on the latest day
    values = _jittered_baseline(10) + [-9.0] + _jittered_baseline(10)
    _seed_daily(tmp_db, goldstein_values=values)

    latest_only = detect_gdelt_anomalies(
        tmp_db, baseline_days=10, whole_history=False
    )
    assert latest_only.events_created == 0  # latest day is back to baseline

    swept = detect_gdelt_anomalies(tmp_db, baseline_days=10, whole_history=True)
    assert swept.events_created == 1


def test_separate_series_per_country_and_quad_class(tmp_db):
    values = _jittered_baseline(12) + [-9.0]
    _seed_daily(tmp_db, goldstein_values=values, country="TW", quad_class=4, start_gid=1)
    _seed_daily(tmp_db, goldstein_values=[-1.0] * 13, country="US", quad_class=1, start_gid=1000)

    result = detect_gdelt_anomalies(tmp_db, baseline_days=12, whole_history=False)

    assert result.series_checked == 2
    assert result.events_created == 1


# ─── backfill_action_geo_country ─────────────────────────────────────────────


def _insert_legacy_gdelt_event(
    conn: sqlite3.Connection,
    *,
    global_event_id: int,
    action_geo_country: str | None,  # last field of the event_key title
    quad_class: int = 4,
) -> None:
    """Mimic a pre-migration row: events.title carries the 5-tuple dedup key
    (Actor1CC|Actor2CC|EventRootCode|SQLDATE|ActionGeoCC, see gdelt.py::store_rows)
    but gdelt_events.action_geo_country is NULL — the column didn't exist yet."""
    title = f"CN|TW|19|20260601|{action_geo_country or ''}"
    cur = conn.execute(
        """INSERT INTO events (title, first_seen, last_seen, origin)
           VALUES (?, '2026-06-01', '2026-06-01', 'gdelt')""",
        (title,),
    )
    event_id = cur.lastrowid
    conn.execute(
        """INSERT INTO gdelt_events
           (global_event_id, event_id, quad_class, goldstein, avg_tone, date_added)
           VALUES (?, ?, ?, -2.0, -1.0, '2026-06-01')""",
        (global_event_id, event_id, quad_class),
    )
    conn.commit()


def test_backfill_recovers_country_from_event_title(tmp_db):
    _insert_legacy_gdelt_event(tmp_db, global_event_id=1, action_geo_country="TW")

    recovered = backfill_action_geo_country(tmp_db)

    assert recovered == 1
    row = tmp_db.execute(
        "SELECT action_geo_country FROM gdelt_events WHERE global_event_id = 1"
    ).fetchone()
    assert row["action_geo_country"] == "TW"


def test_backfill_skips_rows_with_empty_country_in_title(tmp_db):
    # GDELT itself sometimes leaves ActionGeo_CountryCode blank — not recoverable
    _insert_legacy_gdelt_event(tmp_db, global_event_id=2, action_geo_country=None)

    recovered = backfill_action_geo_country(tmp_db)

    assert recovered == 0
    row = tmp_db.execute(
        "SELECT action_geo_country FROM gdelt_events WHERE global_event_id = 2"
    ).fetchone()
    assert row["action_geo_country"] is None


def test_backfill_is_idempotent(tmp_db):
    _insert_legacy_gdelt_event(tmp_db, global_event_id=3, action_geo_country="US")

    first = backfill_action_geo_country(tmp_db)
    second = backfill_action_geo_country(tmp_db)

    assert first == 1
    assert second == 0  # already filled, WHERE action_geo_country IS NULL excludes it


def test_backfill_does_not_touch_already_populated_rows(tmp_db):
    _insert_legacy_gdelt_event(tmp_db, global_event_id=4, action_geo_country=None)
    tmp_db.execute(
        "UPDATE gdelt_events SET action_geo_country = 'FR' WHERE global_event_id = 4"
    )
    tmp_db.commit()

    recovered = backfill_action_geo_country(tmp_db)

    assert recovered == 0
    row = tmp_db.execute(
        "SELECT action_geo_country FROM gdelt_events WHERE global_event_id = 4"
    ).fetchone()
    assert row["action_geo_country"] == "FR"
