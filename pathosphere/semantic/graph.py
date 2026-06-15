"""
Entity graph and narrative divergence — Fase 2 finale.

Two independent, resumable steps:
  1. build_entity_links — populate entity_links from entity co-occurrences
     across events. Aggregated (not per-event), relation_type='co-occurs'.
  2. compute_narrative_divergences — for each event with ≥2 geopolitical
     blocks, compute pairwise cosine divergence between block-averaged
     embeddings.

No LLMs. Memory-safe on M1 8 GB: processes one event at a time.
"""

import sqlite3
import struct
from dataclasses import dataclass

import numpy as np
from loguru import logger

from pathosphere.semantic.embedder import EMBED_DIM


@dataclass
class GraphResult:
    pairs_evaluated: int = 0
    links_written: int = 0
    links_deleted: int = 0


@dataclass
class DivergenceResult:
    events_processed: int = 0
    events_skipped: int = 0
    pairs_written: int = 0


def deserialize(blob: bytes) -> np.ndarray:
    return np.array(struct.unpack(f"{EMBED_DIM}f", blob), dtype=np.float32)


def build_entity_links(
    conn: sqlite3.Connection,
    *,
    min_cooccurrences: int = 1,
) -> GraphResult:
    """Populate entity_links from entity co-occurrences within shared events."""
    result = GraphResult()

    with conn:
        cur = conn.execute(
            "DELETE FROM entity_links WHERE relation_type = 'co-occurs'"
        )
        result.links_deleted = cur.rowcount

    pairs = conn.execute(
        """
        SELECT
            de1.entity_id AS entity_a,
            de2.entity_id AS entity_b,
            COUNT(DISTINCT ed.event_id) AS cooc_count
        FROM event_documents ed
        JOIN document_entities de1 ON de1.document_id = ed.document_id
        JOIN document_entities de2 ON de2.document_id = ed.document_id
        WHERE de1.entity_id < de2.entity_id
        GROUP BY de1.entity_id, de2.entity_id
        HAVING COUNT(DISTINCT ed.event_id) >= ?
        """,
        (min_cooccurrences,),
    ).fetchall()

    result.pairs_evaluated = len(pairs)

    if not pairs:
        return result

    rows = [
        (r["entity_a"], r["entity_b"], "co-occurs", min(1.0, r["cooc_count"] / 10.0))
        for r in pairs
    ]

    with conn:
        conn.executemany(
            "INSERT INTO entity_links (entity_a, entity_b, relation_type, strength, source_event)"
            " VALUES (?, ?, ?, ?, NULL)",
            rows,
        )

    result.links_written = len(rows)
    logger.info(
        f"GRAPH/LINKS: {result.links_written} links written "
        f"({result.links_deleted} deleted), {result.pairs_evaluated} pairs"
    )
    return result


def compute_narrative_divergences(conn: sqlite3.Connection) -> DivergenceResult:
    """Compute pairwise block divergence scores for multi-block events."""
    result = DivergenceResult()

    event_ids = [
        r["event_id"]
        for r in conn.execute(
            """
            SELECT DISTINCT ed.event_id
            FROM event_documents ed
            JOIN raw_documents r ON r.id = ed.document_id
            JOIN sources s ON s.id = r.source_id
            WHERE r.embedded = 1
              AND r.is_duplicate = 0
              AND r.source_id IS NOT NULL
            ORDER BY ed.event_id
            """
        ).fetchall()
    ]

    for event_id in event_ids:
        doc_rows = conn.execute(
            """
            SELECT r.id AS doc_id, s.geopolitical_block
            FROM event_documents ed
            JOIN raw_documents r ON r.id = ed.document_id
            JOIN sources s ON s.id = r.source_id
            WHERE ed.event_id = ?
              AND r.embedded = 1
              AND r.is_duplicate = 0
              AND r.source_id IS NOT NULL
            """,
            (event_id,),
        ).fetchall()

        by_block: dict[str, list[int]] = {}
        for row in doc_rows:
            block = row["geopolitical_block"]
            if block:
                by_block.setdefault(block, []).append(row["doc_id"])

        if len(by_block) < 2:
            result.events_skipped += 1
            continue

        block_centroids: dict[str, np.ndarray] = {}
        for block, doc_ids in by_block.items():
            vecs = []
            for doc_id in doc_ids:
                vec_row = conn.execute(
                    "SELECT embedding FROM vec_documents WHERE document_id = ?",
                    (doc_id,),
                ).fetchone()
                if vec_row:
                    vecs.append(deserialize(vec_row["embedding"]))
            if not vecs:
                continue
            centroid = np.mean(np.stack(vecs), axis=0).astype(np.float32)
            norm = np.linalg.norm(centroid)
            if norm == 0.0:
                continue
            block_centroids[block] = centroid / norm

        if len(block_centroids) < 2:
            result.events_skipped += 1
            continue

        with conn:
            conn.execute(
                "DELETE FROM narrative_divergences WHERE event_id = ?", (event_id,)
            )

            blocks = sorted(block_centroids)
            pairs_inserted = 0
            for i, block_a in enumerate(blocks):
                for block_b in blocks[i + 1 :]:
                    cos_sim = float(
                        np.dot(block_centroids[block_a], block_centroids[block_b])
                    )
                    divergence_score = max(0.0, min(1.0, 1.0 - cos_sim))
                    conn.execute(
                        "INSERT INTO narrative_divergences"
                        " (event_id, block_a, block_b, divergence_score, summary)"
                        " VALUES (?, ?, ?, ?, NULL)",
                        (event_id, block_a, block_b, divergence_score),
                    )
                    pairs_inserted += 1

        result.events_processed += 1
        result.pairs_written += pairs_inserted

    logger.info(
        f"GRAPH/DIVERGENCE: {result.pairs_written} pairs, "
        f"{result.events_processed} events processed, "
        f"{result.events_skipped} skipped"
    )
    return result
