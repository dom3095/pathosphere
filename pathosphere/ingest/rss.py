"""
RSS multi-bloc ingestor.

Fetches feeds from all active sources in the `sources` table.
Inserts articles into `raw_documents` with dedup by URL and content_hash.

Tables updated: raw_documents
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import struct_time

import feedparser
import httpx
from loguru import logger


# ──────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RssIngestResult:
    sources_attempted: int = 0
    sources_ok: int = 0
    sources_error: int = 0
    docs_inserted: int = 0
    docs_skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _struct_to_iso(t: struct_time | None) -> str | None:
    if t is None:
        return None
    try:
        dt = datetime(*t[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _extract_body(entry: feedparser.util.FeedParserDict) -> str:
    """Best-effort text extraction from a feed entry."""
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].get("value", "")
    if hasattr(entry, "summary") and entry.summary:
        return entry.summary
    if hasattr(entry, "description") and entry.description:
        return entry.description
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def ingest_rss(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    source_ids: list[int] | None = None,
    max_age_days: int = 2,
) -> RssIngestResult:
    """
    Fetch and insert RSS articles from active sources.

    source_ids  : restrict to these source IDs; None = all active sources with URL
    max_age_days: skip articles older than this many days (0 = no limit)
    """
    result = RssIngestResult()

    query = "SELECT id, name, url, language FROM sources WHERE active = 1 AND url IS NOT NULL"
    params: list = []
    if source_ids:
        placeholders = ",".join("?" * len(source_ids))
        query += f" AND id IN ({placeholders})"
        params = list(source_ids)

    sources = conn.execute(query, params).fetchall()
    if not sources:
        logger.warning("RSS: no active sources with URL found")
        return result

    cutoff: datetime | None = None
    if max_age_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    logger.info(f"RSS: fetching {len(sources)} sources (max_age_days={max_age_days})")

    with httpx.Client(
        headers={"User-Agent": "pathosphere/0.1 OSINT research"},
        timeout=20,
        follow_redirects=True,
    ) as client:
        for source in sources:
            source_id: int = source["id"]
            name: str = source["name"]
            url: str = source["url"]
            language: str | None = source["language"]

            result.sources_attempted += 1

            try:
                resp = client.get(url)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except httpx.HTTPStatusError as exc:
                result.sources_error += 1
                msg = f"{name}: HTTP {exc.response.status_code}"
                result.errors.append(msg)
                logger.warning(f"RSS {msg}")
                continue
            except Exception as exc:
                result.sources_error += 1
                msg = f"{name}: {exc}"
                result.errors.append(msg)
                logger.warning(f"RSS fetch error {msg}")
                continue

            if not feed.entries:
                result.sources_error += 1
                msg = f"{name}: empty feed (bozo={feed.bozo})"
                result.errors.append(msg)
                logger.warning(f"RSS {msg}")
                continue

            inserted = skipped = 0

            with conn:
                for entry in feed.entries:
                    link: str | None = getattr(entry, "link", None)
                    if not link:
                        skipped += 1
                        continue

                    title: str | None = getattr(entry, "title", None) or None
                    body: str = _extract_body(entry)
                    published_at: str | None = _struct_to_iso(
                        getattr(entry, "published_parsed", None)
                        or getattr(entry, "updated_parsed", None)
                    )

                    if cutoff and published_at:
                        try:
                            pub_dt = datetime.fromisoformat(published_at)
                            if pub_dt.tzinfo is None:
                                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                            if pub_dt < cutoff:
                                skipped += 1
                                continue
                        except ValueError:
                            pass

                    content_hash = (
                        hashlib.sha256(body.encode()).hexdigest() if body else None
                    )

                    conn.execute(
                        """INSERT OR IGNORE INTO raw_documents
                           (source_id, url, title, body, published_at, language,
                            content_hash, embedded)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                        (source_id, link, title, body or None,
                         published_at, language, content_hash),
                    )
                    changed = conn.execute("SELECT changes()").fetchone()[0]
                    if changed:
                        inserted += 1
                    else:
                        skipped += 1

            result.sources_ok += 1
            result.docs_inserted += inserted
            result.docs_skipped += skipped
            logger.info(f"RSS {name}: +{inserted} inserted ({skipped} skipped)")

    logger.info(
        f"RSS complete: {result.sources_ok}/{result.sources_attempted} sources ok | "
        f"+{result.docs_inserted} docs ({result.docs_skipped} skipped) | "
        f"{result.sources_error} errors"
    )
    return result
