"""SQLite connection for the dashboard (read + approval writes).

Opens a fresh connection per call rather than caching one across Streamlit
sessions: sqlite3 connections are not safe to share across threads, and
Streamlit's cache_resource is shared globally. Opening a local SQLite file
is cheap enough that this costs nothing measurable per rerun.
"""

from __future__ import annotations

import sqlite3

from pathosphere.config import get_settings
from pathosphere.db.schema import get_connection as _connect


def get_connection() -> sqlite3.Connection:
    return _connect(get_settings().db_path)
