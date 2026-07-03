"""Tests for pathosphere/agent/predictions.py (v2 two-track model).

All tests use the tmp_db fixture (full schema, migrations applied).
"""

from __future__ import annotations

import sqlite3

import pytest

from pathosphere.agent.predictions import (
    add_prediction,
    create_thesis_prediction,
    get_calibration,
    get_prediction,
    get_prediction_domains,
    get_prediction_revisions,
    link_thesis_prediction_to_trade,
    list_predictions,
    resolve_prediction,
    revise_prediction,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _add(
    conn: sqlite3.Connection,
    description: str = "Test prediction",
    probability: float = 0.65,
    horizon_date: str = "2026-07-10",
    macro_area: str = "world",
    prediction_type: str = "geopolitical",
    domains: list[str] | None = None,
    origin_scope: str | None = "regionale",
    impact_scope: str | None = "globale",
    **kwargs,
) -> sqlite3.Row:
    return add_prediction(
        conn, description, probability, horizon_date,
        macro_area=macro_area, prediction_type=prediction_type,
        domains=domains or ["conflitto_armato"],
        origin_scope=origin_scope, impact_scope=impact_scope,
        **kwargs,
    )


def _add_economic(
    conn: sqlite3.Connection,
    description: str = "Economic prediction",
    probability: float = 0.6,
    horizon_date: str = "2026-08-01",
    **kwargs,
) -> sqlite3.Row:
    cur = conn.execute("INSERT INTO theses (title, causal_chain) VALUES ('T', 'c')")
    conn.commit()
    return add_prediction(
        conn, description, probability, horizon_date,
        macro_area="economic", prediction_type="economic",
        domains=["finanza"], thesis_id=cur.lastrowid,
        **kwargs,
    )


def _add_resolved(
    conn: sqlite3.Connection,
    description: str = "Resolved prediction",
    probability: float = 0.7,
    horizon_date: str = "2026-07-01",
    outcome_eventual: bool = True,
    resolved_date: str | None = None,
    **kwargs,
) -> sqlite3.Row:
    row = _add(conn, description, probability, horizon_date, **kwargs)
    return resolve_prediction(
        conn, row["id"], outcome_eventual, resolved_date or horizon_date
    )


# ── add_prediction ────────────────────────────────────────────────────────────

def test_add_prediction_returns_row(tmp_db):
    row = _add(tmp_db)
    assert row is not None
    assert row["id"] is not None


def test_add_prediction_fields(tmp_db):
    row = _add(tmp_db, description="Taiwan escalation", probability=0.65,
               horizon_date="2026-07-10")
    assert row["description"] == "Taiwan escalation"
    assert row["probability"] == pytest.approx(0.65)
    assert row["horizon_date"] == "2026-07-10"
    assert row["macro_area"] == "world"
    assert row["prediction_type"] == "geopolitical"
    assert row["origin_scope"] == "regionale"
    assert row["impact_scope"] == "globale"
    assert row["resolved"] == 0
    assert row["outcome_eventual"] is None
    assert row["outcome_on_time"] is None
    assert row["brier_score"] is None
    assert row["time_adjusted_score"] is None
    assert row["thesis_id"] is None


def test_add_prediction_with_thesis_id(tmp_db):
    cur = tmp_db.execute("INSERT INTO theses (title, causal_chain) VALUES ('T', 'c')")
    tmp_db.commit()
    row = _add(tmp_db, thesis_id=cur.lastrowid)
    assert row["thesis_id"] == cur.lastrowid


def test_add_prediction_probability_too_low(tmp_db):
    with pytest.raises(ValueError, match="0.0–1.0"):
        _add(tmp_db, probability=-0.01)


def test_add_prediction_probability_too_high(tmp_db):
    with pytest.raises(ValueError, match="0.0–1.0"):
        _add(tmp_db, probability=1.01)


def test_add_prediction_probability_bounds_ok(tmp_db):
    assert _add(tmp_db, probability=0.0)["probability"] == pytest.approx(0.0)
    assert _add(tmp_db, probability=1.0)["probability"] == pytest.approx(1.0)


def test_add_prediction_invalid_date(tmp_db):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _add(tmp_db, horizon_date="10-07-2026")


def test_add_prediction_invalid_date_nonsense(tmp_db):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _add(tmp_db, horizon_date="notadate")


def test_add_prediction_empty_description(tmp_db):
    with pytest.raises(ValueError, match="empty"):
        _add(tmp_db, description="")


def test_add_prediction_whitespace_description(tmp_db):
    with pytest.raises(ValueError, match="empty"):
        _add(tmp_db, description="   ")


def test_add_prediction_persisted(tmp_db):
    row = _add(tmp_db, description="Persist check", probability=0.5)
    fetched = get_prediction(tmp_db, row["id"])
    assert fetched is not None
    assert fetched["description"] == "Persist check"


# ── v2: macro_area / prediction_type coherence ────────────────────────────────

def test_add_invalid_macro_area(tmp_db):
    with pytest.raises(ValueError, match="macro_area"):
        _add(tmp_db, macro_area="cosmic")


def test_add_world_with_economic_type_rejected(tmp_db):
    with pytest.raises(ValueError, match="not valid for macro_area"):
        _add(tmp_db, macro_area="world", prediction_type="economic")


def test_add_economic_with_geopolitical_type_rejected(tmp_db):
    with pytest.raises(ValueError, match="not valid for macro_area"):
        _add(tmp_db, macro_area="economic", prediction_type="geopolitical")


@pytest.mark.parametrize("ptype", ["geopolitical", "political", "social"])
def test_add_world_types_accepted(tmp_db, ptype):
    row = _add(tmp_db, prediction_type=ptype)
    assert row["prediction_type"] == ptype


def test_add_world_requires_scopes(tmp_db):
    with pytest.raises(ValueError, match="origin_scope and impact_scope"):
        _add(tmp_db, origin_scope=None, impact_scope=None)


def test_add_world_rejects_trade_id(tmp_db):
    with pytest.raises(ValueError, match="trade_id"):
        _add(tmp_db, trade_id=1)


def test_add_economic_requires_thesis_id(tmp_db):
    with pytest.raises(ValueError, match="thesis_id"):
        add_prediction(tmp_db, "X", 0.5, "2026-08-01",
                       macro_area="economic", prediction_type="economic",
                       domains=["finanza"])


def test_add_economic_ok(tmp_db):
    row = _add_economic(tmp_db)
    assert row["macro_area"] == "economic"
    assert row["prediction_type"] == "economic"
    assert row["thesis_id"] is not None


def test_add_invalid_scope(tmp_db):
    with pytest.raises(ValueError, match="origin_scope"):
        _add(tmp_db, origin_scope="galattico")


# ── v2: domains ───────────────────────────────────────────────────────────────

def test_add_requires_at_least_one_domain(tmp_db):
    with pytest.raises(ValueError, match="at least one domain"):
        add_prediction(tmp_db, "X", 0.5, "2026-07-10",
                       macro_area="world", prediction_type="geopolitical",
                       domains=[], origin_scope="locale", impact_scope="locale")


def test_add_invalid_domain(tmp_db):
    with pytest.raises(ValueError, match="taxonomy"):
        _add(tmp_db, domains=["astrologia"])


def test_add_duplicate_domains_rejected(tmp_db):
    with pytest.raises(ValueError, match="duplicate"):
        _add(tmp_db, domains=["commercio", "commercio"])


def test_add_multiple_domains_first_is_primary(tmp_db):
    row = _add(tmp_db, domains=["conflitto_armato", "commercio", "tecnologia"])
    doms = get_prediction_domains(tmp_db, row["id"])
    assert len(doms) == 3
    primary = [d["domain"] for d in doms if d["is_primary"]]
    assert primary == ["conflitto_armato"]


def test_add_explicit_primary_domain(tmp_db):
    row = _add(tmp_db, domains=["conflitto_armato", "commercio"],
               primary_domain="commercio")
    doms = get_prediction_domains(tmp_db, row["id"])
    primary = [d["domain"] for d in doms if d["is_primary"]]
    assert primary == ["commercio"]


def test_add_primary_domain_not_in_domains_rejected(tmp_db):
    with pytest.raises(ValueError, match="primary_domain"):
        _add(tmp_db, domains=["commercio"], primary_domain="finanza")


# ── v2: time_horizon_class ────────────────────────────────────────────────────

def test_time_horizon_class_breve(tmp_db):
    row = _add(tmp_db, horizon_date="2026-07-10")  # ~7 days out
    assert row["time_horizon_class"] == "breve"


def test_time_horizon_class_medio(tmp_db):
    row = _add(tmp_db, horizon_date="2026-10-01")  # ~90 days out
    assert row["time_horizon_class"] == "medio"


def test_time_horizon_class_lungo(tmp_db):
    row = _add(tmp_db, horizon_date="2027-07-01")  # ~1 year out
    assert row["time_horizon_class"] == "lungo"


# ── revise_prediction ─────────────────────────────────────────────────────────

def test_revise_updates_probability(tmp_db):
    row = _add(tmp_db, probability=0.5)
    updated = revise_prediction(tmp_db, row["id"], 0.7)
    assert updated["probability"] == pytest.approx(0.7)


def test_revise_logs_history(tmp_db):
    row = _add(tmp_db, probability=0.5)
    revise_prediction(tmp_db, row["id"], 0.6, rationale="new intel")
    revise_prediction(tmp_db, row["id"], 0.8)
    revs = get_prediction_revisions(tmp_db, row["id"])
    assert [r["probability"] for r in revs] == [pytest.approx(0.6), pytest.approx(0.8)]
    assert revs[0]["rationale"] == "new intel"
    assert revs[1]["rationale"] is None


def test_revise_invalid_probability(tmp_db):
    row = _add(tmp_db)
    with pytest.raises(ValueError, match="0.0–1.0"):
        revise_prediction(tmp_db, row["id"], 1.5)


def test_revise_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        revise_prediction(tmp_db, 9999, 0.5)


def test_revise_resolved_rejected(tmp_db):
    row = _add_resolved(tmp_db)
    with pytest.raises(ValueError, match="already resolved"):
        revise_prediction(tmp_db, row["id"], 0.5)


# ── resolve_prediction ────────────────────────────────────────────────────────

def test_resolve_on_time(tmp_db):
    row = _add(tmp_db, probability=0.8, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-07-10")
    assert updated["resolved"] == 1
    assert updated["outcome_eventual"] == 1
    assert updated["outcome_on_time"] == 1
    assert updated["resolved_date"] == "2026-07-10"
    assert updated["brier_score"] == pytest.approx((0.8 - 1.0) ** 2)
    # on time → timing_factor=1 → score = 1 - brier
    assert updated["time_adjusted_score"] == pytest.approx(0.96)
    assert updated["resolved_at"] is not None


def test_resolve_never_happened(tmp_db):
    row = _add(tmp_db, probability=0.3, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], False, "2026-07-10")
    assert updated["outcome_eventual"] == 0
    assert updated["outcome_on_time"] == 0
    assert updated["brier_score"] == pytest.approx((0.3 - 0.0) ** 2)
    assert updated["time_adjusted_score"] == pytest.approx(0.0)


def test_resolve_early_slight_penalty(tmp_db):
    # 10 days early: timing_factor = 1 - 0.001*10 = 0.99
    row = _add(tmp_db, probability=0.8, horizon_date="2026-07-20")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-07-10")
    assert updated["outcome_on_time"] == 1  # early = still on time
    assert updated["time_adjusted_score"] == pytest.approx(0.96 * 0.99)


def test_resolve_late_penalty(tmp_db):
    # 100 days late: timing_factor = 0.9; late → not on time
    row = _add(tmp_db, probability=0.8, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-10-18")
    assert updated["outcome_eventual"] == 1
    assert updated["outcome_on_time"] == 0
    assert updated["time_adjusted_score"] == pytest.approx(0.96 * 0.9)


def test_resolve_extreme_delay_zero_score(tmp_db):
    # >1000 days late with alpha=0.001 → timing_factor floors at 0
    row = _add(tmp_db, probability=1.0, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2029-07-10")
    assert updated["time_adjusted_score"] == pytest.approx(0.0)


def test_resolve_legacy_outcome_mirrors_on_time(tmp_db):
    row = _add(tmp_db, probability=0.8, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-12-01")  # late
    assert updated["outcome"] == updated["outcome_on_time"] == 0


def test_resolve_brier_perfect_true(tmp_db):
    row = _add(tmp_db, probability=1.0, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-07-10")
    assert updated["brier_score"] == pytest.approx(0.0)
    assert updated["time_adjusted_score"] == pytest.approx(1.0)


def test_resolve_brier_worst_case(tmp_db):
    row = _add(tmp_db, probability=1.0, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], False, "2026-07-10")
    assert updated["brier_score"] == pytest.approx(1.0)


def test_resolve_invalid_date(tmp_db):
    row = _add(tmp_db)
    with pytest.raises(ValueError, match="resolved_date"):
        resolve_prediction(tmp_db, row["id"], True, "notadate")


def test_resolve_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        resolve_prediction(tmp_db, 9999, True, "2026-07-10")


def test_resolve_already_resolved(tmp_db):
    row = _add(tmp_db)
    resolve_prediction(tmp_db, row["id"], True, "2026-07-10")
    with pytest.raises(ValueError, match="already resolved"):
        resolve_prediction(tmp_db, row["id"], False, "2026-07-10")


# ── get_prediction / list_predictions ─────────────────────────────────────────

def test_get_prediction_not_found(tmp_db):
    assert get_prediction(tmp_db, 9999) is None


def test_list_predictions_empty(tmp_db):
    assert list_predictions(tmp_db) == []


def test_list_predictions_only_open(tmp_db):
    _add(tmp_db, description="Open")
    _add_resolved(tmp_db, description="Resolved", outcome_eventual=False)
    rows = list_predictions(tmp_db, only_open=True)
    assert len(rows) == 1
    assert rows[0]["description"] == "Open"


def test_list_predictions_only_resolved(tmp_db):
    _add(tmp_db, description="Open")
    _add_resolved(tmp_db, description="Done")
    rows = list_predictions(tmp_db, only_resolved=True)
    assert len(rows) == 1
    assert rows[0]["description"] == "Done"


def test_list_predictions_no_flags_returns_all(tmp_db):
    _add(tmp_db, description="Open")
    _add_resolved(tmp_db, description="Done")
    rows = list_predictions(tmp_db, only_open=False, only_resolved=False)
    assert len(rows) == 2


def test_list_predictions_order_by_horizon(tmp_db):
    _add(tmp_db, description="Later", horizon_date="2026-08-01")
    _add(tmp_db, description="Earlier", horizon_date="2026-07-05")
    rows = list_predictions(tmp_db)
    assert [r["description"] for r in rows] == ["Earlier", "Later"]


def test_list_filter_macro_area(tmp_db):
    _add(tmp_db, description="W")
    _add_economic(tmp_db, description="E")
    rows = list_predictions(tmp_db, macro_area="economic")
    assert [r["description"] for r in rows] == ["E"]


def test_list_filter_prediction_type(tmp_db):
    _add(tmp_db, description="Geo", prediction_type="geopolitical")
    _add(tmp_db, description="Pol", prediction_type="political")
    rows = list_predictions(tmp_db, prediction_type="political")
    assert [r["description"] for r in rows] == ["Pol"]


def test_list_filter_domain(tmp_db):
    _add(tmp_db, description="Conflict", domains=["conflitto_armato"])
    _add(tmp_db, description="Trade", domains=["commercio", "tecnologia"])
    rows = list_predictions(tmp_db, domain="tecnologia")
    assert [r["description"] for r in rows] == ["Trade"]


def test_list_filters_combined(tmp_db):
    _add(tmp_db, description="WGeo", domains=["commercio"])
    _add_economic(tmp_db, description="Eco")
    rows = list_predictions(tmp_db, only_open=True, macro_area="world",
                            domain="commercio")
    assert [r["description"] for r in rows] == ["WGeo"]


# ── thesis integration ────────────────────────────────────────────────────────

def _insert_thesis(conn, title="T", confidence=0.7, horizon_days=15,
                   instrument="TSM", direction="long") -> sqlite3.Row:
    cur = conn.execute(
        """INSERT INTO theses (title, causal_chain, confidence, horizon_days,
                               instrument, direction)
           VALUES (?, 'c', ?, ?, ?, ?)""",
        (title, confidence, horizon_days, instrument, direction),
    )
    conn.commit()
    return conn.execute("SELECT * FROM theses WHERE id = ?", (cur.lastrowid,)).fetchone()


def _insert_trade(conn) -> int:
    conn.execute(
        "INSERT INTO portfolios (name, portfolio_type) VALUES ('agent', 'agent')"
    )
    cur = conn.execute(
        """INSERT INTO trades (portfolio_id, ticker, direction, quantity,
                               price_open, opened_at)
           VALUES (1, 'TSM', 'buy', 1.0, 100.0, '2026-07-03')"""
    )
    conn.commit()
    return cur.lastrowid


def test_create_thesis_prediction(tmp_db):
    thesis = _insert_thesis(tmp_db)
    pred = create_thesis_prediction(tmp_db, thesis)
    assert pred["macro_area"] == "economic"
    assert pred["prediction_type"] == "economic"
    assert pred["thesis_id"] == thesis["id"]
    assert pred["probability"] == pytest.approx(0.7)
    assert "TSM long" in pred["description"]
    doms = get_prediction_domains(tmp_db, pred["id"])
    assert [d["domain"] for d in doms] == ["finanza"]


def test_create_thesis_prediction_null_confidence_defaults(tmp_db):
    thesis = _insert_thesis(tmp_db, confidence=None, horizon_days=None)
    pred = create_thesis_prediction(tmp_db, thesis)
    assert pred["probability"] == pytest.approx(0.5)


def test_create_thesis_prediction_clamps_out_of_range_confidence(tmp_db):
    thesis = _insert_thesis(tmp_db, confidence=65.0)  # LLM emitted 0-100 scale
    pred = create_thesis_prediction(tmp_db, thesis)
    assert pred["probability"] == pytest.approx(1.0)


def test_create_thesis_prediction_null_instrument(tmp_db):
    thesis = _insert_thesis(tmp_db, instrument=None, direction=None)
    pred = create_thesis_prediction(tmp_db, thesis)
    assert "None" not in pred["description"]


def test_link_thesis_prediction_to_trade(tmp_db):
    thesis = _insert_thesis(tmp_db)
    auto = create_thesis_prediction(tmp_db, thesis)
    trade_id = _insert_trade(tmp_db)
    linked_id = link_thesis_prediction_to_trade(tmp_db, thesis["id"], trade_id)
    assert linked_id == auto["id"]
    assert get_prediction(tmp_db, auto["id"])["trade_id"] == trade_id


def test_link_claims_only_oldest_unlinked(tmp_db):
    thesis = _insert_thesis(tmp_db)
    auto = create_thesis_prediction(tmp_db, thesis)
    manual = add_prediction(tmp_db, "manual", 0.4, "2026-09-01",
                            macro_area="economic", prediction_type="economic",
                            domains=["finanza"], thesis_id=thesis["id"])
    trade_id = _insert_trade(tmp_db)
    link_thesis_prediction_to_trade(tmp_db, thesis["id"], trade_id)
    assert get_prediction(tmp_db, auto["id"])["trade_id"] == trade_id
    assert get_prediction(tmp_db, manual["id"])["trade_id"] is None


def test_link_skips_resolved_predictions(tmp_db):
    thesis = _insert_thesis(tmp_db)
    auto = create_thesis_prediction(tmp_db, thesis)
    resolve_prediction(tmp_db, auto["id"], True, "2026-07-03")
    assert link_thesis_prediction_to_trade(tmp_db, thesis["id"], trade_id=1) is None
    assert get_prediction(tmp_db, auto["id"])["trade_id"] is None


def test_link_nothing_to_link_returns_none(tmp_db):
    assert link_thesis_prediction_to_trade(tmp_db, 9999, trade_id=1) is None


# ── get_calibration ───────────────────────────────────────────────────────────

def test_calibration_empty(tmp_db):
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 0
    assert cal["mean_brier_score"] is None
    assert cal["mean_time_adjusted_score"] is None
    assert len(cal["buckets"]) == 5
    assert cal["by_macro_area"] == {}
    assert cal["by_prediction_type"] == {}


def test_calibration_mean_brier(tmp_db):
    # prob=0.8 eventual=true on time → brier 0.04; prob=0.6 false → 0.36
    _add_resolved(tmp_db, probability=0.8, outcome_eventual=True)
    _add_resolved(tmp_db, probability=0.6, outcome_eventual=False)
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 2
    assert cal["mean_brier_score"] == pytest.approx(0.20)


def test_calibration_mean_time_adjusted(tmp_db):
    # on-time true: 1-0.04=0.96; never happened: 0.0 → mean 0.48
    _add_resolved(tmp_db, probability=0.8, outcome_eventual=True)
    _add_resolved(tmp_db, probability=0.6, outcome_eventual=False)
    cal = get_calibration(tmp_db)
    assert cal["mean_time_adjusted_score"] == pytest.approx(0.48)


def test_calibration_bucket_counts(tmp_db):
    _add_resolved(tmp_db, probability=0.1)   # 0-20%
    _add_resolved(tmp_db, probability=0.7)   # 60-80%
    _add_resolved(tmp_db, probability=0.75)  # 60-80%
    cal = get_calibration(tmp_db)
    counts = {b["label"]: b["count"] for b in cal["buckets"]}
    assert counts == {"0-20%": 1, "20-40%": 0, "40-60%": 0,
                      "60-80%": 2, "80-100%": 0}


def test_calibration_bucket_accuracy(tmp_db):
    _add_resolved(tmp_db, probability=0.7, outcome_eventual=True)
    _add_resolved(tmp_db, probability=0.65, outcome_eventual=False)
    cal = get_calibration(tmp_db)
    bucket = next(b for b in cal["buckets"] if b["label"] == "60-80%")
    assert bucket["count"] == 2
    assert bucket["accuracy"] == pytest.approx(0.5)


def test_calibration_bucket_accuracy_uses_eventual_not_on_time(tmp_db):
    # p=0.9 happens late: eventual hit, on_time miss. Accuracy must count the
    # hit — same event the brier is computed on.
    _add_resolved(tmp_db, probability=0.9, horizon_date="2026-07-10",
                  resolved_date="2026-07-20")
    cal = get_calibration(tmp_db)
    bucket = next(b for b in cal["buckets"] if b["label"] == "80-100%")
    assert bucket["accuracy"] == pytest.approx(1.0)


def test_calibration_bucket_accuracy_legacy_fallback(tmp_db):
    # pre-v2 row: outcome_eventual NULL, legacy outcome=1 → counts as hit
    tmp_db.execute(
        """INSERT INTO predictions (description, probability, horizon_date,
                                    resolved, outcome, brier_score)
           VALUES ('legacy', 0.9, '2026-01-01', 1, 1, 0.01)"""
    )
    tmp_db.commit()
    cal = get_calibration(tmp_db)
    bucket = next(b for b in cal["buckets"] if b["label"] == "80-100%")
    assert bucket["accuracy"] == pytest.approx(1.0)


def test_calibration_bucket_has_time_adjusted_score(tmp_db):
    _add_resolved(tmp_db, probability=0.8)  # tas 0.96
    cal = get_calibration(tmp_db)
    bucket = next(b for b in cal["buckets"] if b["label"] == "80-100%")
    assert bucket["mean_time_adjusted_score"] == pytest.approx(0.96)


def test_resolve_explicit_alpha_overrides_settings(tmp_db):
    # alpha=0.01, 10 days late → timing_factor 0.9
    row = _add(tmp_db, probability=1.0, horizon_date="2026-07-10")
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-07-20", alpha=0.01)
    assert updated["time_adjusted_score"] == pytest.approx(0.9)


def test_calibration_probability_one_in_last_bucket(tmp_db):
    _add_resolved(tmp_db, probability=1.0)
    cal = get_calibration(tmp_db)
    assert cal["buckets"][-1]["count"] == 1


def test_calibration_open_predictions_excluded(tmp_db):
    _add(tmp_db, probability=0.9)
    _add_resolved(tmp_db, probability=0.5)
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 1


def test_calibration_breakdown_macro_area(tmp_db):
    _add_resolved(tmp_db, probability=0.8)  # world, on time → tas 0.96
    eco = _add_economic(tmp_db, probability=0.6, horizon_date="2026-08-01")
    resolve_prediction(tmp_db, eco["id"], False, "2026-08-01")  # tas 0.0
    cal = get_calibration(tmp_db)
    assert cal["by_macro_area"]["world"]["count"] == 1
    assert cal["by_macro_area"]["world"]["mean_time_adjusted_score"] == pytest.approx(0.96)
    assert cal["by_macro_area"]["economic"]["count"] == 1
    assert cal["by_macro_area"]["economic"]["mean_time_adjusted_score"] == pytest.approx(0.0)


def test_calibration_breakdown_prediction_type(tmp_db):
    _add_resolved(tmp_db, prediction_type="geopolitical")
    _add_resolved(tmp_db, prediction_type="political")
    _add_resolved(tmp_db, prediction_type="political")
    cal = get_calibration(tmp_db)
    assert cal["by_prediction_type"]["geopolitical"]["count"] == 1
    assert cal["by_prediction_type"]["political"]["count"] == 2
    assert "social" not in cal["by_prediction_type"]


def test_calibration_backward_compat_pre_v2_rows(tmp_db):
    """Pre-v2 rows (brier only, no time_adjusted_score) must not break calibration."""
    tmp_db.execute(
        """
        INSERT INTO predictions (description, probability, horizon_date,
                                 resolved, outcome, brier_score)
        VALUES ('legacy', 0.7, '2026-01-01', 1, 1, 0.09)
        """
    )
    tmp_db.commit()
    _add_resolved(tmp_db, probability=0.8)  # v2 row: brier 0.04, tas 0.96
    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 2
    # brier over both rows, tas over the v2 row only
    assert cal["mean_brier_score"] == pytest.approx((0.09 + 0.04) / 2)
    assert cal["mean_time_adjusted_score"] == pytest.approx(0.96)


# ── integration ───────────────────────────────────────────────────────────────

def test_full_lifecycle(tmp_db):
    """Add → revise → list open → resolve late → list resolved → calibration."""
    row = _add(tmp_db, description="Hormuz closure within 30d", probability=0.25,
               horizon_date="2026-07-31", domains=["infrastruttura", "commercio"])

    revise_prediction(tmp_db, row["id"], 0.4, rationale="tanker traffic dropping")
    open_rows = list_predictions(tmp_db, only_open=True)
    assert any(r["id"] == row["id"] for r in open_rows)

    # happens 20 days late: brier vs eventual=1, timing penalty 0.98
    updated = resolve_prediction(tmp_db, row["id"], True, "2026-08-20")
    assert updated["outcome_eventual"] == 1
    assert updated["outcome_on_time"] == 0
    assert updated["brier_score"] == pytest.approx((0.4 - 1.0) ** 2)
    assert updated["time_adjusted_score"] == pytest.approx((1 - 0.36) * 0.98)

    res_rows = list_predictions(tmp_db, only_resolved=True)
    assert any(r["id"] == row["id"] for r in res_rows)

    cal = get_calibration(tmp_db)
    assert cal["total_resolved"] == 1
    assert cal["by_macro_area"]["world"]["count"] == 1
