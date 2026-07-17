"""
Economic-crisis ingestor — historical financial/economic crises via Wikidata.

SPARQL query for instances of financial crisis (Q3733076), economic crisis
(Q290178) and recession (Q176494) with a start date (P580) or point in time
(P585). Sparse by design: only encyclopedic, verifiable anchor events
(Great Depression, 2008 crisis, Asian crisis '97…), each carrying its QID
for auditability. Country coordinates (P625 of the P17 country) give a map
position; multi-country crises are stored once as global (no point).

Events land directly in `events` (event_type='economic', origin='wikidata');
no raw_documents — see ucdp.py for the rationale.

Tables updated: events
"""

import re
import sqlite3
from dataclasses import dataclass, field

import httpx
from loguru import logger

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# Countries above this count → crisis is stored as global (no single point).
GLOBAL_COUNTRY_THRESHOLD = 3

SPARQL_QUERY = """
SELECT ?item ?itemLabel ?itemDescription ?start ?pit ?countryLabel ?coord WHERE {
  VALUES ?cls { wd:Q3733076 wd:Q290178 wd:Q176494 }
  ?item wdt:P31 ?cls .
  OPTIONAL { ?item wdt:P580 ?start }
  OPTIONAL { ?item wdt:P585 ?pit }
  OPTIONAL {
    ?item wdt:P17 ?country .
    OPTIONAL { ?country wdt:P625 ?coord }
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}
"""

_POINT_RE = re.compile(r"Point\(([-\d.]+) ([-\d.]+)\)")


@dataclass
class EconCrisesResult:
    items_fetched: int = 0
    events_created: int = 0
    errors: list[str] = field(default_factory=list)


def _value(binding: dict, key: str) -> str | None:
    v = binding.get(key)
    return v["value"] if v else None


def _parse_point(wkt: str | None) -> tuple[float, float] | None:
    """'Point(lon lat)' WKT → (lat, lon)."""
    m = _POINT_RE.match(wkt or "")
    return (float(m.group(2)), float(m.group(1))) if m else None


def _group_by_item(bindings: list[dict]) -> dict[str, dict]:
    """One row per (item, country) → one aggregate per item."""
    items: dict[str, dict] = {}
    for b in bindings:
        qid = (_value(b, "item") or "").rsplit("/", 1)[-1]
        label = _value(b, "itemLabel")
        if not qid or not label or label == qid:
            continue
        agg = items.setdefault(qid, {
            "label": label,
            "description": _value(b, "itemDescription"),
            "date": None,
            "countries": [],
            "coord": None,
        })
        date = (_value(b, "start") or _value(b, "pit") or "")[:10]
        if date and (agg["date"] is None or date < agg["date"]):
            agg["date"] = date
        country = _value(b, "countryLabel")
        if country and country not in agg["countries"]:
            agg["countries"].append(country)
            if agg["coord"] is None:
                agg["coord"] = _parse_point(_value(b, "coord"))
    return items


def ingest_econ_crises(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    start: str | None = None,
    end: str | None = None,
    client: httpx.Client | None = None,
) -> EconCrisesResult:
    """Fetch economic/financial crises from Wikidata and store them as events.

    start/end: optional YYYY-MM-DD bounds. Items without any date are
    skipped (an event needs first_seen). Idempotent: dedup by
    (title, first_seen); one-off ingest, re-runs pick up new Wikidata items.
    """
    result = EconCrisesResult()

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={
                "User-Agent": "pathosphere/0.1 OSINT research",
                "Accept": "application/sparql-results+json",
            }
        )

    logger.info("Wikidata: querying economic/financial crises")

    try:
        resp = client.get(
            WIKIDATA_SPARQL_URL,
            params={"query": SPARQL_QUERY, "format": "json"},
            timeout=120,
        )
        resp.raise_for_status()
        bindings = resp.json()["results"]["bindings"]
    except Exception as exc:
        result.errors.append(str(exc))
        logger.warning(f"Wikidata fetch error: {exc}")
        if _own_client:
            client.close()
        return result

    items = _group_by_item(bindings)
    result.items_fetched = len(items)

    with conn:
        for qid, agg in items.items():
            date = agg["date"]
            if not date:
                continue
            if start and date < start:
                continue
            if end and date > end:
                continue

            title = agg["label"]
            exists = conn.execute(
                "SELECT 1 FROM events WHERE title = ? AND first_seen = ?",
                (title, date),
            ).fetchone()
            if exists:
                continue

            countries = agg["countries"]
            is_global = len(countries) > GLOBAL_COUNTRY_THRESHOLD
            if is_global:
                location_name, lat, lon = "global", None, None
            else:
                location_name = countries[0] if countries else None
                lat, lon = agg["coord"] or (None, None)

            desc = agg["description"] or "economic/financial crisis"
            listed = ", ".join(countries[:6]) + ("…" if len(countries) > 6 else "")
            summary = (
                f"{desc.capitalize()}. Started {date}"
                + (f"; countries: {listed}" if countries else "")
                + f". [Wikidata {qid}]"
            )

            conn.execute(
                """INSERT INTO events
                   (title, summary, first_seen, last_seen, event_type,
                    origin, severity, location_name, lat, lon)
                   VALUES (?, ?, ?, ?, 'economic', 'wikidata', ?, ?, ?, ?)""",
                (title, summary, date, date,
                 4 if is_global else 3, location_name, lat, lon),
            )
            result.events_created += 1

    if _own_client:
        client.close()

    logger.info(
        f"Wikidata econ crises complete: {result.items_fetched} items | "
        f"+{result.events_created} events | {len(result.errors)} errors"
    )
    return result
