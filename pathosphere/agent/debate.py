"""
Multi-persona debate pipeline for thesis generation.

Pipeline (all Qwen except the final synthesis):
  Step 1 — Research    (Qwen x6, batches of 2)  : each persona independently reads the brief
  Step 2 — Divergence  (Qwen x1)                : identify 2-3 key disagreement points
  Step 3 — Critique    (Qwen x6, batches of 2)  : each persona responds to divergence points
  Step 4 — Synthesis   (Claude x1)              : generate theses informed by the debate

All intermediate outputs are persisted to persona_analyses; final theses go to theses.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from loguru import logger

from pathosphere.agent.thesis import (
    ThesisResult,
    _MarketEnrichment,
    _maybe_auto_open,
    _price_snapshot,
    _run_market_review,
    _save_thesis,
    _save_watchlist_items,
)
from pathosphere.config import get_settings
from pathosphere.llm.client import LLMClient

# ── persona catalogue ─────────────────────────────────────────────────────────

PERSONAS: dict[str, dict] = {
    "beijing": {
        "name": "Beijing Analyst",
        "context": (
            "Senior analyst at China's Ministry of State Security. "
            "Focus: US containment, Taiwan reunification, Belt & Road infrastructure, "
            "energy security, technology self-sufficiency, yuan internationalisation. "
            "Frames global events through the lens of multipolarity and Chinese sovereignty."
        ),
    },
    "washington": {
        "name": "Washington Analyst",
        "context": (
            "Senior analyst at the US National Security Council. "
            "Focus: alliance management, dollar hegemony, Indo-Pacific deterrence, "
            "technology export controls, NATO cohesion. "
            "Frames events through the rules-based international order and democratic values."
        ),
    },
    "moscow": {
        "name": "Moscow Analyst",
        "context": (
            "Senior analyst at Russia's Foreign Intelligence Service (SVR). "
            "Focus: NATO expansion as existential threat, energy export revenues, "
            "sanctions circumvention, multipolar world order, near-abroad sphere of influence. "
            "Frames events through historical security guarantees and great power competition."
        ),
    },
    "riyadh": {
        "name": "Riyadh Analyst",
        "context": (
            "Senior adviser at Saudi Arabia's Ministry of Finance and NEOM project office. "
            "Focus: OPEC+ cohesion, oil price management ($80-100 target), Vision 2030 "
            "diversification, Iran containment, petrodollar relationships, regional stability. "
            "Pragmatic: balances US security umbrella with growing Chinese trade ties."
        ),
    },
    "jerusalem": {
        "name": "Jerusalem Analyst",
        "context": (
            "Senior analyst at Israel's Mossad. "
            "Focus: Iran nuclear programme as existential threat, Hezbollah/Hamas threat vectors, "
            "regional normalisation (Abraham Accords expansion), US military support continuity, "
            "dual-use technology exports. "
            "Frames events through existential security calculus and regional deterrence."
        ),
    },
    "paris": {
        "name": "Paris Analyst",
        "context": (
            "Senior adviser at France's Directorate-General for External Security (DGSE). "
            "Focus: European strategic autonomy, French interests in Sahel/West Africa, "
            "energy transition (nuclear + renewables), China-EU trade balance, "
            "NATO burden-sharing debate. "
            "Frames events through French grandeur and EU sovereignty distinct from Washington."
        ),
    },
}

STEP_RESEARCH = "research"
STEP_DIVERGENCE = "divergence"
STEP_CRITIQUE = "critique"
STEP_SYNTHESIS = "synthesis"


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class DebateResult:
    debate_id: int
    thesis_result: ThesisResult
    divergence_points: list[dict] = field(default_factory=list)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_brief(conn: sqlite3.Connection, brief_date: str) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT id, content FROM briefs WHERE date = ?", (brief_date,)
    ).fetchone()
    if row:
        return row["id"], row["content"]
    return None, None


def _save_debate(conn: sqlite3.Connection, brief_date: str, brief_id: int | None) -> int:
    cur = conn.execute(
        "INSERT INTO debates (date, brief_id, status) VALUES (?, ?, 'in_progress')",
        (brief_date, brief_id),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _save_persona_analysis(
    conn: sqlite3.Connection,
    debate_id: int,
    persona: str,
    step: str,
    content: dict,
) -> None:
    conn.execute(
        "INSERT INTO persona_analyses (debate_id, persona, step, content) VALUES (?, ?, ?, ?)",
        (debate_id, persona, step, json.dumps(content)),
    )
    conn.commit()


def _update_debate_status(conn: sqlite3.Connection, debate_id: int, status: str) -> None:
    conn.execute("UPDATE debates SET status = ? WHERE id = ?", (status, debate_id))
    conn.commit()


# ── step 1: research ──────────────────────────────────────────────────────────

def _research_prompt(persona_key: str, persona_cfg: dict, brief_content: str) -> list[dict]:
    system = (
        f"You are the {persona_cfg['name']}. {persona_cfg['context']}\n\n"
        "Analyse the intelligence brief provided through your specific geopolitical lens. "
        "Be direct and opinionated — you represent your government's perspective, not a neutral view. "
        "Respond ONLY with valid JSON."
    )
    user = f"""Analyse this morning intelligence brief from your perspective as {persona_cfg['name']}.

