"""Tests for pathosphere/agent/debate.py (3c — multi-persona pipeline).

All LLM calls and price fetches are mocked.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pathosphere.agent.debate import (
    PERSONAS,
    DebateResult,
    _divergence_prompt,
    _load_brief,
    _research_prompt,
    _run_critique,
    _run_divergence_detection,
    _run_research,
    _run_synthesis,
    _save_debate,
    _save_persona_analysis,
    _update_debate_status,
    run_debate,
)


# ── fixtures / helpers ────────────────────────────────────────────────────────

def _insert_brief(conn: sqlite3.Connection, date: str = "2026-06-23",
                  content: str = "## Brief\nTest signals.") -> int:
    cur = conn.execute(
        "INSERT INTO briefs (date, content, event_count, entity_count) VALUES (?, ?, 0, 0)",
        (date, content),
    )
    conn.commit()
    return cur.lastrowid


_SAMPLE_RESEARCH = {
    "key_concerns": ["US containment", "Taiwan strait"],
    "opportunities": ["BRI expansion"],
    "key_actors": ["US", "Taiwan"],
    "narrative": "China sees escalating US pressure as destabilising.",
    "risk_assessment": "high",
    "market_implications": "Chip stocks under pressure.",
}

_SAMPLE_DIVERGENCE = json.dumps({
    "divergence_points": [
        {
            "id": "dp1",
            "title": "Taiwan as sovereignty vs. democracy issue",
            "description": "Beijing sees Taiwan as internal; Washington frames it as democracy defence.",
            "personas_for": ["beijing", "moscow"],
            "personas_against": ["washington", "jerusalem"],
            "personas_neutral": ["riyadh", "paris"],
        },
        {
            "id": "dp2",
            "title": "Oil sanctions as coercion vs. nonproliferation",
            "description": "Riyadh/Beijing see sanctions as US economic warfare; Washington sees necessity.",
            "personas_for": ["riyadh", "beijing"],
            "personas_against": ["washington"],
            "personas_neutral": ["moscow", "jerusalem", "paris"],
        },
    ]
})

_SAMPLE_CRITIQUE = json.dumps({
    "responses": [
        {
            "divergence_id": "dp1",
            "stance": "support",
            "argument": "Taiwan is an internal Chinese matter; US interference violates sovereignty.",
        },
        {
            "divergence_id": "dp2",
            "stance": "nuance",
            "argument": "Sanctions harm Chinese energy supply chains indirectly.",
        },
    ]
})

_SAMPLE_SYNTHESIS = json.dumps({
    "theses": [
        {
            "title": "TSMC supply shock → chip sector spike",
            "trigger_summary": "Taiwan strait exercises disrupt TSMC production schedules.",
            "causal_chain": ["Military exercises near Taiwan", "TSMC shifts capacity", "Chip scarcity premium"],
            "instrument": "SOXX",
            "direction": "long",
            "horizon_days": 21,
            "confidence": 0.60,
            "invalidation": "Exercises end without incident within 10 days",
            "indicators": [{"label": "Taiwan strait activity", "indicator_query": "taiwan strait military"}],
            "debate_context": {
                "supporting_personas": ["washington", "jerusalem"],
                "opposing_personas": ["beijing"],
                "related_divergences": ["dp1"],
            },
            "alternatives": [
                {
                    "title": "Exercises de-escalate — chip stocks retreat",
                    "trigger_summary": "No disruption materialises.",
                    "causal_chain": ["Exercises end", "Risk premium deflates"],
                    "instrument": "SOXS",
                    "direction": "long",
                    "horizon_days": 10,
                    "confidence": 0.30,
                    "invalidation": "TSMC reports supply disruption",
                    "indicators": [{"label": "TSMC statements", "indicator_query": "tsmc supply production"}],
                }
            ],
        },
        {
            "title": "Oil chokepoint risk → energy ETF rally",
            "trigger_summary": "Strait of Hormuz threat from Iran.",
            "causal_chain": ["Iran threatens Hormuz", "Oil supply uncertainty", "Energy ETFs rally"],
            "instrument": "USO",
            "direction": "long",
            "horizon_days": 14,
            "confidence": 0.55,
            "invalidation": "Iran backs down within 7 days",
            "indicators": [{"label": "Hormuz traffic", "indicator_query": "hormuz tanker closure"}],
            "debate_context": {
                "supporting_personas": ["riyadh", "washington"],
                "opposing_personas": ["moscow"],
                "related_divergences": ["dp2"],
            },
            "alternatives": [],
        },
        {
            "title": "Sahel instability → gold safe haven",
            "trigger_summary": "French withdrawal from Sahel increases regional instability.",
            "causal_chain": ["France exits Mali/Niger", "Security vacuum", "Gold demand as safe haven"],
            "instrument": "GLD",
            "direction": "long",
            "horizon_days": 30,
            "confidence": 0.45,
            "invalidation": "Sahel coalition stabilises within 2 weeks",
            "indicators": [{"label": "Sahel security", "indicator_query": "sahel mali niger instability"}],
            "debate_context": {
                "supporting_personas": ["paris", "moscow"],
                "opposing_personas": [],
                "related_divergences": [],
            },
            "alternatives": [],
        },
    ]
})


# ── unit tests ────────────────────────────────────────────────────────────────

def test_personas_catalogue():
    assert set(PERSONAS.keys()) == {"beijing", "washington", "moscow", "riyadh", "jerusalem", "paris"}
    for key, cfg in PERSONAS.items():
        assert "name" in cfg
        assert "context" in cfg
        assert len(cfg["context"]) > 50


def test_load_brief_found(tmp_db):
    brief_id = _insert_brief(tmp_db, "2026-06-23", "## Content")
    loaded_id, content = _load_brief(tmp_db, "2026-06-23")
    assert loaded_id == brief_id
    assert content == "## Content"


def test_load_brief_missing(tmp_db):
    bid, content = _load_brief(tmp_db, "2000-01-01")
    assert bid is None
    assert content is None


def test_save_debate_returns_id(tmp_db):
    debate_id = _save_debate(tmp_db, "2026-06-23", None)
    assert isinstance(debate_id, int) and debate_id > 0
    row = tmp_db.execute("SELECT * FROM debates WHERE id = ?", (debate_id,)).fetchone()
    assert row["status"] == "in_progress"
    assert row["date"] == "2026-06-23"


def test_save_persona_analysis(tmp_db):
    debate_id = _save_debate(tmp_db, "2026-06-23", None)
    _save_persona_analysis(tmp_db, debate_id, "beijing", "research", {"key": "val"})
    row = tmp_db.execute(
        "SELECT * FROM persona_analyses WHERE debate_id = ?", (debate_id,)
    ).fetchone()
    assert row["persona"] == "beijing"
    assert row["step"] == "research"
    assert json.loads(row["content"]) == {"key": "val"}


def test_update_debate_status(tmp_db):
    debate_id = _save_debate(tmp_db, "2026-06-23", None)
    _update_debate_status(tmp_db, debate_id, "complete")
    row = tmp_db.execute("SELECT status FROM debates WHERE id = ?", (debate_id,)).fetchone()
    assert row["status"] == "complete"


def test_research_prompt_contains_persona(tmp_db):
    messages = _research_prompt("beijing", PERSONAS["beijing"], "## Brief content")
    assert "Beijing" in messages[0]["content"]
    assert "## Brief content" in messages[1]["content"]


def test_divergence_prompt_contains_all_personas():
    analyses = {k: _SAMPLE_RESEARCH for k in PERSONAS}
    messages = _divergence_prompt(analyses)
    for cfg in PERSONAS.values():
        assert cfg["name"] in messages[1]["content"]


def test_run_research_parses_json():
    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(return_value=json.dumps(_SAMPLE_RESEARCH))
    key, result = asyncio.run(_run_research(mock_qwen, "beijing", PERSONAS["beijing"], "brief"))
    assert key == "beijing"
    assert result["risk_assessment"] == "high"


def test_run_research_handles_bad_json():
    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(return_value="not json at all")
    key, result = asyncio.run(_run_research(mock_qwen, "paris", PERSONAS["paris"], "brief"))
    assert key == "paris"
    assert "narrative" in result


def test_run_divergence_detection():
    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(return_value=_SAMPLE_DIVERGENCE)
    analyses = {k: _SAMPLE_RESEARCH for k in PERSONAS}
    points = asyncio.run(_run_divergence_detection(mock_qwen, analyses))
    assert len(points) == 2
    assert points[0]["id"] == "dp1"


def test_run_critique_parses_json():
    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(return_value=_SAMPLE_CRITIQUE)
    dp = [{"id": "dp1", "title": "Test", "description": "Desc"}]
    key, result = asyncio.run(
        _run_critique(mock_qwen, "beijing", PERSONAS["beijing"], _SAMPLE_RESEARCH, dp)
    )
    assert key == "beijing"
    assert len(result["responses"]) == 2


def test_run_synthesis_returns_theses():
    mock_claude = MagicMock()
    mock_claude.complete = AsyncMock(return_value=_SAMPLE_SYNTHESIS)
    analyses = {k: _SAMPLE_RESEARCH for k in PERSONAS}
    critiques = {k: {"responses": []} for k in PERSONAS}
    dp = [{"id": "dp1", "title": "T", "description": "D", "personas_for": [], "personas_against": []}]

    theses = asyncio.run(
        _run_synthesis(mock_claude, "brief", analyses, dp, critiques, 3)
    )
    assert len(theses) == 3
    assert theses[0]["instrument"] == "SOXX"


# ── integration test ──────────────────────────────────────────────────────────

def test_run_debate_full(tmp_db):
    _insert_brief(tmp_db, "2026-06-23", "## Brief with signals")

    call_count = [0]

    async def mock_qwen_complete(messages, *, json_mode=False, model=None):
        call_count[0] += 1
        # Research calls (6) → return research JSON
        # Divergence call (1) → return divergence JSON
        # Critique calls (6) → return critique JSON
        content = " ".join(m.get("content", "") for m in messages)
        if "divergence" in content.lower() or "disagreement" in content.lower():
            return _SAMPLE_DIVERGENCE
        if "defend your position" in content.lower() or "critique" in content.lower():
            return _SAMPLE_CRITIQUE
        return json.dumps(_SAMPLE_RESEARCH)

    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(side_effect=mock_qwen_complete)

    mock_claude = MagicMock()
    mock_claude.complete = AsyncMock(return_value=_SAMPLE_SYNTHESIS)

    with patch("pathosphere.agent.debate.fetch_price", return_value=100.0):
        result = asyncio.run(
            run_debate(tmp_db, mock_qwen, mock_claude, brief_date="2026-06-23", n_theses=3)
        )

    assert isinstance(result, DebateResult)
    assert result.debate_id > 0
    assert len(result.divergence_points) == 2
    assert result.thesis_result.theses_created == 4  # 3 primary + 1 alt

    # Debate status = complete
    row = tmp_db.execute(
        "SELECT status FROM debates WHERE id = ?", (result.debate_id,)
    ).fetchone()
    assert row["status"] == "complete"

    # persona_analyses saved for all steps
    analyses = tmp_db.execute(
        "SELECT persona, step FROM persona_analyses WHERE debate_id = ? ORDER BY id",
        (result.debate_id,),
    ).fetchall()
    steps = {(r["persona"], r["step"]) for r in analyses}
    for pk in PERSONAS:
        assert (pk, "research") in steps
        assert (pk, "critique") in steps
    assert ("meta", "divergence") in steps

    # Theses linked to debate
    theses = tmp_db.execute(
        "SELECT debate_id, price_snapshot FROM theses WHERE debate_id = ?",
        (result.debate_id,),
    ).fetchall()
    assert len(theses) == 4
    for t in theses:
        assert t["price_snapshot"] == pytest.approx(100.0)


def test_run_debate_no_brief_raises(tmp_db):
    mock_qwen = MagicMock()
    mock_claude = MagicMock()
    with pytest.raises(ValueError, match="No brief found"):
        asyncio.run(run_debate(tmp_db, mock_qwen, mock_claude, brief_date="2000-01-01"))


def test_run_debate_synthesis_failure_marks_failed(tmp_db):
    _insert_brief(tmp_db, "2026-06-23")
    mock_qwen = MagicMock()
    mock_qwen.complete = AsyncMock(return_value=json.dumps(_SAMPLE_RESEARCH))

    mock_claude = MagicMock()
    mock_claude.complete = AsyncMock(return_value="INVALID JSON {{{")

    with pytest.raises(ValueError):
        asyncio.run(
            run_debate(tmp_db, mock_qwen, mock_claude, brief_date="2026-06-23")
        )

    debate = tmp_db.execute("SELECT status FROM debates ORDER BY id DESC LIMIT 1").fetchone()
    assert debate["status"] == "failed"
