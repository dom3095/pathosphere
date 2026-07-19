"""
Conflict scenario forecasting — structured analytic pipeline.

Methodology (national-intelligence-office tradecraft, adapted to this stack):
  1. Hotspot triage  — deterministic, no LLM. Per-country escalation metrics
     from `gdelt_events` (recent window vs trailing baseline): material-conflict
     z-score, escalation ratio shift (verbal→material), Goldstein deterioration,
     volume surge. Same trailing-baseline/no-lookahead pattern as
     `ingest/anomaly.py`. This is TRIAGE ONLY — it selects what the LLM looks
     at (principle "the LLM sees only the best"); it never predicts.
     Inspired by ACLED CAST / VIEWS feature design, kept intentionally simple.
  2. Dossier         — frozen evidence snapshot per hotspot: metrics, GDELT
     escalation anomalies, RSS event clusters, narrative divergences,
     IODA blackouts, UCDP structural history. Each item gets an evidence id
     (E1..En) so the ACH matrix can reference it. Stored in
     scenario_sets.dossier_json (audit trail, no retroactive edits).
  3. Scenario generation — ONE Claude call per hotspot. Analysis of Competing
     Hypotheses (Heuer): 3-4 mutually exclusive, collectively exhaustive
     scenarios over a horizon, each rated against every evidence item
     (CC/C/N/I/II), with probability mass summing to 1, observable indicators
     (living watchlist), invalidation conditions, and market transmission
     notes. Key Assumptions Check stored on the set.
  4. Scoring         — each scenario spawns one `world` prediction
     (domain conflitto_armato) in the existing predictions-v2 engine, so
     Brier / time-adjusted calibration comes for free. A MECE set of binary
     predictions where exactly one resolves true is a proper multi-class
     probability score.
  5. Review          — superforecaster update loop: recompute current metrics,
     match watchlist indicator queries against new events, ONE Claude call
     revises the probability distribution; changes flow to
     `revise_prediction` with rationale (auditable revision history).
  6. Resolution      — human names the materialized scenario at horizon;
     every linked prediction resolves (winner true, siblings false).

Tables written: scenario_sets, scenarios, watchlist_items, predictions.
Tables read: gdelt_events, events, event_documents, narrative_divergences.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from pathosphere.agent.predictions import (
    VALID_SCOPES,
    add_prediction,
    resolve_prediction,
    revise_prediction,
)
from pathosphere.config import get_settings
from pathosphere.llm.client import LLMClient

# ── triage constants ──────────────────────────────────────────────────────────

WINDOW_DAYS = 14           # recent observation window
BASELINE_DAYS = 90         # trailing baseline immediately before the window
MIN_WINDOW_EVENTS = 30     # noise guard: skip countries with thin recent coverage
MIN_BASELINE_DAYS_WITH_DATA = 30  # skip countries without a usable baseline

# Composite triage score weights. The score only RANKS candidate theaters for
# LLM attention — it is never surfaced as a prediction. Components are each
# normalized to [0, 1] before weighting.
_W_MATERIAL_Z = 0.35       # z-score of daily material-conflict counts vs baseline
_W_ESCALATION = 0.25       # shift of quad4/(quad3+quad4) share vs baseline
_W_GOLDSTEIN = 0.25        # Goldstein mean deterioration vs baseline
_W_VOLUME = 0.15           # overall event-volume surge (log ratio)

_N_SCENARIOS = "3-4"       # asked of the LLM per set
_MIN_PROB = 0.01           # floor so no scenario is persisted at exactly 0
_REVISION_EPSILON = 0.01   # skip revise_prediction below this probability delta

# Scenario predictions always carry the armed-conflict domain pair.
_SCENARIO_DOMAINS = ["conflitto_armato", "tensione_militare"]
_DEFAULT_SCOPE = "regionale"

# GDELT ActionGeo country codes are FIPS 10-4, NOT ISO 3166 alpha-2 (e.g.
# Ukraine=UP, Ireland=EI, Germany=GM) despite the older schema comment.
# Minimal display map for the codes that actually dominate the corpus +
# structurally conflict-prone theaters; unknown codes fall back to the raw
# code (the LLM handles FIPS fine when told). Second field is the ISO-2 code
# used by IODA's internet_metrics.
_FIPS_COUNTRIES: dict[str, tuple[str, str]] = {
    "AE": ("United Arab Emirates", "AE"),
    "AF": ("Afghanistan", "AF"),
    "AG": ("Algeria", "DZ"),
    "AL": ("Albania", "AL"),
    "AR": ("Argentina", "AR"),
    "BA": ("Bahrain", "BH"),
    "BK": ("Bosnia and Herzegovina", "BA"),
    "AM": ("Armenia", "AM"),
    "AJ": ("Azerbaijan", "AZ"),
    "AS": ("Australia", "AU"),
    "BG": ("Bangladesh", "BD"),
    "BM": ("Myanmar", "MM"),
    "BR": ("Brazil", "BR"),
    "CA": ("Canada", "CA"),
    "CD": ("Chad", "TD"),
    "CG": ("DR Congo", "CD"),
    "CH": ("China", "CN"),
    "CM": ("Cameroon", "CM"),
    "CO": ("Colombia", "CO"),
    "CT": ("Central African Republic", "CF"),
    "EG": ("Egypt", "EG"),
    "EI": ("Ireland", "IE"),
    "ET": ("Ethiopia", "ET"),
    "FR": ("France", "FR"),
    "GG": ("Georgia", "GE"),
    "GH": ("Ghana", "GH"),
    "GM": ("Germany", "DE"),
    "GR": ("Greece", "GR"),
    "HA": ("Haiti", "HT"),
    "IN": ("India", "IN"),
    "IR": ("Iran", "IR"),
    "IS": ("Israel", "IL"),
    "ID": ("Indonesia", "ID"),
    "IT": ("Italy", "IT"),
    "IZ": ("Iraq", "IQ"),
    "JA": ("Japan", "JP"),
    "JO": ("Jordan", "JO"),
    "KE": ("Kenya", "KE"),
    "KU": ("Kuwait", "KW"),
    "KN": ("North Korea", "KP"),
    "KS": ("South Korea", "KR"),
    "LE": ("Lebanon", "LB"),
    "LY": ("Libya", "LY"),
    "ML": ("Mali", "ML"),
    "MO": ("Morocco", "MA"),
    "MU": ("Oman", "OM"),
    "MX": ("Mexico", "MX"),
    "MY": ("Malaysia", "MY"),
    "NG": ("Niger", "NE"),
    "NI": ("Nigeria", "NG"),
    "NZ": ("New Zealand", "NZ"),
    "PK": ("Pakistan", "PK"),
    "PL": ("Poland", "PL"),
    "QA": ("Qatar", "QA"),
    "RP": ("Philippines", "PH"),
    "RS": ("Russia", "RU"),
    "SA": ("Saudi Arabia", "SA"),
    "SF": ("South Africa", "ZA"),
    "SO": ("Somalia", "SO"),
    "SP": ("Spain", "ES"),
    "SU": ("Sudan", "SD"),
    "SY": ("Syria", "SY"),
    "TH": ("Thailand", "TH"),
    "TS": ("Tunisia", "TN"),
    "TU": ("Turkey", "TR"),
    "TW": ("Taiwan", "TW"),
    "TX": ("Turkmenistan", "TM"),
    "UK": ("United Kingdom", "GB"),
    "UP": ("Ukraine", "UA"),
    "US": ("United States", "US"),
    "VE": ("Venezuela", "VE"),
    "VM": ("Vietnam", "VN"),
    "WZ": ("Eswatini", "SZ"),
    "YM": ("Yemen", "YE"),
}


def country_label(fips_code: str) -> str:
    """Human-readable name for a GDELT FIPS code; the raw code if unknown."""
    entry = _FIPS_COUNTRIES.get(fips_code)
    return entry[0] if entry else fips_code


# ── result types ──────────────────────────────────────────────────────────────

@dataclass
class HotspotMetrics:
    country: str                 # FIPS code as stored in gdelt_events
    country_name: str
    score: float                 # composite triage score [0, 1] — ranking only
    window_events: int
    baseline_daily_events: float
    window_daily_events: float
    material_z: float            # z of daily material-conflict counts vs baseline
                                 # (4.0-capped proxy when the baseline is flat)
    material_share_window: float
    material_share_baseline: float
    goldstein_window: float
    goldstein_baseline: float

    def summary_line(self) -> str:
        return (
            f"{self.country_name} [{self.country}] score={self.score:.2f} | "
            f"{self.window_events} events/{WINDOW_DAYS}d "
            f"(daily {self.window_daily_events:.1f} vs baseline {self.baseline_daily_events:.1f}) | "
            f"material z={self.material_z:+.1f}, share {self.material_share_window:.0%} "
            f"(baseline {self.material_share_baseline:.0%}) | "
            f"Goldstein {self.goldstein_window:.2f} (baseline {self.goldstein_baseline:.2f})"
        )


@dataclass
class ScenarioGenResult:
    sets_created: int = 0
    scenarios_created: int = 0
    predictions_created: int = 0
    watchlist_created: int = 0
    set_ids: list[int] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # per-hotspot skip reasons


@dataclass
class ScenarioReviewResult:
    sets_reviewed: int = 0
    probabilities_revised: int = 0
    indicators_triggered: int = 0
    overdue_set_ids: list[int] = field(default_factory=list)


# ── 1. hotspot triage (deterministic) ─────────────────────────────────────────

def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_hotspots(
    conn: sqlite3.Connection,
    *,
    window_days: int = WINDOW_DAYS,
    baseline_days: int = BASELINE_DAYS,
    min_window_events: int = MIN_WINDOW_EVENTS,
    as_of: date | None = None,
) -> list[HotspotMetrics]:
    """Rank countries by short-term escalation vs their own trailing baseline.

    Uses only `gdelt_events` rows dated strictly before *as_of* (no lookahead;
    as_of is injectable for tests and retro-analysis). Countries with a thin
    window or an unusable baseline are dropped, not scored at 0.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    window_start = as_of - timedelta(days=window_days)
    baseline_start = window_start - timedelta(days=baseline_days)

    rows = conn.execute(
        """
        SELECT action_geo_country AS cc,
               date(date_added)   AS d,
               COUNT(*)           AS n,
               SUM(CASE WHEN quad_class = 4 THEN 1 ELSE 0 END) AS n4,
               SUM(goldstein)     AS g_sum,
               COUNT(goldstein)   AS g_n
        FROM gdelt_events
        WHERE action_geo_country IS NOT NULL
          AND date(date_added) >= ?
          AND date(date_added) < ?
        GROUP BY cc, d
        """,
        (baseline_start.isoformat(), as_of.isoformat()),
    ).fetchall()

    per_country: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        per_country.setdefault(r["cc"], []).append(r)

    window_start_iso = window_start.isoformat()
    hotspots: list[HotspotMetrics] = []

    for cc, days in per_country.items():
        window = [r for r in days if r["d"] >= window_start_iso]
        baseline = [r for r in days if r["d"] < window_start_iso]

        n_window = sum(r["n"] for r in window)
        if n_window < min_window_events or len(baseline) < MIN_BASELINE_DAYS_WITH_DATA:
            continue

        # Daily material-conflict counts, zero-filled over the full span so
        # quiet days count as 0 rather than being invisible to the stats.
        base_n4 = {r["d"]: r["n4"] for r in baseline}
        base_daily_n4 = [
            base_n4.get((baseline_start + timedelta(days=i)).isoformat(), 0)
            for i in range(baseline_days)
        ]
        win_n4 = {r["d"]: r["n4"] for r in window}
        win_daily_n4 = [
            win_n4.get((window_start + timedelta(days=i)).isoformat(), 0)
            for i in range(window_days)
        ]

        mean_b = statistics.fmean(base_daily_n4)
        std_b = statistics.stdev(base_daily_n4) if len(base_daily_n4) > 1 else 0.0
        mean_w = statistics.fmean(win_daily_n4)
        if std_b > 0:
            material_z = (mean_w - mean_b) / std_b
        else:
            # perfectly flat baseline: any rise from it is the strongest
            # possible anomaly (cap of the normalized scale), no rise is zero
            material_z = 4.0 if mean_w > mean_b else 0.0

        n_baseline = sum(r["n"] for r in baseline)
        n4_window = sum(r["n4"] for r in window)
        n4_baseline = sum(r["n4"] for r in baseline)
        share_w = n4_window / n_window
        share_b = n4_baseline / n_baseline if n_baseline else 0.0

        g_n_w = sum(r["g_n"] for r in window)
        g_n_b = sum(r["g_n"] for r in baseline)
        gold_w = sum(r["g_sum"] or 0.0 for r in window) / g_n_w if g_n_w else 0.0
        gold_b = sum(r["g_sum"] or 0.0 for r in baseline) / g_n_b if g_n_b else 0.0

        daily_w = n_window / window_days
        daily_b = n_baseline / baseline_days if n_baseline else 0.0
        volume_ratio = daily_w / daily_b if daily_b > 0 else 1.0

        score = (
            _W_MATERIAL_Z * _clip01(material_z / 4.0)
            + _W_ESCALATION * _clip01((share_w - share_b) * 2.0)
            + _W_GOLDSTEIN * _clip01((gold_b - gold_w) / 4.0)
            + _W_VOLUME * _clip01(math.log2(volume_ratio) / 2.0 if volume_ratio > 0 else 0.0)
        )

        hotspots.append(HotspotMetrics(
            country=cc,
            country_name=country_label(cc),
            score=round(score, 4),
            window_events=n_window,
            baseline_daily_events=round(daily_b, 2),
            window_daily_events=round(daily_w, 2),
            material_z=round(material_z, 2),
            material_share_window=round(share_w, 4),
            material_share_baseline=round(share_b, 4),
            goldstein_window=round(gold_w, 3),
            goldstein_baseline=round(gold_b, 3),
        ))

    hotspots.sort(key=lambda h: h.score, reverse=True)
    return hotspots


