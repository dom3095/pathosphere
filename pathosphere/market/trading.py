"""
Paper trading engine — open/close/track virtual trades.

Three portfolios (created by init_portfolios):
  agent      — trades from approved theses
  random     — same quantity/direction/timing, random ticker from pool
  benchmark  — buy-and-hold SPY, opened at init

No-lookahead bias: price_open = fresh yfinance fetch at DECISION time.
Costs: transaction_cost = 0.1% per side; slippage = 0.05% per side.
Both sides are accounted at close when computing pnl.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from pathosphere.market.prices import fetch_price

INITIAL_CASH = 100_000.0
TRANSACTION_COST_PCT = 0.001   # 0.1% per trade side
SLIPPAGE_PCT = 0.0005          # 0.05% per trade side
ALLOCATION_PCT = 0.10          # 10% of INITIAL_CASH per trade = $10k notional
RANDOM_TICKER_POOL = [
    "SPY", "QQQ", "GLD", "USO", "TLT", "EEM", "IWM", "XLE", "XLF", "DIA",
]
BENCHMARK_TICKER = "SPY"


# ── result types ──────────────────────────────────────────────────────────────

@dataclass
class OpenTradeResult:
    agent_trade_id: int
    random_trade_id: int
    ticker: str
    random_ticker: str
    direction: str
    quantity: float
    price_open: float


@dataclass
class CloseTradeResult:
    trade_id: int
    ticker: str
    direction: str
    price_open: float
    price_close: float
    quantity: float
    pnl: float


@dataclass
class PortfolioStatus:
    name: str
    portfolio_type: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    return_pct: float
    open_trades: int
    closed_trades: int


@dataclass
class InitResult:
    portfolios_created: list[str]
    portfolios_existing: list[str]
    benchmark_price: float | None


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _direction_mult(direction: str) -> float:
    return 1.0 if direction == "buy" else -1.0


def _trade_costs(price: float, qty: float) -> tuple[float, float]:
    """Return (transaction_cost, slippage) for ONE side of the trade."""
    value = price * qty
    return value * TRANSACTION_COST_PCT, value * SLIPPAGE_PCT


def _thesis_to_direction(thesis_direction: str | None) -> str:
    """Map thesis direction (long/short/neutral) → trade direction (buy/sell)."""
    if thesis_direction and thesis_direction.lower() == "short":
        return "sell"
    return "buy"


# ── core functions ────────────────────────────────────────────────────────────

def init_portfolios(conn: sqlite3.Connection) -> InitResult:
    """Create agent / random / benchmark portfolios if not exist.

    Benchmark gets a buy-and-hold SPY trade opened immediately.
    Idempotent: safe to call multiple times.
    """
    portfolio_specs = [
        ("agent",     "agent"),
        ("random",    "random"),
        ("benchmark", "benchmark"),
    ]
    created: list[str] = []
    existing: list[str] = []
    portfolio_ids: dict[str, int] = {}

    for name, ptype in portfolio_specs:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE name = ?", (name,)
        ).fetchone()
        if row:
            portfolio_ids[name] = row["id"]
            existing.append(name)
        else:
            cur = conn.execute(
                "INSERT INTO portfolios (name, portfolio_type, cash) VALUES (?, ?, ?)",
                (name, ptype, INITIAL_CASH),
            )
            portfolio_ids[name] = cur.lastrowid  # type: ignore[assignment]
            created.append(name)
            logger.info(f"TRADING: portfolio '{name}' created (id={cur.lastrowid})")

    # Benchmark: open SPY trade if none exists yet
    bench_id = portfolio_ids["benchmark"]
    spy_price: float | None = None
    has_bench_trade = conn.execute(
        "SELECT id FROM trades WHERE portfolio_id = ?", (bench_id,)
    ).fetchone()

    if not has_bench_trade:
        spy_price = fetch_price(BENCHMARK_TICKER)
        if spy_price:
            # Allocate all initial cash to SPY (minus open costs)
            tc, slip = _trade_costs(spy_price, 1.0)  # per-share cost ratio
            cost_ratio = tc + slip  # relative to 1 share
            spy_qty = INITIAL_CASH / (spy_price + cost_ratio)
            tc_total, slip_total = _trade_costs(spy_price, spy_qty)
            conn.execute(
                """INSERT INTO trades (
                    portfolio_id, thesis_id, ticker, direction, quantity,
                    price_open, opened_at, transaction_cost, slippage, notes
                ) VALUES (?, NULL, ?, 'buy', ?, ?, ?, ?, ?, ?)""",
                (
                    bench_id, BENCHMARK_TICKER, spy_qty, spy_price, _now(),
                    tc_total, slip_total,
                    f"Benchmark buy-and-hold {BENCHMARK_TICKER} @ {spy_price:.2f}",
                ),
            )
            logger.info(
                f"TRADING: benchmark {BENCHMARK_TICKER} @ {spy_price:.2f} "
                f"({spy_qty:.4f} shares)"
            )
        else:
            logger.warning(
                f"TRADING: could not fetch {BENCHMARK_TICKER} price — benchmark trade skipped"
            )

    conn.commit()
    return InitResult(
        portfolios_created=created,
        portfolios_existing=existing,
        benchmark_price=spy_price,
    )


def open_trade(
    conn: sqlite3.Connection,
    portfolio_id: int,
    ticker: str,
    direction: str,
    quantity: float,
    price_open: float,
    thesis_id: int | None = None,
    notes: str | None = None,
) -> int:
    """Insert one trade row. Returns trade_id."""
    tc, slip = _trade_costs(price_open, quantity)
    cur = conn.execute(
        """INSERT INTO trades (
            portfolio_id, thesis_id, ticker, direction, quantity,
            price_open, opened_at, transaction_cost, slippage, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            portfolio_id, thesis_id, ticker, direction, quantity,
            price_open, _now(), tc, slip, notes,
        ),
    )
    return cur.lastrowid  # type: ignore[return-value]


