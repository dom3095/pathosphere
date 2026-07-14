"""
Thesis generator.

Reads today's morning brief from DB, calls Claude once to produce N primary
theses (each with 1-2 competing alternatives), fetches current price
snapshots via yfinance, and persists all rows to theses + watchlist_items.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timezone

from loguru import logger

from pathosphere.agent.approval import approve_thesis
from pathosphere.agent.predictions import create_thesis_prediction, link_thesis_prediction_to_trade
from pathosphere.config import get_settings
from pathosphere.llm.client import LLMClient
from pathosphere.market.fundamentals import fetch_fundamentals, render_fundamentals_text
from pathosphere.market.prices import fetch_price
from pathosphere.market.trading import open_agent_trade

_N_DEFAULT = 3


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class ThesisResult:
    theses_created: int
    watchlist_created: int
    thesis_ids: list[int] = field(default_factory=list)
    # Set when the LLM declined to return JSON theses (e.g. it judged the
    # brief's signals too thin to ground a falsifiable thesis and explained
    # why in prose instead). theses_created is 0 in that case — a legitimate
    # "no theses today" outcome, not a pipeline failure. See CRITICAL_POINTS.md.
    refusal_reason: str | None = None
    # thesis_ids auto-approved + opened as a paper trade because confidence
    # was >= settings.auto_open_confidence_threshold (virtual money — human
    # reviews/closes after, not a pre-trade gate). Everything else stays
    # 'pending' for manual `pathos thesis approve`, same as before this
    # policy existed.
    auto_opened_ids: list[int] = field(default_factory=list)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_brief(conn: sqlite3.Connection, brief_date: str) -> str | None:
    row = conn.execute(
        "SELECT content FROM briefs WHERE date = ?", (brief_date,)
    ).fetchone()
    return row["content"] if row else None


def _save_thesis(
    conn: sqlite3.Connection,
    t: dict,
    price_snapshot: float | None,
    debate_id: int | None = None,
    fundamentals_json: str | None = None,
) -> int:
    causal_chain_doc = {
        "steps": t.get("causal_chain", []),
        "trigger_summary": t.get("trigger_summary", ""),
        "persona_notes": t.get("persona_notes", {}),
    }
    cur = conn.execute(
        """
        INSERT INTO theses (
            title, causal_chain, instrument, direction,
            horizon_days, invalidation, confidence, status,
            sources_json, price_snapshot, debate_id, fundamentals_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            t.get("title", ""),
            json.dumps(causal_chain_doc),
            t.get("instrument"),
            t.get("direction"),
            t.get("horizon_days"),
            t.get("invalidation"),
            t.get("confidence"),
            json.dumps(t.get("sources", [])),
            price_snapshot,
            debate_id,
            fundamentals_json,
        ),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _save_watchlist_items(
    conn: sqlite3.Connection,
    thesis_id: int,
    indicators: list[dict],
) -> int:
    count = 0
    for ind in indicators:
        label = ind.get("label", "")
        conn.execute(
            """
            INSERT INTO watchlist_items (thesis_id, label, description, indicator_query)
            VALUES (?, ?, ?, ?)
            """,
            (
                thesis_id,
                label,
                f"Thesis {thesis_id}: {label}",
                ind.get("indicator_query", ""),
            ),
        )
        count += 1
    return count


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(brief_content: str, n: int) -> list[dict]:
    system = (
        "You are a geopolitical intelligence analyst specializing in trading thesis generation. "
        "Each thesis must be specific, falsifiable, and grounded in the provided signals. "
        "Causal chains must be concrete — no vague steps. "
        "Invalidation conditions must be observable within the stated horizon."
    )
    user = f"""From the morning intelligence brief below, generate exactly {n} trading theses.

For each primary thesis generate 1-2 alternative/competing scenarios.

Return ONLY valid JSON, no markdown fences, no extra text:

{{
  "theses": [
    {{
      "title": "Short descriptive title (max 80 chars)",
      "trigger_summary": "1-2 sentences: the specific event that triggers this thesis",
      "causal_chain": ["concrete step 1", "concrete step 2", "concrete step 3"],
      "instrument": "TICKER_SYMBOL",
      "direction": "long",
      "horizon_days": 14,
      "confidence": 0.60,
      "invalidation": "Specific observable condition that falsifies the thesis within the horizon",
      "indicators": [
        {{"label": "Short monitor label", "indicator_query": "keyword query for GDELT/RSS"}}
      ],
      "persona_notes": {{
        "beijing": "1-2 sentences from a Beijing analyst perspective",
        "washington": "1-2 sentences from a Washington analyst perspective"
      }},
      "alternatives": [
        {{
          "title": "Alternative scenario (max 80 chars)",
          "trigger_summary": "...",
          "causal_chain": ["step 1", "step 2"],
          "instrument": "TICKER_SYMBOL",
          "direction": "short",
          "horizon_days": 30,
          "confidence": 0.25,
          "invalidation": "...",
          "indicators": [
            {{"label": "...", "indicator_query": "..."}}
          ]
        }}
      ]
    }}
  ]
}}

## MORNING BRIEF
{brief_content}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── fundamentals enrichment (context layer — never decides) ─────────────────

def _fundamentals_doc(ticker: str | None, cache: dict) -> str | None:
    """JSON doc {snapshot, text} for *ticker*, or None if unavailable.

    Caches per ticker within one generate_theses run (primary + alternatives
    often share instruments). fetch_fundamentals never raises — missing data
    is the expected case, not an error.
    """
    if not ticker:
        return None
    if ticker not in cache:
        snap = fetch_fundamentals(ticker)
        if snap is None:
            logger.warning(f"THESIS: fundamentals unavailable for {ticker}")
            cache[ticker] = None
        else:
            cache[ticker] = {
                "snapshot": snap.to_dict(),
                "text": render_fundamentals_text(snap),
            }
    doc = cache[ticker]
    return json.dumps(doc) if doc else None


def _fundamentals_review_prompt(items: list[dict]) -> list[dict]:
    system = (
        "You are a financial analyst supporting a geopolitical intelligence desk. "
        "You provide CONTEXT only, never decisions: do not approve, reject or score "
        "any thesis. For each thesis, assess in 2-3 sentences whether the company "
        "fundamentals SUPPORT, CONTRADICT or are NEUTRAL for the thesis direction, "
        "and flag any balance-sheet risk (e.g. distress-zone Z-score) the human "
        "approver should weigh. If data quality is low, say so explicitly."
    )
    blocks = []
    for item in items:
        blocks.append(
            f"### Thesis {item['thesis_id']}: {item['title']}\n"
            f"Instrument: {item['ticker']} — direction: {item['direction']}\n\n"
            f"{item['text']}"
        )
    joined = "\n\n".join(blocks)
    user = f"""Assess the fundamentals context for each trading thesis below.

