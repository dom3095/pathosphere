"""Tests for IODA internet disruption ingestor.

Network mocked with httpx.MockTransport — no real calls.
"""

from datetime import datetime, timezone

import httpx
import pytest

from pathosphere.ingest.ioda import (
    _aggregate_daily,
    _fetch_signals,
    ingest_ioda,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ioda_response(from_ts: int, step: int, values: list) -> dict:
    """Build a minimal IODA signals/raw response body."""
    return {
        "data": {
            "signals": [
                {
                    "entityCode": "IR",
                    "datasource": "bgp",
                    "from": from_ts,
                    "until": from_ts + step * len(values),
                    "step": step,
                    "nativeStep": step,
                    "values": values,
                }
            ]
        }
    }


def _handler_ok(from_ts: int, step: int, values: list):
    body = _ioda_response(from_ts, step, values)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


def _handler_error(status: int = 500):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)
    return handler


# One day of 5-min slots = 288 values
_ONE_DAY_STEP = 300  # 5 minutes
_SLOTS_PER_DAY = 86400 // _ONE_DAY_STEP  # 288


def _daily_slots(n_days: int, base: float = 9000.0, spike_day: int | None = None,
                 spike_val: float = 1000.0) -> list[float]:
    """Build n_days * 288 signal values; optionally replace one day with a low spike."""
    values = []
    for d in range(n_days):
        v = spike_val if d == spike_day else base + d * 10
        values.extend([v] * _SLOTS_PER_DAY)
    return values


# ─── unit tests ──────────────────────────────────────────────────────────────

def test_aggregate_daily_groups_by_date():
    # 2 days × 288 slots at 5-min step
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = [100.0] * _SLOTS_PER_DAY + [200.0] * _SLOTS_PER_DAY

    daily = _aggregate_daily(base_ts, _ONE_DAY_STEP, values)

    assert "2026-05-01" in daily
    assert "2026-05-02" in daily
    assert daily["2026-05-01"] == pytest.approx(100.0)
    assert daily["2026-05-02"] == pytest.approx(200.0)


def test_aggregate_daily_skips_none():
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = [None, None, 50.0, 50.0]

    daily = _aggregate_daily(base_ts, _ONE_DAY_STEP, values)
    assert "2026-05-01" in daily
    assert daily["2026-05-01"] == pytest.approx(50.0)


def test_aggregate_daily_empty():
    daily = _aggregate_daily(0, 300, [])
    assert daily == {}


def test_fetch_signals_parses_response():
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = [9000.0] * _SLOTS_PER_DAY

    client = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    daily = _fetch_signals(client, "IR", base_ts, base_ts + 86400)

    assert "2026-05-01" in daily
    assert daily["2026-05-01"] == pytest.approx(9000.0)


def test_fetch_signals_http_error():
    client = _mock_client(_handler_error(503))
    with pytest.raises(RuntimeError, match="503"):
        _fetch_signals(client, "IR", 0, 86400)


# ─── integration tests ───────────────────────────────────────────────────────

def test_ioda_upserts_metrics(tmp_db):
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = _daily_slots(3)

    client = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-05-01", end="2026-05-03",
        client=client,
    )

    assert result.countries_checked == 1
    assert result.metrics_upserted == 3
    rows = tmp_db.execute(
        "SELECT date, signal_bgp FROM internet_metrics WHERE country_code='IR' ORDER BY date"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["date"] == "2026-05-01"


def test_ioda_detects_outage_event(tmp_db):
    # 35 normal days then 1 very low day (outage)
    base_ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    # spike_day=35 → day index 35 gets low value 500 vs baseline ~9000
    values = _daily_slots(36, base=9000.0, spike_day=35, spike_val=500.0)

    client = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-04-01", end="2026-05-06",
        baseline_days=30,
        z_threshold=2.5,
        client=client,
    )

    assert result.events_created >= 1
    ev = tmp_db.execute(
        "SELECT title, event_type, origin FROM events WHERE origin='ioda'"
    ).fetchone()
    assert ev is not None
    assert ev["event_type"] == "infrastructure"
    assert "Iran" in ev["title"]
    assert "disruption" in ev["title"].lower()


def test_ioda_no_outage_on_stable_signal(tmp_db):
    base_ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    values = _daily_slots(35, base=9000.0)  # all normal

    client = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-04-01", end="2026-05-05",
        baseline_days=30,
        z_threshold=2.5,
        client=client,
    )

    assert result.events_created == 0


