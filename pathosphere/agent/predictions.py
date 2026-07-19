"""
Predictions v2 — two-track forecasts with Tetlock-style calibration.

Tracks (`macro_area`):
  world    — geopolitical/political/social forecasts; scored with
             time_adjusted_score; requires origin/impact scope + domains.
  economic — financial forecasts tied to a thesis (and later a trade);
             scored with time_adjusted_score, P&L lives on the trade.

Operations on the `predictions` table:
  add_prediction     — insert a new forecast (+ prediction_domains rows)
  revise_prediction  — update probability, log to prediction_revisions
  list_predictions   — query with filters (open/resolved, macro_area, type, domain)
  get_prediction     — fetch single row
  get_prediction_domains — fetch domains for a prediction
  resolve_prediction — record outcome_eventual + resolved_date, compute
                       outcome_on_time, brier_score, time_adjusted_score
  get_calibration    — dual metric (time-adjusted + Brier) with breakdowns

Scoring:
  brier_score          = (probability - outcome_eventual)²   # direction quality
  time_adjusted_score  = 0 if the event never happened, else
                         (1 - brier) * max(0, 1 - alpha*|resolved - horizon| days)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from pathosphere.config import get_settings

VALID_MACRO_AREAS = ("world", "economic")

# world predictions carry geopolitical granularity; economic is 1:1
TYPES_BY_MACRO_AREA = {
    "world": ("geopolitical", "political", "social"),
    "economic": ("economic",),
}

VALID_DOMAINS = (
    "conflitto_armato",
    "tensione_militare",
    "politica_interna",
    "diplomazia",
    "commercio",
    "tecnologia",
    "infrastruttura",
    "finanza",
    "salute",
    "clima_risorse",
)

VALID_PREDICTION_TYPES = ("geopolitical", "political", "social", "economic")

VALID_SCOPES = ("locale", "nazionale", "regionale", "multilaterale", "globale")

# time_horizon_class thresholds (days from creation to horizon_date)
_HORIZON_BREVE_MAX_DAYS = 30
_HORIZON_MEDIO_MAX_DAYS = 180


# ── validation helpers ────────────────────────────────────────────────────────

def _validate_probability(probability: float) -> None:
    if not (0.0 <= probability <= 1.0):
        raise ValueError(f"probability must be 0.0–1.0, got {probability}")


def _parse_iso_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} must be ISO YYYY-MM-DD, got '{value}'")


def _validate_macro_area_type(macro_area: str, prediction_type: str) -> None:
    if macro_area not in VALID_MACRO_AREAS:
        raise ValueError(
            f"macro_area must be one of {VALID_MACRO_AREAS}, got '{macro_area}'"
        )
    allowed = TYPES_BY_MACRO_AREA[macro_area]
    if prediction_type not in allowed:
        raise ValueError(
            f"prediction_type '{prediction_type}' not valid for macro_area "
            f"'{macro_area}' (allowed: {allowed})"
        )


def _validate_scope(value: str | None, field: str) -> None:
    if value is not None and value not in VALID_SCOPES:
        raise ValueError(f"{field} must be one of {VALID_SCOPES}, got '{value}'")


def _validate_domains(domains: list[str]) -> None:
    for d in domains:
        if d not in VALID_DOMAINS:
            raise ValueError(f"domain '{d}' not in taxonomy {VALID_DOMAINS}")
    if len(set(domains)) != len(domains):
        raise ValueError(f"duplicate domains in {domains}")


# ── scoring helpers ───────────────────────────────────────────────────────────

def _compute_time_horizon_class(created: date, horizon: date) -> str:
    days = (horizon - created).days
    if days <= _HORIZON_BREVE_MAX_DAYS:
        return "breve"
    if days <= _HORIZON_MEDIO_MAX_DAYS:
        return "medio"
    return "lungo"


def _compute_time_adjusted_score(
    brier_score: float,
    outcome_eventual: bool,
    resolved: date,
    horizon: date,
    alpha: float,
) -> float:
    if not outcome_eventual:
        return 0.0
    days_delta = (resolved - horizon).days  # negative = early, positive = late
    timing_factor = max(0.0, 1.0 - alpha * abs(days_delta))
    return (1.0 - brier_score) * timing_factor


# ── query helpers ─────────────────────────────────────────────────────────────

def list_predictions(
    conn: sqlite3.Connection,
    only_open: bool = False,
    only_resolved: bool = False,
    macro_area: str | None = None,
    prediction_type: str | None = None,
    domain: str | None = None,
) -> list[sqlite3.Row]:
    """Return predictions ordered by horizon_date ASC then id ASC.

    only_open/only_resolved are mutually exclusive; if both False → all.
    macro_area / prediction_type / domain filters combine with AND.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if only_open:
        clauses.append("p.resolved = 0")
    elif only_resolved:
        clauses.append("p.resolved = 1")
    if macro_area is not None:
        clauses.append("p.macro_area = ?")
        params.append(macro_area)
    if prediction_type is not None:
        clauses.append("p.prediction_type = ?")
        params.append(prediction_type)
    if domain is not None:
        clauses.append(
            "p.id IN (SELECT prediction_id FROM prediction_domains WHERE domain = ?)"
        )
        params.append(domain)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"""
        SELECT p.*
        FROM predictions p
        {where}
        ORDER BY p.horizon_date ASC, p.id ASC
        """,
        params,
    ).fetchall()


