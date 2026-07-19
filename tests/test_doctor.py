"""
Tests for pathosphere/doctor.py (`pathos doctor`).

Everything is mocked: no real PATH lookups, no sockets, no yfinance.
The autouse `hermetic` fixture pins claude-on-PATH and Ollama-up-with-model
so each test only perturbs the axis it exercises.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import pathosphere.doctor as doctor_mod
from pathosphere.config import Settings
from pathosphere.doctor import FAIL, OK, SKIP, WARN, has_failures, run_doctor


def _iso(hours_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago)
    return dt.isoformat(sep=" ", timespec="seconds")


class _FakeResp:
    def __init__(self, models: list[str]):
        self._models = models

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"models": [{"name": m} for m in self._models]}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.setattr(
        "pathosphere.doctor.shutil.which", lambda _cmd: "/fake/bin/claude"
    )
    monkeypatch.setattr(
        "pathosphere.doctor.httpx.get",
        lambda _url, timeout: _FakeResp(["qwen3:4b"]),
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_path / "missing.db")


def _by_name(results) -> dict:
    return {(r.section, r.name): r for r in results}


# ── smoke ────────────────────────────────────────────────────────────────────

def test_smoke_no_failures_on_fresh_db(tmp_db, settings):
    results = run_doctor(tmp_db, settings)
    assert results
    assert not has_failures(results)


def test_sections_are_grouped_in_order(tmp_db, settings):
    sections = [r.section for r in run_doctor(tmp_db, settings)]
    # each section appears as one contiguous block (CLI prints headers on change)
    seen = []
    for s in sections:
        if not seen or seen[-1] != s:
            seen.append(s)
    assert len(seen) == len(set(seen))


# ── prerequisites ────────────────────────────────────────────────────────────

def test_claude_missing_fails_with_claude_backend(tmp_db, settings, monkeypatch):
    monkeypatch.setattr("pathosphere.doctor.shutil.which", lambda _cmd: None)
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "claude CLI")]
    assert res.status == FAIL
    assert "CP-001" in res.detail


def test_claude_missing_warns_with_local_backend(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr("pathosphere.doctor.shutil.which", lambda _cmd: None)
    settings = Settings(
        _env_file=None, db_path=tmp_path / "x.db", reasoning_model="qwen-local"
    )
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "claude CLI")]
    assert res.status == WARN


def test_claude_present_ok(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "claude CLI")]
    assert res.status == OK
    assert "/fake/bin/claude" in res.detail


def test_ollama_unreachable_warns(tmp_db, settings, monkeypatch):
    def _raise(_url, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("pathosphere.doctor.httpx.get", _raise)
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "ollama")]
    assert res.status == WARN
    assert "unreachable" in res.detail


def test_ollama_model_not_pulled_warns(tmp_db, settings, monkeypatch):
    monkeypatch.setattr(
        "pathosphere.doctor.httpx.get",
        lambda _url, timeout: _FakeResp(["llama3:8b"]),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "ollama")]
    assert res.status == WARN
    assert "ollama pull" in res.detail


def test_ollama_model_present_ok(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("prerequisites", "ollama")]
    assert res.status == OK


# ── config ───────────────────────────────────────────────────────────────────

def test_missing_optional_keys_warn(tmp_db, settings):
    results = _by_name(run_doctor(tmp_db, settings))
    assert results[("config", "firms_map_key")].status == WARN
    if hasattr(settings, "reliefweb_appname"):  # field lands with PR #15
        assert results[("config", "reliefweb_appname")].status == WARN


def test_present_key_reports_set_and_never_leaks_value(tmp_db, tmp_path):
    settings = Settings(
        _env_file=None, db_path=tmp_path / "x.db", firms_map_key="SECRETVALUE123"
    )
    results = run_doctor(tmp_db, settings)
    res = _by_name(results)[("config", "firms_map_key")]
    assert res.status == OK
    assert res.detail == "set"
    assert all("SECRETVALUE123" not in r.detail for r in results)


def test_invalid_reasoning_model_fails(tmp_db, tmp_path):
    settings = Settings(
        _env_file=None, db_path=tmp_path / "x.db", reasoning_model="gpt-5"
    )
    res = _by_name(run_doctor(tmp_db, settings))[("config", "reasoning_model")]
    assert res.status == FAIL
    assert has_failures(run_doctor(tmp_db, settings))


# ── freshness ────────────────────────────────────────────────────────────────

def test_fresh_rss_ok(tmp_db, settings):
    tmp_db.execute(
        "INSERT INTO raw_documents (origin, url, fetched_at) VALUES ('rss', 'u1', ?)",
        (_iso(1),),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "rss")]
    assert res.status == OK


def test_stale_rss_warns_with_hint(tmp_db, settings):
    tmp_db.execute(
        "INSERT INTO raw_documents (origin, url, fetched_at) VALUES ('rss', 'u1', ?)",
        (_iso(100),),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "rss")]
    assert res.status == WARN
    assert "pathos ingest rss" in res.detail


def test_never_ingested_warns(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "ioda")]
    assert res.status == WARN
    assert "no data yet" in res.detail


def test_firms_skipped_without_key(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "firms")]
    assert res.status == SKIP


def test_firms_checked_with_key(tmp_db, tmp_path):
    settings = Settings(_env_file=None, db_path=tmp_path / "x.db", firms_map_key="k")
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "firms")]
    assert res.status == WARN  # key set, but no data yet


def test_tz_aware_timestamp_parsed(tmp_db, settings):
    aware = datetime.now(timezone.utc).isoformat()  # includes +00:00
    tmp_db.execute(
        "INSERT INTO raw_documents (origin, url, fetched_at) VALUES ('rss', 'u1', ?)",
        (aware,),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("freshness", "rss")]
    assert res.status == OK


# ── backlog ──────────────────────────────────────────────────────────────────

def test_backlog_counts_pending_embeddings(tmp_db, settings):
    for i in range(3):
        tmp_db.execute(
            "INSERT INTO raw_documents (origin, url, embedded) VALUES ('rss', ?, 0)",
            (f"u{i}",),
        )
    res = _by_name(run_doctor(tmp_db, settings))[("backlog", "embedding")]
    assert res.status == OK
    assert res.detail.startswith("3 pending")


def test_backlog_warns_above_threshold(tmp_db, settings, monkeypatch):
    monkeypatch.setitem(doctor_mod.BACKLOG_WARN_AT, "embedding", 2)
    for i in range(3):
        tmp_db.execute(
            "INSERT INTO raw_documents (origin, url, embedded) VALUES ('rss', ?, 0)",
            (f"u{i}",),
        )
    res = _by_name(run_doctor(tmp_db, settings))[("backlog", "embedding")]
    assert res.status == WARN
    assert "pathos embed" in res.detail


def test_backlog_excludes_non_prose_origins(tmp_db, settings):
    tmp_db.execute(
        "INSERT INTO raw_documents (origin, url, embedded) VALUES ('gdelt', 'u1', 0)"
    )
    res = _by_name(run_doctor(tmp_db, settings))[("backlog", "embedding")]
    assert res.detail.startswith("0 pending")


def test_geoloc_backlog_skips_on_pre_migration_db(tmp_db, settings):
    # a pre-CP-022 DB has no events.geoloc_checked: the check must degrade
    # to SKIP, not crash (current schema ships the column, so strip it)
    tmp_db.execute("DROP INDEX idx_events_geoloc_checked")
    tmp_db.execute("ALTER TABLE events DROP COLUMN geoloc_checked")
    res = _by_name(run_doctor(tmp_db, settings))[("backlog", "rss geolocation")]
    assert res.status == SKIP


def test_geoloc_backlog_counts_when_column_exists(tmp_db, settings):
    tmp_db.execute(
        "INSERT INTO events (title, first_seen, last_seen, origin) "
        "VALUES ('e', '2026-01-01', '2026-01-01', 'rss')"
    )
    res = _by_name(run_doctor(tmp_db, settings))[("backlog", "rss geolocation")]
    assert res.status == OK
    assert res.detail.startswith("1 pending")


# ── agent state ──────────────────────────────────────────────────────────────

def test_portfolios_warn_when_not_initialized(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "portfolios")]
    assert res.status == WARN
    assert "portfolio init" in res.detail


def test_portfolios_ok_when_initialized(tmp_db, settings):
    for name in ("agent", "random", "benchmark"):
        tmp_db.execute(
            "INSERT INTO portfolios (name, portfolio_type) VALUES (?, ?)",
            (name, name),
        )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "portfolios")]
    assert res.status == OK


def test_pending_theses_warn(tmp_db, settings):
    tmp_db.execute(
        "INSERT INTO theses (title, causal_chain, status) VALUES ('t', 'c', 'pending')"
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "theses")]
    assert res.status == WARN
    assert "1 pending" in res.detail


def _insert_trade(conn: sqlite3.Connection, opened_hours_ago: float,
                  horizon_days: int) -> None:
    conn.execute(
        "INSERT INTO portfolios (name, portfolio_type) VALUES ('agent', 'agent')"
    )
    conn.execute(
        "INSERT INTO theses (title, causal_chain, status, horizon_days) "
        "VALUES ('t', 'c', 'approved', ?)",
        (horizon_days,),
    )
    conn.execute(
        "INSERT INTO trades (portfolio_id, thesis_id, ticker, direction, "
        "quantity, price_open, opened_at) VALUES (1, 1, 'SPY', 'buy', 1, 100, ?)",
        (_iso(opened_hours_ago),),
    )


def test_open_trade_past_horizon_warns(tmp_db, settings):
    _insert_trade(tmp_db, opened_hours_ago=10 * 24, horizon_days=5)
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "open trades")]
    assert res.status == WARN
    assert "1 of 1" in res.detail


def test_open_trade_within_horizon_ok(tmp_db, settings):
    _insert_trade(tmp_db, opened_hours_ago=1, horizon_days=30)
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "open trades")]
    assert res.status == OK


def test_overdue_prediction_warns(tmp_db, settings):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    tmp_db.execute(
        "INSERT INTO predictions (description, probability, horizon_date, resolved) "
        "VALUES ('p', 0.6, ?, 0)",
        (yesterday,),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "predictions")]
    assert res.status == WARN
    assert "predict resolve" in res.detail


def test_open_prediction_within_horizon_ok(tmp_db, settings):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    tmp_db.execute(
        "INSERT INTO predictions (description, probability, horizon_date, resolved) "
        "VALUES ('p', 0.6, ?, 0)",
        (tomorrow,),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "predictions")]
    assert res.status == OK
    assert "1 open" in res.detail


def test_scenarios_skip_when_tables_absent(tmp_db, settings):
    # a pre-#17 DB has no scenario tables: the check must degrade to SKIP,
    # not crash (current schema ships them, so drop them)
    tmp_db.execute("DROP TABLE scenarios")
    tmp_db.execute("DROP TABLE scenario_sets")
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "conflict scenarios")]
    assert res.status == SKIP


def test_scenarios_overdue_warns_when_tables_exist(tmp_db, settings):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    tmp_db.execute(
        "INSERT INTO scenario_sets (country, country_name, created_date, "
        "horizon_date, status) VALUES ('IS', 'Israel', '2026-01-01', ?, 'active')",
        (yesterday,),
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "conflict scenarios")]
    assert res.status == WARN
    assert "scenario resolve" in res.detail


def test_no_brief_warns(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "brief")]
    assert res.status == WARN
    assert "pathos brief" in res.detail


def test_recent_brief_ok(tmp_db, settings):
    today = datetime.now(timezone.utc).date().isoformat()
    tmp_db.execute(
        "INSERT INTO briefs (date, content) VALUES (?, 'x')", (today,)
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "brief")]
    assert res.status == OK


def test_old_brief_warns(tmp_db, settings):
    old = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    tmp_db.execute(
        "INSERT INTO briefs (date, content) VALUES (?, 'x')", (old,)
    )
    res = _by_name(run_doctor(tmp_db, settings))[("agent", "brief")]
    assert res.status == WARN


# ── network probe ────────────────────────────────────────────────────────────

def test_market_probe_skipped_by_default(tmp_db, settings):
    res = _by_name(run_doctor(tmp_db, settings))[("network", "market data")]
    assert res.status == SKIP
    assert "--network" in res.detail


# ── exit-code helper ─────────────────────────────────────────────────────────

def test_has_failures_only_on_fail(tmp_db, settings):
    results = run_doctor(tmp_db, settings)
    assert not has_failures(results)  # warns/skips don't fail the run