# ── 2. dossier (frozen evidence snapshot) ─────────────────────────────────────

_MAX_ANOMALY_EVIDENCE = 8
_MAX_RSS_EVIDENCE = 8
_MAX_DIVERGENCE_EVIDENCE = 4
_EVIDENCE_LOOKBACK_DAYS = 30


def build_dossier(
    conn: sqlite3.Connection,
    hotspot: HotspotMetrics,
    *,
    as_of: date | None = None,
) -> dict:
    """Assemble the evidence dossier for one hotspot country.

    Every item gets an id (E1..En) the ACH matrix can cite. The dossier is
    frozen into scenario_sets.dossier_json at generation time — the audit
    trail of exactly what the scenarios were based on.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    cutoff = (as_of - timedelta(days=_EVIDENCE_LOOKBACK_DAYS)).isoformat()
    name = hotspot.country_name
    evidence: list[dict] = []

    def add(source: str, text: str) -> None:
        evidence.append({"id": f"E{len(evidence) + 1}", "source": source, "text": text})

    add("gdelt_metrics", hotspot.summary_line())

    # GDELT escalation anomalies already promoted to events (location = FIPS code)
    for r in conn.execute(
        """
        SELECT title, summary, severity, last_seen FROM events
        WHERE event_type = 'gdelt_anomaly' AND location_name = ? AND last_seen >= ?
        ORDER BY severity DESC, last_seen DESC LIMIT ?
        """,
        (hotspot.country, cutoff, _MAX_ANOMALY_EVIDENCE),
    ).fetchall():
        add("gdelt_anomaly", f"{r['summary'] or r['title']} (severity {r['severity']})")

    # RSS event clusters mentioning the country (title or geocoded location)
    rss_rows = conn.execute(
        """
        SELECT e.id, e.title, e.location_name, e.last_seen,
               COUNT(ed.document_id) AS doc_count
        FROM events e
        JOIN event_documents ed ON ed.event_id = e.id
        WHERE e.origin = 'rss' AND e.last_seen >= ?
          AND (e.location_name LIKE ? OR e.title LIKE ?)
        GROUP BY e.id
        ORDER BY doc_count DESC, e.last_seen DESC LIMIT ?
        """,
        (cutoff, f"%{name}%", f"%{name}%", _MAX_RSS_EVIDENCE),
    ).fetchall()
    rss_ids = [r["id"] for r in rss_rows]
    for r in rss_rows:
        add("rss_event", f"{r['title']} ({r['doc_count']} sources, {r['last_seen'][:10]})")

    # Narrative divergences across blocs on those same events — the gap itself
    # is a signal (project principle: divergence is data).
    if rss_ids:
        placeholders = ",".join("?" * len(rss_ids))
        for r in conn.execute(
            f"""
            SELECT nd.block_a, nd.block_b, nd.divergence_score, nd.summary, e.title
            FROM narrative_divergences nd JOIN events e ON e.id = nd.event_id
            WHERE nd.event_id IN ({placeholders})
            ORDER BY nd.divergence_score DESC LIMIT ?
            """,  # noqa: S608 — placeholders are literal '?' marks
            (*rss_ids, _MAX_DIVERGENCE_EVIDENCE),
        ).fetchall():
            add(
                "narrative_divergence",
                f"'{r['title']}': {r['block_a']} vs {r['block_b']} "
                f"diverge (score {r['divergence_score']:.2f}). {r['summary'] or ''}".strip(),
            )

    # IODA blackout events (location = country name or ISO code)
    entry = _FIPS_COUNTRIES.get(hotspot.country)
    iso2 = entry[1] if entry else hotspot.country
    for r in conn.execute(
        """
        SELECT title, summary, last_seen FROM events
        WHERE origin = 'ioda' AND last_seen >= ?
          AND (location_name LIKE ? OR location_name = ?)
        ORDER BY last_seen DESC LIMIT 3
        """,
        (cutoff, f"%{name}%", iso2),
    ).fetchall():
        add("ioda", f"{r['summary'] or r['title']} ({r['last_seen'][:10]})")

    # UCDP structural prior — decades of georeferenced violence, if backfilled.
    ucdp = conn.execute(
        """
        SELECT COUNT(*) AS n, MAX(severity) AS max_sev, MAX(last_seen) AS latest
        FROM events WHERE origin = 'ucdp' AND location_name LIKE ?
        """,
        (f"%{name}%",),
    ).fetchone()
    if ucdp and ucdp["n"]:
        add(
            "ucdp_history",
            f"UCDP structural record: {ucdp['n']} significant violent events on file, "
            f"max severity {ucdp['max_sev']}, most recent {ucdp['latest'][:10]}.",
        )

    return {
        "country": hotspot.country,
        "country_name": name,
        "as_of": as_of.isoformat(),
        "window_days": WINDOW_DAYS,
        "baseline_days": BASELINE_DAYS,
        "metrics": {
            "score": hotspot.score,
            "window_events": hotspot.window_events,
            "window_daily_events": hotspot.window_daily_events,
            "baseline_daily_events": hotspot.baseline_daily_events,
            "material_z": hotspot.material_z,
            "material_share_window": hotspot.material_share_window,
            "material_share_baseline": hotspot.material_share_baseline,
            "goldstein_window": hotspot.goldstein_window,
            "goldstein_baseline": hotspot.goldstein_baseline,
        },
        "evidence": evidence,
    }


# ── 3. LLM prompts ────────────────────────────────────────────────────────────

def _evidence_block(dossier: dict) -> str:
    return "\n".join(
        f"- [{e['id']}] ({e['source']}) {e['text']}" for e in dossier["evidence"]
    )


def _generation_prompt(dossier: dict, horizon_date: str) -> list[dict]:
    system = (
        "You are the senior warning officer of a national intelligence "
        "assessment staff. You apply structured analytic techniques — Analysis "
        "of Competing Hypotheses (Heuer), Key Assumptions Check, Indicators & "
        "Warnings — with rigor. Scenarios must be mutually exclusive and "
        "collectively exhaustive over the horizon; probabilities are honest "
        "degrees of belief, not hedges. Every judgment must trace to the "
        "evidence ids provided. Respond ONLY with valid JSON."
    )
    user = f"""Build a conflict scenario set for {dossier['country_name']} with horizon {horizon_date}.