Return JSON:
{{
  "key_concerns": ["top 2-3 things that worry you most from this brief"],
  "opportunities": ["1-2 strategic opportunities your side can exploit"],
  "key_actors": ["actors you are watching most closely"],
  "narrative": "Your 2-paragraph interpretation of what is really happening and why",
  "risk_assessment": "high|medium|low",
  "market_implications": "1-2 sentences on how you expect markets to react"
}}

## MORNING BRIEF
{brief_content}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _run_research(
    qwen: LLMClient, persona_key: str, persona_cfg: dict, brief_content: str
) -> tuple[str, dict]:
    messages = _research_prompt(persona_key, persona_cfg, brief_content)
    raw = await qwen.complete(messages, json_mode=True)
    try:
        return persona_key, json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"DEBATE: research parse failed for {persona_key}, using raw")
        return persona_key, {"narrative": raw, "key_concerns": [], "opportunities": [],
                              "key_actors": [], "risk_assessment": "unknown",
                              "market_implications": ""}


# ── step 2: divergence detection ──────────────────────────────────────────────

def _divergence_prompt(analyses: dict[str, dict]) -> list[dict]:
    system = (
        "You are a meta-analyst comparing six geopolitical intelligence assessments. "
        "Your task is to identify where the analysts fundamentally disagree — not surface differences "
        "but deep structural disagreements about causes, actors, and implications. "
        "Respond ONLY with valid JSON."
    )
    summaries = "\n\n".join(
        f"### {PERSONAS[p]['name']}\n"
        f"Concerns: {', '.join(a.get('key_concerns', []))}\n"
        f"Narrative: {a.get('narrative', '')}\n"
        f"Market view: {a.get('market_implications', '')}"
        for p, a in analyses.items()
    )
    user = f"""Six analysts have independently assessed the same intelligence brief. Find their key disagreements.

{summaries}

Return JSON with 2-3 divergence points (the most important structural disagreements):
{{
  "divergence_points": [
    {{
      "id": "dp1",
      "title": "Short title of the disagreement (max 60 chars)",
      "description": "What specifically do the analysts disagree about, and why does it matter for markets",
      "personas_for": ["persona_key1", "persona_key2"],
      "personas_against": ["persona_key3"],
      "personas_neutral": ["persona_key4", "persona_key5", "persona_key6"]
    }}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _run_divergence_detection(
    qwen: LLMClient, analyses: dict[str, dict]
) -> list[dict]:
    messages = _divergence_prompt(analyses)
    raw = await qwen.complete(messages, json_mode=True)
    try:
        data = json.loads(raw)
        return data.get("divergence_points", [])
    except json.JSONDecodeError:
        logger.warning("DEBATE: divergence parse failed, returning empty list")
        return []


# ── step 3: critique ─────────────────────────────────────────────────────────

def _critique_prompt(
    persona_key: str,
    persona_cfg: dict,
    own_analysis: dict,
    divergence_points: list[dict],
) -> list[dict]:
    system = (
        f"You are the {persona_cfg['name']}. {persona_cfg['context']}\n\n"
        "You have seen the key points of disagreement between yourself and other analysts. "
        "Defend your position on each divergence point — be precise and argumentative. "
        "Respond ONLY with valid JSON."
    )
    dps = "\n".join(
        f"- [{dp['id']}] {dp['title']}: {dp['description']}"
        for dp in divergence_points
    )
    user = f"""You previously assessed the brief. Other analysts disagree with you on these points:

{dps}

Your earlier narrative: {own_analysis.get('narrative', '')}

For each divergence point, state your position. Return JSON:
{{
  "responses": [
    {{
      "divergence_id": "dp1",
      "stance": "support|oppose|nuance",
      "argument": "Your specific argument (2-3 sentences) defending your view on this point"
    }}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _run_critique(
    qwen: LLMClient,
    persona_key: str,
    persona_cfg: dict,
    own_analysis: dict,
    divergence_points: list[dict],
) -> tuple[str, dict]:
    messages = _critique_prompt(persona_key, persona_cfg, own_analysis, divergence_points)
    raw = await qwen.complete(messages, json_mode=True)
    try:
        return persona_key, json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"DEBATE: critique parse failed for {persona_key}")
        return persona_key, {"responses": []}


# ── step 4: synthesis ────────────────────────────────────────────────────────

def _synthesis_prompt(
    brief_content: str,
    analyses: dict[str, dict],
    divergence_points: list[dict],
    critiques: dict[str, dict],
    n: int,
) -> list[dict]:
    system = (
        "You are a senior intelligence analyst synthesising a multi-perspective geopolitical debate "
        "into actionable trading theses. Each thesis must be grounded in the debate: cite which "
        "perspectives support or oppose it and why. Be specific and falsifiable. "
        "Respond ONLY with valid JSON."
    )

    debate_summary = []
    for p, analysis in analyses.items():
        critique = critiques.get(p, {})
        debate_summary.append(
            f"### {PERSONAS[p]['name']}\n"
            f"Assessment: {analysis.get('narrative', '')}\n"
            f"Market view: {analysis.get('market_implications', '')}\n"
            f"Critique responses: {json.dumps(critique.get('responses', []))}"
        )

    dp_text = "\n".join(
        f"- [{dp['id']}] {dp['title']}: {dp['description']} "
        f"(FOR: {dp.get('personas_for', [])} | AGAINST: {dp.get('personas_against', [])})"
        for dp in divergence_points
    )

    user = f"""Six geopolitical analysts have debated the morning brief. Generate {n} trading theses.

## KEY DIVERGENCE POINTS
{dp_text}

## ANALYST POSITIONS + CRITIQUES
{''.join(debate_summary)}

## ORIGINAL BRIEF
{brief_content}

Return ONLY valid JSON:
{{
  "theses": [
    {{
      "title": "Short descriptive title (max 80 chars)",
      "trigger_summary": "The specific event triggering this thesis",
      "causal_chain": ["concrete step 1", "concrete step 2", "concrete step 3"],
      "instrument": "TICKER",
      "direction": "long|short|neutral",
      "horizon_days": 14,
      "confidence": 0.60,
      "invalidation": "Specific observable condition that falsifies the thesis",
      "indicators": [
        {{"label": "Short label", "indicator_query": "keyword query for monitoring"}}
      ],
      "debate_context": {{
        "supporting_personas": ["persona_key1"],
        "opposing_personas": ["persona_key2"],
        "related_divergences": ["dp1"]
      }},
      "alternatives": [
        {{
          "title": "Alternative scenario (max 80 chars)",
          "trigger_summary": "...",
          "causal_chain": ["step 1", "step 2"],
          "instrument": "TICKER",
          "direction": "short",
          "horizon_days": 30,
          "confidence": 0.25,
          "invalidation": "...",
          "indicators": [{{"label": "...", "indicator_query": "..."}}]
        }}
      ]
    }}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _run_synthesis(
    claude: LLMClient,
    brief_content: str,
    analyses: dict[str, dict],
    divergence_points: list[dict],
    critiques: dict[str, dict],
    n: int,
) -> list[dict]:
    messages = _synthesis_prompt(brief_content, analyses, divergence_points, critiques, n)
    raw = await claude.complete(messages, json_mode=True)
    try:
        data = json.loads(raw)
        return data.get("theses", [])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Synthesis returned invalid JSON: {exc}\nRaw: {raw[:500]}"
        ) from exc


# ── persistence helpers ───────────────────────────────────────────────────────

async def _persist_theses(
    conn: sqlite3.Connection,
    theses_data: list[dict],
    debate_id: int,
    n: int,
    llm_client: LLMClient,
    *,
    enrich_fundamentals: bool = True,
    enrich_technicals: bool = True,
    auto_open: bool = True,
    auto_open_threshold: float | None = None,
) -> ThesisResult:
    """Same persistence + fundamentals/technicals enrichment +
    confidence-threshold auto-open as `thesis.py::generate_theses` — reuses
    its private helpers directly instead of re-implementing them, so the
    debate pipeline can't silently drift from the fast-path pipeline's
    feature set again (it previously had neither fundamentals nor auto-open)."""
    thesis_ids: list[int] = []
    watchlist_count = 0
    enrichment = _MarketEnrichment(enrich_fundamentals, enrich_technicals)
    auto_open_candidates: list[tuple[int, float | None]] = []

    for t in theses_data[:n]:
        ticker = t.get("instrument")
        fund_json, tech_json = enrichment.docs(ticker)
        price = _price_snapshot(ticker, tech_json)
        if ticker and price is None:
            logger.warning(f"DEBATE: price fetch failed for {ticker}")

        # Embed debate_context into causal_chain JSON
        t.setdefault("causal_chain", [])
        thesis_id = _save_thesis(
            conn, t, price, debate_id=debate_id,
            fundamentals_json=fund_json, technicals_json=tech_json,
        )
        thesis_ids.append(thesis_id)
        watchlist_count += _save_watchlist_items(conn, thesis_id, t.get("indicators", []))
        enrichment.queue_review(thesis_id, t, ticker, fund_json, tech_json)
        auto_open_candidates.append((thesis_id, t.get("confidence")))

        logger.info(
            f"DEBATE: thesis id={thesis_id} | {t.get('title', '')} | "
            f"{ticker} {t.get('direction')} | price={price}"
        )

        for alt in t.get("alternatives", []):
            alt_ticker = alt.get("instrument")
            alt_fund, alt_tech = enrichment.docs(alt_ticker)
            alt_price = price if alt_ticker == ticker else _price_snapshot(alt_ticker, alt_tech)
            alt_id = _save_thesis(
                conn, alt, alt_price, debate_id=debate_id,
                fundamentals_json=alt_fund, technicals_json=alt_tech,
            )
            thesis_ids.append(alt_id)
            watchlist_count += _save_watchlist_items(conn, alt_id, alt.get("indicators", []))
            enrichment.queue_review(alt_id, alt, alt_ticker, alt_fund, alt_tech)
            auto_open_candidates.append((alt_id, alt.get("confidence")))
            logger.info(f"DEBATE: alt id={alt_id} | {alt.get('title', '')} | {alt_ticker}")

    if enrichment.review_items:
        try:
            annotated = await _run_market_review(conn, llm_client, enrichment.review_items)
            logger.info(f"DEBATE: market review annotated {annotated} theses")
        except Exception as exc:
            logger.warning(
                f"DEBATE: market review failed ({exc}) — theses saved without assessment"
            )
    enrichment.log_fundamentals_degradation("DEBATE")

    conn.commit()

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
            logger.success(f"DEBATE: {len(auto_opened_ids)} auto-opened as paper trades")

    return ThesisResult(
        theses_created=len(thesis_ids),
        watchlist_created=watchlist_count,
        thesis_ids=thesis_ids,
        auto_opened_ids=auto_opened_ids,
    )


# ── public API ────────────────────────────────────────────────────────────────

QWEN_BATCH_SIZE = 2  # Ollama on 8GB M1 holds one model in memory — cap concurrent local calls.


async def _gather_in_batches(coros: list, batch_size: int = QWEN_BATCH_SIZE) -> list:
    """Run coroutines in fixed-size concurrent batches, one batch at a time.

    Firing all 6 persona calls at once against a single local Ollama instance
    queues them internally and blows past the per-call HTTP timeout (CP-029).
    """
    results = []
    for i in range(0, len(coros), batch_size):
        batch = coros[i : i + batch_size]
        results.extend(await asyncio.gather(*batch))
    return results


async def run_debate(
    conn: sqlite3.Connection,
    qwen_client: LLMClient,
    claude_client: LLMClient,
    *,
    brief_date: str | None = None,
    n_theses: int = 3,
    enrich_fundamentals: bool = True,
    enrich_technicals: bool = True,
    auto_open: bool = True,
    auto_open_threshold: float | None = None,
) -> DebateResult:
    """Run the full multi-persona debate pipeline and persist results.

    Args:
        conn:          Open SQLite connection.
        qwen_client:   LLMClient with backend='qwen-local' (research + critique).
        claude_client: LLMClient with backend='claude' (synthesis + market review).
        brief_date:    ISO date of the brief to use (default: today UTC).
        n_theses:      Number of primary theses to generate.
        enrich_fundamentals: same as thesis.py::generate_theses.
        enrich_technicals: same as thesis.py::generate_theses.
        auto_open:     same as thesis.py::generate_theses.
        auto_open_threshold: same as thesis.py::generate_theses.

    Returns:
        DebateResult with debate_id, ThesisResult, and divergence_points.

    Raises:
        ValueError: If no brief found or synthesis returns invalid JSON.
    """
    if brief_date is None:
        brief_date = date.today().isoformat()

    logger.info(f"DEBATE: starting for {brief_date} | personas={list(PERSONAS)} | n={n_theses}")

    brief_id, brief_content = _load_brief(conn, brief_date)
    if not brief_content:
        raise ValueError(f"No brief found for {brief_date}. Run `pathos brief` first.")

    debate_id = _save_debate(conn, brief_date, brief_id)
    logger.info(f"DEBATE: id={debate_id}")

    try:
        # ── Step 1: Research (batches of QWEN_BATCH_SIZE) ──────────────────────
        logger.info(f"DEBATE: step 1 — research (6 personas, batches of {QWEN_BATCH_SIZE})")
        research_tasks = [
            _run_research(qwen_client, pk, pc, brief_content)
            for pk, pc in PERSONAS.items()
        ]
        research_results = await _gather_in_batches(research_tasks)
        analyses: dict[str, dict] = dict(research_results)

        for persona_key, analysis in analyses.items():
            _save_persona_analysis(conn, debate_id, persona_key, STEP_RESEARCH, analysis)
        logger.info(f"DEBATE: research complete — {len(analyses)} analyses saved")

        # ── Step 2: Divergence detection ─────────────────────────────────────
        logger.info("DEBATE: step 2 — divergence detection")
        divergence_points = await _run_divergence_detection(qwen_client, analyses)
        _save_persona_analysis(conn, debate_id, "meta", STEP_DIVERGENCE,
                               {"divergence_points": divergence_points})
        logger.info(f"DEBATE: {len(divergence_points)} divergence points identified")
        for dp in divergence_points:
            logger.debug(f"  [{dp.get('id')}] {dp.get('title')}")

        # ── Step 3: Critique (batches of QWEN_BATCH_SIZE) ───────────────────────
        logger.info(f"DEBATE: step 3 — critique (6 personas, batches of {QWEN_BATCH_SIZE})")
        critique_tasks = [
            _run_critique(qwen_client, pk, pc, analyses[pk], divergence_points)
            for pk, pc in PERSONAS.items()
        ]
        critique_results = await _gather_in_batches(critique_tasks)
        critiques: dict[str, dict] = dict(critique_results)

        for persona_key, critique in critiques.items():
            _save_persona_analysis(conn, debate_id, persona_key, STEP_CRITIQUE, critique)
        logger.info("DEBATE: critiques complete")

        # ── Step 4: Synthesis + Thesis (Claude) ───────────────────────────────
        logger.info("DEBATE: step 4 — synthesis (Claude)")
        theses_data = await _run_synthesis(
            claude_client, brief_content, analyses, divergence_points, critiques, n_theses
        )
        _save_persona_analysis(conn, debate_id, "claude", STEP_SYNTHESIS,
                               {"theses_count": len(theses_data)})

        thesis_result = await _persist_theses(
            conn, theses_data, debate_id, n_theses, claude_client,
            enrich_fundamentals=enrich_fundamentals,
            enrich_technicals=enrich_technicals,
            auto_open=auto_open,
            auto_open_threshold=auto_open_threshold,
        )
        _update_debate_status(conn, debate_id, "complete")

        logger.success(
            f"DEBATE: complete | id={debate_id} | "
            f"{thesis_result.theses_created} theses | "
            f"{thesis_result.watchlist_created} watchlist items"
        )
        return DebateResult(
            debate_id=debate_id,
            thesis_result=thesis_result,
            divergence_points=divergence_points,
        )

    except Exception as exc:
        _update_debate_status(conn, debate_id, "failed")
        logger.error(f"DEBATE: failed — {exc}")
        raise
