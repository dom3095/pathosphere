"""
Tests for the IMF PortWatch ingestor.

Network is mocked with httpx.MockTransport — no real ArcGIS calls. Anomaly
detection is tested against synthetic timeseries injected into the DB.
"""

import sqlite3
from datetime import date, timedelta

import httpx
import pytest

from pathosphere.ingest.portwatch import (
    _detect_anomaly,
    _iso_date,
    ingest_portwatch,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _arcgis_response(records: list[dict]) -> dict:
    return {"features": [{"attributes": a} for a in records]}


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _seed_metrics(
    conn: sqlite3.Connection,
    portid: str,
    counts: list[int],
    *,
    portname: str = "Suez Canal",
    end: date = date(2026, 6, 7),
) -> None:
    """Insert `counts` as consecutive daily n_total, oldest first, ending at `end`."""
    n = len(counts)
    for i, c in enumerate(counts):
        d = (end - timedelta(days=n - 1 - i)).isoformat()
        conn.execute(
            """INSERT INTO chokepoint_metrics (portid, portname, date, n_total)
               VALUES (?, ?, ?, ?)""",
            (portid, portname, d, c),
        )
    conn.commit()


# ─── _iso_date ───────────────────────────────────────────────────────────────


def test_iso_date_from_string():
    assert _iso_date("2026-06-07") == "2026-06-07"
    assert _iso_date("2026-06-07T00:00:00") == "2026-06-07"


def test_iso_date_from_epoch_ms():
    # 2026-06-07 00:00 UTC
    ms = 1780790400000
    assert _iso_date(ms) == "2026-06-07"


def test_iso_date_none():
    assert _iso_date(None) is None


# ─── anomaly detection ───────────────────────────────────────────────────────


# 30-day baseline oscillating around ~40 with real variance (stdev > 0)
_BASELINE = [38, 41, 39, 42, 40, 37, 43, 40, 38, 41,
             40, 39, 42, 38, 41, 40, 37, 43, 39, 40,
             41, 38, 42, 40, 39, 41, 37, 40, 43, 38]


def test_anomaly_drop_creates_event(tmp_db):
    # stable baseline (~40 ± ~2), then a sharp drop to 10
    _seed_metrics(tmp_db, "chokepoint1", _BASELINE + [10])

    created = _detect_anomaly(
        tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0
    )

    assert created == 1
    ev = tmp_db.execute(
        "SELECT title, event_type, location_name, summary FROM events"
    ).fetchone()
    assert ev["event_type"] == "infrastructure"
    assert ev["location_name"] == "Suez Canal"
    assert "drop" in ev["summary"]
    assert ev["title"] == "Suez Canal transit anomaly 2026-06-07"


def test_anomaly_surge_creates_event(tmp_db):
    _seed_metrics(tmp_db, "chokepoint1", _BASELINE + [120])
    created = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    assert created == 1
    ev = tmp_db.execute("SELECT summary FROM events").fetchone()
    assert "surge" in ev["summary"]


def test_no_anomaly_when_within_threshold(tmp_db):
    # latest barely differs from baseline
    _seed_metrics(tmp_db, "chokepoint1", [40, 41, 39, 40, 42, 38] * 5 + [41])
    created = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    assert created == 0
    assert tmp_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0


def test_no_anomaly_with_insufficient_history(tmp_db):
    _seed_metrics(tmp_db, "chokepoint1", [40, 41, 10])  # < MIN_BASELINE_POINTS+1
    created = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    assert created == 0


def test_no_anomaly_when_baseline_flat_zero_stdev(tmp_db):
    # constant baseline → stdev 0 → cannot z-score, no event
    _seed_metrics(tmp_db, "chokepoint1", [40] * 30 + [40])
    created = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    assert created == 0


def test_anomaly_dedup_on_rerun(tmp_db):
    _seed_metrics(tmp_db, "chokepoint1", _BASELINE + [10])
    first = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    second = _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    assert first == 1
    assert second == 0
    assert tmp_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1


def test_anomaly_severity_scales_with_z(tmp_db):
    _seed_metrics(tmp_db, "chokepoint1", _BASELINE + [0])  # extreme drop
    _detect_anomaly(tmp_db, "chokepoint1", baseline_days=30, z_threshold=2.0)
    sev = tmp_db.execute("SELECT severity FROM events").fetchone()["severity"]
    assert 1 <= sev <= 5


# ─── full ingest flow (mocked network) ───────────────────────────────────────


def test_ingest_upserts_and_detects(tmp_db):
    # newest first: index 0 = drop to 10, rest = noisy baseline ~40
    series = [10] + list(reversed(_BASELINE))
    records = [
        {"date": (date(2026, 6, 7) - timedelta(days=i)).isoformat(),
         "portid": "chokepoint1", "portname": "Suez Canal",
         "n_total": series[i], "n_tanker": 5, "n_container": 3,
         "n_dry_bulk": 1, "n_cargo": 1, "capacity": 1_000_000.0}
        for i in range(31)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_arcgis_response(records))

    result = ingest_portwatch(
        tmp_db, portids=["chokepoint1"], client=_mock_client(handler)
    )

    assert result.chokepoints_fetched == 1
    assert result.metrics_upserted == 31
    assert result.events_created == 1
    stored = tmp_db.execute(
        "SELECT COUNT(*) c FROM chokepoint_metrics WHERE portid='chokepoint1'"
    ).fetchone()["c"]
    assert stored == 31


def test_ingest_upsert_is_idempotent(tmp_db):
    records = [
        {"date": "2026-06-07", "portid": "chokepoint1", "portname": "Suez Canal",
         "n_total": 40, "n_tanker": 5, "n_container": 3, "n_dry_bulk": 1,
         "n_cargo": 1, "capacity": 1_000_000.0}
    ]

    def handler(request):
        return httpx.Response(200, json=_arcgis_response(records))

    client = _mock_client(handler)
    ingest_portwatch(tmp_db, portids=["chokepoint1"], client=client)
    ingest_portwatch(tmp_db, portids=["chokepoint1"], client=client)

    rows = tmp_db.execute(
        "SELECT COUNT(*) c FROM chokepoint_metrics WHERE portid='chokepoint1'"
    ).fetchone()["c"]
    assert rows == 1


def test_ingest_handles_arcgis_error(tmp_db):
    def handler(request):
        return httpx.Response(200, json={"error": {"code": 400, "message": "bad"}})

    result = ingest_portwatch(
        tmp_db, portids=["chokepoint1"], client=_mock_client(handler)
    )
    assert result.chokepoints_fetched == 0
    assert len(result.errors) == 1


def test_ingest_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)

    result = ingest_portwatch(
        tmp_db, portids=["chokepoint1"], client=_mock_client(handler)
    )
    assert result.chokepoints_fetched == 0
    assert len(result.errors) == 1


def test_ingest_empty_features(tmp_db):
    def handler(request):
        return httpx.Response(200, json={"features": []})

    result = ingest_portwatch(
        tmp_db, portids=["chokepoint1"], client=_mock_client(handler)
    )
    assert result.chokepoints_fetched == 0
    assert result.metrics_upserted == 0
    assert result.events_created == 0
