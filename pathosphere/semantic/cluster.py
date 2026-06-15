"""
Event clustering — Phase 2.

Groups non-duplicate embedded docs into events via greedy union-find:
  1. Load candidates (embedded, non-duplicate, not yet in an event, within time window)
  2. For each candidate, find similar neighbours via sqlite-vec KNN
  3. Union-find to merge connected components
  4. Each component → one event row + event_documents entries

Similarity threshold 0.75 (lower than dedup 0.92) to group loosely related
articles about the same story.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

DEFAULT_TIME_WINDOW_H = 72
DEFAULT_SIMILARITY = 0.85  # 0.75 caused full-chain collapse; 0.85 separates stories
DEFAULT_KNN = 20
DEFAULT_MAX_CLUSTER_SIZE = 30  # cap to prevent runaway single-linkage chaining


@dataclass
class ClusterResult:
    events_created: int = 0
    docs_assigned: int = 0


def _l2_threshold(cosine_similarity: float) -> float:
    return math.sqrt(2 * (1 - cosine_similarity))


def _find(parent: dict, x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: dict, size: dict, x: int, y: int, max_size: int) -> None:
    px, py = _find(parent, x), _find(parent, y)
    if px == py:
        return
    if size[px] + size[py] > max_size:
        return
    if size[px] < size[py]:
        px, py = py, px
    parent[py] = px
    size[px] += size[py]


def cluster_documents(
    conn: sqlite3.Connection,
    *,
    time_window_hours: float = DEFAULT_TIME_WINDOW_H,
    similarity: float = DEFAULT_SIMILARITY,
    knn: int = DEFAULT_KNN,
    max_cluster_size: int = DEFAULT_MAX_CLUSTER_SIZE,
) -> ClusterResult:
    """Group recent non-duplicate docs into event records."""
    result = ClusterResult()
    l2_thresh = _l2_threshold(similarity)
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(hours=time_window_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    candidates = conn.execute(
        """
        SELECT r.id, r.title, r.origin, COALESCE(r.published_at, r.fetched_at) AS pub_at
        FROM raw_documents r
        WHERE r.embedded = 1
          AND r.is_duplicate = 0
          AND COALESCE(r.published_at, r.fetched_at) >= ?
          AND r.id NOT IN (SELECT document_id FROM event_documents)
        ORDER BY pub_at ASC
        """,
        (cutoff,),
    ).fetchall()

    if not candidates:
        return result

    doc_ids = [r["id"] for r in candidates]
    id_set = set(doc_ids)
    parent = {d: d for d in doc_ids}
    size = {d: 1 for d in doc_ids}
    id_to_row = {r["id"]: r for r in candidates}

    logger.info(f"Clustering {len(doc_ids)} candidate docs")

    for row in candidates:
        doc_id = row["id"]
        vec_row = conn.execute(
            "SELECT embedding FROM vec_documents WHERE document_id = ?", (doc_id,)
        ).fetchone()
        if not vec_row:
            continue

        try:
            neighbors = conn.execute(
                """
                SELECT document_id, distance
                FROM vec_documents
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (vec_row["embedding"], knn),
            ).fetchall()
        except Exception as exc:
            logger.warning(f"KNN failed for doc {doc_id}: {exc}")
            continue

        for nb in neighbors:
            nb_id = nb["document_id"]
            if nb_id == doc_id:
                continue
            if nb["distance"] > l2_thresh:
                break
            if nb_id in id_set:
                _union(parent, size, doc_id, nb_id, max_cluster_size)

    # Build components
    components: dict[int, list[int]] = {}
    for doc_id in doc_ids:
        root = _find(parent, doc_id)
        components.setdefault(root, []).append(doc_id)

    # Create one event per component — single transaction for all clusters
    with conn:
        for cluster_ids in components.values():
            cluster_rows = [id_to_row[i] for i in cluster_ids]
            # Title from oldest doc with a non-empty title
            title = next(
                (r["title"] for r in cluster_rows if r["title"]),
                f"Event {cluster_ids[0]}",
            )
            pub_times = [r["pub_at"] for r in cluster_rows if r["pub_at"]]
            first_seen = min(pub_times) if pub_times else cutoff
            last_seen = max(pub_times) if pub_times else cutoff
            # Majority origin among cluster docs
            origins = [r["origin"] for r in cluster_rows if r["origin"]]
            from collections import Counter
            origin = Counter(origins).most_common(1)[0][0] if origins else "rss"

            cur = conn.execute(
                "INSERT INTO events (title, first_seen, last_seen, origin) VALUES (?, ?, ?, ?)",
                (title, first_seen, last_seen, origin),
            )
            event_id = cur.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO event_documents (event_id, document_id) VALUES (?, ?)",
                [(event_id, did) for did in cluster_ids],
            )
            result.events_created += 1
            result.docs_assigned += len(cluster_ids)

    logger.info(
        f"Cluster complete: {result.events_created} events, {result.docs_assigned} docs"
    )
    return result
