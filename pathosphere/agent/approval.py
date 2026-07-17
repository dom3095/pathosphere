"""
Thesis approval flow — list, show, approve, reject.

All mutations are synchronous (no LLM calls) and operate on the
`theses` + `watchlist_items` tables directly.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yfinance as yf
from loguru import logger

from pathosphere.agent.predictions import create_thesis_prediction, link_thesis_prediction_to_trade
from pathosphere.market.trading import OpenTradeResult, open_agent_trade


# ── query helpers ─────────────────────────────────────────────────────────────

def list_theses(conn: sqlite3.Connection, status: str = "pending") -> list[sqlite3.Row]:
    """Return theses filtered by status, newest first."""
    return conn.execute(
        """
        SELECT id, title, instrument, direction, price_snapshot,
               horizon_days, confidence, status, debate_id, created_at
        FROM theses
        WHERE status = ?
        ORDER BY id DESC
        """,
        (status,),
    ).fetchall()


def get_thesis(conn: sqlite3.Connection, thesis_id: int) -> sqlite3.Row | None:
    """Return a single thesis row or None if not found."""
    return conn.execute(
        "SELECT * FROM theses WHERE id = ?", (thesis_id,)
    ).fetchone()


def get_watchlist_items(conn: sqlite3.Connection, thesis_id: int) -> list[sqlite3.Row]:
    """Return watchlist items linked to thesis_id."""
    return conn.execute(
        "SELECT id, label, description, indicator_query, status FROM watchlist_items WHERE thesis_id = ?",
        (thesis_id,),
    ).fetchall()


# ── ticker validation ─────────────────────────────────────────────────────────

def validate_ticker(ticker: str) -> bool:
    """True if yfinance fast_info has a price; False if unknown/empty. Never raises."""
    if not ticker or not ticker.strip():
        return False
    try:
        info = yf.Ticker(ticker.strip().upper()).fast_info
        last_price = getattr(info, "last_price", None)
        return last_price is not None and last_price > 0
    except Exception as exc:
        logger.debug(f"APPROVAL: ticker validation failed for {ticker}: {exc}")
        return False


# ── mutations ─────────────────────────────────────────────────────────────────

def approve_thesis(conn: sqlite3.Connection, thesis_id: int) -> sqlite3.Row:
    """Set status → approved, record approved_at. Returns updated row.

    Raises ValueError if thesis not found or already approved/rejected.
    """
    thesis = get_thesis(conn, thesis_id)
    if thesis is None:
        raise ValueError(f"Thesis {thesis_id} not found.")
    if thesis["status"] != "pending":
        raise ValueError(
            f"Thesis {thesis_id} is '{thesis['status']}' — can only approve pending theses."
        )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE theses SET status = 'approved', approved_at = ? WHERE id = ?",
        (now, thesis_id),
    )
    conn.commit()
    logger.success(f"APPROVAL: thesis {thesis_id} approved at {now}")
    return get_thesis(conn, thesis_id)  # type: ignore[return-value]


def reject_thesis(
    conn: sqlite3.Connection, thesis_id: int, reason: str
) -> sqlite3.Row:
    """Set status → rejected, record rejection_reason + rejected_at. Returns updated row.

    Raises ValueError if thesis not found or not pending.
    """
    if not reason or not reason.strip():
        raise ValueError("Rejection reason must not be empty.")

    thesis = get_thesis(conn, thesis_id)
    if thesis is None:
        raise ValueError(f"Thesis {thesis_id} not found.")
    if thesis["status"] != "pending":
        raise ValueError(
            f"Thesis {thesis_id} is '{thesis['status']}' — can only reject pending theses."
        )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE theses
        SET status = 'rejected', rejected_at = ?, rejection_reason = ?
        WHERE id = ?
        """,
        (now, reason.strip(), thesis_id),
    )
    conn.commit()
    logger.success(f"APPROVAL: thesis {thesis_id} rejected at {now} — {reason[:80]}")
    return get_thesis(conn, thesis_id)  # type: ignore[return-value]


# ── workflow (single source of truth for both the manual CLI path and ─────────
# ── thesis.py's confidence-threshold auto-open path — see CRITICAL_POINTS.md) ─

@dataclass
class ApproveResult:
    thesis: sqlite3.Row
    ticker_valid: bool | None  # None = no ticker to check
    prediction: sqlite3.Row | None  # None = not created (see prediction_error)
    prediction_error: str | None = None


def approve_thesis_with_prediction(
    conn: sqlite3.Connection, thesis_id: int, *, warn_on_bad_ticker: bool = True
) -> ApproveResult:
    """Validate ticker (non-blocking) → approve_thesis → auto-create the
    linked economic prediction (degrades on failure, never blocks approval).

    Used by both `pathos thesis approve` (cli.py) and thesis.py's
    `_maybe_auto_open` so the two paths can't drift out of sync — previously
    each hand-duplicated this sequence separately, and the auto-open copy
    was missing the ticker validation the manual path had.

    Raises ValueError/sqlite3.Error only from approve_thesis itself (thesis
    not found, or not pending) — ticker validation and prediction creation
    are both non-fatal by design, reported via the returned ApproveResult.
    """
    thesis = get_thesis(conn, thesis_id)
    ticker = thesis["instrument"] if thesis else None
    ticker_valid = validate_ticker(ticker) if ticker else None
    if warn_on_bad_ticker and ticker_valid is False:
        logger.warning(
            f"APPROVAL: ticker {ticker!r} not found on yfinance for thesis "
            f"{thesis_id} — approving anyway, check before trading"
        )

    updated = approve_thesis(conn, thesis_id)

    prediction: dict | None = None
    prediction_error: str | None = None
    try:
        prediction = create_thesis_prediction(conn, updated)
    except (ValueError, sqlite3.Error) as exc:
        prediction_error = str(exc)
        logger.warning(
            f"APPROVAL: thesis {thesis_id} approved but economic prediction "
            f"NOT created: {exc}"
        )

    return ApproveResult(
        thesis=updated, ticker_valid=ticker_valid,
        prediction=prediction, prediction_error=prediction_error,
    )


def open_trade_and_link(conn: sqlite3.Connection, thesis_id: int) -> OpenTradeResult:
    """open_agent_trade → link_thesis_prediction_to_trade, as one step.

    Used by both `pathos trade open` (cli.py) and thesis.py's
    `_maybe_auto_open` — same rationale as `approve_thesis_with_prediction`.
    Raises ValueError/sqlite3.Error from open_agent_trade (thesis not
    approved, no portfolios, price fetch failed...); linking the prediction
    is best-effort and doesn't raise.
    """
    trade = open_agent_trade(conn, thesis_id)
    link_thesis_prediction_to_trade(conn, thesis_id, trade.agent_trade_id)
    return trade


# ── display helpers ───────────────────────────────────────────────────────────

def format_causal_chain(causal_chain_raw: str) -> dict[str, Any]:
    """Parse causal_chain JSON; return empty dict on failure."""
    if not causal_chain_raw:
        return {}
    try:
        return json.loads(causal_chain_raw)
    except json.JSONDecodeError:
        return {"raw": causal_chain_raw}