## EVIDENCE DOSSIER (frozen {dossier['as_of']}, ids citable)
{_evidence_block(dossier)}

Escalation metrics: last {dossier['window_days']} days vs the prior {dossier['baseline_days']}-day baseline (see E1).

## TASK
1. State 2-4 key assumptions your analysis rests on (Key Assumptions Check).
2. Define {_N_SCENARIOS} mutually exclusive, collectively exhaustive scenarios for how the
   situation evolves by {horizon_date} (e.g. de-escalation / frozen status quo /
   limited escalation / major escalation — adapt to the actual theater).
3. Rate each scenario against each evidence id: CC (strongly consistent),
   C (consistent), N (neutral), I (inconsistent), II (strongly inconsistent).
4. Assign probabilities summing to 1.00 across scenarios.
5. For each scenario give 2-3 OBSERVABLE indicators watchable in our data
   streams (GDELT/RSS keyword queries, shipping chokepoints, internet outages),
   an invalidation condition, and 1-2 sentences on market transmission.

Return ONLY valid JSON:
{{
  "summary": "3-4 sentence net assessment of the situation",
  "key_assumptions": ["assumption 1", "assumption 2"],
  "scenarios": [
    {{
      "label": "A",
      "title": "Short scenario title (max 80 chars)",
      "description": "2-3 sentences: the concrete course of events",
      "probability": 0.40,
      "ach_ratings": {{"E1": "C", "E2": "N"}},
      "indicators": [
        {{"label": "Short monitor label", "indicator_query": "keyword query"}}
      ],
      "invalidation": "Observable condition that kills this scenario early",
      "market_implications": "1-2 sentences on instruments/flows affected",
      "origin_scope": "nazionale|regionale|multilaterale",
      "impact_scope": "regionale|multilaterale|globale"
    }}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _review_prompt(
    set_row: sqlite3.Row,
    scenarios: list[sqlite3.Row],
    current_metrics: HotspotMetrics | None,
    triggered: list[dict],
) -> list[dict]:
    system = (
        "You are the senior warning officer updating a standing conflict "
        "scenario set. Update probabilities like a superforecaster: small "
        "frequent adjustments grounded in new observations, large moves only "
        "on decisive evidence. Probabilities must still sum to 1.00. "
        "Respond ONLY with valid JSON."
    )
    scen_lines = "\n".join(
        f"- [{s['label']}] {s['title']} — current p={s['probability']:.2f}. "
        f"Invalidation: {s['invalidation'] or 'n/d'}"
        for s in scenarios
    )
    metrics_txt = (
        current_metrics.summary_line()
        if current_metrics is not None
        else "No fresh GDELT metrics available for this country (thin recent coverage)."
    )
    trig_lines = "\n".join(
        f"- [{t['scenario_label']}] indicator '{t['label']}' TRIGGERED by: {t['matched_titles']}"
        for t in triggered
    ) or "- none"
    dossier = json.loads(set_row["dossier_json"] or "{}")
    baseline_metrics = dossier.get("metrics", {})

    user = f"""Standing scenario set for {set_row['country_name']} (horizon {set_row['horizon_date']}, opened {set_row['created_date']}).

## SCENARIOS
{scen_lines}

## METRICS AT GENERATION TIME
{json.dumps(baseline_metrics)}

## CURRENT METRICS
{metrics_txt}

## INDICATORS TRIGGERED SINCE LAST REVIEW
{trig_lines}

Return ONLY valid JSON (include ALL scenarios, revised or not; probabilities sum to 1.00):
{{
  "revisions": [
    {{"label": "A", "probability": 0.45, "rationale": "1-2 sentences on why (or 'unchanged')"}}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ── persistence helpers ───────────────────────────────────────────────────────

def _normalize_probabilities(scenarios: list[dict]) -> None:
    """Clamp LLM probabilities to sane floats and renormalize to sum 1.

    json_mode is a prompt instruction, not a schema — non-numeric or
    non-summing values are realistic inputs. Garbage in every slot degrades
    to a uniform distribution rather than crashing after evidence gathering.
    """
    probs: list[float] = []
    for s in scenarios:
        p = s.get("probability")
        probs.append(float(p) if isinstance(p, (int, float)) and p > 0 else 0.0)
    total = sum(probs)
    if total <= 0:
        probs = [1.0 / len(scenarios)] * len(scenarios)
        total = 1.0
    for s, p in zip(scenarios, probs):
        s["probability"] = max(_MIN_PROB, round(p / total, 4))


def _valid_scope(value, fallback: str = _DEFAULT_SCOPE) -> str:
    return value if value in VALID_SCOPES else fallback


def _save_scenario_watchlist(
    conn: sqlite3.Connection, scenario_id: int, indicators: list[dict]
) -> int:
    count = 0
    for ind in indicators:
        if not isinstance(ind, dict):
            continue
        label = str(ind.get("label", "")).strip()
        if not label:
            continue
        conn.execute(
            """
            INSERT INTO watchlist_items (scenario_id, label, description, indicator_query)
            VALUES (?, ?, ?, ?)
            """,
            (scenario_id, label, f"Scenario {scenario_id}: {label}",
             str(ind.get("indicator_query", ""))),
        )
        count += 1
    return count


def _persist_scenario_set(
    conn: sqlite3.Connection,
    dossier: dict,
    parsed: dict,
    horizon_date: str,
) -> tuple[int, int, int, int]:
    """Insert set + scenarios + linked predictions + watchlist items.

    Single transaction (CP-030): add_prediction runs with commit=False, one
    commit at the end; any failure rolls back the whole set so no partial
    set/scenario/prediction rows survive — and no leftover uncommitted rows
    can be swept into the next hotspot's commit on the same connection.

    Returns (set_id, scenarios_created, predictions_created, watchlist_created).
    """
    try:
        return _persist_scenario_set_inner(conn, dossier, parsed, horizon_date)
    except Exception:
        conn.rollback()
        raise


def _persist_scenario_set_inner(
    conn: sqlite3.Connection,
    dossier: dict,
    parsed: dict,
    horizon_date: str,
) -> tuple[int, int, int, int]:
    scenarios = parsed["scenarios"]
    _normalize_probabilities(scenarios)

    cur = conn.execute(
        """
        INSERT INTO scenario_sets (
            country, country_name, created_date, horizon_date, status,
            dossier_json, key_assumptions, summary
        ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            dossier["country"],
            dossier["country_name"],
            dossier["as_of"],
            horizon_date,
            json.dumps(dossier),
            json.dumps(parsed.get("key_assumptions", [])),
            str(parsed.get("summary", "")),
        ),
    )
    set_id = cur.lastrowid
    n_scen = n_pred = n_watch = 0

    for i, s in enumerate(scenarios):
        label = str(s.get("label") or chr(ord("A") + i))
        title = str(s.get("title", "")).strip() or f"Scenario {label}"
        cur = conn.execute(
            """
            INSERT INTO scenarios (
                set_id, label, title, description, probability,
                ach_evidence_json, invalidation, market_implications
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                set_id, label, title,
                str(s.get("description", "")),
                s["probability"],
                json.dumps(s.get("ach_ratings", {})),
                s.get("invalidation"),
                s.get("market_implications"),
            ),
        )
        scenario_id = cur.lastrowid
        n_scen += 1

        pred = add_prediction(
            conn,
            description=(
                f"Scenario {label} — {title} [{dossier['country_name']}] "
                f"entro {horizon_date}"
            ),
            probability=s["probability"],
            horizon_date=horizon_date,
            macro_area="world",
            prediction_type="geopolitical",
            domains=list(_SCENARIO_DOMAINS),
            primary_domain=_SCENARIO_DOMAINS[0],
            origin_scope=_valid_scope(s.get("origin_scope")),
            impact_scope=_valid_scope(s.get("impact_scope")),
            commit=False,
        )
        conn.execute(
            "UPDATE scenarios SET prediction_id = ? WHERE id = ?",
            (pred["id"], scenario_id),
        )
        n_pred += 1
        n_watch += _save_scenario_watchlist(conn, scenario_id, s.get("indicators", []))

    conn.commit()
    return set_id, n_scen, n_pred, n_watch


# ── query helpers ─────────────────────────────────────────────────────────────

def list_scenario_sets(
    conn: sqlite3.Connection, status: str | None = None
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM scenario_sets WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM scenario_sets ORDER BY id DESC").fetchall()


def get_scenario_set(conn: sqlite3.Connection, set_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scenario_sets WHERE id = ?", (set_id,)
    ).fetchone()


def get_scenarios(conn: sqlite3.Connection, set_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scenarios WHERE set_id = ? ORDER BY label ASC", (set_id,)
    ).fetchall()


# ── 3-4. generation pipeline ──────────────────────────────────────────────────

async def generate_scenarios(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    *,
    country: str | None = None,
    horizon_days: int | None = None,
    max_hotspots: int | None = None,
    as_of: date | None = None,
) -> ScenarioGenResult:
    """Generate conflict scenario sets for the top hotspots (or one country).

    One Claude call per hotspot — sized for the 2-3 reasoning tasks/day
    budget (default max_hotspots comes from settings, 2). A country with an
    already-active scenario set is skipped: the standing set gets updated via
    `review_scenarios`, not duplicated.

    Args:
        conn:         Open SQLite connection.
        llm_client:   Configured LLMClient (claude backend for real runs).
        country:      Force a specific GDELT FIPS country code (bypasses
                      hotspot ranking but still requires metrics for it).
        horizon_days: Scenario horizon (default: settings, 90).
        max_hotspots: How many hotspots to cover (default: settings, 2).
        as_of:        Evaluation date override (tests / retro-analysis).

    Returns:
        ScenarioGenResult with counters and per-hotspot skip reasons.
    """
    settings = get_settings()
    if horizon_days is None:
        horizon_days = settings.scenario_horizon_days
    if max_hotspots is None:
        max_hotspots = settings.scenario_max_hotspots
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    horizon_date = (as_of + timedelta(days=horizon_days)).isoformat()

    hotspots = compute_hotspots(conn, as_of=as_of)
    if country:
        country = country.upper()
        hotspots = [h for h in hotspots if h.country == country]
        if not hotspots:
            raise ValueError(
                f"No usable GDELT metrics for country '{country}' "
                f"(needs >= {MIN_WINDOW_EVENTS} events in the last {WINDOW_DAYS} days)."
            )
    logger.info(
        f"SCENARIO: {len(hotspots)} candidate hotspots, taking top {max_hotspots}"
    )

    result = ScenarioGenResult()
    for hotspot in hotspots[:max_hotspots]:
        active = conn.execute(
            "SELECT id FROM scenario_sets WHERE country = ? AND status = 'active'",
            (hotspot.country,),
        ).fetchone()
        if active:
            reason = (
                f"{hotspot.country_name}: active set id={active['id']} exists — "
                f"use `pathos scenario review` instead"
            )
            logger.info(f"SCENARIO: skip — {reason}")
            result.skipped.append(reason)
            continue

        dossier = build_dossier(conn, hotspot, as_of=as_of)
        messages = _generation_prompt(dossier, horizon_date)
        raw = await llm_client.complete(messages, json_mode=True)

        try:
            parsed = json.loads(raw)
            if not parsed.get("scenarios"):
                raise KeyError("scenarios")
        except (json.JSONDecodeError, KeyError) as exc:
            # Same contract as thesis.py: a refusal/parse failure is a logged
            # skip, never a crash that loses the other hotspots.
            reason = f"{hotspot.country_name}: LLM did not return scenarios JSON ({exc})"
            logger.warning(f"SCENARIO: {reason} — raw: {raw[:500]}")
            result.skipped.append(reason)
            continue

        set_id, n_scen, n_pred, n_watch = _persist_scenario_set(
            conn, dossier, parsed, horizon_date
        )
        result.sets_created += 1
        result.scenarios_created += n_scen
        result.predictions_created += n_pred
        result.watchlist_created += n_watch
        result.set_ids.append(set_id)
        logger.success(
            f"SCENARIO: set id={set_id} {hotspot.country_name} — "
            f"{n_scen} scenarios, {n_pred} predictions, {n_watch} indicators"
        )

    return result


# ── 5. review / update loop ───────────────────────────────────────────────────

def _match_indicators(
    conn: sqlite3.Connection, set_id: int, since_iso: str
) -> list[dict]:
    """Match active watchlist indicator queries against events seen since
    *since_iso*. A title matches when it contains at least half of the query
    terms (all of them for 1-2 term queries) — a deliberately simple keyword
    heuristic, same spirit as the ingest-side GDELT keyword filters.
    Triggered items are marked (status='triggered', triggered_at) so they
    fire once, not on every review.
    """
    items = conn.execute(
        """
        SELECT w.id, w.label, w.indicator_query, s.label AS scenario_label
        FROM watchlist_items w
        JOIN scenarios s ON s.id = w.scenario_id
        WHERE s.set_id = ? AND w.status = 'active' AND w.indicator_query != ''
        """,
        (set_id,),
    ).fetchall()
    if not items:
        return []

    # date() on both sides: last_seen formats vary by ingestor ('T' vs space
    # separator vs date-only) and lexicographic comparison across them drops
    # same-day events. Day granularity may re-scan same-day titles on the next
    # review, but items fire once (status flips to 'triggered' below).
    events = conn.execute(
        """
        SELECT title FROM events
        WHERE date(last_seen) >= date(?)
          AND (origin = 'rss' OR event_type = 'gdelt_anomaly')
        """,
        (since_iso,),
    ).fetchall()
    titles = [e["title"].lower() for e in events if e["title"]]

    now = datetime.now(timezone.utc).isoformat()
    triggered: list[dict] = []
    for item in items:
        terms = [t for t in item["indicator_query"].lower().split() if len(t) > 2]
        if not terms:
            continue
        needed = len(terms) if len(terms) <= 2 else math.ceil(len(terms) / 2)
        matches = [
            t for t in titles if sum(1 for term in terms if term in t) >= needed
        ]
        if matches:
            conn.execute(
                "UPDATE watchlist_items SET status = 'triggered', triggered_at = ? WHERE id = ?",
                (now, item["id"]),
            )
            triggered.append({
                "watchlist_id": item["id"],
                "scenario_label": item["scenario_label"],
                "label": item["label"],
                "matched_titles": "; ".join(matches[:3]),
            })
    if triggered:
        conn.commit()
    return triggered


async def review_scenarios(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    *,
    set_id: int | None = None,
    as_of: date | None = None,
) -> ScenarioReviewResult:
    """Superforecaster update pass over active scenario sets.

    Per set: recompute current hotspot metrics, check watchlist indicator
    triggers, then ONE Claude call revises the probability distribution.
    Revisions above _REVISION_EPSILON update both scenarios.probability and
    the linked prediction (via revise_prediction → auditable history).
    Sets past their horizon are flagged overdue — resolution stays human
    (`pathos scenario resolve`).
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()

    if set_id is not None:
        row = get_scenario_set(conn, set_id)
        if row is None:
            raise ValueError(f"Scenario set {set_id} not found.")
        if row["status"] != "active":
            raise ValueError(f"Scenario set {set_id} is not active (status={row['status']}).")
        sets = [row]
    else:
        sets = list_scenario_sets(conn, status="active")

    result = ScenarioReviewResult()
    hotspots = {h.country: h for h in compute_hotspots(conn, as_of=as_of)} if sets else {}

    for set_row in sets:
        if set_row["horizon_date"] < as_of.isoformat():
            # Past-horizon sets get flagged, never revised: a probability
            # changed after the window closed would contaminate the Brier /
            # time-adjusted score at resolution (post-horizon hindsight).
            result.overdue_set_ids.append(set_row["id"])
            logger.warning(
                f"SCENARIO: set id={set_row['id']} ({set_row['country_name']}) past "
                f"horizon {set_row['horizon_date']} — resolve it with "
                f"`pathos scenario resolve {set_row['id']} --winner <label>`"
            )
            continue

        scenarios = get_scenarios(conn, set_row["id"])
        if not scenarios:
            continue

        since = set_row["last_reviewed_at"] or set_row["created_at"]
        triggered = _match_indicators(conn, set_row["id"], since)
        result.indicators_triggered += len(triggered)

        messages = _review_prompt(
            set_row, scenarios, hotspots.get(set_row["country"]), triggered
        )
        raw = await llm_client.complete(messages, json_mode=True)
        try:
            revisions = {
                str(r["label"]): r
                for r in json.loads(raw)["revisions"]
                if isinstance(r, dict) and "label" in r
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                f"SCENARIO: review parse failed for set {set_row['id']} ({exc}) — "
                f"probabilities left unchanged. Raw: {raw[:500]}"
            )
            revisions = {}

        # Renormalize the full revised distribution before applying.
        if revisions:
            dist = []
            for s in scenarios:
                rev = revisions.get(s["label"])
                p = rev.get("probability") if rev else None
                dist.append({
                    "probability": p if isinstance(p, (int, float)) and p > 0
                    else s["probability"],
                })
            _normalize_probabilities(dist)
            for s, d in zip(scenarios, dist):
                new_p = d["probability"]
                if abs(new_p - s["probability"]) < _REVISION_EPSILON:
                    continue
                rationale = str(
                    (revisions.get(s["label"]) or {}).get("rationale", "")
                )[:500]
                conn.execute(
                    "UPDATE scenarios SET probability = ? WHERE id = ?",
                    (new_p, s["id"]),
                )
                if s["prediction_id"] is not None:
                    revise_prediction(conn, s["prediction_id"], new_p, rationale or None)
                result.probabilities_revised += 1
                logger.info(
                    f"SCENARIO: set {set_row['id']} [{s['label']}] "
                    f"p {s['probability']:.2f} → {new_p:.2f} — {rationale or 'n/d'}"
                )

        conn.execute(
            "UPDATE scenario_sets SET last_reviewed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), set_row["id"]),
        )
        conn.commit()
        result.sets_reviewed += 1

    return result


# ── 6. resolution ─────────────────────────────────────────────────────────────

def resolve_scenario_set(
    conn: sqlite3.Connection,
    set_id: int,
    winner_label: str,
    resolved_date: str | None = None,
) -> dict:
    """Resolve a set: *winner_label* materialized, siblings did not.

    Resolves every linked prediction through the existing predictions-v2
    engine (winner outcome_eventual=True, others False → Brier +
    time-adjusted scores). Marks scenarios.is_outcome and closes the set.
    """
    set_row = get_scenario_set(conn, set_id)
    if set_row is None:
        raise ValueError(f"Scenario set {set_id} not found.")
    if set_row["status"] == "resolved":
        raise ValueError(f"Scenario set {set_id} is already resolved.")

    scenarios = get_scenarios(conn, set_id)
    labels = [s["label"] for s in scenarios]
    if winner_label not in labels:
        raise ValueError(
            f"Winner '{winner_label}' not in set {set_id} (labels: {labels})."
        )
    if resolved_date is None:
        resolved_date = datetime.now(timezone.utc).date().isoformat()

    resolved_predictions = 0
    for s in scenarios:
        is_winner = s["label"] == winner_label
        conn.execute(
            "UPDATE scenarios SET is_outcome = ? WHERE id = ?",
            (int(is_winner), s["id"]),
        )
        if s["prediction_id"] is not None:
            resolve_prediction(
                conn, s["prediction_id"],
                outcome_eventual=is_winner,
                resolved_date=resolved_date,
            )
            resolved_predictions += 1

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE scenario_sets SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (now, set_id),
    )
    conn.execute(
        """
        UPDATE watchlist_items SET status = 'expired'
        WHERE status = 'active'
          AND scenario_id IN (SELECT id FROM scenarios WHERE set_id = ?)
        """,
        (set_id,),
    )
    conn.commit()

    logger.success(
        f"SCENARIO: set {set_id} resolved — winner [{winner_label}], "
        f"{resolved_predictions} predictions scored"
    )
    return {
        "set_id": set_id,
        "winner": winner_label,
        "resolved_date": resolved_date,
        "predictions_resolved": resolved_predictions,
    }