def test_ioda_dedup_on_rerun(tmp_db):
    base_ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    values = _daily_slots(36, base=9000.0, spike_day=35, spike_val=500.0)

    countries = {"IR": "Iran"}
    client_a = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    client_b = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))

    r1 = ingest_ioda(tmp_db, countries=countries, start="2026-04-01", end="2026-05-06",
                     baseline_days=30, z_threshold=2.5, client=client_a)
    r2 = ingest_ioda(tmp_db, countries=countries, start="2026-04-01", end="2026-05-06",
                     baseline_days=30, z_threshold=2.5, client=client_b)

    assert r1.events_created >= 1
    assert r2.events_created == 0


def test_ioda_http_error_recorded_in_result(tmp_db):
    client = _mock_client(_handler_error(503))
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-05-01", end="2026-05-02",
        client=client,
    )

    assert result.countries_checked == 1
    assert len(result.errors) == 1
    assert result.events_created == 0


def test_ioda_multiple_countries(tmp_db):
    base_ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    values = _daily_slots(5)

    client = _mock_client(_handler_ok(base_ts, _ONE_DAY_STEP, values))
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran", "RU": "Russia"},
        start="2026-04-01", end="2026-04-05",
        client=client,
    )

    assert result.countries_checked == 2
    assert result.metrics_upserted == 10  # 5 days × 2 countries


def test_ioda_flat_data_response_format(tmp_db):
    """Handle IODA response with flat data list instead of nested signals key."""
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = [9000.0] * _SLOTS_PER_DAY

    flat_body = {
        "data": [
            {
                "entityCode": "IR",
                "datasource": "bgp",
                "from": base_ts,
                "step": _ONE_DAY_STEP,
                "nativeStep": _ONE_DAY_STEP,
                "values": values,
            }
        ]
    }

    def handler(request):
        return httpx.Response(200, json=flat_body)

    client = _mock_client(handler)
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-05-01", end="2026-05-01",
        client=client,
    )

    assert result.metrics_upserted == 1


def test_ioda_nested_list_response_format(tmp_db):
    """Real API v2 wraps signals one level deeper: {"data": [[{...sig...}]]}."""
    base_ts = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    values = [9000.0] * _SLOTS_PER_DAY

    nested_body = {
        "data": [
            [
                {
                    "entityCode": "IR",
                    "datasource": "bgp",
                    "from": base_ts,
                    "step": _ONE_DAY_STEP,
                    "nativeStep": _ONE_DAY_STEP,
                    "values": values,
                }
            ]
        ]
    }

    def handler(request):
        return httpx.Response(200, json=nested_body)

    client = _mock_client(handler)
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-05-01", end="2026-05-01",
        client=client,
    )

    assert result.metrics_upserted == 1
    assert result.errors == []


def test_ioda_long_range_split_into_chunks(tmp_db, monkeypatch):
    """Ranges over 90 days are split: each request spans <100 days (API cap)."""
    monkeypatch.setattr("pathosphere.ingest.ioda.IODA_REQUEST_DELAY", 0)
    seen_spans: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        from_ts, until_ts = int(params["from"]), int(params["until"])
        seen_spans.append(until_ts - from_ts)
        step = 86400  # one value per day keeps the payload small
        n_days = (until_ts - from_ts) // step
        return httpx.Response(
            200, json=_ioda_response(from_ts, step, [9000.0] * n_days)
        )

    client = _mock_client(handler)
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-01-01", end="2026-07-05",  # 185 days
        client=client,
    )

    assert len(seen_spans) == 3  # 90 + 90 + 5 days
    assert all(span < 100 * 86400 for span in seen_spans)
    assert result.errors == []
    assert result.metrics_upserted == 185


def test_ioda_non_json_response_recorded_as_error(tmp_db):
    """HTML 200 (e.g. SPA fallback page) must not crash the ingest loop."""

    def handler(request):
        return httpx.Response(
            200, text="<!doctype html><html></html>",
            headers={"content-type": "text/html"},
        )

    client = _mock_client(handler)
    result = ingest_ioda(
        tmp_db,
        countries={"IR": "Iran"},
        start="2026-05-01", end="2026-05-02",
        client=client,
    )

    assert len(result.errors) == 1
    assert "non-JSON" in result.errors[0]
    assert result.metrics_upserted == 0
