"""Tests for pathosphere/agent/scenarios.py (conflict scenario forecasting).

All LLM calls are mocked (AsyncMock). DB tests use the tmp_db fixture.
GDELT rows are seeded straight into gdelt_events (the module only reads the
aggregates, not the ingest path).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from pathosphere.agent.scenarios import (
    MIN_WINDOW_EVENTS,
    _match_indicators,
    _normalize_probabilities,
    build_dossier,
    compute_hotspots,
    country_label,
    generate_scenarios,
    get_scenario_set,
    get_scenarios,
    list_scenario_sets,
    resolve_scenario_set,
    review_scenarios,
)

_AS_OF = date(2026, 7, 15)
_gid = itertools.count(500_000_000)


# ── seed helpers ──────────────────────────────────────────────────────────────

def _seed_gdelt(
    conn: sqlite3.Connection,
    cc: str,
    start: date,
    days: int,
    per_day: int,
    quad: int,
    goldstein: float,
) -> None:
    rows = [
        (next(_gid), (start + timedelta(days=d)).isoformat(), quad, goldstein, cc)
        for d in range(days)
        for _ in range(per_day)
    ]
    conn.executemany(
        """INSERT INTO gdelt_events
           (global_event_id, date_added, quad_class, goldstein, action_geo_country)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def _seed_escalating_country(conn: sqlite3.Connection, cc: str = "IS") -> None:
    """Quiet 90d baseline (verbal, mild Goldstein) then a hot 14d window
    (material conflict, deep negative Goldstein, 4x volume)."""
    baseline_start = _AS_OF - timedelta(days=104)
    window_start = _AS_OF - timedelta(days=14)
    _seed_gdelt(conn, cc, baseline_start, 90, per_day=2, quad=3, goldstein=-2.0)
    _seed_gdelt(conn, cc, window_start, 14, per_day=8, quad=4, goldstein=-8.0)


_SCENARIO_LLM_RESPONSE = json.dumps({
    "summary": "Escalation dynamics point to a contested trajectory.",
    "key_assumptions": ["External patrons stay out", "No leadership change"],
    "scenarios": [
        {
            "label": "A",
            "title": "Negotiated de-escalation",
            "description": "Back-channel talks freeze the confrontation.",
            "probability": 0.2,
            "ach_ratings": {"E1": "I"},
            "indicators": [
                {"label": "Ceasefire talks", "indicator_query": "ceasefire talks mediation"},
            ],
            "invalidation": "New cross-border strike within 14 days",
            "market_implications": "Oil risk premium unwinds.",
            "origin_scope": "nazionale",
            "impact_scope": "regionale",
        },
        {
            "label": "B",
            "title": "Frozen status quo",
            "description": "Attrition continues at current intensity.",
            "probability": 0.5,
            "ach_ratings": {"E1": "C"},
            "indicators": [
                {"label": "Material conflict plateau", "indicator_query": "artillery frontline static"},
            ],
            "invalidation": "Sustained drop in material-conflict events",
            "market_implications": "Defence names hold their bid.",
            "origin_scope": "regionale",
            "impact_scope": "regionale",
        },
        {
            "label": "C",
            "title": "Major escalation",
            "description": "Direct great-power involvement widens the war.",
            "probability": 0.3,
            "ach_ratings": {"E1": "CC"},
            "indicators": [
                {"label": "Mobilization", "indicator_query": "mobilization reserves draft"},
                {"label": "Chokepoint disruption", "indicator_query": "strait shipping attack"},
            ],
            "invalidation": "Patron publicly rules out intervention",
            "market_implications": "Energy spike, broad risk-off.",
            "origin_scope": "not-a-scope",
            "impact_scope": "globale",
        },
    ],
})

_REVIEW_LLM_RESPONSE = json.dumps({
    "revisions": [
        {"label": "A", "probability": 0.10, "rationale": "Talks collapsed."},
        {"label": "B", "probability": 0.50, "rationale": "unchanged"},
        {"label": "C", "probability": 0.40, "rationale": "Mobilization indicator fired."},
    ],
})


