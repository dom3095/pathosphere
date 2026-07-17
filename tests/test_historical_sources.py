"""
Tests for historical-event ingestors: UCDP GED, WHO DON, ReliefWeb, Wikidata
econ crises.

Network mocked with httpx.MockTransport — no real calls. UCDP is also
exercised via a local CSV path (the production one-off path).
"""

import csv
import io
import zipfile

import httpx
import pytest

from pathosphere.ingest.econ_crises import _parse_point, ingest_econ_crises
from pathosphere.ingest.reliefweb import ingest_reliefweb
from pathosphere.ingest.ucdp import _severity, ingest_ucdp
from pathosphere.ingest.who_don import (
    _country_from_title,
    _strip_html,
    ingest_who_don,
)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ─── UCDP GED ────────────────────────────────────────────────────────────────


UCDP_FIELDS = [
    "id", "type_of_violence", "conflict_name", "side_a", "side_b",
    "where_description", "adm_1", "latitude", "longitude", "country",
    "region", "date_start", "date_end", "deaths_civilians", "best",
]


def _ucdp_row(**overrides) -> dict:
    row = {
        "id": "244657",
        "type_of_violence": "1",
        "conflict_name": "Iraq: Government",
        "side_a": "Government of Iraq",
        "side_b": "IS",
        "where_description": "Iraqi embassy in Kabul",
        "adm_1": "Kabul province",
        "latitude": "34.531094",
        "longitude": "69.162796",
        "country": "Afghanistan",
        "region": "Asia",
        "date_start": "2017-07-31 00:00:00.000",
        "date_end": "2017-07-31 00:00:00.000",
        "deaths_civilians": "2",
        "best": "60",
    }
    row.update(overrides)
    return row


def _ucdp_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=UCDP_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _ucdp_zip_handler(rows: list[dict]):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("GEDEvent_test.csv", _ucdp_csv(rows))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload.getvalue())
    return handler


def test_ucdp_severity_buckets():
    assert _severity(25) == 2
    assert _severity(100) == 3
    assert _severity(250) == 4
    assert _severity(1000) == 5
    assert _severity(5) == 1


def test_ucdp_creates_events_from_zip(tmp_db):
    rows = [_ucdp_row(), _ucdp_row(id="2", best="10")]  # second below threshold
    result = ingest_ucdp(tmp_db, client=_mock_client(_ucdp_zip_handler(rows)))

    assert result.rows_read == 2
    assert result.rows_kept == 1
    assert result.events_created == 1
    row = tmp_db.execute(
        "SELECT * FROM events WHERE origin = 'ucdp'"
    ).fetchone()
    assert row["event_type"] == "conflict"
    assert row["title"] == "Iraq: Government — Iraqi embassy in Kabul"
    assert row["first_seen"] == "2017-07-31"
    assert row["lat"] == pytest.approx(34.531094)
    assert row["severity"] == 2
    assert "Government of Iraq vs IS" in row["summary"]


def test_ucdp_local_csv_and_date_range(tmp_db, tmp_path):
    rows = [
        _ucdp_row(id="1", date_start="1995-01-01 00:00:00.000",
                  date_end="1995-01-01 00:00:00.000"),
        _ucdp_row(id="2", where_description="Mosul",
                  date_start="2017-07-31 00:00:00.000"),
    ]
    path = tmp_path / "ged.csv"
    path.write_text(_ucdp_csv(rows), encoding="utf-8")

    result = ingest_ucdp(tmp_db, csv_path=path, start="2000-01-01")
    assert result.rows_kept == 1
    assert result.events_created == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) c FROM events WHERE first_seen < '2000'"
    ).fetchone()["c"] == 0


def test_ucdp_dedup_on_rerun(tmp_db):
    handler = _ucdp_zip_handler([_ucdp_row()])
    first = ingest_ucdp(tmp_db, client=_mock_client(handler))
    second = ingest_ucdp(tmp_db, client=_mock_client(handler))
    assert first.events_created == 1
    assert second.events_created == 0


