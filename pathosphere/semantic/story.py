"""
Story linking — Fase 2 follow-up to clustering.

Complete-linkage clustering (cluster.py) intentionally produces small, tight
micro-events to avoid chain-collapse (study_13/study_14) — but a real,
sustained, multi-day, multi-angle story (e.g. a state funeral covered from
dozens of angles over a week) gets fragmented into many micro-events, since
no single embedding-similarity threshold holds across the full angle
diversity of genuine coverage.

link_related_events groups those micro-events into a macro-story using a
stronger signal than embedding similarity: a shared canonical PERSON entity
(after extract.py's canonicalize_person_entities has merged name variants)
within a time window. Person entities are specific enough that shared
mentions reliably indicate the same real story, unlike generic entities
(countries, common org names) which would cause runaway over-merging.

Non-destructive: sets events.story_id (self-referential pointer, same
COALESCE convention as entities.canonical_entity_id) rather than merging
event_documents — each micro-event's internal coherence stays inspectable.

Chain-collapse safety: merging events pairwise-then-transitively (A-B via
person X, B-C via person Y) can silently produce a group spanning far more
than the time window, the same "bridging" bug complete-linkage fixed for
embeddings (cluster.py). Time is a 1-D ordered quantity, so the fix is exact
and cheap here: a merge is only allowed if the RESULTING group's total span
(max time - min time across all members) stays within the window — since any
two points within a bounded 1-D interval are automatically within that bound
of each other, this single span check is equivalent to checking every pair.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

DEFAULT_TIME_WINDOW_DAYS = 10.0


@dataclass
class StoryLinkResult:
    stories_formed: int = 0   # groups of >=2 micro-events merged into one story
    events_linked: int = 0    # total non-canonical events pointed at a story


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find(parent: dict[int, int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def link_related_events(
    conn: sqlite3.Connection,
    *,
    time_window_days: float = DEFAULT_TIME_WINDOW_DAYS,
) -> StoryLinkResult:
    """Group micro-events sharing a canonical person entity within a time window."""
    result = StoryLinkResult()
    window = timedelta(days=time_window_days)

    rows = conn.execute(
        """
        SELECT DISTINCT
            COALESCE(e.canonical_entity_id, e.id) AS person_id,
            ed.event_id,
            ev.first_seen,
            ev.last_seen
        FROM document_entities de
        JOIN entities e ON e.id = de.entity_id
        JOIN event_documents ed ON ed.document_id = de.document_id
        JOIN events ev ON ev.id = ed.event_id
        WHERE e.entity_type = 'person' AND ev.story_id IS NULL
        """
    ).fetchall()
    if not rows:
        return result

    event_span: dict[int, tuple[datetime, datetime]] = {}
    person_to_events: dict[int, set[int]] = {}
    for row in rows:
        eid = row["event_id"]
        start = _parse_dt(row["first_seen"])
        end = _parse_dt(row["last_seen"]) or start
        if start is None:
            continue
        if eid not in event_span:
            event_span[eid] = (start, end)
        person_to_events.setdefault(row["person_id"], set()).add(eid)

    all_event_ids = set(event_span.keys())
    if len(all_event_ids) < 2:
        return result

    # Candidate pairs: any two events sharing >=1 common person entity.
    # Sorted by temporal proximity so tight, obviously-same-story pairs merge
    # before looser ones — reduces the chance an early wide merge blocks a
    # later tight one that would otherwise have gone through.
    candidate_pairs: set[tuple[int, int]] = set()
    for event_ids in person_to_events.values():
        if len(event_ids) < 2:
            continue
        ids = sorted(event_ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                candidate_pairs.add((ids[i], ids[j]))

    def pair_gap(a: int, b: int) -> timedelta:
        a_lo, a_hi = event_span[a]
        b_lo, b_hi = event_span[b]
        latest_start = max(a_lo, b_lo)
        earliest_end = min(a_hi, b_hi)
        return max(timedelta(0), latest_start - earliest_end)

    sorted_pairs = sorted(candidate_pairs, key=lambda p: pair_gap(*p))

    parent = {eid: eid for eid in all_event_ids}
    group_span: dict[int, tuple[datetime, datetime]] = dict(event_span)

    for a, b in sorted_pairs:
        ra, rb = _find(parent, a), _find(parent, b)
        if ra == rb:
            continue
        a_lo, a_hi = group_span[ra]
        b_lo, b_hi = group_span[rb]
        merged_lo, merged_hi = min(a_lo, b_lo), max(a_hi, b_hi)
        if merged_hi - merged_lo > window:
            continue  # would exceed the time window — reject this merge
        parent[rb] = ra
        group_span[ra] = (merged_lo, merged_hi)

    components: dict[int, list[int]] = {}
    for eid in all_event_ids:
        root = _find(parent, eid)
        components.setdefault(root, []).append(eid)

    with conn:
        for members in components.values():
            if len(members) < 2:
                continue
            doc_counts = {
                eid: conn.execute(
                    "SELECT COUNT(*) as c FROM event_documents WHERE event_id = ?", (eid,)
                ).fetchone()["c"]
                for eid in members
            }
            canonical = max(
                members,
                key=lambda e: (doc_counts[e], -event_span[e][0].timestamp()),
            )
            for eid in members:
                if eid == canonical:
                    continue
                conn.execute("UPDATE events SET story_id = ? WHERE id = ?", (canonical, eid))
                result.events_linked += 1
            result.stories_formed += 1

    logger.info(
        f"Story linking: {result.stories_formed} stories formed, "
        f"{result.events_linked} events linked"
    )
    return result
