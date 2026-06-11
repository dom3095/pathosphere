"""
Shared fixtures for all tests.
"""

import sqlite3
from pathlib import Path

import pytest

from pathosphere.db.schema import get_connection, init_db
from pathosphere.ingest.gdelt import GDELT_COLS


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """Temporary SQLite DB with full schema initialized."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


def make_gdelt_row(**overrides) -> dict:
    """
    GDELT row with sensible default values.
    Selective overrides via kwargs.
    Defaults: material conflict CN→TW, 50 mentions, Goldstein -8.
    """
    defaults = {col: "" for col in GDELT_COLS}
    defaults.update(
        {
            "GlobalEventID": "999000001",
            "SQLDATE": "20260611",
            "MonthYear": "202606",
            "Year": "2026",
            "FractionDate": "2026.44",
            "Actor1Code": "CHN",
            "Actor1Name": "CHINA",
            "Actor1CountryCode": "CN",
            "Actor2Code": "TWN",
            "Actor2Name": "TAIWAN",
            "Actor2CountryCode": "TW",
            "IsRootEvent": "1",
            "EventCode": "195",
            "EventBaseCode": "19",
            "EventRootCode": "19",
            "QuadClass": "4",          # Material Conflict
            "GoldsteinScale": "-8.0",
            "NumMentions": "50",
            "NumSources": "10",
            "NumArticles": "10",
            "AvgTone": "-5.2",
            "ActionGeo_Type": "1",
            "ActionGeo_FullName": "Taiwan",
            "ActionGeo_CountryCode": "TW",
            "ActionGeo_Lat": "23.6978",
            "ActionGeo_Long": "120.9605",
            "DATEADDED": "20260611153000",
            "SOURCEURL": "https://example.com/news/cn-tw-conflict",
        }
    )
    defaults.update(overrides)
    return defaults
