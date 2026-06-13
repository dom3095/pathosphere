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


def _firms_csv(n: int, lat=23.5, lon=120.5, frp=15.0) -> str:
    lines = [_FIRMS_HEADER]
    for _ in range(n):
        lines.append(f"{lat},{lon},320.0,0.4,0.4,2026-06-13,nominal,{frp}")
    return "\n".join(lines)


def test_firms_skips_without_key(tmp_db):
    result = ingest_firms(tmp_db, map_key=None)
    assert result.skipped_no_key is True
    assert result.events_created == 0


def test_firms_creates_event_above_threshold(tmp_db):
    def handler(request):
        return httpx.Response(200, text=_firms_csv(120))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        threshold=50, client=_mock_client(handler),
    )
    assert result.areas_checked == 1
    assert result.detections_total == 120
    assert result.events_created == 1
    ev = tmp_db.execute("SELECT event_type, location_name, summary FROM events").fetchone()
    assert ev["event_type"] == "hazard"
    assert ev["location_name"] == "Taiwan"
    assert "120 active-fire detections" in ev["summary"]


def test_firms_below_threshold_no_event(tmp_db):
    def handler(request):
        return httpx.Response(200, text=_firms_csv(10))

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        threshold=50, client=_mock_client(handler),
    )
    assert result.detections_total == 10
    assert result.events_created == 0


def test_firms_dedup_on_rerun(tmp_db):
    def handler(request):
        return httpx.Response(200, text=_firms_csv(120))

    client = _mock_client(handler)
    areas = {"Taiwan": "118,21,123,26"}
    first = ingest_firms(tmp_db, map_key="KEY", areas=areas, threshold=50, client=client)
    second = ingest_firms(tmp_db, map_key="KEY", areas=areas, threshold=50, client=client)
    assert first.events_created == 1
    assert second.events_created == 0


def test_firms_handles_area_error(tmp_db):
    def handler(request):
        return httpx.Response(500)

    result = ingest_firms(
        tmp_db, map_key="KEY", areas={"Taiwan": "118,21,123,26"},
        client=_mock_client(handler),
    )
    assert len(result.errors) == 1
    assert result.events_created == 0