def _mock_llm(response: str) -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(return_value=response)
    return client


def _generate(conn, client, **kw):
    return asyncio.run(generate_scenarios(conn, client, as_of=_AS_OF, **kw))


# ── hotspot triage ────────────────────────────────────────────────────────────

def test_compute_hotspots_ranks_escalating_country(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    # calm control country: flat volume, no material shift
    _seed_gdelt(tmp_db, "FR", _AS_OF - timedelta(days=104), 104, per_day=3,
                quad=3, goldstein=-2.0)

    hotspots = compute_hotspots(tmp_db, as_of=_AS_OF)
    assert [h.country for h in hotspots][0] == "IS"
    hot = hotspots[0]
    assert hot.country_name == "Israel"
    assert hot.score > 0.5
    assert hot.material_z is not None and hot.material_z > 2
    assert hot.material_share_window > hot.material_share_baseline
    assert hot.goldstein_window < hot.goldstein_baseline
    calm = next(h for h in hotspots if h.country == "FR")
    assert calm.score < 0.1


def test_compute_hotspots_skips_thin_window(tmp_db):
    # plenty of baseline but almost nothing in the window
    _seed_gdelt(tmp_db, "GM", _AS_OF - timedelta(days=104), 90, per_day=3,
                quad=3, goldstein=-2.0)
    _seed_gdelt(tmp_db, "GM", _AS_OF - timedelta(days=14), 14, per_day=1,
                quad=4, goldstein=-8.0)
    assert 14 < MIN_WINDOW_EVENTS
    assert compute_hotspots(tmp_db, as_of=_AS_OF) == []


def test_compute_hotspots_no_lookahead(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    # rows dated on/after as_of must be invisible
    _seed_gdelt(tmp_db, "UP", _AS_OF, 30, per_day=50, quad=4, goldstein=-10.0)
    hotspots = compute_hotspots(tmp_db, as_of=_AS_OF)
    assert all(h.country != "UP" for h in hotspots)


def test_country_label_fallback():
    assert country_label("UP") == "Ukraine"
    assert country_label("ZZ") == "ZZ"


# ── dossier ───────────────────────────────────────────────────────────────────

def test_build_dossier_collects_evidence(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    recent = (_AS_OF - timedelta(days=2)).isoformat()
    tmp_db.execute(
        """INSERT INTO events (title, summary, first_seen, last_seen, event_type,
                               origin, severity, location_name)
           VALUES ('IS material conflict anomaly', 'z=4 escalation', ?, ?,
                   'gdelt_anomaly', 'gdelt', 4, 'IS')""",
        (recent, recent),
    )
    cur = tmp_db.execute(
        """INSERT INTO events (title, first_seen, last_seen, origin, location_name)
           VALUES ('Israel strikes deepen crisis', ?, ?, 'rss', 'Israel')""",
        (recent, recent),
    )
    rss_id = cur.lastrowid
    tmp_db.execute(
        "INSERT INTO raw_documents (url, title) VALUES ('http://x.test/1', 'doc')"
    )
    doc_id = tmp_db.execute("SELECT id FROM raw_documents").fetchone()["id"]
    tmp_db.execute(
        "INSERT INTO event_documents (event_id, document_id) VALUES (?, ?)",
        (rss_id, doc_id),
    )
    tmp_db.execute(
        """INSERT INTO narrative_divergences (event_id, block_a, block_b,
                                              divergence_score, summary)
           VALUES (?, 'western', 'arab', 0.8, 'Framing gap on casualties')""",
        (rss_id,),
    )
    tmp_db.execute(
        """INSERT INTO events (title, first_seen, last_seen, origin, severity,
                               location_name, event_type)
           VALUES ('UCDP: state-based conflict', '2024-01-01', '2024-01-01',
                   'ucdp', 4, 'Israel', 'conflict')""",
    )
    tmp_db.commit()

    hotspot = compute_hotspots(tmp_db, as_of=_AS_OF)[0]
    dossier = build_dossier(tmp_db, hotspot, as_of=_AS_OF)

    sources = {e["source"] for e in dossier["evidence"]}
    assert {"gdelt_metrics", "gdelt_anomaly", "rss_event",
            "narrative_divergence", "ucdp_history"} <= sources
    ids = [e["id"] for e in dossier["evidence"]]
    assert ids == [f"E{i + 1}" for i in range(len(ids))]
    assert dossier["as_of"] == _AS_OF.isoformat()
    assert dossier["metrics"]["score"] == hotspot.score


# ── probability normalization ─────────────────────────────────────────────────

def test_normalize_probabilities_renormalizes_and_floors():
    scenarios = [{"probability": 0.5}, {"probability": 0.5}, {"probability": 0.5}]
    _normalize_probabilities(scenarios)
    assert sum(s["probability"] for s in scenarios) == pytest.approx(1.0, abs=0.02)


def test_normalize_probabilities_garbage_degrades_to_uniform():
    scenarios = [{"probability": "high"}, {"probability": None}, {}]
    _normalize_probabilities(scenarios)
    assert all(s["probability"] == pytest.approx(1 / 3, abs=0.01) for s in scenarios)


# ── generation pipeline ───────────────────────────────────────────────────────

def test_generate_scenarios_persists_full_chain(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    client = _mock_llm(_SCENARIO_LLM_RESPONSE)

    result = _generate(tmp_db, client, max_hotspots=1)

    assert result.sets_created == 1
    assert result.scenarios_created == 3
    assert result.predictions_created == 3
    assert result.watchlist_created == 4

    set_row = get_scenario_set(tmp_db, result.set_ids[0])
    assert set_row["country"] == "IS"
    assert set_row["status"] == "active"
    assert set_row["horizon_date"] == (_AS_OF + timedelta(days=90)).isoformat()
    assert json.loads(set_row["key_assumptions"]) == [
        "External patrons stay out", "No leadership change",
    ]
    assert json.loads(set_row["dossier_json"])["country"] == "IS"

    scenarios = get_scenarios(tmp_db, set_row["id"])
    assert [s["label"] for s in scenarios] == ["A", "B", "C"]
    assert sum(s["probability"] for s in scenarios) == pytest.approx(1.0, abs=0.05)

    for s in scenarios:
        pred = tmp_db.execute(
            "SELECT * FROM predictions WHERE id = ?", (s["prediction_id"],)
        ).fetchone()
        assert pred["macro_area"] == "world"
        assert pred["prediction_type"] == "geopolitical"
        assert pred["probability"] == pytest.approx(s["probability"])
        domains = {
            r["domain"] for r in tmp_db.execute(
                "SELECT domain FROM prediction_domains WHERE prediction_id = ?",
                (pred["id"],),
            )
        }
        assert domains == {"conflitto_armato", "tensione_militare"}

    # invalid origin_scope on C degraded to the default, not a crash
    pred_c = tmp_db.execute(
        "SELECT p.origin_scope, p.impact_scope FROM predictions p "
        "JOIN scenarios s ON s.prediction_id = p.id WHERE s.label = 'C'"
    ).fetchone()
    assert pred_c["origin_scope"] == "regionale"
    assert pred_c["impact_scope"] == "globale"

    items = tmp_db.execute(
        "SELECT scenario_id, thesis_id FROM watchlist_items WHERE scenario_id IS NOT NULL"
    ).fetchall()
    assert len(items) == 4
    assert all(i["thesis_id"] is None for i in items)


def test_generate_scenarios_skips_country_with_active_set(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    client = _mock_llm(_SCENARIO_LLM_RESPONSE)
    first = _generate(tmp_db, client, max_hotspots=1)
    assert first.sets_created == 1

    second = _generate(tmp_db, client, max_hotspots=1)
    assert second.sets_created == 0
    assert len(second.skipped) == 1
    assert "active set" in second.skipped[0]


def test_generate_scenarios_bad_llm_json_is_skip_not_crash(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    client = _mock_llm("the situation is too fluid to commit to scenarios")

    result = _generate(tmp_db, client, max_hotspots=1)
    assert result.sets_created == 0
    assert len(result.skipped) == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM scenario_sets").fetchone()[0] == 0


def test_generate_scenarios_unknown_country_raises(tmp_db):
    _seed_escalating_country(tmp_db, "IS")
    with pytest.raises(ValueError, match="No usable GDELT metrics"):
        _generate(tmp_db, _mock_llm(_SCENARIO_LLM_RESPONSE), country="ZZ")


# ── review loop ───────────────────────────────────────────────────────────────

def _make_active_set(tmp_db) -> int:
    _seed_escalating_country(tmp_db, "IS")
    result = _generate(tmp_db, _mock_llm(_SCENARIO_LLM_RESPONSE), max_hotspots=1)
    return result.set_ids[0]


def test_review_scenarios_revises_probabilities_and_predictions(tmp_db):
    set_id = _make_active_set(tmp_db)
    client = _mock_llm(_REVIEW_LLM_RESPONSE)

    result = asyncio.run(review_scenarios(tmp_db, client, set_id=set_id, as_of=_AS_OF))

    assert result.sets_reviewed == 1
    assert result.probabilities_revised >= 2  # A and C moved, B unchanged
    scenarios = {s["label"]: s for s in get_scenarios(tmp_db, set_id)}
    assert scenarios["A"]["probability"] == pytest.approx(0.10, abs=0.02)
    assert scenarios["C"]["probability"] == pytest.approx(0.40, abs=0.02)

    revisions = tmp_db.execute(
        "SELECT * FROM prediction_revisions WHERE prediction_id = ?",
        (scenarios["C"]["prediction_id"],),
    ).fetchall()
    assert len(revisions) == 1
    assert "Mobilization" in revisions[0]["rationale"]
    pred = tmp_db.execute(
        "SELECT probability FROM predictions WHERE id = ?",
        (scenarios["C"]["prediction_id"],),
    ).fetchone()
    assert pred["probability"] == pytest.approx(0.40, abs=0.02)

    assert get_scenario_set(tmp_db, set_id)["last_reviewed_at"] is not None


def test_review_scenarios_triggers_indicators(tmp_db):
    set_id = _make_active_set(tmp_db)
    tmp_db.execute(
        """INSERT INTO events (title, first_seen, last_seen, origin)
           VALUES ('Full mobilization of reserves announced', '2099-01-01',
                   '2099-01-01', 'rss')""",
    )
    tmp_db.commit()

    triggered = _match_indicators(tmp_db, set_id, "2000-01-01")
    assert len(triggered) == 1
    assert triggered[0]["scenario_label"] == "C"

    row = tmp_db.execute(
        "SELECT status, triggered_at FROM watchlist_items WHERE id = ?",
        (triggered[0]["watchlist_id"],),
    ).fetchone()
    assert row["status"] == "triggered"
    assert row["triggered_at"] is not None

    # already-triggered items must not fire again
    assert _match_indicators(tmp_db, set_id, "2000-01-01") == []


def test_review_scenarios_bad_json_leaves_probabilities(tmp_db):
    set_id = _make_active_set(tmp_db)
    before = {s["label"]: s["probability"] for s in get_scenarios(tmp_db, set_id)}

    result = asyncio.run(review_scenarios(
        tmp_db, _mock_llm("no json here"), set_id=set_id, as_of=_AS_OF,
    ))
    assert result.probabilities_revised == 0
    after = {s["label"]: s["probability"] for s in get_scenarios(tmp_db, set_id)}
    assert after == before


def test_review_scenarios_overdue_sets_flagged_never_revised(tmp_db):
    set_id = _make_active_set(tmp_db)
    tmp_db.execute(
        "UPDATE scenario_sets SET horizon_date = '2026-01-01' WHERE id = ?", (set_id,)
    )
    tmp_db.commit()
    before = {s["label"]: s["probability"] for s in get_scenarios(tmp_db, set_id)}

    client = _mock_llm(_REVIEW_LLM_RESPONSE)
    result = asyncio.run(review_scenarios(tmp_db, client, set_id=set_id, as_of=_AS_OF))

    assert result.overdue_set_ids == [set_id]
    # post-horizon: no LLM call, no probability changes (would poison Brier)
    assert result.sets_reviewed == 0
    assert client.complete.await_count == 0
    after = {s["label"]: s["probability"] for s in get_scenarios(tmp_db, set_id)}
    assert after == before


# ── resolution ────────────────────────────────────────────────────────────────

def test_resolve_scenario_set_scores_all_predictions(tmp_db):
    set_id = _make_active_set(tmp_db)

    outcome = resolve_scenario_set(tmp_db, set_id, "C", resolved_date="2026-09-30")

    assert outcome["predictions_resolved"] == 3
    set_row = get_scenario_set(tmp_db, set_id)
    assert set_row["status"] == "resolved"
    assert set_row["resolved_at"] is not None

    scenarios = {s["label"]: s for s in get_scenarios(tmp_db, set_id)}
    assert scenarios["C"]["is_outcome"] == 1
    assert scenarios["A"]["is_outcome"] == 0

    for label, expected in (("C", 1), ("A", 0), ("B", 0)):
        pred = tmp_db.execute(
            "SELECT * FROM predictions WHERE id = ?",
            (scenarios[label]["prediction_id"],),
        ).fetchone()
        assert pred["resolved"] == 1
        assert pred["outcome_eventual"] == expected
        assert pred["brier_score"] is not None

    watch = tmp_db.execute(
        """SELECT status FROM watchlist_items
           WHERE scenario_id IN (SELECT id FROM scenarios WHERE set_id = ?)""",
        (set_id,),
    ).fetchall()
    assert all(w["status"] == "expired" for w in watch)


def test_resolve_scenario_set_validates_input(tmp_db):
    set_id = _make_active_set(tmp_db)
    with pytest.raises(ValueError, match="not in set"):
        resolve_scenario_set(tmp_db, set_id, "Z")
    resolve_scenario_set(tmp_db, set_id, "A")
    with pytest.raises(ValueError, match="already resolved"):
        resolve_scenario_set(tmp_db, set_id, "A")
    with pytest.raises(ValueError, match="not found"):
        resolve_scenario_set(tmp_db, 9999, "A")


# ── brief wiring ──────────────────────────────────────────────────────────────

def test_brief_prompt_includes_active_scenarios(tmp_db):
    from pathosphere.agent.brief import _build_prompt, _query_active_scenarios

    set_id = _make_active_set(tmp_db)
    active = _query_active_scenarios(tmp_db)
    assert len(active) == 1
    assert active[0]["id"] == set_id
    assert len(active[0]["scenarios"]) == 3

    messages = _build_prompt([], [], [], [], "2026-07-15", active_scenarios=active)
    prompt = messages[1]["content"]
    assert "ACTIVE CONFLICT SCENARIOS" in prompt
    assert "Israel" in prompt
    assert "Major escalation" in prompt


def test_scenario_sets_listing_filters_status(tmp_db):
    set_id = _make_active_set(tmp_db)
    assert [s["id"] for s in list_scenario_sets(tmp_db, status="active")] == [set_id]
    resolve_scenario_set(tmp_db, set_id, "B")
    assert list_scenario_sets(tmp_db, status="active") == []
    assert [s["id"] for s in list_scenario_sets(tmp_db)] == [set_id]
