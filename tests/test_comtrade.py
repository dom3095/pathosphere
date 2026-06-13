"""
Tests for the UN Comtrade semiconductor-pilot ingestor.

Network mocked with httpx.MockTransport — no real Comtrade calls.
"""

from datetime import date

import httpx
import pytest

from pathosphere.ingest.comtrade import (
    PILOT_SOURCE_NAME,
    _synthesize_doc,
    ensure_source,
    ingest_comtrade,
    recent_periods,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _record(**over) -> dict:
    rec = {
        "reporterCode": 842, "reporterISO": "USA", "reporterDesc": "USA",
        "flowCode": "M", "flowDesc": "Import",
        "partnerDesc": "World", "cmdCode": "8541",
        "cmdDesc": "Semiconductor devices", "period": "202601",
        "refYear": 2026, "refMonth": 1,
        "primaryValue": 956625651, "netWgt": 12345,
    }
    rec.update(over)
    return rec


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _data_handler(records: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": records})
    return handler


# ─── recent_periods ──────────────────────────────────────────────────────────


def test_recent_periods_basic():
    out = recent_periods(n=3, lag_months=2, today=date(2026, 6, 13))
    assert out == ["202604", "202603", "202602"]


def test_recent_periods_year_boundary():
    out = recent_periods(n=3, lag_months=2, today=date(2026, 1, 15))
    assert out == ["202511", "202510", "202509"]


# ─── ensure_source ───────────────────────────────────────────────────────────


def test_ensure_source_creates_once(tmp_db):
    a = ensure_source(tmp_db)
    b = ensure_source(tmp_db)
    assert a == b
    n = tmp_db.execute(
        "SELECT COUNT(*) c FROM sources WHERE name = ?", (PILOT_SOURCE_NAME,)
    ).fetchone()["c"]
    assert n == 1


def test_pilot_source_excluded_from_rss(tmp_db):
    # url IS NULL → RSS query (active=1 AND url IS NOT NULL) never selects it
    ensure_source(tmp_db)
    row = tmp_db.execute(
        "SELECT url FROM sources WHERE name = ?", (PILOT_SOURCE_NAME,)
    ).fetchone()
    assert row["url"] is None


# ─── _synthesize_doc ─────────────────────────────────────────────────────────


def test_synthesize_doc_fields():
    url, title, body, pub, chash = _synthesize_doc(_record())
    assert url == "comtrade://USA/8541/M/202601"
    assert "USA Import HS8541" in title
    assert "$956.6M" in title
    assert "Semiconductor devices" in body
    assert "Net weight" in body
    assert pub == "2026-01-01T00:00:00"
    assert len(chash) == 64


def test_synthesize_doc_handles_missing_weight():
    _, _, body, _, _ = _synthesize_doc(_record(netWgt=None))
    assert "Net weight" not in body


# ─── ingest flow ─────────────────────────────────────────────────────────────


def test_ingest_inserts_documents(tmp_db):
    records = [
        _record(reporterISO="USA", cmdCode="8541", flowCode="M"),
        _record(reporterISO="JPN", cmdCode="8542", flowCode="X",
                period="202601", primaryValue=3_181_000_000),
    ]
    result = ingest_comtrade(
        tmp_db, periods=["202601"], client=_mock_client(_data_handler(records))
    )
    assert result.records_fetched == 2
    assert result.docs_inserted == 2
    n = tmp_db.execute(
        "SELECT COUNT(*) c FROM raw_documents WHERE url LIKE 'comtrade://%'"
    ).fetchone()["c"]
    assert n == 2


def test_ingest_dedup_on_rerun(tmp_db):
    records = [_record()]
    client = _mock_client(_data_handler(records))
    first = ingest_comtrade(tmp_db, periods=["202601"], client=client)
    second = ingest_comtrade(tmp_db, periods=["202601"], client=client)
    assert first.docs_inserted == 1
    assert second.docs_inserted == 0
    assert second.docs_skipped == 1


def test_ingest_links_to_pilot_source(tmp_db):
    ingest_comtrade(
        tmp_db, periods=["202601"], client=_mock_client(_data_handler([_record()]))
    )
    row = tmp_db.execute(
        """SELECT s.name FROM raw_documents r JOIN sources s ON s.id = r.source_id
           WHERE r.url LIKE 'comtrade://%'"""
    ).fetchone()
    assert row["name"] == PILOT_SOURCE_NAME


def test_ingest_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)

    result = ingest_comtrade(
        tmp_db, periods=["202601"], client=_mock_client(handler)
    )
    assert result.docs_inserted == 0
    assert len(result.errors) == 1


def test_ingest_multiple_periods(tmp_db):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["period"])
        return httpx.Response(200, json={"data": [_record(period=request.url.params["period"])]})

    result = ingest_comtrade(
        tmp_db, periods=["202601", "202602"], client=_mock_client(handler)
    )
    assert seen == ["202601", "202602"]
    assert result.docs_inserted == 2