def test_ucdp_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)
    result = ingest_ucdp(tmp_db, client=_mock_client(handler))
    assert result.events_created == 0
    assert result.errors


# ─── WHO DON ─────────────────────────────────────────────────────────────────


def _don_item(title: str, pub: str, overview: str = "<p>Outbreak details.</p>",
              don_id: str = "2024-DON001") -> dict:
    return {
        "Title": title,
        "PublicationDateAndTime": pub,
        "Overview": overview,
        "Summary": "",
        "DonId": don_id,
    }


def _don_handler(pages: list[list[dict]]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]] if calls["n"] < len(pages) else []
        calls["n"] += 1
        return httpx.Response(200, json={"value": page})
    return handler


def test_strip_html():
    assert _strip_html("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    assert _strip_html(None) == ""


def test_country_from_title():
    assert _country_from_title("Ebola virus disease – Uganda") == "Uganda"
    assert _country_from_title("MERS-CoV - Saudi Arabia") is None  # hyphen, not en-dash
    assert _country_from_title("Cholera update") is None


def test_who_don_creates_events(tmp_db):
    items = [
        _don_item("Ebola virus disease – Uganda", "2022-09-20T00:00:00Z"),
        _don_item("Cholera – Haiti", "2022-10-02T00:00:00Z"),
    ]
    result = ingest_who_don(tmp_db, client=_mock_client(_don_handler([items])))

    assert result.items_fetched == 2
    assert result.events_created == 2
    row = tmp_db.execute(
        "SELECT * FROM events WHERE title LIKE 'Ebola%'"
    ).fetchone()
    assert row["event_type"] == "epidemic"
    assert row["origin"] == "who_don"
    assert row["location_name"] == "Uganda"
    assert row["lat"] is None  # geocoded later by the extract phase
    assert "Outbreak details." in row["summary"]


def test_who_don_incremental_resume(tmp_db):
    old = [_don_item("Cholera – Haiti", "2010-10-22T00:00:00Z")]
    ingest_who_don(tmp_db, client=_mock_client(_don_handler([old])))

    both = [
        _don_item("Cholera – Haiti", "2010-10-22T00:00:00Z"),
        _don_item("Mpox – Nigeria", "2024-05-01T00:00:00Z"),
    ]
    result = ingest_who_don(tmp_db, client=_mock_client(_don_handler([both])))
    # resume date == last stored day: old item deduped, new one created
    assert result.events_created == 1
    assert tmp_db.execute(
        "SELECT COUNT(*) c FROM events WHERE origin = 'who_don'"
    ).fetchone()["c"] == 2


def test_who_don_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)
    result = ingest_who_don(tmp_db, client=_mock_client(handler))
    assert result.events_created == 0
    assert result.errors


# ─── ReliefWeb ───────────────────────────────────────────────────────────────


def _rw_item(name: str, date_event: str, country: str = "Pakistan",
             lat: float = 30.0, lon: float = 70.0) -> dict:
    return {
        "fields": {
            "name": name,
            "description": "Severe monsoon flooding affected millions.",
            "date": {"event": date_event, "created": date_event},
            "status": "past",
            "primary_country": {
                "name": country,
                "location": {"lat": lat, "lon": lon},
            },
            "primary_type": {"name": "Flood"},
        }
    }


def _rw_handler(pages: list[list[dict]]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]] if calls["n"] < len(pages) else []
        calls["n"] += 1
        return httpx.Response(200, json={"data": page})
    return handler


def test_reliefweb_skips_without_appname(tmp_db):
    result = ingest_reliefweb(tmp_db, appname=None)
    assert result.skipped_no_appname
    assert result.events_created == 0


