"""
Non-financial predictions with Tetlock-style calibration.

Operations on the `predictions` table:
  add_prediction   — insert a new forecast
  list_predictions — query open / resolved / all
  get_prediction   — fetch single row
  resolve_prediction — record outcome, compute brier_score
  get_calibration  — aggregate Brier score + per-bucket breakdown
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


# ── validation helpers ────────────────────────────────────────────────────────

def _validate_probability(probability: float) -> None:
    if not (0.0 <= probability <= 1.0):
        raise ValueError(f"probability must be 0.0–1.0, got {probability}")


def _validate_horizon_date(horizon_date: str) -> None:
    try:
        datetime.strptime(horizon_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"horizon_date must be ISO YYYY-MM-DD, got '{horizon_date}'")


# ── query helpers ─────────────────────────────────────────────────────────────

def list_predictions(
    conn: sqlite3.Connection,
    only_open: bool = False,
    only_resolved: bool = False,
) -> list[sqlite3.Row]:
    """Return predictions ordered by horizon_date ASC then id ASC.

    Flags are mutually exclusive; if both False → return all.
    """
    where = ""
    if only_open:
        where = "WHERE resolved = 0"
    elif only_resolved:
        where = "WHERE resolved = 1"

    return conn.execute(
        f"""
        SELECT id, thesis_id, description, probability, horizon_date,
               resolved, outcome, brier_score, resolved_at, created_at
        FROM predictions
        {where}
        ORDER BY horizon_date ASC, id ASC
        """
    ).fetchall()


def get_prediction(conn: sqlite3.Connection, prediction_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
    ).fetchone()


# ── mutations ─────────────────────────────────────────────────────────────────

def add_prediction(
    conn: sqlite3.Connection,
    description: str,
    probability: float,
    horizon_date: str,
    thesis_id: int | None = None,
) -> sqlite3.Row:
    """Insert a new prediction. Returns the inserted row.

    Raises ValueError on invalid probability or horizon_date.
    """
    _validate_probability(probability)
    _validate_horizon_date(horizon_date)

    if not description or not description.strip():
        raise ValueError("description must not be empty")

    cur = conn.execute(
        """
        INSERT INTO predictions (thesis_id, description, probability, horizon_date)
        VALUES (?, ?, ?, ?)
        """,
        (thesis_id, description.strip(), probability, horizon_date),
    )
    conn.commit()
    return get_prediction(conn, cur.lastrowid)  # type: ignore[arg-type]


def resolve_prediction(
    conn: sqlite3.Connection,
    prediction_id: int,
    outcome: bool,
) -> sqlite3.Row:
    """Record outcome, compute brier_score = (probability - outcome)². Returns updated row.

    Raises ValueError if prediction not found or already resolved.
    """
    pred = get_prediction(conn, prediction_id)
    if pred is None:
        raise ValueError(f"Prediction {prediction_id} not found.")
    if pred["resolved"]:
        raise ValueError(f"Prediction {prediction_id} is already resolved.")

    outcome_float = 1.0 if outcome else 0.0
    brier_score = (pred["probability"] - outcome_float) ** 2
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        UPDATE predictions
        SET resolved = 1, outcome = ?, brier_score = ?, resolved_at = ?
        WHERE id = ?
        """,
        (int(outcome), brier_score, now, prediction_id),
    )
    conn.commit()
    return get_prediction(conn, prediction_id)  # type: ignore[return-value]


# ── calibration ───────────────────────────────────────────────────────────────

_BUCKETS = [
    ("0-20%",  0.0, 0.2),
    ("20-40%", 0.2, 0.4),
    ("40-60%", 0.4, 0.6),
    ("60-80%", 0.6, 0.8),
    ("80-100%", 0.8, 1.0),
]


def get_calibration(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return mean Brier score and per-bucket breakdown for all resolved predictions."""
    rows = conn.execute(
        "SELECT probability, outcome, brier_score FROM predictions WHERE resolved = 1"
    ).fetchall()

    if not rows:
        return {
            "total_resolved": 0,
            "mean_brier_score": None,
            "buckets": [
                {"label": label, "min": lo, "max": hi, "count": 0,
                 "mean_brier": None, "accuracy": None}
                for label, lo, hi in _BUCKETS
            ],
        }

    total_brier = sum(r["brier_score"] for r in rows if r["brier_score"] is not None)
    mean_brier = total_brier / len(rows)

    buckets = []
    for label, lo, hi in _BUCKETS:
        # last bucket is inclusive on both ends (probability == 1.0 lands here)
        if hi == 1.0:
            bucket_rows = [r for r in rows if lo <= r["probability"] <= hi]
        else:
            bucket_rows = [r for r in rows if lo <= r["probability"] < hi]

        count = len(bucket_rows)
        if count == 0:
            buckets.append({"label": label, "min": lo, "max": hi,
                             "count": 0, "mean_brier": None, "accuracy": None})
        else:
            b_mean = sum(r["brier_score"] for r in bucket_rows if r["brier_score"] is not None) / count
            accuracy = sum(1 for r in bucket_rows if r["outcome"] == 1) / count
            buckets.append({"label": label, "min": lo, "max": hi,
                             "count": count, "mean_brier": b_mean, "accuracy": accuracy})

    return {
        "total_resolved": len(rows),
        "mean_brier_score": mean_brier,
        "buckets": buckets,
    }
