"""Tests for pathosphere/agent/predictions.py (3f).

All tests use the tmp_db fixture (full schema).
"""

from __future__ import annotations

import sqlite3

import pytest

from pathosphere.agent.predictions import (
    add_prediction,
    get_calibration,
    get_prediction,
    list_predictions,
    resolve_prediction,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _add(
    conn: sqlite3.Connection,
    description: str = "Test prediction",
    probability: float = 0.65,
    horizon_date: str = "2026-07-10",
    thesis_id: int | None = None,
) -> sqlite3.Row:
    return add_prediction(conn, description, probability, horizon_date, thesis_id=thesis_id)


def _add_resolved(
    conn: sqlite3.Connection,
    description: str = "Resolved prediction",
    probability: float = 0.7,
    horizon_date: str = "2026-07-01",
    outcome: bool = True,
) -> sqlite3.Row:
    row = add_prediction(conn, description, probability, horizon_date)
    return resolve_prediction(conn, row["id"], outcome)


# ── add_prediction ────────────────────────────────────────────────────────────

def test_add_prediction_returns_row(tmp_db):
    row = _add(tmp_db)
    assert row is not None
    assert row["id"] is not None


def test_add_prediction_fields(tmp_db):
    row = _add(tmp_db, description="Taiwan escalation", probability=0.65, horizon_date="2026-07-10")
    assert row["description"] == "Taiwan escalation"
    assert row["probability"] == pytest.approx(0.65)
    assert row["horizon_date"] == "2026-07-10"
    assert row["resolved"] == 0
    assert row["outcome"] is None
    assert row["brier_score"] is None
    assert row["thesis_id"] is None


def test_add_prediction_with_thesis_id(tmp_db):
    # Insert a thesis first to satisfy FK
    cur = tmp_db.execute(
        "INSERT INTO theses (title, causal_chain) VALUES ('T', 'c')"
    )
    tmp_db.commit()
    thesis_id = cur.lastrowid
    row = _add(tmp_db, thesis_id=thesis_id)
    assert row["thesis_id"] == thesis_id


def test_add_prediction_without_thesis_id(tmp_db):
    row = _add(tmp_db)
    assert row["thesis_id"] is None


def test_add_prediction_probability_too_low(tmp_db):
    with pytest.raises(ValueError, match="0.0–1.0"):
        add_prediction(tmp_db, "X", -0.01, "2026-07-10")


def test_add_prediction_probability_too_high(tmp_db):
    with pytest.raises(ValueError, match="0.0–1.0"):
        add_prediction(tmp_db, "X", 1.01, "2026-07-10")


def test_add_prediction_probability_zero_ok(tmp_db):
    row = add_prediction(tmp_db, "Zero prob", 0.0, "2026-07-10")
    assert row["probability"] == pytest.approx(0.0)


def test_add_prediction_probability_one_ok(tmp_db):
    row = add_prediction(tmp_db, "Certain", 1.0, "2026-07-10")
    assert row["probability"] == pytest.approx(1.0)


def test_add_prediction_invalid_date(tmp_db):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        add_prediction(tmp_db, "X", 0.5, "10-07-2026")


def test_add_prediction_invalid_date_nonsense(tmp_db):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        add_prediction(tmp_db, "X", 0.5, "notadate")


def test_add_prediction_empty_description(tmp_db):
    with pytest.raises(ValueError, match="empty"):
        add_prediction(tmp_db, "", 0.5, "2026-07-10")


def test_add_prediction_whitespace_description(tmp_db):
    with pytest.raises(ValueError, match="empty"):
        add_prediction(tmp_db, "   ", 0.5, "2026-07-10")


def test_add_prediction_persisted(tmp_db):
    row = _add(tmp_db, description="Persist check", probability=0.5)
    fetched = get_prediction(tmp_db, row["id"])
    assert fetched is not None
    assert fetched["description"] == "Persist check"


# ── get_prediction ────────────────────────────────────────────────────────────

def test_get_prediction_found(tmp_db):
    row = _add(tmp_db)
    fetched = get_prediction(tmp_db, row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]


def test_get_prediction_not_found(tmp_db):
    assert get_prediction(tmp_db, 9999) is None


# ── list_predictions ──────────────────────────────────────────────────────────

def test_list_predictions_empty(tmp_db):
    assert list_predictions(tmp_db) == []


def test_list_predictions_all(tmp_db):
    _add(tmp_db, description="A")
    _add_resolved(tmp_db, description="B", outcome=True)
    rows = list_predictions(tmp_db)
    assert len(rows) == 2


def test_list_predictions_only_open(tmp_db):
    _add(tmp_db, description="Open")
    _add_resolved(tmp_db, description="Resolved", outcome=False)
    rows = list_predictions(tmp_db, only_open=True)
    assert len(rows) == 1
    assert rows[0]["description"] == "Open"


def test_list_predictions_only_resolved(tmp_db):
    _add(tmp_db, description="Open")
    _add_resolved(tmp_db, description="Done", outcome=True)
    rows = list_predictions(tmp_db, only_resolved=True)
    assert len(rows) == 1
    assert rows[0]["description"] == "Done"


def test_list_predictions_order_by_horizon(tmp_db):
    _add(tmp_db, description="Later", horizon_date="2026-08-01")
    _add(tmp_db, description="Earlier", horizon_date="2026-07-01")
    rows = list_predictions(tmp_db)
    assert rows[0]["description"] == "Earlier"
    assert rows[1]["description"] == "Later"


def test_list_predictions_no_flags_returns_all(tmp_db):
    _add(tmp_db)
    _add_resolved(tmp_db, outcome=True)
    rows = list_predictions(tmp_db, only_open=False, only_resolved=False)
    assert len(rows) == 2


# ── resolve_prediction ────────────────────────────────────────────────────────

def test_resolve_prediction_true(tmp_db):
    row = _add(tmp_db, probability=0.8)
    updated = resolve_prediction(tmp_db, row["id"], True)
    assert updated["resolved"] == 1
    assert updated["outcome"] == 1
    assert updated["brier_score"] == pytest.approx((0.8 - 1.0) ** 2)
    assert updated["resolved_at"] is not None


def test_resolve_prediction_false(tmp_db):
    row = _add(tmp_db, probability=0.3)
    updated = resolve_prediction(tmp_db, row["id"], False)
    assert updated["resolved"] == 1
    assert updated["outcome"] == 0
    assert updated["brier_score"] == pytest.approx((0.3 - 0.0) ** 2)


def test_resolve_prediction_brier_perfect_true(tmp_db):
    row = _add(tmp_db, probability=1.0)
    updated = resolve_prediction(tmp_db, row["id"], True)
    assert updated["brier_score"] == pytest.approx(0.0)


def test_resolve_prediction_brier_perfect_false(tmp_db):
    row = _add(tmp_db, probability=0.0)
    updated = resolve_prediction(tmp_db, row["id"], False)
    assert updated["brier_score"] == pytest.approx(0.0)


def test_resolve_prediction_brier_worst_case(tmp_db):
    row = _add(tmp_db, probability=1.0)
    updated = resolve_prediction(tmp_db, row["id"], False)
    assert updated["brier_score"] == pytest.approx(1.0)


def test_resolve_prediction_persisted(tmp_db):
    row = _add(tmp_db, probability=0.6)
    resolve_prediction(tmp_db, row["id"], True)
    fetched = get_prediction(tmp_db, row["id"])
    assert fetched["resolved"] == 1
    assert fetched["outcome"] == 1
    assert fetched["brier_score"] == pytest.approx((0.6 - 1.0) ** 2)


def test_resolve_prediction_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        resolve_prediction(tmp_db, 9999, True)


def test_resolve_prediction_already_resolved(tmp_db):
    row = _add(tmp_db)
    resolve_prediction(tmp_db, row["id"], True)
    with pytest.raises(ValueError, match="already resolved"):
        resolve_prediction(tmp_db, row["id"], False)


# ── get_calibration ───────────────────────────────────────────────────────────

def test_calibration_empty(tmp_db):
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 0
    assert cal["mean_brier_score"] is None
    assert len(cal["buckets"]) == 5
    for b in cal["buckets"]:
        assert b["count"] == 0
        assert b["mean_brier"] is None
        assert b["accuracy"] is None


def test_calibration_total_resolved(tmp_db):
    _add_resolved(tmp_db, probability=0.7, outcome=True)
    _add_resolved(tmp_db, probability=0.3, outcome=False)
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 2


def test_calibration_mean_brier(tmp_db):
    # prob=0.8, outcome=true → brier = (0.8-1)^2 = 0.04
    # prob=0.6, outcome=false → brier = (0.6-0)^2 = 0.36
    # mean = 0.20
    _add_resolved(tmp_db, probability=0.8, outcome=True)
    _add_resolved(tmp_db, probability=0.6, outcome=False)
    cal = get_calibration(tmp_db)
    assert cal["mean_brier_score"] == pytest.approx(0.20)


def test_calibration_bucket_labels(tmp_db):
    _add_resolved(tmp_db, probability=0.5, outcome=True)
    cal = get_calibration(tmp_db)
    labels = [b["label"] for b in cal["buckets"]]
    assert labels == ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]


