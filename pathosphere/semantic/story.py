"""
Story linking — Fase 2 follow-up to clustering.

Complete-linkage clustering (cluster.py) intentionally produces small, tight
micro-events to avoid chain-collapse (study_13/study_14) — but a real,
sustained, multi-day, multi-angle story (e.g. a state funeral covered from
dozens of angles over a week) gets fragmented into many micro-events, since
no single embedding-similarity threshold holds across the full angle
diversity of genuine coverage.

link_related_events groups those micro-events into a macro-story using a
stronger signal than embedding similarity ALONE: a shared canonical PERSON
entity (after extract.py's canonicalize_person_entities has merged name
variants) within a time window, PLUS an embedding-similarity check between
the two events being linked.

Both signals are necessary. Entity+time alone is not enough: a globally
prominent person (a head of state) gets a passing one-line mention in dozens
of genuinely unrelated stories that merely happen to fall in the same news
cycle — e.g. "Trump" appearing once in a World Cup recap AND once in an
unrelated NATO summit report AND once in an oil-market piece. Empirically,
this produced a single 244-event mega-story on real data (all the "same
week" news, bridged solely through Trump mentions) — the same runaway
chain-collapse embedding-only clustering suffered from, just moved one layer
up. A first attempt gated merges on the SIMILARITY OF THE TWO TRIGGERING
EVENTS only (like average-linkage) and still produced a 206-event mega-story
— checking only the bridging pair misses that the two GROUPS being merged
may be incoherent overall, the exact average-linkage blind spot cluster.py
already had to fix once with true complete-linkage. The fix here is the
same: before merging group A into group B, require the MINIMUM pairwise
cosine similarity across every doc in A against every doc in B to clear the
floor (0.82) — not just the two trigger events' own similarity.

Non-destructive: sets events.story_id (self-referential pointer, same
COALESCE convention as entities.canonical_entity_id) rather than merging
event_documents — each micro-event's internal coherence stays inspectable.

Chain-collapse safety (time dimension): merging events pairwise-then-
transitively (A-B via person X, B-C via person Y) can silently produce a
group spanning far more than the time window, the same "bridging" bug
complete-linkage fixed for embeddings (cluster.py). Time is a 1-D ordered
quantity, so the fix is exact and cheap here: a merge is only allowed if the
RESULTING group's total span (max time - min time across all members) stays
within the window — since any two points within a bounded 1-D interval are
automatically within that bound of each other, this single span check is
equivalent to checking every pair.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from loguru import logger

DEFAULT_TIME_WINDOW_DAYS = 10.0
DEFAULT_EMBEDDING_SIMILARITY = 0.82


@dataclass
class StoryLinkResult:
    stories_formed: int = 0   # groups of >=2 micro-events merged into one story
    events_linked: int = 0    # total non-canonical events pointed at a story


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    # DB has a mix of naive (assume UTC) and aware timestamps depending on
    # ingest path — normalize so comparisons never raise.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find(parent: dict[int, int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _min_pairwise_similarity(
    docs_a: set[int], docs_b: set[int], embeddings: dict[int, np.ndarray]
) -> float | None:
    """Complete-linkage check: minimum cosine similarity across the full
    cross-product of two document sets. Embeddings are pre-normalized
    (embedder.py encodes with normalize_embeddings=True), so dot product
    IS cosine similarity — no norm division needed."""
    vecs_a = [embeddings[d] for d in docs_a if d in embeddings]
    vecs_b = [embeddings[d] for d in docs_b if d in embeddings]
    if not vecs_a or not vecs_b:
        return None
    sims = np.array(vecs_a) @ np.array(vecs_b).T
    return float(sims.min())


def link_related_events(
    conn: sqlite3.Connection,
    *,
    time_window_days: float = DEFAULT_TIME_WINDOW_DAYS,
    embedding_similarity: float = DEFAULT_EMBEDDING_SIMILARITY,
) -> StoryLinkResult:
    """Group micro-events sharing a canonical person entity within a time
    window AND clearing an embedding-similarity floor (see module docstring
    for why entity+time alone lets prominent hub figures over-merge)."""
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

    # Bulk-load doc membership and embeddings for every event that appears
    # in a candidate pair — group_docs tracks the FULL document set per
    # union-find root, so the embedding check below is true complete-linkage
    # (every doc in A vs every doc in B), not just the two trigger events.
    involved_events = {eid for pair in candidate_pairs for eid in pair}
    group_docs: dict[int, set[int]] = {}
    all_doc_ids: set[int] = set()
    for eid in involved_events:
        docs = {
            r["document_id"]
            for r in conn.execute(
                "SELECT document_id FROM event_documents WHERE event_id = ?", (eid,)
            ).fetchall()
        }
        group_docs[eid] = docs
        all_doc_ids |= docs

    doc_embeddings: dict[int, np.ndarray] = {}
    if all_doc_ids:
        placeholders = ",".join("?" * len(all_doc_ids))
        for r in conn.execute(
            f"SELECT document_id, embedding FROM vec_documents WHERE document_id IN ({placeholders})",
            tuple(all_doc_ids),
        ).fetchall():
            if r["embedding"]:
                doc_embeddings[r["document_id"]] = np.frombuffer(
                    r["embedding"], dtype=np.float32
                )

    def embedding_ok(ra: int, rb: int) -> bool:
        sim = _min_pairwise_similarity(group_docs[ra], group_docs[rb], doc_embeddings)
        return sim is not None and sim >= embedding_similarity

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
        if not embedding_ok(ra, rb):
            continue  # entity+time alone isn't enough (hub-figure false bridge);
            # true complete-linkage over the FULL groups, not just a-vs-b
        parent[rb] = ra
        group_span[ra] = (merged_lo, merged_hi)
        group_docs[ra] = group_docs[ra] | group_docs[rb]

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
