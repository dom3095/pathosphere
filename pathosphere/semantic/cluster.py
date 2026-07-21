"""
Event clustering — Phase 2.

Groups non-duplicate embedded docs into events via greedy complete-linkage union-find:
  1. Load candidates (embedded, non-duplicate, not yet in an event, within time window)
  2. For each candidate, find similar neighbours via sqlite-vec KNN
  3. Cheap centroid pre-filter (loose threshold) skips obviously-unrelated clusters
  4. True complete-linkage check: merging cluster A into cluster B requires EVERY
     member of A within threshold of EVERY member of B (not just the bridging doc)
  5. Union-find to merge connected components
  6. Each component → one event row + event_documents entries

Similarity threshold 0.85 (KNN neighbor search). Complete-linkage coherence (0.88)
prevents a single "bridging" document from welding two unrelated clusters together:
average-linkage (checking only the new doc against the target centroid) allows doc D
to pass against both cluster A's centroid and cluster B's centroid independently, then
silently merge A and B wholesale even though A's and B's other members were never
checked against each other. Complete-linkage closes this by checking the full
cross-product of both clusters' members before any merge.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from loguru import logger

DEFAULT_TIME_WINDOW_H = 72
DEFAULT_SIMILARITY = 0.85  # KNN neighbor threshold
DEFAULT_COHERENCE_SIMILARITY = 0.88  # complete-linkage gate: max pairwise dist across both clusters
DEFAULT_PREFILTER_SIMILARITY = 0.75  # cheap centroid pre-filter, looser than the real gate
DEFAULT_KNN = 20
DEFAULT_MAX_CLUSTER_SIZE = 30  # cap to prevent runaway clusters (kept as defense-in-depth)


@dataclass
class ClusterResult:
    events_created: int = 0
    docs_assigned: int = 0
    coherence_rejections: int = 0  # cluster-pairs rejected by the complete-linkage gate


def _l2_threshold(cosine_similarity: float) -> float:
    return math.sqrt(2 * (1 - cosine_similarity))


def _find(parent: dict, x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _complete_linkage_max_dist(
    embeddings: dict[int, np.ndarray],
    members_a: set[int],
    members_b: set[int],
) -> float:
    """Max pairwise L2 distance across two clusters' full membership (complete-linkage)."""
    max_dist = 0.0
    for a in members_a:
        emb_a = embeddings[a]
        for b in members_b:
            dist = np.linalg.norm(emb_a - embeddings[b])
            if dist > max_dist:
                max_dist = dist
    return max_dist


def _union(
    parent: dict,
    size: dict,
    x: int,
    y: int,
    max_size: int,
    cluster_members: dict[int, set[int]],
    centroids: dict[int, np.ndarray],
) -> bool:
    """Union two clusters if coherent, return True if merged."""
    px, py = _find(parent, x), _find(parent, y)
    if px == py:
        return False
    if size[px] + size[py] > max_size:
        return False

    # Merge smaller into larger
    if size[px] < size[py]:
        px, py = py, px

    parent[py] = px
    size[px] += size[py]
    cluster_members[px] = cluster_members[px] | cluster_members[py]
    centroids[px] = (centroids[px] * (size[px] - size[py]) + centroids[py] * size[py]) / size[px]

    del cluster_members[py]
    del centroids[py]
    return True


def cluster_documents(
    conn: sqlite3.Connection,
    *,
    time_window_hours: float = DEFAULT_TIME_WINDOW_H,
    similarity: float = DEFAULT_SIMILARITY,
    knn: int = DEFAULT_KNN,
    max_cluster_size: int = DEFAULT_MAX_CLUSTER_SIZE,
) -> ClusterResult:
    """Group recent non-duplicate docs into events via complete-linkage clustering."""
    result = ClusterResult()
    l2_thresh = _l2_threshold(similarity)
    l2_thresh_coherence = _l2_threshold(DEFAULT_COHERENCE_SIMILARITY)
    l2_thresh_prefilter = _l2_threshold(DEFAULT_PREFILTER_SIMILARITY)
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(hours=time_window_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    candidates = conn.execute(
        """
        SELECT r.id, r.title, r.origin, COALESCE(r.published_at, r.fetched_at) AS pub_at
        FROM raw_documents r
        WHERE r.embedded = 1
          AND r.is_duplicate = 0
          AND (r.origin IS NULL OR r.origin != 'gdelt')
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
    cluster_members: dict[int, set[int]] = {d: {d} for d in doc_ids}
    centroids: dict[int, np.ndarray] = {}

    # Load all embeddings into memory for centroid computation
    embeddings: dict[int, np.ndarray] = {}
    for row in candidates:
        vec_row = conn.execute(
            "SELECT embedding FROM vec_documents WHERE document_id = ?", (row["id"],)
        ).fetchone()
        if vec_row:
            emb_bytes = vec_row["embedding"]
            # sqlite-vec stores embeddings as 4-byte float vectors; deserialize
            emb = np.frombuffer(emb_bytes, dtype=np.float32).copy()
            embeddings[row["id"]] = emb
            centroids[row["id"]] = emb.copy()

    logger.info(
        f"Clustering {len(doc_ids)} candidate docs with complete-linkage coherence check"
    )

    for row in candidates:
        doc_id = row["id"]
        if doc_id not in embeddings:
            continue

        try:
            neighbors = conn.execute(
                """
                SELECT document_id, distance
                FROM vec_documents
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (embeddings[doc_id].tobytes(), knn),
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
            if nb_id not in id_set:
                continue

            nb_root = _find(parent, nb_id)
            doc_root = _find(parent, doc_id)
            if nb_root == doc_root:
                continue  # Already same cluster

            # Cheap pre-filter: doc vs nb's cluster centroid, loose threshold.
            # Purely a performance shortcut to skip obviously-unrelated clusters
            # before paying the O(|A|*|B|) exact check below — not the real gate.
            centroid = centroids[nb_root]
            dist_to_centroid = np.linalg.norm(embeddings[doc_id] - centroid)
            if dist_to_centroid > l2_thresh_prefilter:
                continue

            # Complete-linkage gate: every member of doc's cluster must be within
            # threshold of every member of nb's cluster. Prevents a single bridging
            # doc from welding two unrelated clusters together (average-linkage's
            # blind spot: checking only doc-vs-centroid on each side independently).
            max_dist = _complete_linkage_max_dist(
                embeddings, cluster_members[doc_root], cluster_members[nb_root]
            )
            if max_dist > l2_thresh_coherence:
                result.coherence_rejections += 1
                continue

            # Merge
            merged = _union(parent, size, doc_id, nb_id, max_cluster_size,
                          cluster_members, centroids)
            if not merged:
                continue

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
        f"Cluster complete: {result.events_created} events, {result.docs_assigned} docs, "
        f"{result.coherence_rejections} rejected (complete-linkage gate failed)"
    )
    return result
