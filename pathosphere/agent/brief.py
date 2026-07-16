"""
Morning brief generator.

Queries the DB for:
  - Recent RSS-clustered events, ranked by source coverage (CP-025 — always
    populated when RSS data exists, independent of narrative divergence)
  - High-divergence narrative clusters (divergence_score > 0.5)
  - Hub entities (highest-degree nodes in entity_links)
  - Recent physical / infrastructure anomaly events (portwatch, usgs, firms, ioda)

Generates a structured Markdown brief via the LLM client and persists it to:
  - data/briefs/YYYY-MM-DD.md  (filesystem)
  - briefs table               (SQLite, upsert on date)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from pathosphere.config import get_settings
from pathosphere.llm.client import LLMClient

# ── tuneable constants ─────────────────────────────────────────────────────────

_DIVERGENCE_THRESHOLD = 0.5
_MAX_DIVERGENCES = 8
_MAX_HUB_ENTITIES = 10
_MAX_ANOMALY_EVENTS = 12
_MAX_RECENT_EVENTS = 12
_ANOMALY_ORIGINS = ("portwatch", "usgs", "firms", "ioda")


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class BriefResult:
    date: str
    content: str
    file_path: Path
    brief_id: int
    event_count: int
    entity_count: int


# ── DB queries ────────────────────────────────────────────────────────────────

def _query_divergences(conn: sqlite3.Connection, lookback_days: int) -> list[dict]:
    """Return high-divergence narrative cluster rows from the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT
            e.id            AS event_id,
            e.title,
            e.event_type,
            e.location_name,
            e.last_seen,
            nd.block_a,
            nd.block_b,
            nd.divergence_score,
            nd.summary      AS divergence_summary
        FROM narrative_divergences nd
        JOIN events e ON e.id = nd.event_id
        WHERE nd.divergence_score > ?
          AND e.last_seen >= ?
        ORDER BY nd.divergence_score DESC, e.last_seen DESC
        LIMIT ?
        """,
        (_DIVERGENCE_THRESHOLD, cutoff, _MAX_DIVERGENCES),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_hub_entities(conn: sqlite3.Connection) -> list[dict]:
    """Return entities ordered by total co-occurrence link degree (both directions)."""
    rows = conn.execute(
        """
        WITH entity_degree AS (
            SELECT entity_a AS eid FROM entity_links
            UNION ALL
            SELECT entity_b AS eid FROM entity_links
        )
        SELECT
            e.id,
            e.name,
            e.entity_type,
            e.canonical_name,
            COUNT(*) AS degree
        FROM entity_degree ed
        JOIN entities e ON e.id = ed.eid
        GROUP BY e.id
        ORDER BY degree DESC
        LIMIT ?
        """,
        (_MAX_HUB_ENTITIES,),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_recent_events(conn: sqlite3.Connection, lookback_days: int) -> list[dict]:
    """Return recent RSS-clustered events, ranked by source coverage (doc
    count) then recency — independent of narrative_divergences.

    _query_divergences only surfaces events where TWO OR MORE geopolitical
    blocs covered the *same* clustered story with diverging framing
    (divergence_score > 0.5) — a narrow, often-empty signal (0 rows is a
    normal day, not missing data). Without this query the brief had no
    fallback source of real narrative content on such days: only entity
    co-occurrence degree (numbers, no story) and physical-sensor anomalies
    (earthquakes/fires/outages, not political/economic events) — see CP-025
    in CRITICAL_POINTS.md. Every RSS event that made it into a cluster is
    real reporting regardless of whether a divergence was detected.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT
            e.id AS event_id, e.title, e.event_type, e.location_name, e.last_seen,
            COUNT(ed.document_id) AS doc_count
        FROM events e
        JOIN event_documents ed ON ed.event_id = e.id
        WHERE e.origin = 'rss' AND e.last_seen >= ?
        GROUP BY e.id
        ORDER BY doc_count DESC, e.last_seen DESC
        LIMIT ?
        """,
        (cutoff, _MAX_RECENT_EVENTS),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_active_scenarios(conn: sqlite3.Connection) -> list[dict]:
    """Return active conflict scenario sets with their current probability
    distribution, so the brief reads new signals against the standing
    assessments (and can suggest which scenario they favor).

    Wrapped defensively: on a DB created before the scenario migration ran
    the query would fail — an empty section, not a dead brief, is correct.
    """
    try:
        sets = conn.execute(
            """
            SELECT id, country, country_name, horizon_date, summary
            FROM scenario_sets WHERE status = 'active'
            ORDER BY id DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for s in sets:
        scenarios = conn.execute(
            "SELECT label, title, probability FROM scenarios WHERE set_id = ? ORDER BY label",
            (s["id"],),
        ).fetchall()
        out.append({**dict(s), "scenarios": [dict(r) for r in scenarios]})
    return out


