"""
WHO Disease Outbreak News ingestor — historical epidemic events (1996→today).

Official WHO outbreak notifications via the public OData API
(www.who.int/api/news/diseaseoutbreaknews, no key). Each item has real prose
(Overview/Summary HTML) and a title of the form "Disease – Country", from
which the country is parsed into location_name; lat/lon are left NULL so the
extract phase's geocoder resolves them.

Supports historical backfill (--start) and incremental resume from the last
stored item. Events land directly in `events` (event_type='epidemic',
origin='who_don'); no raw_documents — see ucdp.py for the rationale.

Tables updated: events
"""

import html
import re
import sqlite3
from dataclasses import dataclass, field

import httpx
from loguru import logger

WHO_DON_URL = "https://www.who.int/api/news/diseaseoutbreaknews"

PAGE_SIZE = 100
MAX_SUMMARY_CHARS = 1500

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class WHODONResult:
    items_fetched: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


def _strip_html(raw: str | None) -> str:
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    return _WS_RE.sub(" ", text).strip()


def _country_from_title(title: str) -> str | None:
    """'Ebola virus disease – Democratic Republic of the Congo' → country.

    Only the en-dash form is a reliable separator; hyphens appear inside
    disease names ('MERS-CoV') and years ('1996 - Global Cholera Update').
    """
    if "–" in title:
        tail = title.rsplit("–", 1)[1].strip()
        if tail and not tail[0].isdigit():
            return tail
    return None


def _last_event_date(conn) -> str | None:
    row = conn.execute(
        "SELECT max(first_seen) FROM events WHERE origin = 'who_don'"
    ).fetchone()
    return row[0][:10] if row and row[0] else None


def ingest_who_don(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    start: str | None = None,
    end: str | None = None,
    max_items: int | None = None,
    client: httpx.Client | None = None,
) -> WHODONResult:
    """Fetch WHO Disease Outbreak News and store each item as an epidemic event.

    start: YYYY-MM-DD historical backfill anchor (overrides resume). When
    None, resume from the last stored item; if none exists, fetch everything
    (full history is only ~3k items). Date filtering is client-side: the
    API's OData $filter support is unreliable, pages are cheap.
    """
    result = WHODONResult()

    if start is None:
        start = _last_event_date(conn)

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(f"WHO DON: from {start or 'beginning'}" + (f" to {end}" if end else ""))

    skip = 0
    try:
        while True:
            params = {
                "$top": PAGE_SIZE,
                "$skip": skip,
                "$orderby": "PublicationDateAndTime asc",
            }
            try:
                resp = client.get(WHO_DON_URL, params=params, timeout=60)
                resp.raise_for_status()
                items = resp.json().get("value", [])
            except Exception as exc:
                result.errors.append(f"skip={skip}: {exc}")
                logger.warning(f"WHO DON fetch error at skip={skip}: {exc}")
                break

            if not items:
                break
            result.items_fetched += len(items)

            with conn:
                for item in items:
                    pub = (item.get("PublicationDateAndTime") or "")[:10]
                    if not pub:
                        continue
                    if start and pub < start:
                        continue
                    if end and pub > end:
                        continue

                    title = (item.get("Title") or "").strip()
                    if not title:
                        continue

                    exists = conn.execute(
                        "SELECT 1 FROM events WHERE title = ? AND first_seen = ?",
                        (title, pub),
                    ).fetchone()
                    if exists:
                        continue

                    body = _strip_html(
                        item.get("Summary") or item.get("Overview")
                    )[:MAX_SUMMARY_CHARS]
                    don_id = item.get("DonId") or item.get("UrlName") or ""
                    summary = (
                        f"{body} [WHO Disease Outbreak News {don_id}]".strip()
                    )

                    conn.execute(
                        """INSERT INTO events
                           (title, summary, first_seen, last_seen, event_type,
                            origin, severity, location_name, lat, lon)
                           VALUES (?, ?, ?, ?, 'epidemic', 'who_don', 2, ?,
                                   NULL, NULL)""",
                        (title, summary, pub, pub, _country_from_title(title)),
                    )
                    result.events_created += 1

            if len(items) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
            if max_items and result.items_fetched >= max_items:
                break
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"WHO DON complete: {result.items_fetched} items | "
        f"+{result.events_created} events | {len(result.errors)} errors"
    )
    return result
