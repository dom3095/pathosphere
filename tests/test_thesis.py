"""Tests for pathosphere/agent/thesis.py (3c).

All LLM calls and price fetches are mocked.
DB tests use the tmp_db fixture (full schema).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pathosphere.agent.thesis import (
    ThesisResult,
    _build_prompt,
    _load_brief,
    _save_thesis,
    _save_watchlist_items,
    generate_theses,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_brief(conn: sqlite3.Connection, date: str = "2026-06-23", content: str = "## Brief\nTest content.") -> None:
    conn.execute(
        "INSERT INTO briefs (date, content, event_count, entity_count) VALUES (?, ?, 0, 0)",
        (date, content),
    )
    conn.commit()


_SAMPLE_LLM_RESPONSE = json.dumps({
    "theses": [
        {
            "title": "Hormuz closure → oil spike",
            "trigger_summary": "Iran threatens Strait of Hormuz closure.",
            "causal_chain": [
                "Iran closes Hormuz",
                "30% global oil supply disrupted",
                "Brent price spikes >$120",
            ],
            "instrument": "USO",
            "direction": "long",
            "horizon_days": 14,
            "confidence": 0.65,
            "invalidation": "Iran backs down within 7 days without action",
            "indicators": [
                {"label": "Hormuz tanker traffic", "indicator_query": "hormuz tanker strait"},
                {"label": "Iran military activity", "indicator_query": "iran military navy"},
            ],
            "persona_notes": {
                "beijing": "China diversifies oil routes, watches Hormuz carefully.",
                "washington": "US 5th Fleet on high alert, strategic reserve deployment likely.",
            },
            "alternatives": [
                {
                    "title": "Hormuz bluster only — oil retreats",
                    "trigger_summary": "Iran rhetoric without actual closure.",
                    "causal_chain": ["Threat not executed", "Oil fear premium deflates"],
                    "instrument": "SCO",
                    "direction": "short",
                    "horizon_days": 7,
                    "confidence": 0.25,
                    "invalidation": "Hormuz actually closes",
                    "indicators": [
                        {"label": "Iran rhetoric vs action", "indicator_query": "iran strait hormuz threat"}
                    ],
                }
            ],
        },
        {
            "title": "TSMC disruption → semiconductor rally",
            "trigger_summary": "Taiwan strait tensions affect TSMC production.",
            "causal_chain": [
                "Taiwan strait military exercises",
                "TSMC supply uncertainty",
                "Chip stocks rally on scarcity premium",
            ],
            "instrument": "SOXX",
            "direction": "long",
            "horizon_days": 30,
            "confidence": 0.50,
            "invalidation": "Exercises end without incident within 10 days",
            "indicators": [
                {"label": "Taiwan strait activity", "indicator_query": "taiwan strait military exercise"}
            ],
            "persona_notes": {
                "beijing": "Exercises are routine and proportionate.",
                "washington": "Allies on standby, supply chain diversification accelerated.",
            },
            "alternatives": [],
        },
        {
            "title": "Black Sea grain deal collapse → wheat spike",
            "trigger_summary": "Russia suspends Black Sea grain deal.",
            "causal_chain": [
                "Russia blocks Black Sea exports",
                "Ukrainian wheat supply disrupted",
                "Global wheat futures spike",
            ],
            "instrument": "WEAT",
            "direction": "long",
            "horizon_days": 21,
            "confidence": 0.55,
            "invalidation": "Deal renewed within 14 days",
            "indicators": [
                {"label": "Black Sea shipping", "indicator_query": "black sea grain export ship"}
            ],
            "persona_notes": {
                "beijing": "China already has bilateral wheat agreements insulating it.",
                "washington": "Food security pressure on Global South allies.",
            },
            "alternatives": [],
        },
    ]
})


# ── unit tests ────────────────────────────────────────────────────────────────

def test_load_brief_found(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Test brief")
    result = _load_brief(tmp_db, "2026-06-23")
    assert result == "## Test brief"


def test_load_brief_missing_returns_none(tmp_db):
    result = _load_brief(tmp_db, "2000-01-01")
    assert result is None


def test_build_prompt_structure():
    messages = _build_prompt("## Brief content", n=3)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "3" in messages[1]["content"]
    assert "## Brief content" in messages[1]["content"]


def test_save_thesis_returns_id(tmp_db):
    t = {
        "title": "Test thesis",
        "trigger_summary": "Something happened",
        "causal_chain": ["step1", "step2"],
        "instrument": "USO",
        "direction": "long",
        "horizon_days": 14,
        "confidence": 0.6,
        "invalidation": "Price drops",
        "persona_notes": {"beijing": "...", "washington": "..."},
    }
    thesis_id = _save_thesis(tmp_db, t, price_snapshot=99.50)
    assert isinstance(thesis_id, int)
    assert thesis_id > 0

    row = tmp_db.execute("SELECT * FROM theses WHERE id = ?", (thesis_id,)).fetchone()
    assert row["title"] == "Test thesis"
    assert row["instrument"] == "USO"
    assert row["direction"] == "long"
    assert row["price_snapshot"] == pytest.approx(99.50)
    assert row["status"] == "pending"

    chain = json.loads(row["causal_chain"])
    assert chain["steps"] == ["step1", "step2"]
    assert chain["trigger_summary"] == "Something happened"


def test_save_thesis_null_price(tmp_db):
    t = {"title": "No price", "causal_chain": [], "instrument": "UNKNOWN"}
    thesis_id = _save_thesis(tmp_db, t, price_snapshot=None)
    row = tmp_db.execute("SELECT price_snapshot FROM theses WHERE id = ?", (thesis_id,)).fetchone()
    assert row["price_snapshot"] is None


def test_save_watchlist_items(tmp_db):
    t = {"title": "T", "causal_chain": [], "instrument": "X"}
    thesis_id = _save_thesis(tmp_db, t, None)
    indicators = [
        {"label": "Hormuz traffic", "indicator_query": "hormuz tanker"},
        {"label": "Iran navy", "indicator_query": "iran navy"},
    ]
    count = _save_watchlist_items(tmp_db, thesis_id, indicators)
    assert count == 2

    rows = tmp_db.execute(
        "SELECT * FROM watchlist_items WHERE thesis_id = ?", (thesis_id,)
    ).fetchall()
    assert len(rows) == 2
    labels = {r["label"] for r in rows}
    assert labels == {"Hormuz traffic", "Iran navy"}


# ── integration test ──────────────────────────────────────────────────────────

def test_generate_theses_full(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Morning brief\nSome signals.")

    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_SAMPLE_LLM_RESPONSE)

    with patch("pathosphere.agent.thesis.fetch_price", return_value=75.50), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", return_value=None):
        result = asyncio.run(
            generate_theses(tmp_db, mock_client, brief_date="2026-06-23", n=3)
        )

    # 3 primaries + 1 alternative (thesis 1) = 4 total
    assert result.theses_created == 4
    assert len(result.thesis_ids) == 4
    assert result.watchlist_created > 0

    rows = tmp_db.execute("SELECT * FROM theses ORDER BY id").fetchall()
    assert len(rows) == 4
    for row in rows:
        assert row["status"] == "pending"
        assert row["price_snapshot"] == pytest.approx(75.50)

    # Primary theses have persona_notes in causal_chain JSON
    primary = tmp_db.execute(
        "SELECT causal_chain FROM theses WHERE id = ?", (result.thesis_ids[0],)
    ).fetchone()
    chain = json.loads(primary["causal_chain"])
    assert "persona_notes" in chain
    assert "beijing" in chain["persona_notes"]


def test_generate_theses_no_brief_raises(tmp_db):
    mock_client = MagicMock()
    with pytest.raises(ValueError, match="No brief found"):
        asyncio.run(generate_theses(tmp_db, mock_client, brief_date="2000-01-01"))


def test_generate_theses_invalid_json_returns_zero_theses(tmp_db):
    """Malformed/non-JSON output degrades to 'no theses proposed' instead of
    crashing the pipeline (unattended `pathos loop` must survive this)."""
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value="not valid json at all")

    result = asyncio.run(generate_theses(tmp_db, mock_client, brief_date="2026-06-23"))

    assert result.theses_created == 0
    assert result.watchlist_created == 0
    assert result.thesis_ids == []
    assert result.refusal_reason == "not valid json at all"


def test_generate_theses_prose_refusal_preserved_as_reason(tmp_db):
    """A model that explains in prose why it declined to propose theses
    (thin signal, no-lookahead-bias concerns) is a legitimate outcome — the
    explanation must survive in refusal_reason, not just be discarded."""
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    prose = (
        "No theses proposed — insufficient event-level data this cycle. "
        "Forcing 3 concrete theses would fabricate signal that doesn't exist."
    )
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=prose)

    result = asyncio.run(generate_theses(tmp_db, mock_client, brief_date="2026-06-23"))

    assert result.theses_created == 0
    assert result.refusal_reason == prose


def test_generate_theses_valid_json_has_no_refusal_reason(tmp_db):
    """The normal success path must not set refusal_reason."""
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_SAMPLE_LLM_RESPONSE)

    with patch("pathosphere.agent.thesis.fetch_price", return_value=100.0), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", return_value=None):
        result = asyncio.run(generate_theses(tmp_db, mock_client, brief_date="2026-06-23"))

    assert result.theses_created > 0
    assert result.refusal_reason is None


def test_generate_theses_price_fetch_failure(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_SAMPLE_LLM_RESPONSE)

    with patch("pathosphere.agent.thesis.fetch_price", return_value=None), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", return_value=None):
        result = asyncio.run(
            generate_theses(tmp_db, mock_client, brief_date="2026-06-23", n=3)
        )

    assert result.theses_created == 4
    rows = tmp_db.execute("SELECT price_snapshot FROM theses").fetchall()
    for row in rows:
        assert row["price_snapshot"] is None


# ── fundamentals enrichment ───────────────────────────────────────────────────

def _fake_snapshot(ticker: str):
    from pathosphere.market.fundamentals import FundamentalsSnapshot
    return FundamentalsSnapshot(
        ticker=ticker,
        quote_type="EQUITY",
        sector="Technology",
        pe_trailing=30.0,
        altman_z=4.1,
        altman_zone="safe",
        piotroski_f=7,
        piotroski_testable=9,
        data_quality="full",
        fetched_at="2026-06-23T00:00:00+00:00",
    )


_REVIEW_RESPONSE = json.dumps({
    "assessments": [
        {"thesis_id": 1, "assessment": "Fundamentals support the long thesis."},
        {"thesis_id": 2, "assessment": "Neutral: data quality low."},
        {"thesis_id": 3, "assessment": "Fundamentals support the thesis."},
        {"thesis_id": 4, "assessment": "Fundamentals support the thesis."},
    ]
})


def test_generate_theses_with_fundamentals(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    # 1st call = thesis JSON, 2nd call = fundamentals review JSON
    mock_client.complete = AsyncMock(side_effect=[_SAMPLE_LLM_RESPONSE, _REVIEW_RESPONSE])

    with patch("pathosphere.agent.thesis.fetch_price", return_value=75.50), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", side_effect=_fake_snapshot):
        result = asyncio.run(
            generate_theses(tmp_db, mock_client, brief_date="2026-06-23", n=3)
        )

    assert result.theses_created == 4
    assert mock_client.complete.await_count == 2

    rows = tmp_db.execute("SELECT id, fundamentals_json FROM theses ORDER BY id").fetchall()
    for row in rows:
        assert row["fundamentals_json"] is not None
        doc = json.loads(row["fundamentals_json"])
        assert doc["snapshot"]["quote_type"] == "EQUITY"
        assert "FUNDAMENTALS" in doc["text"]
        assert doc["llm_assessment"]  # review pass annotated every thesis


def test_generate_theses_fundamentals_review_failure_degrades(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(side_effect=[_SAMPLE_LLM_RESPONSE, "not json at all"])

    with patch("pathosphere.agent.thesis.fetch_price", return_value=75.50), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", side_effect=_fake_snapshot):
        result = asyncio.run(
            generate_theses(tmp_db, mock_client, brief_date="2026-06-23", n=3)
        )

    # Review failed → theses still saved, snapshot present, no assessment
    assert result.theses_created == 4
    rows = tmp_db.execute("SELECT fundamentals_json FROM theses").fetchall()
    for row in rows:
        doc = json.loads(row["fundamentals_json"])
        assert "llm_assessment" not in doc


def test_generate_theses_fundamentals_fetch_none_skips_review(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_SAMPLE_LLM_RESPONSE)

    with patch("pathosphere.agent.thesis.fetch_price", return_value=75.50), \
         patch("pathosphere.agent.thesis.fetch_fundamentals", return_value=None):
        result = asyncio.run(
            generate_theses(tmp_db, mock_client, brief_date="2026-06-23", n=3)
        )

    # No fundamentals → single LLM call (no review pass), NULL column
    assert result.theses_created == 4
    assert mock_client.complete.await_count == 1
    rows = tmp_db.execute("SELECT fundamentals_json FROM theses").fetchall()
    assert all(row["fundamentals_json"] is None for row in rows)


def test_generate_theses_enrich_disabled(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief")
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=_SAMPLE_LLM_RESPONSE)

    with patch("pathosphere.agent.thesis.fetch_price", return_value=75.50), \
         patch("pathosphere.agent.thesis.fetch_fundamentals") as mock_fund:
        asyncio.run(
            generate_theses(
                tmp_db, mock_client, brief_date="2026-06-23", n=3,
                enrich_fundamentals=False,
            )
        )

    mock_fund.assert_not_called()
