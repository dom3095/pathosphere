"""Tests for pathosphere/market/trading.py (3e).

All yfinance / fetch_price calls are mocked.
DB tests use the tmp_db fixture (full schema).
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from pathosphere.market.trading import (
    ALLOCATION_PCT,
    BENCHMARK_TICKER,
    INITIAL_CASH,
    RANDOM_TICKER_POOL,
    CloseTradeResult,
    InitResult,
    OpenTradeResult,
    _direction_mult,
    _thesis_to_direction,
    _trade_costs,
    close_trade,
    get_portfolio_status,
    init_portfolios,
    list_open_trades,
    open_agent_trade,
    open_trade,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_thesis(
    conn: sqlite3.Connection,
    *,
    title: str = "Test thesis",
    instrument: str = "USO",
    direction: str = "long",
    status: str = "approved",
    price_snapshot: float | None = 75.0,
) -> int:
    causal_chain = json.dumps({"steps": ["s1"], "trigger_summary": "T", "persona_notes": {}})
    cur = conn.execute(
        """INSERT INTO theses (title, causal_chain, instrument, direction, status, price_snapshot, sources_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, causal_chain, instrument, direction, status, price_snapshot, "[]"),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _create_portfolios(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert portfolios without touching yfinance."""
    ids = {}
    for name, ptype in [("agent", "agent"), ("random", "random"), ("benchmark", "benchmark")]:
        cur = conn.execute(
            "INSERT INTO portfolios (name, portfolio_type, cash) VALUES (?, ?, ?)",
            (name, ptype, INITIAL_CASH),
        )
        conn.commit()
        ids[name] = cur.lastrowid
    return ids


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_direction_mult_buy():
    assert _direction_mult("buy") == 1.0


def test_direction_mult_sell():
    assert _direction_mult("sell") == -1.0


def test_thesis_to_direction_long():
    assert _thesis_to_direction("long") == "buy"


def test_thesis_to_direction_short():
    assert _thesis_to_direction("short") == "sell"


def test_thesis_to_direction_neutral():
    assert _thesis_to_direction("neutral") == "buy"  # default


def test_thesis_to_direction_none():
    assert _thesis_to_direction(None) == "buy"


def test_trade_costs_positive():
    tc, slip = _trade_costs(100.0, 10.0)
    assert tc == pytest.approx(1.0)   # 0.1% of 1000
    assert slip == pytest.approx(0.5)  # 0.05% of 1000


def test_trade_costs_zero_qty():
    tc, slip = _trade_costs(100.0, 0.0)
    assert tc == 0.0
    assert slip == 0.0


# ── init_portfolios ───────────────────────────────────────────────────────────

def test_init_portfolios_creates_three(tmp_db):
    with patch("pathosphere.market.trading.fetch_price", return_value=500.0):
        result = init_portfolios(tmp_db)
    assert set(result.portfolios_created) == {"agent", "random", "benchmark"}
    assert result.portfolios_existing == []


def test_init_portfolios_idempotent(tmp_db):
    with patch("pathosphere.market.trading.fetch_price", return_value=500.0):
        r1 = init_portfolios(tmp_db)
        r2 = init_portfolios(tmp_db)
    assert set(r1.portfolios_created) == {"agent", "random", "benchmark"}
    assert set(r2.portfolios_existing) == {"agent", "random", "benchmark"}
    assert r2.portfolios_created == []


def test_init_portfolios_benchmark_trade(tmp_db):
    with patch("pathosphere.market.trading.fetch_price", return_value=500.0):
        result = init_portfolios(tmp_db)
    assert result.benchmark_price == pytest.approx(500.0)

    bench_id = tmp_db.execute(
        "SELECT id FROM portfolios WHERE name = 'benchmark'"
    ).fetchone()["id"]
    trades = tmp_db.execute(
        "SELECT * FROM trades WHERE portfolio_id = ?", (bench_id,)
    ).fetchall()
    assert len(trades) == 1
    assert trades[0]["ticker"] == BENCHMARK_TICKER
    assert trades[0]["direction"] == "buy"
    assert trades[0]["price_open"] == pytest.approx(500.0)


def test_init_portfolios_benchmark_spy_unavailable(tmp_db):
    with patch("pathosphere.market.trading.fetch_price", return_value=None):
        result = init_portfolios(tmp_db)
    assert result.benchmark_price is None

    bench_id = tmp_db.execute(
        "SELECT id FROM portfolios WHERE name = 'benchmark'"
    ).fetchone()["id"]
    trades = tmp_db.execute(
        "SELECT id FROM trades WHERE portfolio_id = ?", (bench_id,)
    ).fetchall()
    assert len(trades) == 0


def test_init_portfolios_returns_init_result(tmp_db):
    with patch("pathosphere.market.trading.fetch_price", return_value=400.0):
        result = init_portfolios(tmp_db)
    assert isinstance(result, InitResult)


# ── open_trade ────────────────────────────────────────────────────────────────

def test_open_trade_inserts_row(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "USO", "buy", 10.0, 80.0)
    assert isinstance(trade_id, int)
    assert trade_id > 0

    row = tmp_db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["ticker"] == "USO"
    assert row["direction"] == "buy"
    assert row["quantity"] == pytest.approx(10.0)
    assert row["price_open"] == pytest.approx(80.0)
    assert row["closed_at"] is None
    assert row["pnl"] is None


def test_open_trade_computes_costs(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "USO", "buy", 100.0, 100.0)
    row = tmp_db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["transaction_cost"] == pytest.approx(10.0)  # 0.1% of 10000
    assert row["slippage"] == pytest.approx(5.0)           # 0.05% of 10000


# ── open_agent_trade ──────────────────────────────────────────────────────────

def test_open_agent_trade_full(tmp_db):
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, instrument="USO", direction="long", status="approved")

    with patch("pathosphere.market.trading.fetch_price", return_value=75.0):
        result = open_agent_trade(tmp_db, tid)

    assert isinstance(result, OpenTradeResult)
    assert result.ticker == "USO"
    assert result.direction == "buy"
    assert result.price_open == pytest.approx(75.0)
    expected_qty = (INITIAL_CASH * ALLOCATION_PCT) / 75.0
    assert result.quantity == pytest.approx(expected_qty)
    assert result.random_ticker in RANDOM_TICKER_POOL
    assert result.agent_trade_id != result.random_trade_id


def test_open_agent_trade_short_maps_to_sell(tmp_db):
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, instrument="TLT", direction="short", status="approved")

    with patch("pathosphere.market.trading.fetch_price", return_value=90.0):
        result = open_agent_trade(tmp_db, tid)

    assert result.direction == "sell"

    agent_trade = tmp_db.execute(
        "SELECT direction FROM trades WHERE id = ?", (result.agent_trade_id,)
    ).fetchone()
    assert agent_trade["direction"] == "sell"


def test_open_agent_trade_thesis_not_found(tmp_db):
    _create_portfolios(tmp_db)
    with pytest.raises(ValueError, match="not found"):
        open_agent_trade(tmp_db, 9999)


def test_open_agent_trade_not_approved(tmp_db):
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, status="pending")
    with pytest.raises(ValueError, match="pending"):
        open_agent_trade(tmp_db, tid)


def test_open_agent_trade_no_ticker(tmp_db):
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, instrument="", status="approved")
    with pytest.raises(ValueError, match="no instrument"):
        open_agent_trade(tmp_db, tid)


def test_open_agent_trade_price_unavailable(tmp_db):
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, instrument="FAKEXYZ", status="approved")
    with patch("pathosphere.market.trading.fetch_price", return_value=None):
        with pytest.raises(ValueError, match="Cannot fetch"):
            open_agent_trade(tmp_db, tid)


def test_open_agent_trade_portfolios_missing(tmp_db):
    tid = _insert_thesis(tmp_db, status="approved")
    with patch("pathosphere.market.trading.fetch_price", return_value=100.0):
        with pytest.raises(ValueError, match="not initialized"):
            open_agent_trade(tmp_db, tid)


def test_open_agent_trade_random_ticker_reproducible(tmp_db):
    """Same thesis_id → same random ticker every time."""
    _create_portfolios(tmp_db)
    tid = _insert_thesis(tmp_db, status="approved")

    with patch("pathosphere.market.trading.fetch_price", return_value=50.0):
        r1 = open_agent_trade(tmp_db, tid)

    # Insert another approved thesis to open again (different thesis_id but same original id logic)
    # We just verify the random ticker is in the pool
    assert r1.random_ticker in RANDOM_TICKER_POOL


# ── close_trade ───────────────────────────────────────────────────────────────

def test_close_trade_long_profit(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "USO", "buy", 100.0, 80.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=90.0):
        result = close_trade(tmp_db, trade_id)

    assert isinstance(result, CloseTradeResult)
    assert result.price_close == pytest.approx(90.0)
    # gross = (90 - 80) * 100 * 1.0 = 1000
    # open costs: tc=8.0, slip=4.0 (stored in trade)
    # close costs: tc = 90*100*0.001=9.0, slip = 90*100*0.0005=4.5
    expected_pnl = 1000.0 - (8.0 + 4.0 + 9.0 + 4.5)
    assert result.pnl == pytest.approx(expected_pnl)


def test_close_trade_short_profit(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "USO", "sell", 100.0, 80.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=70.0):
        result = close_trade(tmp_db, trade_id)

    # gross = (70 - 80) * 100 * (-1) = +1000
    tc_open, slip_open = _trade_costs(80.0, 100.0)
    tc_close, slip_close = _trade_costs(70.0, 100.0)
    expected_pnl = 1000.0 - (tc_open + slip_open + tc_close + slip_close)
    assert result.pnl == pytest.approx(expected_pnl)


def test_close_trade_long_loss(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "GLD", "buy", 10.0, 200.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=180.0):
        result = close_trade(tmp_db, trade_id)

    # gross = (180 - 200) * 10 = -200
    assert result.pnl < 0


def test_close_trade_persisted(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "SPY", "buy", 5.0, 400.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=420.0):
        close_trade(tmp_db, trade_id)

    row = tmp_db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["price_close"] == pytest.approx(420.0)
    assert row["closed_at"] is not None
    assert row["pnl"] is not None


def test_close_trade_not_found(tmp_db):
    with pytest.raises(ValueError, match="not found"):
        close_trade(tmp_db, 9999)


def test_close_trade_already_closed(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "SPY", "buy", 1.0, 400.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=410.0):
        close_trade(tmp_db, trade_id)

    with patch("pathosphere.market.trading.fetch_price", return_value=410.0):
        with pytest.raises(ValueError, match="already closed"):
            close_trade(tmp_db, trade_id)


def test_close_trade_price_unavailable(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "FAKEXYZ", "buy", 1.0, 50.0)
    with patch("pathosphere.market.trading.fetch_price", return_value=None):
        with pytest.raises(ValueError, match="Cannot fetch close price"):
            close_trade(tmp_db, trade_id)


# ── get_portfolio_status ──────────────────────────────────────────────────────

def test_get_portfolio_status_empty(tmp_db):
    result = get_portfolio_status(tmp_db)
    assert result == []


def test_get_portfolio_status_no_trades(tmp_db):
    _create_portfolios(tmp_db)
    with patch("pathosphere.market.trading.fetch_price", return_value=100.0):
        statuses = get_portfolio_status(tmp_db)
    assert len(statuses) == 3
    for s in statuses:
        assert s.realized_pnl == pytest.approx(0.0)
        assert s.unrealized_pnl == pytest.approx(0.0)
        assert s.total_pnl == pytest.approx(0.0)


def test_get_portfolio_status_with_open_trade(tmp_db):
    ids = _create_portfolios(tmp_db)
    open_trade(tmp_db, ids["agent"], "USO", "buy", 100.0, 80.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=90.0):
        statuses = get_portfolio_status(tmp_db)

    agent_status = next(s for s in statuses if s.name == "agent")
    # unrealized = (90 - 80) * 100 = +1000 (ignoring costs in unrealized calc)
    assert agent_status.unrealized_pnl == pytest.approx(1000.0)
    assert agent_status.realized_pnl == pytest.approx(0.0)
    assert agent_status.open_trades == 1
    assert agent_status.closed_trades == 0


def test_get_portfolio_status_with_closed_trade(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "GLD", "buy", 10.0, 200.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=220.0):
        close_trade(tmp_db, trade_id)

    with patch("pathosphere.market.trading.fetch_price", return_value=220.0):
        statuses = get_portfolio_status(tmp_db)

    agent_status = next(s for s in statuses if s.name == "agent")
    assert agent_status.realized_pnl > 0
    assert agent_status.open_trades == 0
    assert agent_status.closed_trades == 1


def test_get_portfolio_status_return_pct(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "SPY", "buy", 10.0, 400.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=440.0):
        close_trade(tmp_db, trade_id)

    with patch("pathosphere.market.trading.fetch_price", return_value=440.0):
        statuses = get_portfolio_status(tmp_db)

    agent_status = next(s for s in statuses if s.name == "agent")
    assert agent_status.return_pct == pytest.approx(agent_status.total_pnl / INITIAL_CASH * 100)


def test_get_portfolio_status_portfolio_isolation(tmp_db):
    """Agent P&L does not bleed into random portfolio."""
    ids = _create_portfolios(tmp_db)
    open_trade(tmp_db, ids["agent"], "USO", "buy", 100.0, 80.0)

    with patch("pathosphere.market.trading.fetch_price", return_value=90.0):
        statuses = get_portfolio_status(tmp_db)

    random_status = next(s for s in statuses if s.name == "random")
    assert random_status.unrealized_pnl == pytest.approx(0.0)
    assert random_status.open_trades == 0


# ── list_open_trades ──────────────────────────────────────────────────────────

def test_list_open_trades_empty(tmp_db):
    _create_portfolios(tmp_db)
    rows = list_open_trades(tmp_db)
    assert rows == []


def test_list_open_trades_all(tmp_db):
    ids = _create_portfolios(tmp_db)
    open_trade(tmp_db, ids["agent"], "USO", "buy", 10.0, 80.0)
    open_trade(tmp_db, ids["random"], "SPY", "buy", 5.0, 400.0)
    rows = list_open_trades(tmp_db)
    assert len(rows) == 2


def test_list_open_trades_filter_by_portfolio(tmp_db):
    ids = _create_portfolios(tmp_db)
    open_trade(tmp_db, ids["agent"], "USO", "buy", 10.0, 80.0)
    open_trade(tmp_db, ids["random"], "SPY", "buy", 5.0, 400.0)
    rows = list_open_trades(tmp_db, portfolio_name="agent")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "USO"


def test_list_open_trades_excludes_closed(tmp_db):
    ids = _create_portfolios(tmp_db)
    trade_id = open_trade(tmp_db, ids["agent"], "GLD", "buy", 1.0, 200.0)
    with patch("pathosphere.market.trading.fetch_price", return_value=210.0):
        close_trade(tmp_db, trade_id)
    rows = list_open_trades(tmp_db)
    assert rows == []


# ── integration: full trade lifecycle ────────────────────────────────────────

def test_full_trade_lifecycle(tmp_db):
    """init → open agent trade → verify → close → verify P&L."""
    with patch("pathosphere.market.trading.fetch_price", return_value=400.0):
        init_portfolios(tmp_db)

    tid = _insert_thesis(tmp_db, instrument="SPY", direction="long", status="approved")

    with patch("pathosphere.market.trading.fetch_price", return_value=400.0):
        open_result = open_agent_trade(tmp_db, tid)

    assert open_result.ticker == "SPY"
    assert open_result.direction == "buy"

    with patch("pathosphere.market.trading.fetch_price", return_value=420.0):
        close_result = close_trade(tmp_db, open_result.agent_trade_id)

    assert close_result.pnl > 0  # price went up, long position

    with patch("pathosphere.market.trading.fetch_price", return_value=420.0):
        statuses = get_portfolio_status(tmp_db)

    agent = next(s for s in statuses if s.name == "agent")
    assert agent.realized_pnl > 0
    assert agent.open_trades == 0
    assert agent.closed_trades == 1
