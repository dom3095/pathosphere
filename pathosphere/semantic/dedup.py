"""
Semantic dedup — Phase 2.

For each embedded doc not yet dedup-checked, finds nearest neighbours via
sqlite-vec KNN. If a neighbour with lower ID (older/canonical) is within
the cosine similarity threshold and time window, marks the current doc as
is_duplicate=1.

Unit vectors: L2_dist = sqrt(2*(1-cos_sim)), so cos_sim=0.92 → L2≈0.4.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

DEFAULT_SIMILARITY = 0.92
DEFAULT_TIME_WINDOW_H = 72
DEFAULT_KNN = 20
BATCH_SIZE = 32


@dataclass
class DedupResult:
    docs_checked: int = 0
    duplicates_found: int = 0


def _l2_threshold(cosine_similarity: float) -> float:
    return math.sqrt(2 * (1 - cosine_similarity))


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def dedup_documents(
    conn: sqlite3.Connection,
    *,
    similarity: float = DEFAULT_SIMILARITY,
    time_window_hours: float = DEFAULT_TIME_WINDOW_H,
    knn: int = DEFAULT_KNN,
    batch_size: int = BATCH_SIZE,
) -> DedupResult:
    """Mark near-duplicates among embedded, unchecked docs.

    Commits per batch (CP-012), same pattern as embedder.py, instead of one
    transaction for the whole run — on a large backfill (169k docs, brute-
    force KNN, hours), a Ctrl+C or crash mid-run only loses the in-flight
    batch: docs already committed with dedup_checked=1 are not re-processed
    on retry, since the initial SELECT's `WHERE dedup_checked = 0` filter is
    re-evaluated fresh on the next call.
    """
    result = DedupResult()
    l2_thresh = _l2_threshold(similarity)
    time_delta = timedelta(hours=time_window_hours)

    # Oldest first: lower ID becomes canonical
    rows = conn.execute(
        """
        SELECT id, published_at
        FROM raw_documents
        WHERE embedded = 1 AND is_duplicate = 0 AND dedup_checked = 0
        ORDER BY published_at ASC, id ASC
        """
    ).fetchall()

    if not rows:
        return result

    logger.info(f"Dedup: checking {len(rows)} docs (similarity≥{similarity})")

    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]

        with conn:
            for row in batch:
                doc_id = row["id"]
                doc_pub = _parse_dt(row["published_at"])

                vec_row = conn.execute(
                    "SELECT embedding FROM vec_documents WHERE document_id = ?", (doc_id,)
                ).fetchone()

                if vec_row is None:
                    conn.execute(
                        "UPDATE raw_documents SET dedup_checked = 1 WHERE id = ?", (doc_id,)
                    )
                    result.docs_checked += 1
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
                    logger.warning(f"KNN query failed for doc {doc_id}: {exc}")
                    conn.execute(
                        "UPDATE raw_documents SET dedup_checked = 1 WHERE id = ?", (doc_id,)
                    )
                    result.docs_checked += 1
                    continue

                marked = False
                for nb in neighbors:
                    nb_id = nb["document_id"]
                    nb_dist = nb["distance"]

                    if nb_id == doc_id:
                        continue
                    if nb_dist > l2_thresh:
                        break  # results sorted by distance

                    if nb_id >= doc_id:
                        continue  # only older docs can be canonical

                    nb_row = conn.execute(
                        "SELECT published_at, is_duplicate FROM raw_documents WHERE id = ?",
                        (nb_id,),
                    ).fetchone()

                    if nb_row is None or nb_row["is_duplicate"]:
                        continue  # skip — it's itself a duplicate

                    # Time window check
                    nb_pub = _parse_dt(nb_row["published_at"])
                    if doc_pub and nb_pub:
                        if abs(doc_pub - nb_pub) > time_delta:
                            continue

                    conn.execute(
                        "UPDATE raw_documents SET is_duplicate = 1, duplicate_of = ?, dedup_checked = 1 WHERE id = ?",
                        (nb_id, doc_id),
                    )
                    result.duplicates_found += 1
                    marked = True
                    break

                if not marked:
                    conn.execute(
                        "UPDATE raw_documents SET dedup_checked = 1 WHERE id = ?",
                        (doc_id,),
                    )

                result.docs_checked += 1

        logger.info(
            f"Dedup progress: {min(batch_start + batch_size, len(rows))}/{len(rows)} checked, "
            f"{result.duplicates_found} duplicates so far"
        )

    logger.info(
        f"Dedup complete: {result.docs_checked} checked, {result.duplicates_found} duplicates"
    )
    return result
