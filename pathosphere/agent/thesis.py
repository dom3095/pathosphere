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

from pathosphere.llm.client import LLMClient
from pathosphere.market.prices import fetch_price

_N_DEFAULT = 3


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class ThesisResult:
    theses_created: int
    watchlist_created: int
    thesis_ids: list[int] = field(default_factory=list)


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
            sources_json, price_snapshot, debate_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
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


# ── public API ────────────────────────────────────────────────────────────────

async def generate_theses(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    *,
    brief_date: str | None = None,
    n: int = _N_DEFAULT,
) -> ThesisResult:
    """Generate and persist trading theses from today's morning brief.

    Args:
        conn:         Open SQLite connection (theses + watchlist_items must exist).
        llm_client:   Configured LLMClient — should use the claude backend.
        brief_date:   ISO date of the brief to use (default: today UTC).
        n:            Number of primary theses to generate.

    Returns:
        ThesisResult with counts of created theses and watchlist items.

    Raises:
        ValueError: If no brief exists for the given date, or LLM returns invalid JSON.
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
        raise ValueError(
            f"LLM returned invalid thesis JSON: {exc}\nRaw output: {raw[:500]}"
        ) from exc

    thesis_ids: list[int] = []
    watchlist_count = 0

    for t in theses_data[:n]:
        ticker = t.get("instrument")
        price = fetch_price(ticker) if ticker else None
        if ticker and price is None:
            logger.warning(f"THESIS: price fetch failed for {ticker}")

        thesis_id = _save_thesis(conn, t, price)
        thesis_ids.append(thesis_id)
        watchlist_count += _save_watchlist_items(conn, thesis_id, t.get("indicators", []))

        logger.info(
            f"THESIS: id={thesis_id} | {t.get('title', '')} | "
            f"{ticker} {t.get('direction')} | price={price} | "
            f"horizon={t.get('horizon_days')}d | confidence={t.get('confidence')}"
        )

        for alt in t.get("alternatives", []):
            alt_ticker = alt.get("instrument")
            # Reuse already-fetched price if same ticker
            alt_price = price if alt_ticker == ticker else fetch_price(alt_ticker) if alt_ticker else None
            alt_id = _save_thesis(conn, alt, alt_price)
            thesis_ids.append(alt_id)
            watchlist_count += _save_watchlist_items(conn, alt_id, alt.get("indicators", []))
            logger.info(
                f"THESIS: alt id={alt_id} | {alt.get('title', '')} | "
                f"{alt_ticker} {alt.get('direction')}"
            )

    conn.commit()
    logger.success(
        f"THESIS: {len(thesis_ids)} rows persisted "
        f"({n} primary + {len(thesis_ids) - n} alternatives) | "
        f"{watchlist_count} watchlist items"
    )

    return ThesisResult(
        theses_created=len(thesis_ids),
        watchlist_created=watchlist_count,
        thesis_ids=thesis_ids,
    )
