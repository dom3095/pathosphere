"""
Embedding pipeline — Phase 2.

Processes raw_documents where embedded=0, computes 384-dim unit vectors
with multilingual-e5-small, inserts into vec_documents, marks embedded=1.

Memory: model is ~500 MB; load once, batch-process, unload explicitly when done.
Prefix "passage: " follows intfloat/multilingual-e5 training convention.
"""

import struct
import sqlite3
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loguru import logger

EMBED_DIM = 384
MODEL_NAME = "intfloat/multilingual-e5-small"
BATCH_SIZE = 32
MAX_TEXT_CHARS = 1024  # generous; tokenizer will truncate at 512 tokens


@dataclass
class EmbedResult:
    docs_processed: int = 0
    docs_skipped: int = 0   # no usable text (title+body both empty)
    errors: int = 0


@runtime_checkable
class EmbedModel(Protocol):
    def encode(self, texts: list[str], normalize_embeddings: bool = True): ...


def load_model() -> EmbedModel:
    from sentence_transformers import SentenceTransformer
    logger.info(f"Loading embedding model: {MODEL_NAME}")
    return SentenceTransformer(MODEL_NAME)


def _build_text(title: str | None, body: str | None) -> str | None:
    parts = []
    if title:
        parts.append(title.strip())
    if body:
        parts.append(body.strip()[:MAX_TEXT_CHARS])
    if not parts:
        return None
    return "passage: " + " ".join(parts)


def serialize(vec) -> bytes:
    return struct.pack(f"{EMBED_DIM}f", *[float(x) for x in vec])


def embed_documents(
    conn: sqlite3.Connection,
    *,
    model: EmbedModel | None = None,
    batch_size: int = BATCH_SIZE,
) -> EmbedResult:
    """Embed all raw_documents with embedded=0. Returns counts."""
    import gc

    _own_model = model is None
    if _own_model:
        model = load_model()

    result = EmbedResult()

    rows = conn.execute(
        "SELECT id, title, body FROM raw_documents WHERE embedded = 0"
    ).fetchall()

    if not rows:
        return result

    logger.info(f"Embedding {len(rows)} documents (batch_size={batch_size})")

    from tqdm import tqdm

    progress = tqdm(
        total=len(rows), unit="doc", desc="Embedding", dynamic_ncols=True
    )
    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        texts: list[str] = []
        valid_ids: list[int] = []
        skip_ids: list[int] = []

        for row in batch:
            text = _build_text(row["title"], row["body"])
            if text is None:
                skip_ids.append(row["id"])
            else:
                texts.append(text)
                valid_ids.append(row["id"])

        # Mark empty docs as embedded (no vector to compute)
        if skip_ids:
            with conn:
                conn.executemany(
                    "UPDATE raw_documents SET embedded = 1 WHERE id = ?",
                    [(i,) for i in skip_ids],
                )
            result.docs_skipped += len(skip_ids)

        if not texts:
            progress.update(len(batch))
            continue

        try:
            embeddings = model.encode(texts, normalize_embeddings=True)
        except Exception as exc:
            logger.warning(f"Batch {batch_start // batch_size} encode failed: {exc}")
            result.errors += len(texts)
            progress.update(len(batch))
            continue

        with conn:
            for doc_id, vec in zip(valid_ids, embeddings):
                blob = serialize(vec)
                conn.execute(
                    "INSERT OR REPLACE INTO vec_documents(document_id, embedding) VALUES (?, ?)",
                    (doc_id, blob),
                )
                conn.execute(
                    "UPDATE raw_documents SET embedded = 1 WHERE id = ?",
                    (doc_id,),
                )
                result.docs_processed += 1

        progress.update(len(batch))
        logger.debug(
            f"Embedded batch {batch_start // batch_size + 1}: {len(valid_ids)} docs"
        )

    progress.close()

    if _own_model:
        del model
        gc.collect()

    return result
