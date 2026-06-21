"""
Tests for physical-signal ingestors: USGS earthquakes + NASA FIRMS fires.

Network mocked with httpx.MockTransport — no real calls.
"""

import httpx
import pytest

from pathosphere.ingest.physical import (
    _epoch_ms_to_iso,
    _quake_severity,
    ingest_firms,
    ingest_usgs,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _quake(mag: float, place: str, time_ms: int, lon=10.0, lat=20.0, depth=12.0):
    return {
        "type": "Feature",
        "id": f"us{time_ms}",
        "properties": {"mag": mag, "place": place, "time": time_ms, "type": "earthquake"},
        "geometry": {"type": "Point", "coordinates": [lon, lat, depth]},
    }


def _usgs_handler(features: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"features": features})
    return handler


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ─── helpers under test ──────────────────────────────────────────────────────


def test_epoch_ms_to_iso():
    assert _epoch_ms_to_iso(1781180526272).startswith("2026-")


def test_epoch_ms_to_iso_bad():
    assert _epoch_ms_to_iso(None) is None
    assert _epoch_ms_to_iso("nope") is None


def test_quake_severity_scale():
    assert _quake_severity(5.0) == 3
    assert _quake_severity(6.5) == 4
    assert _quake_severity(7.2) == 5
    assert _quake_severity(9.0) == 5   # capped
    assert _quake_severity(3.0) == 1   # floored


# ─── USGS ────────────────────────────────────────────────────────────────────


def test_usgs_creates_events(tmp_db):
    feats = [
        _quake(6.1, "near Chile", 1781180526272, lon=-71.7, lat=-27.8),
        _quake(5.5, "off Japan", 1781190000000, lon=141.0, lat=38.0),
    ]
    result = ingest_usgs(tmp_db, client=_mock_client(_usgs_handler(feats)))

    assert result.quakes_fetched == 2
    assert result.events_created == 2
    row = tmp_db.execute(
        "SELECT event_type, lat, lon, severity FROM events WHERE title LIKE 'M6.1%'"
    ).fetchone()
    assert row["event_type"] == "hazard"
    assert row["lat"] == pytest.approx(-27.8)
    assert row["severity"] == 4


def test_usgs_dedup_on_rerun(tmp_db):
    feats = [_quake(6.1, "near Chile", 1781180526272)]
    client = _mock_client(_usgs_handler(feats))
    first = ingest_usgs(tmp_db, client=client)
    second = ingest_usgs(tmp_db, client=client)
    assert first.events_created == 1
    assert second.events_created == 0
    assert tmp_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1


def test_usgs_skips_record_without_mag(tmp_db):
    feat = _quake(5.5, "somewhere", 1781180526272)
    feat["properties"]["mag"] = None
    result = ingest_usgs(tmp_db, client=_mock_client(_usgs_handler([feat])))
    assert result.quakes_fetched == 1
    assert result.events_created == 0


def test_usgs_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)

    result = ingest_usgs(tmp_db, client=_mock_client(handler))
    assert result.events_created == 0
    assert len(result.errors) == 1


# ─── FIRMS ───────────────────────────────────────────────────────────────────

_FIRMS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,confidence,frp"


def _firms_rows(date_counts: dict[str, int], lat=23.5, lon=120.5, frp=15.0) -> str:
    """CSV with `count` detections per acq_date."""
    lines = [_FIRMS_HEADER]
    for d, n in date_counts.items():
        for _ in range(n):
            lines.append(f"{lat},{lon},320.0,0.4,0.4,{d},nominal,{frp}")
    return "\n".join(lines)


def _baseline_plus_spike(spike: int = 200) -> dict[str, int]:
    """11 quiet days (slight variance) + a final high-count day."""
    counts = {f"2026-05-{day:02d}": 8 + (day % 5) for day in range(1, 12)}
    counts["2026-05-12"] = spike
    return counts


def test_firms_skips_without_key(tmp_db):
    result = ingest_firms(tmp_db, map_key=None)
    assert result.skipped_no_key is True
    assert result.events_created == 0


def test_firms_upserts_daily_metrics(tmp_db):
    counts = {"2026-05-10": 5, "2026-05-11": 7, "2026-05-12": 12}

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-10", end="2026-05-12", client=_mock_client(handler),
    )
    assert result.metrics_upserted == 3
    rows = tmp_db.execute(
        "SELECT date, n_detections FROM fire_metrics WHERE area='Taiwan' ORDER BY date"
    ).fetchall()
    assert [(r["date"], r["n_detections"]) for r in rows] == [
        ("2026-05-10", 5), ("2026-05-11", 7), ("2026-05-12", 12),
    ]


def test_firms_anomaly_event_on_spike(tmp_db):
    counts = _baseline_plus_spike(spike=200)

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-01", end="2026-05-12", client=_mock_client(handler),
    )
    assert result.events_created == 1
    ev = tmp_db.execute(
        "SELECT event_type, origin, location_name, summary FROM events"
    ).fetchone()
    assert ev["event_type"] == "hazard"
    assert ev["origin"] == "firms"
    assert ev["location_name"] == "Taiwan"
    assert "fire surge" in ev["summary"]


def test_firms_no_anomaly_below_min_detections(tmp_db):
    # spike that is statistically high but below the absolute floor
    counts = {f"2026-05-{day:02d}": 1 for day in range(1, 12)}
    counts["2026-05-12"] = 20

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-01", end="2026-05-12", min_detections=50,
        client=_mock_client(handler),
    )
    assert result.events_created == 0


def test_firms_no_anomaly_insufficient_baseline(tmp_db):
    counts = {"2026-05-11": 5, "2026-05-12": 300}  # only 2 points

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-11", end="2026-05-12", client=_mock_client(handler),
    )
    assert result.events_created == 0


def test_firms_dedup_on_rerun(tmp_db):
    counts = _baseline_plus_spike(spike=200)

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    client = _mock_client(handler)
    areas = {"Taiwan": "118,21,123,26"}
    first = ingest_firms(tmp_db, map_key="KEY", areas=areas,
                         start="2026-05-01", end="2026-05-12", client=client)
    second = ingest_firms(tmp_db, map_key="KEY", areas=areas,
                          start="2026-05-01", end="2026-05-12", client=client)
    assert first.events_created == 1
    assert second.events_created == 0


def test_firms_backfill_detects_mid_history_spike(tmp_db):
    # 14 quiet days, a spike on 2026-05-15, then quiet days — the latest day is
    # quiet, so only a whole-history sweep (start given) recovers the spike.
    counts = {f"2026-05-{day:02d}": 8 + (day % 5) for day in range(1, 21)}
    counts["2026-05-15"] = 300

    def handler(request):
        return httpx.Response(200, text=_firms_rows(counts))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-01", end="2026-05-20", client=_mock_client(handler),
    )
    assert result.events_created == 1
    ev = tmp_db.execute("SELECT title, first_seen FROM events").fetchone()
    assert ev["first_seen"] == "2026-05-15"
    assert "2026-05-15" in ev["title"]


def test_firms_windows_split_over_5day_span(tmp_db):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=_FIRMS_HEADER)  # header only, 0 rows

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        start="2026-05-01", end="2026-05-25", client=_mock_client(handler),
    )
    # 24 days gap → ceil(24/5) = 5 windows (FIRMS_MAX_SPAN=5)
    assert result.windows_fetched == 5
    assert len(seen) == 5


def test_firms_handles_area_error(tmp_db):
    def handler(request):
        return httpx.Response(500)

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        client=_mock_client(handler),
    )
    assert len(result.errors) == 1
    assert result.events_created == 0