def get_prediction(conn: sqlite3.Connection, prediction_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
    ).fetchone()


def get_prediction_domains(
    conn: sqlite3.Connection, prediction_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT domain, is_primary FROM prediction_domains
        WHERE prediction_id = ?
        ORDER BY is_primary DESC, domain ASC
        """,
        (prediction_id,),
    ).fetchall()


def get_prediction_revisions(
    conn: sqlite3.Connection, prediction_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, probability, rationale, revised_at
        FROM prediction_revisions
        WHERE prediction_id = ?
        ORDER BY id ASC
        """,
        (prediction_id,),
    ).fetchall()


# ── mutations ─────────────────────────────────────────────────────────────────

def add_prediction(
    conn: sqlite3.Connection,
    description: str,
    probability: float,
    horizon_date: str,
    macro_area: str,
    prediction_type: str,
    domains: list[str],
    primary_domain: str | None = None,
    origin_scope: str | None = None,
    impact_scope: str | None = None,
    thesis_id: int | None = None,
    trade_id: int | None = None,
    commit: bool = True,
) -> sqlite3.Row:
    """Insert a new prediction with its domains. Returns the inserted row.

    world    → origin_scope, impact_scope and at least one domain required.
    economic → thesis_id required; trade_id optional (set at trade open).
    commit=False lets a caller compose the insert into its own transaction
    (e.g. scenario-set persistence, CP-030) — the caller then owns commit/rollback.
    Raises ValueError on any invalid or incoherent field.
    """
    _validate_probability(probability)
    horizon = _parse_iso_date(horizon_date, "horizon_date")
    _validate_macro_area_type(macro_area, prediction_type)

    if not description or not description.strip():
        raise ValueError("description must not be empty")

    if not domains:
        raise ValueError("at least one domain is required")
    _validate_domains(domains)
    primary = primary_domain or domains[0]
    if primary not in domains:
        raise ValueError(f"primary_domain '{primary}' must be one of {domains}")

    if macro_area == "world":
        if origin_scope is None or impact_scope is None:
            raise ValueError("origin_scope and impact_scope are required for macro_area='world'")
        if trade_id is not None:
            raise ValueError("trade_id is only valid for macro_area='economic'")
    else:  # economic
        if thesis_id is None:
            raise ValueError("thesis_id is required for macro_area='economic'")
    _validate_scope(origin_scope, "origin_scope")
    _validate_scope(impact_scope, "impact_scope")

    today = datetime.now(timezone.utc).date()
    horizon_class = _compute_time_horizon_class(today, horizon)

    cur = conn.execute(
        """
        INSERT INTO predictions (
            thesis_id, description, probability, horizon_date,
            macro_area, prediction_type, origin_scope, impact_scope,
            time_horizon_class, trade_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (thesis_id, description.strip(), probability, horizon_date,
         macro_area, prediction_type, origin_scope, impact_scope,
         horizon_class, trade_id),
    )
    prediction_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO prediction_domains (prediction_id, domain, is_primary) VALUES (?, ?, ?)",
        [(prediction_id, d, 1 if d == primary else 0) for d in domains],
    )
    if commit:
        conn.commit()
    return get_prediction(conn, prediction_id)  # type: ignore[arg-type]


def _get_open_prediction(conn: sqlite3.Connection, prediction_id: int) -> sqlite3.Row:
    pred = get_prediction(conn, prediction_id)
    if pred is None:
        raise ValueError(f"Prediction {prediction_id} not found.")
    if pred["resolved"]:
        raise ValueError(f"Prediction {prediction_id} is already resolved.")
    return pred


def revise_prediction(
    conn: sqlite3.Connection,
    prediction_id: int,
    probability: float,
    rationale: str | None = None,
) -> sqlite3.Row:
    """Update probability and log the revision (Superforecaster pattern).

    Raises ValueError if prediction not found or already resolved.
    """
    _validate_probability(probability)
    _get_open_prediction(conn, prediction_id)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE predictions SET probability = ? WHERE id = ?",
        (probability, prediction_id),
    )
    conn.execute(
        """
        INSERT INTO prediction_revisions (prediction_id, probability, rationale, revised_at)
        VALUES (?, ?, ?, ?)
        """,
        (prediction_id, probability, rationale, now),
    )
    conn.commit()
    return get_prediction(conn, prediction_id)  # type: ignore[return-value]


def resolve_prediction(
    conn: sqlite3.Connection,
    prediction_id: int,
    outcome_eventual: bool,
    resolved_date: str,
    alpha: float | None = None,
) -> sqlite3.Row:
    """Resolve a prediction with timing-aware scoring. Returns updated row.

    outcome_eventual — did the event ever happen (timing-independent).
    resolved_date    — actual date the event happened, or the evaluation
                       date when it never did (ISO YYYY-MM-DD).
    alpha            — timing penalty per day [default: settings]. NB: changing
                       alpha makes new scores incomparable with old ones.
    Derives outcome_on_time, brier_score (vs outcome_eventual) and
    time_adjusted_score. Legacy `outcome` mirrors outcome_on_time.
    Raises ValueError if prediction not found or already resolved.
    """
    resolved = _parse_iso_date(resolved_date, "resolved_date")
    pred = _get_open_prediction(conn, prediction_id)

    horizon = _parse_iso_date(pred["horizon_date"], "horizon_date")
    outcome_on_time = outcome_eventual and resolved <= horizon
    outcome_float = 1.0 if outcome_eventual else 0.0
    brier_score = (pred["probability"] - outcome_float) ** 2

    if alpha is None:
        alpha = get_settings().timing_penalty_alpha
    time_adjusted = _compute_time_adjusted_score(
        brier_score, outcome_eventual, resolved, horizon, alpha
    )
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        UPDATE predictions
        SET resolved = 1,
            outcome = ?,
            outcome_eventual = ?,
            outcome_on_time = ?,
            resolved_date = ?,
            brier_score = ?,
            time_adjusted_score = ?,
            resolved_at = ?
        WHERE id = ?
        """,
        (int(outcome_on_time), int(outcome_eventual), int(outcome_on_time),
         resolved_date, brier_score, time_adjusted, now, prediction_id),
    )
    conn.commit()
    return get_prediction(conn, prediction_id)  # type: ignore[return-value]


