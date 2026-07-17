"""
ReliefWeb disasters ingestor — historical natural-disaster events (1981→today).

UN OCHA ReliefWeb API v2: one entry per declared disaster (floods, storms,
droughts, volcanoes…), with real prose descriptions and country-level
coordinates. Complements USGS (earthquakes only) for the historical map.

Requires a registered appname (free, https://apidoc.reliefweb.int/parameters#appname)
in settings.reliefweb_appname; skips gracefully when not configured — same
pattern as FIRMS without MAP_KEY.

Supports historical backfill (--start) and incremental resume from the last
stored disaster. Events land directly in `events` (event_type='hazard',
origin='reliefweb'); no raw_documents — see ucdp.py for the rationale.

Tables updated: events
"""

import sqlite3
from dataclasses import dataclass, field

import httpx
from loguru import logger

RELIEFWEB_URL = "https://api.reliefweb.int/v2/disasters"

PAGE_SIZE = 200
MAX_SUMMARY_CHARS = 1500


@dataclass
class ReliefWebResult:
    items_fetched: int = 0
    events_created: int = 0
    skipped_no_appname: bool = False
    errors: list[str] = field(default_factory=list)


def _last_event_date(conn) -> str | None:
    row = conn.execute(
        "SELECT max(first_seen) FROM events WHERE origin = 'reliefweb'"
    ).fetchone()
    return row[0][:10] if row and row[0] else None


def ingest_reliefweb(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    appname: str | None,
    start: str | None = None,
    end: str | None = None,
    max_items: int | None = None,
    client: httpx.Client | None = None,
) -> ReliefWebResult:
    """Fetch ReliefWeb disasters and store each as a hazard event.

    appname: registered ReliefWeb appname; when None/empty the ingest is
    skipped. start: YYYY-MM-DD backfill anchor (overrides resume); when None,
    resume from the last stored disaster; if none exists, fetch everything.
    """
    result = ReliefWebResult()
    if not appname:
        result.skipped_no_appname = True
        logger.info(
            "ReliefWeb: no appname configured — skipping (register one free "
            "at https://apidoc.reliefweb.int/parameters#appname)"
        )
        return result

    if start is None:
        start = _last_event_date(conn)

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(
        f"ReliefWeb: disasters from {start or 'beginning'}"
        + (f" to {end}" if end else "")
    )

    offset = 0
    try:
        while True:
            payload: dict = {
                "appname": appname,
                "limit": PAGE_SIZE,
                "offset": offset,
                "sort": ["date.event:asc"],
                "fields": {
                    "include": [
                        "name", "description", "date", "status",
                        "primary_country", "primary_type", "url_alias",
                    ]
                },
            }
            conditions = []
            if start:
                conditions.append(
                    {"field": "date.event", "value": {"from": f"{start}T00:00:00+00:00"}}
                )
            if end:
                conditions.append(
                    {"field": "date.event", "value": {"to": f"{end}T23:59:59+00:00"}}
                )
            if conditions:
                payload["filter"] = (
                    conditions[0] if len(conditions) == 1
                    else {"operator": "AND", "conditions": conditions}
                )

            try:
                resp = client.post(RELIEFWEB_URL, json=payload, timeout=60)
                resp.raise_for_status()
                items = resp.json().get("data", [])
            except Exception as exc:
                result.errors.append(f"offset={offset}: {exc}")
                logger.warning(f"ReliefWeb fetch error at offset={offset}: {exc}")
                break

            if not items:
                break
            result.items_fetched += len(items)

            with conn:
                for item in items:
                    f = item.get("fields", {})
                    title = (f.get("name") or "").strip()
                    date_event = (
                        (f.get("date") or {}).get("event")
                        or (f.get("date") or {}).get("created")
                        or ""
                    )[:10]
                    if not title or not date_event:
                        continue

                    exists = conn.execute(
                        "SELECT 1 FROM events WHERE title = ? AND first_seen = ?",
                        (title, date_event),
                    ).fetchone()
                    if exists:
                        continue

                    country = f.get("primary_country") or {}
                    loc = country.get("location") or {}
                    dtype = (f.get("primary_type") or {}).get("name") or "disaster"
                    desc = (f.get("description") or "").strip()[:MAX_SUMMARY_CHARS]
                    summary = (
                        f"{dtype}: {desc}" if desc
                        else f"{dtype} — {country.get('name', '')}, {date_event}."
                    ) + " [ReliefWeb]"

                    conn.execute(
                        """INSERT INTO events
                           (title, summary, first_seen, last_seen, event_type,
                            origin, severity, location_name, lat, lon)
                           VALUES (?, ?, ?, ?, 'hazard', 'reliefweb', 3, ?, ?, ?)""",
                        (title, summary, date_event, date_event,
                         country.get("name"), loc.get("lat"), loc.get("lon")),
                    )
                    result.events_created += 1

            if len(items) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            if max_items and result.items_fetched >= max_items:
                break
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"ReliefWeb complete: {result.items_fetched} disasters | "
        f"+{result.events_created} events | {len(result.errors)} errors"
    )
    return result