Return ONLY valid JSON, no markdown fences, no extra text:

{{
  "assessments": [
    {{"thesis_id": 1, "assessment": "2-3 sentence fundamentals assessment"}}
  ]
}}

## THESES AND FUNDAMENTALS
{joined}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _run_fundamentals_review(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    items: list[dict],
) -> int:
    """One batched LLM call: per-thesis fundamentals assessment.

    Stores each assessment inside theses.fundamentals_json (key
    'llm_assessment'). Returns number of theses annotated. Raises on
    LLM/JSON failure — the caller catches and degrades.
    """
    messages = _fundamentals_review_prompt(items)
    raw = await llm_client.complete(messages, json_mode=True)
    data = json.loads(raw)
    assessments = {
        int(a["thesis_id"]): str(a["assessment"])
        for a in data["assessments"]
        if "thesis_id" in a and "assessment" in a
    }

    annotated = 0
    for item in items:
        text = assessments.get(item["thesis_id"])
        if not text:
            continue
        row = conn.execute(
            "SELECT fundamentals_json FROM theses WHERE id = ?", (item["thesis_id"],)
        ).fetchone()
        if row is None or not row["fundamentals_json"]:
            continue
        doc = json.loads(row["fundamentals_json"])
        doc["llm_assessment"] = text
        conn.execute(
            "UPDATE theses SET fundamentals_json = ? WHERE id = ?",
            (json.dumps(doc), item["thesis_id"]),
        )
        annotated += 1
    return annotated