def open_agent_trade(conn: sqlite3.Connection, thesis_id: int) -> OpenTradeResult:
    """Open agent + corresponding random trade from an approved thesis.

    - price_open: fresh yfinance fetch at decision time (no-lookahead)
    - quantity: ALLOCATION_PCT × INITIAL_CASH / price_open
    - Random trade: same qty/direction, reproducible random ticker (seeded by thesis_id)

    Raises ValueError if thesis not found, not approved, ticker missing, or price unavailable.
    """
    thesis = conn.execute("SELECT * FROM theses WHERE id = ?", (thesis_id,)).fetchone()
    if thesis is None:
        raise ValueError(f"Thesis {thesis_id} not found.")
    if thesis["status"] != "approved":
        raise ValueError(
            f"Thesis {thesis_id} is '{thesis['status']}' — only approved theses can be traded."
        )

    ticker = thesis["instrument"]
    if not ticker or not ticker.strip():
        raise ValueError(f"Thesis {thesis_id} has no instrument ticker.")
    ticker = ticker.strip().upper()

    agent_port = conn.execute(
        "SELECT id FROM portfolios WHERE name = 'agent'"
    ).fetchone()
    rand_port = conn.execute(
        "SELECT id FROM portfolios WHERE name = 'random'"
    ).fetchone()
    if agent_port is None or rand_port is None:
        raise ValueError("Portfolios not initialized. Run: pathos portfolio init")

    price = fetch_price(ticker)
    if price is None:
        raise ValueError(
            f"Cannot fetch current price for {ticker}. "
            "Check the ticker or verify the market is open."
        )

    direction = _thesis_to_direction(thesis["direction"])
    quantity = (INITIAL_CASH * ALLOCATION_PCT) / price

    agent_trade_id = open_trade(
        conn, agent_port["id"], ticker, direction, quantity, price,
        thesis_id=thesis_id,
        notes=f"Agent: {(thesis['title'] or '')[:60]}",
    )

    # Random control trade — same quantity notional, reproducible ticker
    rng = random.Random(thesis_id)
    rand_ticker = rng.choice(RANDOM_TICKER_POOL)
    rand_price = fetch_price(rand_ticker) or price
    rand_qty = (INITIAL_CASH * ALLOCATION_PCT) / rand_price

    random_trade_id = open_trade(
        conn, rand_port["id"], rand_ticker, direction, rand_qty, rand_price,
        thesis_id=thesis_id,
        notes=f"Random control for thesis {thesis_id}",
    )

    conn.commit()
    logger.success(
        f"TRADING: agent {ticker} {direction} qty={quantity:.4f} @ {price:.2f} "
        f"(trade #{agent_trade_id}) | "
        f"random {rand_ticker} {direction} qty={rand_qty:.4f} @ {rand_price:.2f} "
        f"(trade #{random_trade_id})"
    )

    return OpenTradeResult(
        agent_trade_id=agent_trade_id,
        random_trade_id=random_trade_id,
        ticker=ticker,
        random_ticker=rand_ticker,
        direction=direction,
        quantity=quantity,
        price_open=price,
    )