def test_calibration_bucket_counts(tmp_db):
    _add_resolved(tmp_db, probability=0.1, outcome=True)   # bucket 0-20%
    _add_resolved(tmp_db, probability=0.7, outcome=False)  # bucket 60-80%
    _add_resolved(tmp_db, probability=0.75, outcome=True)  # bucket 60-80%
    cal = get_calibration(tmp_db)
    bucket_counts = {b["label"]: b["count"] for b in cal["buckets"]}
    assert bucket_counts["0-20%"] == 1
    assert bucket_counts["20-40%"] == 0
    assert bucket_counts["40-60%"] == 0
    assert bucket_counts["60-80%"] == 2
    assert bucket_counts["80-100%"] == 0


def test_calibration_bucket_accuracy(tmp_db):
    # 60-80% bucket: 2 predictions, 1 true → accuracy = 0.5
    _add_resolved(tmp_db, probability=0.7, outcome=True)
    _add_resolved(tmp_db, probability=0.65, outcome=False)
    cal = get_calibration(tmp_db)
    bucket = next(b for b in cal["buckets"] if b["label"] == "60-80%")
    assert bucket["count"] == 2
    assert bucket["accuracy"] == pytest.approx(0.5)


def test_calibration_probability_one_in_last_bucket(tmp_db):
    _add_resolved(tmp_db, probability=1.0, outcome=True)
    cal = get_calibration(tmp_db)
    last = cal["buckets"][-1]
    assert last["label"] == "80-100%"
    assert last["count"] == 1


