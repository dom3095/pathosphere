"""
Test logica GDELT: generazione URL, parsing, filtraggio, storage, dedup.
Nessuna chiamata HTTP reale — tutto in-process o mockato.
"""

import io
import zipfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from pathosphere.ingest.gdelt import (
    QUAD_CONFLICT,
    IngestResult,
    _extract_csv,
    _parse_rows,
    _safe_float,
    _safe_int,
    _sqldate_to_iso,
    filter_rows,
    generate_file_urls,
    store_rows,
)
from tests.conftest import make_gdelt_row


# ─────────────────────────────────────────────────────────────
# generate_file_urls
# ─────────────────────────────────────────────────────────────

def test_generate_file_urls_one_day_count():
    """Un giorno = 24 ore × 4 slot = 96 file."""
    urls = generate_file_urls(1)
    assert len(urls) == 96


def test_generate_file_urls_two_days_count():
    urls = generate_file_urls(2)
    assert len(urls) == 192


def test_generate_file_urls_filename_format():
    """Filename deve matchare YYYYMMDDHHMMSS.export.CSV.zip."""
    urls = generate_file_urls(1)
    for fname, url in urls:
        assert fname.endswith(".export.CSV.zip")
        # 14 cifre data + ".export.CSV.zip"
        timestamp_part = fname.replace(".export.CSV.zip", "")
        assert len(timestamp_part) == 14
        assert timestamp_part.isdigit()


def test_generate_file_urls_url_prefix():
    urls = generate_file_urls(1)
    for fname, url in urls:
        assert url.startswith("http://data.gdeltproject.org/gdeltv2/")
        assert url.endswith(fname)


def test_generate_file_urls_minutes_slots():
    """Minuti devono essere solo 00, 15, 30, 45."""
    urls = generate_file_urls(1)
    for fname, _ in urls:
        # YYYYMMDDHHMMSS → minuti sono caratteri [10:12]
        minutes = int(fname[10:12])
        assert minutes in (0, 15, 30, 45)