def test_reliefweb_creates_events(tmp_db):
    items = [_rw_item("Pakistan: Floods - Aug 2010", "2010-08-01T00:00:00+00:00")]
    result = ingest_reliefweb(
        tmp_db, appname="test", client=_mock_client(_rw_handler([items]))
    )

    assert result.events_created == 1
    row = tmp_db.execute(
        "SELECT * FROM events WHERE origin = 'reliefweb'"
    ).fetchone()
    assert row["event_type"] == "hazard"
    assert row["first_seen"] == "2010-08-01"
    assert row["location_name"] == "Pakistan"
    assert row["lat"] == pytest.approx(30.0)
    assert "Flood" in row["summary"]


def test_reliefweb_dedup_on_rerun(tmp_db):
    items = [_rw_item("Pakistan: Floods - Aug 2010", "2010-08-01T00:00:00+00:00")]
    ingest_reliefweb(tmp_db, appname="test",
                     client=_mock_client(_rw_handler([items])))
    result = ingest_reliefweb(tmp_db, appname="test",
                              client=_mock_client(_rw_handler([items])))
    assert result.events_created == 0


def test_reliefweb_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)
    result = ingest_reliefweb(tmp_db, appname="test", client=_mock_client(handler))
    assert result.events_created == 0
    assert result.errors


# ─── Wikidata econ crises ────────────────────────────────────────────────────


def _wd_binding(qid: str, label: str, start: str | None = None,
                country: str | None = None, coord: str | None = None,
                description: str = "worldwide economic depression") -> dict:
    b: dict = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "itemLabel": {"value": label},
        "itemDescription": {"value": description},
    }
    if start:
        b["start"] = {"value": f"{start}T00:00:00Z"}
    if country:
        b["countryLabel"] = {"value": country}
    if coord:
        b["coord"] = {"value": coord}
    return b


def _wd_handler(bindings: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": bindings}})
    return handler


def test_parse_point():
    assert _parse_point("Point(-100.0 40.0)") == (40.0, -100.0)
    assert _parse_point("garbage") is None
    assert _parse_point(None) is None


def test_econ_crises_single_country(tmp_db):
    bindings = [
        _wd_binding("Q123", "1997 Asian financial crisis", "1997-07-02",
                    "Thailand", "Point(100.5 13.7)"),
    ]
    result = ingest_econ_crises(tmp_db, client=_mock_client(_wd_handler(bindings)))

    assert result.events_created == 1
    row = tmp_db.execute(
        "SELECT * FROM events WHERE origin = 'wikidata'"
    ).fetchone()
    assert row["event_type"] == "economic"
    assert row["first_seen"] == "1997-07-02"
    assert row["location_name"] == "Thailand"
    assert row["lat"] == pytest.approx(13.7)
    assert row["severity"] == 3
    assert "Q123" in row["summary"]


def test_econ_crises_multicountry_is_global(tmp_db):
    bindings = [
        _wd_binding("Q8698", "Great Depression", "1929-10-29", c,
                    "Point(0.0 0.0)")
        for c in ["United States", "Germany", "France", "United Kingdom"]
    ]
    result = ingest_econ_crises(tmp_db, client=_mock_client(_wd_handler(bindings)))

    assert result.items_fetched == 1
    assert result.events_created == 1
    row = tmp_db.execute(
        "SELECT * FROM events WHERE title = 'Great Depression'"
    ).fetchone()
    assert row["location_name"] == "global"
    assert row["lat"] is None
    assert row["severity"] == 4


def test_econ_crises_skips_undated_and_dedups(tmp_db):
    bindings = [
        _wd_binding("Q1", "Some undated crisis"),
        _wd_binding("Q2", "Dot-com bubble", "2000-03-10", "United States"),
    ]
    client_handler = _wd_handler(bindings)
    first = ingest_econ_crises(tmp_db, client=_mock_client(client_handler))
    second = ingest_econ_crises(tmp_db, client=_mock_client(client_handler))
    assert first.events_created == 1
    assert second.events_created == 0


def test_econ_crises_handles_http_error(tmp_db):
    def handler(request):
        return httpx.Response(500)
    result = ingest_econ_crises(tmp_db, client=_mock_client(handler))
    assert result.events_created == 0
    assert result.errors