def test_calibration_open_predictions_excluded(tmp_db):
    _add(tmp_db, probability=0.9)  # open, not resolved
    _add_resolved(tmp_db, probability=0.5, outcome=True)
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 1


def test_calibration_five_buckets_always_returned(tmp_db):
    cal = get_calibration(tmp_db)
    assert len(cal["buckets"]) == 5


# ── integration ───────────────────────────────────────────────────────────────

def test_full_lifecycle(tmp_db):
    """Add → list open → resolve → list resolved → calibration."""
    row = _add(tmp_db, description="Hormuz closure within 30d", probability=0.25)

    # Must be open
    open_rows = list_predictions(tmp_db, only_open=True)
    assert any(r["id"] == row["id"] for r in open_rows)

    # Resolve false
    updated = resolve_prediction(tmp_db, row["id"], False)
    assert updated["brier_score"] == pytest.approx(0.25 ** 2)

    # Now appears in resolved list
    res_rows = list_predictions(tmp_db, only_resolved=True)
    assert any(r["id"] == row["id"] for r in res_rows)

    # Not in open list anymore
    open_after = list_predictions(tmp_db, only_open=True)
    assert all(r["id"] != row["id"] for r in open_after)

    # Calibration includes it
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 1
    bucket = next(b for b in cal["buckets"] if b["label"] == "20-40%")
    assert bucket["count"] == 1