# ── thesis integration ────────────────────────────────────────────────────────

def create_thesis_prediction(
    conn: sqlite3.Connection,
    thesis: sqlite3.Row,
    default_probability: float = 0.5,
    default_horizon_days: int = 30,
) -> sqlite3.Row:
    """Create the auto economic prediction for an approved thesis.

    Makes the geopolitical→thesis→trade→economic chain measurable end to end.
    Defensive against LLM-generated theses: probability clamped to [0,1],
    NULL confidence/horizon_days/instrument handled.
    """
    from datetime import timedelta

    horizon_days = (thesis["horizon_days"]
                    if thesis["horizon_days"] is not None else default_horizon_days)
    horizon_date = (datetime.now(timezone.utc).date()
                    + timedelta(days=horizon_days)).isoformat()
    confidence = thesis["confidence"]
    probability = (min(max(confidence, 0.0), 1.0)
                   if confidence is not None else default_probability)
    instrument = " ".join(
        p for p in (thesis["instrument"], thesis["direction"]) if p
    ) or "strumento n/d"

    return add_prediction(
        conn,
        description=f"{instrument} entro {horizon_days}gg — {thesis['title']}",
        probability=probability,
        horizon_date=horizon_date,
        macro_area="economic",
        prediction_type="economic",
        domains=["finanza"],
        thesis_id=thesis["id"],
    )


