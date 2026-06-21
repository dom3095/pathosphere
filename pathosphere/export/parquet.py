"""
Parquet export — storicizzazione raw_documents, events, entities, entity_links.

Tabelle datate (raw_documents, events) → partizionate per anno/mese:
  data/parquet/<table>/year=YYYY/month=MM/data.parquet

Tabelle non datate (entities, entity_links) → file singolo:
  data/parquet/<table>/data.parquet

I Parquet sono la fonte di verità ricostruibile: se il DB viene perso
si può rigenerare via DuckDB — es. duckdb.sql("SELECT * FROM 'data/parquet/raw_documents/**/*.parquet'").
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger


@dataclass
class ExportResult:
    tables_written: list[str] = field(default_factory=list)
    rows_written: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# Tables with a date column used for partitioning
DATED_TABLES: dict[str, str] = {
    "raw_documents": "published_at",
    "events": "first_seen",
}

# Tables exported as single files (no natural date column)
UNDATED_TABLES: list[str] = ["entities", "entity_links"]


def _rows_to_arrow(rows: list[sqlite3.Row]) -> pa.Table:
    if not rows:
        return pa.table({})
    cols = list(rows[0].keys())
    return pa.table({col: [row[col] for row in rows] for col in cols})


def export_to_parquet(
    conn: sqlite3.Connection,
    parquet_dir: Path,
    tables: list[str] | None = None,
) -> ExportResult:
    """Export SQLite tables to partitioned Parquet files.

    Idempotent: overwrites existing partition files on each run.
    tables: if given, only export these table names; else export all.
    """
    result = ExportResult()

    for table, date_col in DATED_TABLES.items():
        if tables and table not in tables:
            continue
        try:
            periods = conn.execute(
                f"SELECT DISTINCT substr({date_col}, 1, 7) AS ym "
                f"FROM {table} WHERE {date_col} IS NOT NULL ORDER BY ym"
            ).fetchall()

            total = 0
            for row in periods:
                ym: str = row["ym"]
                if not ym or len(ym) < 7:
                    continue
                year, month = ym[:4], ym[5:7]

                chunk = conn.execute(
                    f"SELECT * FROM {table} WHERE substr({date_col}, 1, 7) = ?",
                    (ym,),
                ).fetchall()
                if not chunk:
                    continue

                out_dir = parquet_dir / table / f"year={year}" / f"month={month}"
                out_dir.mkdir(parents=True, exist_ok=True)
                pq.write_table(_rows_to_arrow(chunk), out_dir / "data.parquet",
                               compression="snappy")
                total += len(chunk)

            # rows with NULL date in their own partition
            null_rows = conn.execute(
                f"SELECT * FROM {table} WHERE {date_col} IS NULL"
            ).fetchall()
            if null_rows:
                out_dir = parquet_dir / table / "undated"
                out_dir.mkdir(parents=True, exist_ok=True)
                pq.write_table(_rows_to_arrow(null_rows), out_dir / "data.parquet",
                               compression="snappy")
                total += len(null_rows)

            result.tables_written.append(table)
            result.rows_written[table] = total
            logger.info(f"Parquet export: {table} → {total:,} rows ({len(periods)} partitions)")

        except Exception as exc:
            msg = f"{table}: {exc}"
            result.errors.append(msg)
            logger.warning(f"Parquet export error — {msg}")

    for table in UNDATED_TABLES:
        if tables and table not in tables:
            continue
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            out_dir = parquet_dir / table
            out_dir.mkdir(parents=True, exist_ok=True)
            pq.write_table(_rows_to_arrow(rows), out_dir / "data.parquet",
                           compression="snappy")
            result.tables_written.append(table)
            result.rows_written[table] = len(rows)
            logger.info(f"Parquet export: {table} → {len(rows):,} rows")

        except Exception as exc:
            msg = f"{table}: {exc}"
            result.errors.append(msg)
            logger.warning(f"Parquet export error — {msg}")

    return result
