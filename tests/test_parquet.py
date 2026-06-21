"""Tests for Parquet export."""

import pyarrow.parquet as pq
import pytest

from pathosphere.export.parquet import ExportResult, export_to_parquet


def _seed_documents(conn, n: int = 3) -> None:
    with conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO raw_documents (title, body, published_at, origin) "
                "VALUES (?, ?, ?, 'test')",
                (f"Doc {i}", f"body {i}", f"2026-0{(i % 6) + 1}-01T00:00:00"),
            )


def _seed_events(conn, n: int = 2) -> None:
    with conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO events (title, first_seen, last_seen, origin) "
                "VALUES (?, ?, ?, 'test')",
                (f"Event {i}", f"2026-0{i + 1}-15", f"2026-0{i + 1}-15"),
            )


def test_export_raw_documents_partitioned(tmp_db, tmp_path):
    _seed_documents(tmp_db, n=6)

    result = export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])

    assert "raw_documents" in result.tables_written
    assert result.rows_written["raw_documents"] == 6
    assert not result.errors

    # at least one partition file exists
    parts = list((tmp_path / "raw_documents").rglob("data.parquet"))
    assert len(parts) >= 1


def test_export_events_partitioned(tmp_db, tmp_path):
    _seed_events(tmp_db, n=2)

    result = export_to_parquet(tmp_db, tmp_path, tables=["events"])

    assert "events" in result.tables_written
    assert result.rows_written["events"] == 2


def test_export_parquet_content_roundtrip(tmp_db, tmp_path):
    _seed_documents(tmp_db, n=2)

    export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])

    parts = list((tmp_path / "raw_documents").rglob("data.parquet"))
    rows_back = []
    for p in parts:
        tbl = pq.read_table(p)
        rows_back.extend(tbl.to_pydict()["title"])

    assert sorted(rows_back) == ["Doc 0", "Doc 1"]


def test_export_undated_entities(tmp_db, tmp_path):
    with tmp_db:
        tmp_db.execute(
            "INSERT INTO entities (name, entity_type) VALUES ('TSMC', 'company')"
        )

    result = export_to_parquet(tmp_db, tmp_path, tables=["entities"])

    assert "entities" in result.tables_written
    assert result.rows_written["entities"] == 1
    assert (tmp_path / "entities" / "data.parquet").exists()


def test_export_empty_table(tmp_db, tmp_path):
    result = export_to_parquet(tmp_db, tmp_path, tables=["entities"])

    assert "entities" in result.tables_written
    assert result.rows_written["entities"] == 0
    # empty table still produces a file (empty schema)
    assert (tmp_path / "entities" / "data.parquet").exists()


def test_export_table_filter(tmp_db, tmp_path):
    _seed_documents(tmp_db)

    result = export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])

    assert "raw_documents" in result.tables_written
    assert "events" not in result.tables_written


def test_export_all_tables(tmp_db, tmp_path):
    _seed_documents(tmp_db)
    _seed_events(tmp_db)

    result = export_to_parquet(tmp_db, tmp_path)

    assert set(result.tables_written) == {
        "raw_documents", "events", "entities", "entity_links"
    }


def test_export_null_date_partition(tmp_db, tmp_path):
    with tmp_db:
        tmp_db.execute(
            "INSERT INTO raw_documents (title, body, published_at, origin) "
            "VALUES ('no-date', 'x', NULL, 'test')"
        )

    result = export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])

    assert result.rows_written["raw_documents"] == 1
    assert (tmp_path / "raw_documents" / "undated" / "data.parquet").exists()


def test_export_idempotent(tmp_db, tmp_path):
    _seed_documents(tmp_db, n=3)

    r1 = export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])
    r2 = export_to_parquet(tmp_db, tmp_path, tables=["raw_documents"])

    assert r1.rows_written == r2.rows_written
    assert not r1.errors
    assert not r2.errors