def _query_recent_anomalies(
    conn: sqlite3.Connection, lookback_days: int
) -> list[dict]:
    """Return recent physical / infrastructure events from sensor-based ingestors."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    placeholders = ",".join("?" * len(_ANOMALY_ORIGINS))
    rows = conn.execute(
        f"""
        SELECT
            id, title, event_type, origin, severity,
            location_name, last_seen, summary
        FROM events
        WHERE origin IN ({placeholders})
          AND last_seen >= ?
        ORDER BY severity DESC, last_seen DESC
        LIMIT ?
        """,
        (*_ANOMALY_ORIGINS, cutoff, _MAX_ANOMALY_EVENTS),
    ).fetchall()
    return [dict(r) for r in rows]


# ── prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(
    divergences: list[dict],
    hub_entities: list[dict],
    anomalies: list[dict],
    recent_events: list[dict],
    brief_date: str,
    active_scenarios: list[dict] | None = None,
) -> list[dict]:
    """Construct the chat-message list to send to the LLM."""
    system = (
        "You are a geopolitical intelligence analyst producing a morning brief "
        "for a single analyst. Be concise, analytical, and structured. "
        "Focus on causal connections and second-order effects. "
        "Output valid Markdown with clear section headers."
    )

    lines: list[str] = [
        f"# Intelligence Brief — {brief_date}",
        "",
        "## TASK",
        "Generate a structured morning intelligence brief from the signals below.",
        "For recent events, summarize the 2-4 most newsworthy and note why they matter.",
        "For each high-divergence event explain what the narrative gap implies.",
        "For hub entities flag any strategic significance of their centrality.",
        "For anomalies hypothesize the most likely cause and downstream impact.",
        "If active conflict scenarios are listed, note which scenario today's",
        "signals favor or undercut (do NOT re-assign probabilities here).",
        "End with a **SYNTHESIS** section: 2-3 key takeaways and watchlist updates.",
        "",
    ]

    if active_scenarios:
        lines += ["## ACTIVE CONFLICT SCENARIOS (standing assessments)", ""]
        for s in active_scenarios:
            dist = " | ".join(
                f"[{sc['label']}] {sc['title']} p={sc['probability']:.2f}"
                for sc in s["scenarios"]
            )
            lines.append(
                f"- **Set {s['id']} — {s['country_name'] or s['country']}** "
                f"(horizon {s['horizon_date']}): {dist}"
            )
            if s.get("summary"):
                lines.append(f"  _{s['summary']}_")
        lines.append("")

    if recent_events:
        lines += ["## RECENT EVENTS (top by source coverage)", ""]
        for e in recent_events:
            lines.append(
                f"- **Event {e['event_id']}**: {e['title']} | "
                f"type={e['event_type'] or 'unknown'} | "
                f"location={e['location_name'] or 'unknown'} | "
                f"sources={e['doc_count']} | "
                f"last_seen={e['last_seen']}"
            )
        lines.append("")

    if divergences:
        lines += ["## NARRATIVE DIVERGENCES (score > 0.5)", ""]
        for d in divergences:
            lines.append(
                f"- **Event {d['event_id']}**: {d['title']} | "
                f"score={d['divergence_score']:.2f} | "
                f"{d['block_a']} vs {d['block_b']} | "
                f"type={d['event_type'] or 'unknown'} | "
                f"location={d['location_name'] or 'unknown'} | "
                f"last_seen={d['last_seen']}"
            )
            if d.get("divergence_summary"):
                lines.append(f"  _{d['divergence_summary']}_")
        lines.append("")

    if hub_entities:
        lines += ["## HUB ENTITIES (by co-occurrence degree)", ""]
        for e in hub_entities:
            name = e.get("canonical_name") or e["name"]
            lines.append(f"- {name} [{e['entity_type']}] — degree={e['degree']}")
        lines.append("")

    if anomalies:
        lines += ["## PHYSICAL / INFRASTRUCTURE ANOMALIES", ""]
        for a in anomalies:
            lines.append(
                f"- **{a['origin'].upper()}** | {a['title']} | "
                f"severity={a['severity']} | "
                f"location={a['location_name'] or 'unknown'} | "
                f"last_seen={a['last_seen']}"
            )
            if a.get("summary"):
                lines.append(f"  _{a['summary']}_")
        lines.append("")

    if not divergences and not hub_entities and not anomalies and not recent_events:
        lines += [
            "## NOTE",
            "No significant signals found for this period.",
            "The database may be empty or the lookback window too short.",
            "Generate a brief acknowledging the absence of data and suggest",
            "running the ingestion cycle to populate the database.",
            "",
        ]

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ── persistence ───────────────────────────────────────────────────────────────

def _save_brief_file(content: str, brief_date: str, briefs_dir: Path) -> Path:
    """Write the brief Markdown to <briefs_dir>/YYYY-MM-DD.md."""
    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / f"{brief_date}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"BRIEF: file saved → {path}")
    return path


def _save_brief_db(
    conn: sqlite3.Connection,
    brief_date: str,
    content: str,
    event_count: int,
    entity_count: int,
) -> int:
    """Upsert the brief into the briefs table; return the row id."""
    conn.execute(
        """
        INSERT INTO briefs (date, content, event_count, entity_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            content      = excluded.content,
            event_count  = excluded.event_count,
            entity_count = excluded.entity_count,
            generated_at = datetime('now')
        """,
        (brief_date, content, event_count, entity_count),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM briefs WHERE date = ?", (brief_date,)
    ).fetchone()
    return row[0]


# ── public API ────────────────────────────────────────────────────────────────

async def generate_brief(
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    *,
    brief_date: str | None = None,
    lookback_days: int = 7,
    briefs_dir: Path | None = None,
) -> BriefResult:
    """Generate and persist a structured morning intelligence brief.

    Args:
        conn:         Open SQLite connection (briefs table must exist).
        llm_client:   Configured LLMClient instance.
        brief_date:   ISO date for this brief (default: today UTC).
        lookback_days: How far back to search for relevant events.
        briefs_dir:   Directory for .md files (default: data/briefs/).

    Returns:
        BriefResult with content, saved file path, DB id, and signal counts.
    """
    if brief_date is None:
        brief_date = date.today().isoformat()

    if briefs_dir is None:
        settings = get_settings()
        briefs_dir = settings.db_path.parent.parent / "briefs"

    logger.info(f"BRIEF: generating for {brief_date} (lookback={lookback_days}d)")

    divergences = _query_divergences(conn, lookback_days)
    hub_entities = _query_hub_entities(conn)
    anomalies = _query_recent_anomalies(conn, lookback_days)
    recent_events = _query_recent_events(conn, lookback_days)
    active_scenarios = _query_active_scenarios(conn)

    logger.info(
        f"BRIEF: {len(divergences)} divergences | "
        f"{len(hub_entities)} hub entities | "
        f"{len(anomalies)} anomalies | {len(recent_events)} recent events | "
        f"{len(active_scenarios)} active scenario sets"
    )

    messages = _build_prompt(
        divergences, hub_entities, anomalies, recent_events, brief_date,
        active_scenarios=active_scenarios,
    )
    content = await llm_client.complete(messages)

    # Dedup by event_id before counting: an RSS event can appear in both
    # divergences (score > 0.5) and recent_events (top by source coverage) —
    # summing raw list lengths double-counted it. anomalies pull from
    # disjoint origins (portwatch/usgs/firms/ioda vs rss) so they can't
    # collide with the other two, but included for correctness regardless.
    unique_event_ids = (
        {d["event_id"] for d in divergences}
        | {a["id"] for a in anomalies}
        | {e["event_id"] for e in recent_events}
    )
    total_events = len(unique_event_ids)

    file_path = _save_brief_file(content, brief_date, briefs_dir)
    brief_id = _save_brief_db(
        conn,
        brief_date=brief_date,
        content=content,
        event_count=total_events,
        entity_count=len(hub_entities),
    )

    logger.success(
        f"BRIEF: id={brief_id} | events={total_events} | "
        f"entities={len(hub_entities)} | file={file_path}"
    )

    return BriefResult(
        date=brief_date,
        content=content,
        file_path=file_path,
        brief_id=brief_id,
        event_count=total_events,
        entity_count=len(hub_entities),
    )