def link_thesis_prediction_to_trade(
    conn: sqlite3.Connection,
    thesis_id: int,
    trade_id: int,
) -> int | None:
    """Link the thesis's oldest open, unlinked economic prediction to a trade.

    Targets exactly one row (the auto-created prediction is the oldest);
    manually added or resolved predictions are never claimed.
    Returns the linked prediction id, or None if nothing to link.
    """
    row = conn.execute(
        """
        SELECT MIN(id) AS id FROM predictions
        WHERE thesis_id = ? AND macro_area = 'economic'
          AND trade_id IS NULL AND resolved = 0
        """,
        (thesis_id,),
    ).fetchone()
    if row is None or row["id"] is None:
        return None
    conn.execute(
        "UPDATE predictions SET trade_id = ? WHERE id = ?", (trade_id, row["id"])
    )
    conn.commit()
    return row["id"]


# ── calibration ───────────────────────────────────────────────────────────────

_BUCKETS = [
    ("0-20%",  0.0, 0.2),
    ("20-40%", 0.2, 0.4),
    ("40-60%", 0.4, 0.6),
    ("60-80%", 0.6, 0.8),
    ("80-100%", 0.8, 1.0),
]


def _effective_outcome(row: sqlite3.Row) -> int | None:
    """Eventual outcome, falling back to legacy `outcome` for pre-v2 rows."""
    return row["outcome_eventual"] if row["outcome_eventual"] is not None else row["outcome"]


def _aggregate(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Dual-metric aggregate over resolved rows; NULL scores excluded per metric."""
    briers = [r["brier_score"] for r in rows if r["brier_score"] is not None]
    tas = [r["time_adjusted_score"] for r in rows if r["time_adjusted_score"] is not None]
    return {
        "count": len(rows),
        "mean_brier_score": sum(briers) / len(briers) if briers else None,
        "mean_time_adjusted_score": sum(tas) / len(tas) if tas else None,
    }


def get_calibration(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dual-metric calibration over all resolved predictions.

    time_adjusted_score is the primary operational metric; brier_score is
    kept for Tetlock comparability (pre-v2 rows only have Brier).
    Breakdown per probability bucket, macro_area and prediction_type.
    """
    rows = conn.execute(
        """
        SELECT probability, outcome, outcome_eventual, brier_score,
               time_adjusted_score, macro_area, prediction_type
        FROM predictions WHERE resolved = 1
        """
    ).fetchall()

    overall = _aggregate(rows)

    buckets = []
    for label, lo, hi in _BUCKETS:
        # last bucket is inclusive on both ends (probability == 1.0 lands here)
        if hi == 1.0:
            bucket_rows = [r for r in rows if lo <= r["probability"] <= hi]
        else:
            bucket_rows = [r for r in rows if lo <= r["probability"] < hi]

        agg = _aggregate(bucket_rows)
        # accuracy vs eventual outcome — same event brier_score is computed on
        accuracy = (sum(1 for r in bucket_rows if _effective_outcome(r) == 1)
                    / agg["count"] if agg["count"] else None)
        buckets.append({
            "label": label, "min": lo, "max": hi, "count": agg["count"],
            "mean_brier": agg["mean_brier_score"],
            "mean_time_adjusted_score": agg["mean_time_adjusted_score"],
            "accuracy": accuracy,
        })

    by_macro = {
        area: _aggregate([r for r in rows if r["macro_area"] == area])
        for area in VALID_MACRO_AREAS
        if any(r["macro_area"] == area for r in rows)
    }
    by_type = {
        ptype: _aggregate([r for r in rows if r["prediction_type"] == ptype])
        for ptype in VALID_PREDICTION_TYPES
        if any(r["prediction_type"] == ptype for r in rows)
    }

    return {
        "total_resolved": len(rows),
        "mean_brier_score": overall["mean_brier_score"],
        "mean_time_adjusted_score": overall["mean_time_adjusted_score"],
        "buckets": buckets,
        "by_macro_area": by_macro,
        "by_prediction_type": by_type,
    }
