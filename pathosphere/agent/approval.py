"""
Thesis approval flow — list, show, approve, reject.

All mutations are synchronous (no LLM calls) and operate on the
`theses` + `watchlist_items` tables directly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import yfinance as yf
from loguru import logger


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


# ── display helpers ───────────────────────────────────────────────────────────

def format_causal_chain(causal_chain_raw: str) -> dict[str, Any]:
    """Parse causal_chain JSON; return empty dict on failure."""
    if not causal_chain_raw:
        return {}
    try:
        return json.loads(causal_chain_raw)
    except json.JSONDecodeError:
        return {"raw": causal_chain_raw}