def _maybe_auto_open(
    conn: sqlite3.Connection, thesis_id: int, confidence: float | None, threshold: float
) -> bool:
    """Auto-approve + auto-open a paper trade for a thesis at/above
    `threshold` confidence (virtual money — human reviews/closes after,
    never a pre-trade gate for these). Mirrors the exact manual sequence of
    `pathos thesis approve` + `pathos trade open`: approve_thesis →
    create_thesis_prediction → open_agent_trade → link_thesis_prediction_to_trade.

    approve_thesis commits on its own — if a later step fails (no
    portfolios initialized, price fetch failed...) the thesis is left
    'approved' but not traded, NOT reverted to 'pending'. That's the same
    state a manual `pathos thesis approve` followed by a failed `pathos
    trade open` would leave it in — `pathos trade open <id>` can complete
    it later. Returns True only on full success (approved AND traded).
    """
    if confidence is None or confidence < threshold:
        return False
    try:
        updated = approve_thesis(conn, thesis_id)
    except (ValueError, sqlite3.Error) as exc:
        logger.warning(f"THESIS: auto-approve {thesis_id} failed — left pending: {exc}")
        return False

    try:
        create_thesis_prediction(conn, updated)
    except (ValueError, sqlite3.Error) as exc:
        logger.warning(
            f"THESIS: auto-open {thesis_id} — economic prediction not created: {exc}"
        )

    try:
        trade = open_agent_trade(conn, thesis_id)
        link_thesis_prediction_to_trade(conn, thesis_id, trade.agent_trade_id)
    except (ValueError, sqlite3.Error) as exc:
        logger.warning(
            f"THESIS: {thesis_id} auto-approved (confidence={confidence}) but trade "
            f"NOT opened — retry via `pathos trade open {thesis_id}`: {exc}"
        )
        return False

    logger.success(
        f"THESIS: {thesis_id} auto-opened (confidence={confidence:.2f} >= {threshold})"
    )
    return True


# ── public API ────────────────────────────────────────────────────────────────