def test_generate_file_urls_day_is_yesterday():
    """n_days=1 deve generare URL per ieri, non oggi."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    urls = generate_file_urls(1)
    first_fname = urls[0][0]
    assert first_fname.startswith(yesterday)


# ─────────────────────────────────────────────────────────────
# _sqldate_to_iso
# ─────────────────────────────────────────────────────────────

def test_sqldate_to_iso_valid():
    assert _sqldate_to_iso("20260611") == "2026-06-11"


def test_sqldate_to_iso_passthrough_if_not_8():
    assert _sqldate_to_iso("2026-06-11") == "2026-06-11"
    assert _sqldate_to_iso("") == ""


# ─────────────────────────────────────────────────────────────
# _safe_int / _safe_float
# ─────────────────────────────────────────────────────────────

def test_safe_int_valid():
    assert _safe_int("42") == 42


def test_safe_int_invalid_returns_default():
    assert _safe_int("abc") == 0
    assert _safe_int("") == 0
    assert _safe_int(None) == 0


def test_safe_int_custom_default():
    assert _safe_int("xyz", default=-1) == -1


def test_safe_float_valid():
    assert _safe_float("-8.0") == pytest.approx(-8.0)


def test_safe_float_invalid_returns_default():
    assert _safe_float("abc") == pytest.approx(0.0)
    assert _safe_float("") == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────
# _parse_rows
# ─────────────────────────────────────────────────────────────

def _make_tsv_line(row: dict) -> str:
    from pathosphere.ingest.gdelt import GDELT_COLS
    return "\t".join(row.get(col, "") for col in GDELT_COLS)


def test_parse_rows_returns_correct_keys():
    from pathosphere.ingest.gdelt import GDELT_COLS
    row = make_gdelt_row()
    tsv = _make_tsv_line(row)
    parsed = list(_parse_rows(tsv))
    assert len(parsed) == 1
    assert set(parsed[0].keys()) == set(GDELT_COLS)


def test_parse_rows_values_match():
    row = make_gdelt_row()
    tsv = _make_tsv_line(row)
    parsed = list(_parse_rows(tsv))
    assert parsed[0]["Actor1CountryCode"] == "CN"
    assert parsed[0]["QuadClass"] == "4"
    assert parsed[0]["NumMentions"] == "50"


def test_parse_rows_multiple_lines():
    rows = [make_gdelt_row(GlobalEventID=str(i), SOURCEURL=f"http://ex.com/{i}") for i in range(3)]
    tsv = "\n".join(_make_tsv_line(r) for r in rows)
    parsed = list(_parse_rows(tsv))
    assert len(parsed) == 3


# ─────────────────────────────────────────────────────────────
# filter_rows
# ─────────────────────────────────────────────────────────────

def test_filter_rows_keeps_conflict():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries=None)
    assert len(result) == 1


def test_filter_rows_drops_cooperation():
    rows = [make_gdelt_row(QuadClass="1", NumMentions="50")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries=None)
    assert len(result) == 0


def test_filter_rows_drops_low_mentions():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="5")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries=None)
    assert len(result) == 0


def test_filter_rows_keeps_exact_min_mentions():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="10")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries=None)
    assert len(result) == 1


def test_filter_rows_goldstein_drops_positive():
    """min_goldstein=-1 scarta eventi con Goldstein > -1 (cioè meno destabilizzanti)."""
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50", GoldsteinScale="2.0")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=-1.0, countries=None)
    assert len(result) == 0


def test_filter_rows_goldstein_keeps_negative():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50", GoldsteinScale="-8.0")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=-1.0, countries=None)
    assert len(result) == 1


def test_filter_rows_country_match_actor1():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50", Actor1CountryCode="IR")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries={"IR"})
    assert len(result) == 1


def test_filter_rows_country_match_action_geo():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50",
                           Actor1CountryCode="", Actor2CountryCode="",
                           ActionGeo_CountryCode="UA")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries={"UA"})
    assert len(result) == 1


def test_filter_rows_country_no_match():
    rows = [make_gdelt_row(QuadClass="4", NumMentions="50",
                           Actor1CountryCode="CN", Actor2CountryCode="TW",
                           ActionGeo_CountryCode="TW")]
    result = filter_rows(iter(rows), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries={"IR"})
    assert len(result) == 0


def test_filter_rows_empty_input():
    result = filter_rows(iter([]), quad_classes={3, 4}, min_mentions=10,
                         min_goldstein=None, countries=None)
    assert result == []


# ─────────────────────────────────────────────────────────────
# store_rows
# ─────────────────────────────────────────────────────────────

def test_store_rows_inserts_event_and_doc(tmp_db):
    rows = [make_gdelt_row()]
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, rows)
    assert ev_ins == 1
    assert doc_ins == 1


def test_store_rows_event_in_db(tmp_db):
    rows = [make_gdelt_row()]
    with tmp_db:
        store_rows(tmp_db, rows)
    count = tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1


def test_store_rows_doc_in_db(tmp_db):
    rows = [make_gdelt_row()]
    with tmp_db:
        store_rows(tmp_db, rows)
    count = tmp_db.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    assert count == 1


def test_store_rows_event_document_link_created(tmp_db):
    rows = [make_gdelt_row()]
    with tmp_db:
        store_rows(tmp_db, rows)
    count = tmp_db.execute("SELECT COUNT(*) FROM event_documents").fetchone()[0]
    assert count == 1


def test_store_rows_url_dedup(tmp_db):
    """Stessa SOURCEURL → solo 1 raw_document inserito."""
    rows = [
        make_gdelt_row(SOURCEURL="https://example.com/same"),
        make_gdelt_row(SOURCEURL="https://example.com/same", Actor1Name="RUSSIA"),
    ]
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, rows)
    assert doc_ins == 1
    count = tmp_db.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    assert count == 1


def test_store_rows_event_key_dedup(tmp_db):
    """Stessa chiave semantica (actor1+actor2+eventroot+date+geo) → 1 evento."""
    rows = [
        make_gdelt_row(SOURCEURL="https://example.com/a"),
        make_gdelt_row(SOURCEURL="https://example.com/b"),
    ]
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, rows)
    assert ev_ins == 1
    assert doc_ins == 2
    assert tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_store_rows_different_events(tmp_db):
    """Chiavi diverse → 2 eventi distinti."""
    rows = [
        make_gdelt_row(SOURCEURL="https://ex.com/a", Actor1CountryCode="CN"),
        make_gdelt_row(SOURCEURL="https://ex.com/b", Actor1CountryCode="RU"),
    ]
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, rows)
    assert ev_ins == 2
    assert doc_ins == 2


def test_store_rows_skips_row_without_url(tmp_db):
    rows = [make_gdelt_row(SOURCEURL="")]
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, rows)
    assert ev_ins == 0
    assert doc_ins == 0


def test_store_rows_severity_mapping(tmp_db):
    """Goldstein -8 → severity alta (4 o 5)."""
    rows = [make_gdelt_row(GoldsteinScale="-8.0")]
    with tmp_db:
        store_rows(tmp_db, rows)
    severity = tmp_db.execute("SELECT severity FROM events").fetchone()[0]
    assert severity >= 4


def test_store_rows_event_type_from_cameo(tmp_db):
    rows = [make_gdelt_row(EventRootCode="19")]
    with tmp_db:
        store_rows(tmp_db, rows)
    event_type = tmp_db.execute("SELECT event_type FROM events").fetchone()[0]
    assert event_type == "fight"


def test_store_rows_location_stored(tmp_db):
    rows = [make_gdelt_row(ActionGeo_FullName="Strait of Taiwan",
                           ActionGeo_Lat="24.0", ActionGeo_Long="122.0")]
    with tmp_db:
        store_rows(tmp_db, rows)
    row = tmp_db.execute("SELECT location_name, lat, lon FROM events").fetchone()
    assert row["location_name"] == "Strait of Taiwan"
    assert row["lat"] == pytest.approx(24.0)
    assert row["lon"] == pytest.approx(122.0)


def test_store_rows_empty_list(tmp_db):
    with tmp_db:
        ev_ins, doc_ins = store_rows(tmp_db, [])
    assert ev_ins == 0
    assert doc_ins == 0


# ─────────────────────────────────────────────────────────────
# _extract_csv
# ─────────────────────────────────────────────────────────────

def _make_zip(content: str, inner_name: str = "test.CSV") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, content)
    return buf.getvalue()


def test_extract_csv_returns_content():
    csv_content = "col1\tcol2\nval1\tval2\n"
    zip_bytes = _make_zip(csv_content)
    result = _extract_csv(zip_bytes)
    assert result == csv_content


def test_extract_csv_finds_csv_entry():
    """Deve trovare il file .CSV anche se ci sono altri file nello zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "ignore me")
        zf.writestr("20260611153000.export.CSV", "data\there")
    result = _extract_csv(buf.getvalue())
    assert "data" in result
