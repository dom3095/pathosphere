"""Tests for pathosphere/agent/approval.py (3d).

All yfinance calls are mocked.
DB tests use the tmp_db fixture (full schema).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from unittest.mock import MagicMock, patch

from pathosphere.agent.approval import (
    approve_thesis,
    format_causal_chain,
    get_thesis,
    get_watchlist_items,
    list_theses,
    reject_thesis,
    validate_ticker,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_thesis(
    conn: sqlite3.Connection,
    *,
    title: str = "Test thesis",
    instrument: str = "USO",
    direction: str = "long",
    horizon_days: int = 14,
    confidence: float = 0.65,
    price_snapshot: float | None = 75.0,
    debate_id: int | None = None,
    status: str = "pending",
) -> int:
    causal_chain = json.dumps({
        "steps": ["step1", "step2"],
        "trigger_summary": "Something happened",
        "persona_notes": {"beijing": "Cautious", "washington": "Alert"},
        "debate_context": {
            "supporters": ["beijing"],
            "opponents": ["washington"],
            "summary": "Moderate consensus.",
        },
    })
    cur = conn.execute(
        """
        INSERT INTO theses (
            title, causal_chain, instrument, direction,
            horizon_days, confidence, price_snapshot, status, debate_id,
            invalidation, sources_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title, causal_chain, instrument, direction,
            horizon_days, confidence, price_snapshot, status, debate_id,
            "Price drops below entry",
            json.dumps([]),
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _insert_debate(conn: sqlite3.Connection, date: str = "2026-06-23") -> int:
    cur = conn.execute(
        "INSERT INTO debates (date, status) VALUES (?, 'done')", (date,)
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _insert_watchlist(conn: sqlite3.Connection, thesis_id: int, n: int = 2) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO watchlist_items (thesis_id, label, description, indicator_query) VALUES (?, ?, ?, ?)",
            (thesis_id, f"Indicator {i}", f"Desc {i}", f"query {i}"),
        )
    conn.commit()


# ── list_theses ───────────────────────────────────────────────────────────────

def test_list_theses_empty(tmp_db):
    rows = list_theses(tmp_db, status="pending")
    assert rows == []


def test_list_theses_returns_pending(tmp_db):
    _insert_thesis(tmp_db, title="A", status="pending")
    _insert_thesis(tmp_db, title="B", status="approved")
    rows = list_theses(tmp_db, status="pending")
    assert len(rows) == 1
    assert rows[0]["title"] == "A"


def test_list_theses_status_filter(tmp_db):
    _insert_thesis(tmp_db, title="P", status="pending")
    _insert_thesis(tmp_db, title="A", status="approved")
    _insert_thesis(tmp_db, title="R", status="rejected")

    assert len(list_theses(tmp_db, "approved")) == 1
    assert len(list_theses(tmp_db, "rejected")) == 1
    assert len(list_theses(tmp_db, "pending")) == 1


def test_list_theses_newest_first(tmp_db):
    id1 = _insert_thesis(tmp_db, title="First")
    id2 = _insert_thesis(tmp_db, title="Second")
    rows = list_theses(tmp_db)
    assert rows[0]["id"] == id2
    assert rows[1]["id"] == id1


def test_list_theses_shows_debate_flag(tmp_db):
    debate_id = _insert_debate(tmp_db)
    _insert_thesis(tmp_db, title="Fast", debate_id=None)
    _insert_thesis(tmp_db, title="Debated", debate_id=debate_id)
    rows = list_theses(tmp_db)
    debate_flags = {r["title"]: r["debate_id"] for r in rows}
    assert debate_flags["Fast"] is None
    assert debate_flags["Debated"] == debate_id


# ── get_thesis ────────────────────────────────────────────────────────────────

def test_get_thesis_found(tmp_db):
    thesis_id = _insert_thesis(tmp_db)
    row = get_thesis(tmp_db, thesis_id)
    assert row is not None
    assert row["id"] == thesis_id


def test_get_thesis_not_found(tmp_db):
    assert get_thesis(tmp_db, 9999) is None


# ── get_watchlist_items ───────────────────────────────────────────────────────

def test_get_watchlist_items_empty(tmp_db):
    tid = _insert_thesis(tmp_db)
    items = get_watchlist_items(tmp_db, tid)
    assert items == []


def test_get_watchlist_items_returns_linked(tmp_db):
    tid = _insert_thesis(tmp_db)
    _insert_watchlist(tmp_db, tid, n=3)
    items = get_watchlist_items(tmp_db, tid)
    assert len(items) == 3
    labels = {i["label"] for i in items}
    assert "Indicator 0" in labels


def test_get_watchlist_items_isolation(tmp_db):
    tid1 = _insert_thesis(tmp_db, title="T1")
    tid2 = _insert_thesis(tmp_db, title="T2")
    _insert_watchlist(tmp_db, tid1, n=2)
    _insert_watchlist(tmp_db, tid2, n=1)
    assert len(get_watchlist_items(tmp_db, tid1)) == 2
    assert len(get_watchlist_items(tmp_db, tid2)) == 1


# ── validate_ticker ───────────────────────────────────────────────────────────

def test_validate_ticker_valid():
    mock_info = MagicMock()
    mock_info.last_price = 150.0
    with patch("pathosphere.agent.approval.yf.Ticker") as mock_yf:
        mock_yf.return_value.fast_info = mock_info
        assert validate_ticker("AAPL") is True


def test_validate_ticker_unknown():
    mock_info = MagicMock()
    mock_info.last_price = None
    with patch("pathosphere.agent.approval.yf.Ticker") as mock_yf:
        mock_yf.return_value.fast_info = mock_info
        assert validate_ticker("FAKEXYZ999") is False


def test_validate_ticker_zero_price():
    mock_info = MagicMock()
    mock_info.last_price = 0
    with patch("pathosphere.agent.approval.yf.Ticker") as mock_yf:
        mock_yf.return_value.fast_info = mock_info
        assert validate_ticker("BAD") is False


def test_validate_ticker_exception():
    with patch("pathosphere.agent.approval.yf.Ticker", side_effect=Exception("network")):
        assert validate_ticker("ERR") is False


def test_validate_ticker_empty():
    assert validate_ticker("") is False
    assert validate_ticker("   ") is False


# ── approve_thesis ────────────────────────────────────────────────────────────

def test_approve_thesis_sets_status(tmp_db):
    tid = _insert_thesis(tmp_db, status="pending")
    updated = approve_thesis(tmp_db, tid)
    assert updated["status"] == "approved"
    assert updated["approved_at"] is not None


def test_approve_thesis_persisted(tmp_db):
    tid = _insert_thesis(tmp_db)
    approve_thesis(tmp_db, tid)
    row = get_thesis(tmp_db, tid)
    assert row["status"] == "approved"
    assert row["approved_at"] is not None


def test_approve_thesis_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        approve_thesis(tmp_db, 9999)


def test_approve_thesis_already_approved(tmp_db):
    tid = _insert_thesis(tmp_db, status="approved")
    with pytest.raises(ValueError, match="approved"):
        approve_thesis(tmp_db, tid)


def test_approve_thesis_rejected_raises(tmp_db):
    tid = _insert_thesis(tmp_db, status="rejected")
    with pytest.raises(ValueError, match="rejected"):
        approve_thesis(tmp_db, tid)


# ── reject_thesis ─────────────────────────────────────────────────────────────

def test_reject_thesis_sets_status(tmp_db):
    tid = _insert_thesis(tmp_db)
    updated = reject_thesis(tmp_db, tid, "Invalidation condition met")
    assert updated["status"] == "rejected"
    assert updated["rejection_reason"] == "Invalidation condition met"
    assert updated["rejected_at"] is not None


def test_reject_thesis_persisted(tmp_db):
    tid = _insert_thesis(tmp_db)
    reject_thesis(tmp_db, tid, "Test reason")
    row = get_thesis(tmp_db, tid)
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "Test reason"


def test_reject_thesis_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        reject_thesis(tmp_db, 9999, "some reason")


def test_reject_thesis_empty_reason(tmp_db):
    tid = _insert_thesis(tmp_db)
    with pytest.raises(ValueError, match="empty"):
        reject_thesis(tmp_db, tid, "")


def test_reject_thesis_whitespace_reason(tmp_db):
    tid = _insert_thesis(tmp_db)
    with pytest.raises(ValueError, match="empty"):
        reject_thesis(tmp_db, tid, "   ")


def test_reject_thesis_already_approved(tmp_db):
    tid = _insert_thesis(tmp_db, status="approved")
    with pytest.raises(ValueError, match="approved"):
        reject_thesis(tmp_db, tid, "changed mind")


# ── format_causal_chain ───────────────────────────────────────────────────────

def test_format_causal_chain_valid():
    raw = json.dumps({"steps": ["a", "b"], "trigger_summary": "X", "persona_notes": {}})
    result = format_causal_chain(raw)
    assert result["steps"] == ["a", "b"]
    assert result["trigger_summary"] == "X"


def test_format_causal_chain_invalid_json():
    result = format_causal_chain("not json {{")
    assert "raw" in result


def test_format_causal_chain_empty():
    result = format_causal_chain("")
    assert result == {}


def test_format_causal_chain_none():
    result = format_causal_chain(None)
    assert result == {}


# ── integration: full approval flow ──────────────────────────────────────────

def test_full_approval_flow(tmp_db):
    """Pending → approved. Watchlist items survive unmodified."""
    tid = _insert_thesis(tmp_db, title="Hormuz spike")
    _insert_watchlist(tmp_db, tid, n=2)

    # Approve
    approve_thesis(tmp_db, tid)

    thesis = get_thesis(tmp_db, tid)
    assert thesis["status"] == "approved"

    # Watchlist unchanged
    items = get_watchlist_items(tmp_db, tid)
    assert len(items) == 2


def test_full_rejection_flow(tmp_db):
    """Pending → rejected with reason logged."""
    tid = _insert_thesis(tmp_db, title="Grain deal")

    reject_thesis(tmp_db, tid, "Ukraine signed extension, deal renewed.")

    thesis = get_thesis(tmp_db, tid)
    assert thesis["status"] == "rejected"
    assert "extension" in thesis["rejection_reason"]


def test_cannot_approve_after_reject(tmp_db):
    tid = _insert_thesis(tmp_db)
    reject_thesis(tmp_db, tid, "Reason A")
    with pytest.raises(ValueError):
        approve_thesis(tmp_db, tid)


def test_list_includes_fast_and_debate_theses(tmp_db):
    """list_theses returns both debate=None (fast path) and debate=<id> rows."""
    debate_id = _insert_debate(tmp_db)
    _insert_thesis(tmp_db, title="Fast path", debate_id=None)
    _insert_thesis(tmp_db, title="Debate path", debate_id=debate_id)

    rows = list_theses(tmp_db, status="pending")
    assert len(rows) == 2
    sources = {r["debate_id"] for r in rows}
    assert None in sources
    assert debate_id in sources