async def generate_theses(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    *,
    brief_date: str | None = None,
    n: int = _N_DEFAULT,
    enrich_fundamentals: bool = True,
    auto_open: bool = True,
    auto_open_threshold: float | None = None,
) -> ThesisResult:
    """Generate and persist trading theses from today's morning brief.

    Args:
        conn:         Open SQLite connection (theses + watchlist_items must exist).
        llm_client:   Configured LLMClient — should use the claude backend.
        brief_date:   ISO date of the brief to use (default: today UTC).
        n:            Number of primary theses to generate.
        enrich_fundamentals: fetch fundamentals per proposed ticker and run one
                      batched LLM review pass (context annotation, never a
                      decision). Any failure degrades to plain theses.
        auto_open:    Auto-approve + auto-open a paper trade for theses at/above
                      `auto_open_threshold` confidence, evaluated AFTER the
                      fundamentals review pass so a contradicted thesis still
                      benefits from that context before opening. Virtual money —
                      human reviews/closes after, never a pre-trade gate for
                      these. Everything below threshold stays 'pending' as
                      before this policy existed.
        auto_open_threshold: confidence cutoff (default: settings value, 0.6).

    Returns:
        ThesisResult with counts of created theses and watchlist items.
        theses_created=0 with refusal_reason set means the LLM declined to
        propose theses (thin signal) rather than a failure.

    Raises:
        ValueError: If no brief exists for the given date.
    """
    if brief_date is None:
        brief_date = date.today().isoformat()

    logger.info(f"THESIS: generating {n} theses from brief {brief_date}")

    brief_content = _load_brief(conn, brief_date)
    if not brief_content:
        raise ValueError(
            f"No brief found for {brief_date}. Run `pathos brief` first."
        )

    messages = _build_prompt(brief_content, n)
    raw = await llm_client.complete(messages, json_mode=True)

    try:
        data = json.loads(raw)
        theses_data: list[dict] = data["theses"]
    except (json.JSONDecodeError, KeyError) as exc:
        # The model can legitimately decline to fabricate theses from a thin
        # brief (no divergences/events to ground a falsifiable claim) and
        # explain why in prose instead of JSON — that's correct behaviour
        # per the no-lookahead-bias principle, not a crash. Any other
        # malformed-JSON case (real bug, truncated output, ...) surfaces the
        # same way: 0 theses, reason preserved for a human to read, instead
        # of an unhandled exception aborting `pathos loop`/`pathos cycle`.
        logger.warning(
            f"THESIS: LLM did not return theses JSON ({exc}) — "
            f"treating as 0 theses proposed. Raw output: {raw[:2000]}"
        )
        return ThesisResult(
            theses_created=0,
            watchlist_created=0,
            thesis_ids=[],
            refusal_reason=raw[:2000],
        )

    thesis_ids: list[int] = []
    watchlist_count = 0
    fund_cache: dict[str, dict | None] = {}
    review_items: list[dict] = []
    auto_open_candidates: list[tuple[int, float | None]] = []

    def _enrich(ticker: str | None) -> str | None:
        if not enrich_fundamentals:
            return None
        return _fundamentals_doc(ticker, fund_cache)

    def _queue_review(thesis_id: int, t: dict, ticker: str | None, fund_json: str | None) -> None:
        if fund_json:
            review_items.append({
                "thesis_id": thesis_id,
                "title": t.get("title", ""),
                "ticker": ticker,
                "direction": t.get("direction"),
                "text": json.loads(fund_json)["text"],
            })

    for t in theses_data[:n]:
        ticker = t.get("instrument")
        price = fetch_price(ticker) if ticker else None
        if ticker and price is None:
            logger.warning(f"THESIS: price fetch failed for {ticker}")
        fund_json = _enrich(ticker)

        thesis_id = _save_thesis(conn, t, price, fundamentals_json=fund_json)
        thesis_ids.append(thesis_id)
        watchlist_count += _save_watchlist_items(conn, thesis_id, t.get("indicators", []))
        _queue_review(thesis_id, t, ticker, fund_json)
        auto_open_candidates.append((thesis_id, t.get("confidence")))

        logger.info(
            f"THESIS: id={thesis_id} | {t.get('title', '')} | "
            f"{ticker} {t.get('direction')} | price={price} | "
            f"horizon={t.get('horizon_days')}d | confidence={t.get('confidence')}"
        )

        for alt in t.get("alternatives", []):
            alt_ticker = alt.get("instrument")
            # Reuse already-fetched price if same ticker
            alt_price = price if alt_ticker == ticker else fetch_price(alt_ticker) if alt_ticker else None
            alt_fund = _enrich(alt_ticker)
            alt_id = _save_thesis(conn, alt, alt_price, fundamentals_json=alt_fund)
            thesis_ids.append(alt_id)
            watchlist_count += _save_watchlist_items(conn, alt_id, alt.get("indicators", []))
            _queue_review(alt_id, alt, alt_ticker, alt_fund)
            auto_open_candidates.append((alt_id, alt.get("confidence")))
            logger.info(
                f"THESIS: alt id={alt_id} | {alt.get('title', '')} | "
                f"{alt_ticker} {alt.get('direction')}"
            )

    # ── fundamentals review pass: ONE batched LLM call, degrades on failure ──
    if review_items:
        try:
            annotated = await _run_fundamentals_review(conn, llm_client, review_items)
            logger.info(f"THESIS: fundamentals review annotated {annotated} theses")
        except Exception as exc:
            logger.warning(
                f"THESIS: fundamentals review failed ({exc}) — theses saved without assessment"
            )

    conn.commit()
    logger.success(
        f"THESIS: {len(thesis_ids)} rows persisted "
        f"({n} primary + {len(thesis_ids) - n} alternatives) | "
        f"{watchlist_count} watchlist items"
    )

    # ── auto-open pass: AFTER the fundamentals review so a thesis whose
    # fundamentals contradict it still benefits from that context first ──
    auto_opened_ids: list[int] = []
    if auto_open:
        threshold = (
            auto_open_threshold
            if auto_open_threshold is not None
            else get_settings().auto_open_confidence_threshold
        )
        for thesis_id, confidence in auto_open_candidates:
            if _maybe_auto_open(conn, thesis_id, confidence, threshold):
                auto_opened_ids.append(thesis_id)
        if auto_opened_ids:
            logger.success(f"THESIS: {len(auto_opened_ids)} auto-opened as paper trades")

    return ThesisResult(
        theses_created=len(thesis_ids),
        watchlist_created=watchlist_count,
        thesis_ids=thesis_ids,
        auto_opened_ids=auto_opened_ids,
    )