def close_trade(conn: sqlite3.Connection, trade_id: int) -> CloseTradeResult:
    """Close a trade: fetch current price, compute pnl, persist.

    pnl = gross_pnl - total_costs (both open+close sides).

    Raises ValueError if trade not found or already closed.
    """
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if trade is None:
        raise ValueError(f"Trade {trade_id} not found.")
    if trade["closed_at"] is not None:
        raise ValueError(
            f"Trade {trade_id} is already closed (closed_at={trade['closed_at']})."
        )

    price_close = fetch_price(trade["ticker"])
    if price_close is None:
        raise ValueError(
            f"Cannot fetch close price for {trade['ticker']}."
        )

    mult = _direction_mult(trade["direction"])
    gross_pnl = (price_close - trade["price_open"]) * trade["quantity"] * mult
    tc_close, slip_close = _trade_costs(price_close, trade["quantity"])
    total_costs = trade["transaction_cost"] + trade["slippage"] + tc_close + slip_close
    pnl = gross_pnl - total_costs

    conn.execute(
        "UPDATE trades SET price_close = ?, closed_at = ?, pnl = ? WHERE id = ?",
        (price_close, _now(), pnl, trade_id),
    )
    conn.commit()

    logger.success(
        f"TRADING: closed #{trade_id} {trade['ticker']} {trade['direction']} "
        f"open={trade['price_open']:.2f} close={price_close:.2f} pnl={pnl:+.2f}"
    )

    return CloseTradeResult(
        trade_id=trade_id,
        ticker=trade["ticker"],
        direction=trade["direction"],
        price_open=trade["price_open"],
        price_close=price_close,
        quantity=trade["quantity"],
        pnl=pnl,
    )


def get_portfolio_status(conn: sqlite3.Connection) -> list[PortfolioStatus]:
    """Compute realized + unrealized P&L for each portfolio.

    Fetches current prices for open trades (one call per distinct ticker).
    Returns list ordered by portfolio name.
    """
    portfolios = conn.execute(
        "SELECT id, name, portfolio_type FROM portfolios ORDER BY name"
    ).fetchall()

    if not portfolios:
        return []

    open_trades = conn.execute(
        "SELECT id, portfolio_id, ticker, direction, quantity, price_open "
        "FROM trades WHERE closed_at IS NULL"
    ).fetchall()

    tickers = {t["ticker"] for t in open_trades}
    current_prices: dict[str, float | None] = {
        ticker: fetch_price(ticker) for ticker in tickers
    }

    result: list[PortfolioStatus] = []
    for p in portfolios:
        pid = p["id"]

        closed_row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) AS s, COUNT(*) AS n "
            "FROM trades WHERE portfolio_id = ? AND closed_at IS NOT NULL",
            (pid,),
        ).fetchone()
        realized_pnl = closed_row["s"] if closed_row["s"] is not None else 0.0
        n_closed = closed_row["n"]

        my_open = [t for t in open_trades if t["portfolio_id"] == pid]
        n_open = len(my_open)
        unrealized_pnl = 0.0
        for t in my_open:
            cp = current_prices.get(t["ticker"])
            if cp is not None:
                mult = _direction_mult(t["direction"])
                unrealized_pnl += (cp - t["price_open"]) * t["quantity"] * mult

        total_pnl = realized_pnl + unrealized_pnl
        return_pct = (total_pnl / INITIAL_CASH) * 100.0

        result.append(PortfolioStatus(
            name=p["name"],
            portfolio_type=p["portfolio_type"],
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=total_pnl,
            return_pct=return_pct,
            open_trades=n_open,
            closed_trades=n_closed,
        ))

    return result


def list_open_trades(conn: sqlite3.Connection, portfolio_name: str | None = None) -> list[sqlite3.Row]:
    """Return open trades, optionally filtered by portfolio name."""
    if portfolio_name:
        return conn.execute(
            """
            SELECT t.*, p.name AS portfolio_name
            FROM trades t JOIN portfolios p ON t.portfolio_id = p.id
            WHERE t.closed_at IS NULL AND p.name = ?
            ORDER BY t.opened_at DESC
            """,
            (portfolio_name,),
        ).fetchall()
    return conn.execute(
        """
        SELECT t.*, p.name AS portfolio_name
        FROM trades t JOIN portfolios p ON t.portfolio_id = p.id
        WHERE t.closed_at IS NULL
        ORDER BY t.opened_at DESC
        """
    ).fetchall()
