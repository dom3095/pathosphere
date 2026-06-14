"""
UN Comtrade ingestor — semiconductor supply-chain pilot.

Source: Comtrade free preview API (no subscription key, ≤500 records/call):
  https://comtradeapi.un.org/public/v1/preview/C/M/HS

Pilot scope: HS 8541/8542 (semiconductor devices) + 8486 (chip-making
machinery), monthly, key reporters vs World, both import and export flows.
One call per period batches all reporters/commodities/flows (well under the
500-record cap for this pilot).

Each trade record is synthesized into a `raw_document` (title + textual body)
under a dedicated "UN Comtrade" source, so the semantic pipeline treats trade
flows as documents alongside news. Dedup by synthetic URL key — re-fetches of
the same (reporter, commodity, flow, period) are skipped (revisions ignored
for MVP).

Tables updated: sources (pilot source row), raw_documents
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date

import httpx
from loguru import logger

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"

# Free preview endpoint throttles bursts hard (HTTP 429). Space out period
# calls and retry with exponential backoff honouring Retry-After.
DEFAULT_REQUEST_DELAY = 6.0   # seconds between period calls
MAX_RETRIES = 5
BACKOFF_BASE = 5.0            # seconds; doubled each retry

# ISO numeric reporter code → label. Core semiconductor supply-chain players.
SEMICON_REPORTERS: dict[int, str] = {
    842: "USA",
    156: "China",
    490: "Taiwan (Other Asia, nes)",
    410: "South Korea",
    392: "Japan",
    528: "Netherlands",
    276: "Germany",
    702: "Singapore",
    458: "Malaysia",
}

# HS code → short label (cmdDesc from the API is verbose; this is for titles).
SEMICON_COMMODITIES: dict[str, str] = {
    "8541": "semiconductor devices",
    "8542": "integrated circuits",
    "8486": "chip-making machinery",
}

FLOW_LABEL = {"M": "Import", "X": "Export"}

PILOT_SOURCE_NAME = "UN Comtrade"
DEFAULT_LAG_MONTHS = 2   # Comtrade publishing lag
DEFAULT_PERIODS = 3      # months of history per run


@dataclass
class ComtradeResult:
    records_fetched: int = 0
    docs_inserted: int = 0
    docs_skipped: int = 0
    flows_upserted: int = 0
    periods: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def recent_periods(n: int = DEFAULT_PERIODS, lag_months: int = DEFAULT_LAG_MONTHS,
                   today: date | None = None) -> list[str]:
    """`n` YYYYMM strings ending `lag_months` before the current month."""
    today = today or date.today()
    # zero-based month index of the most recent target month
    idx = today.year * 12 + (today.month - 1) - lag_months
    out = []
    for k in range(n):
        m = idx - k
        out.append(f"{m // 12:04d}{m % 12 + 1:02d}")
    return out


def month_range(start: str, end: str | None = None,
                today: date | None = None) -> list[str]:
    """Inclusive YYYYMM list from `start` to `end`, chronological ascending.

    `end` defaults to the most recent period within the publishing lag.
    """
    if end is None:
        end = recent_periods(1, today=today)[0]
    s_idx = int(start[:4]) * 12 + (int(start[4:6]) - 1)
    e_idx = int(end[:4]) * 12 + (int(end[4:6]) - 1)
    if e_idx < s_idx:
        s_idx, e_idx = e_idx, s_idx
    return [f"{m // 12:04d}{m % 12 + 1:02d}" for m in range(s_idx, e_idx + 1)]


def ensure_source(conn) -> int:
    """Return the pilot source id, creating the row if absent."""
    row = conn.execute(
        "SELECT id FROM sources WHERE name = ?", (PILOT_SOURCE_NAME,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO sources
           (name, url, country, geopolitical_block, orientation,
            state_control, language, active, notes)
           VALUES (?, NULL, 'XX', 'other', 'intergovernmental', 0, 'en', 1, ?)""",
        (PILOT_SOURCE_NAME, "UN Comtrade trade-flow records (semiconductor pilot)"),
    )
    conn.commit()
    return cur.lastrowid


def _fetch_period(
    client: httpx.Client, reporters: list[int], commodities: list[str],
    flows: list[str], period: str
) -> list[dict]:
    params = {
        "reporterCode": ",".join(str(r) for r in reporters),
        "cmdCode": ",".join(commodities),
        "flowCode": ",".join(flows),
        "period": period,
        "partnerCode": 0,          # World
        "partner2Code": 0,         # totals only — avoid partner2 breakdown
        "motCode": 0,              # TOTAL mode of transport
        "customsCode": "C00",      # TOTAL customs procedure
        "includeDesc": "True",
    }
    for attempt in range(MAX_RETRIES):
        resp = client.get(PREVIEW_URL, params=params, timeout=60)
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() \
                else BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                f"Comtrade 429 on {period}, retry {attempt + 1}/{MAX_RETRIES - 1} "
                f"in {wait:.0f}s"
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json().get("data", [])
    return []


def _synthesize_doc(rec: dict) -> tuple[str, str, str, str, str]:
    """(url, title, body, published_at, content_hash) from a Comtrade record."""
    reporter_iso = rec.get("reporterISO", str(rec.get("reporterCode", "?")))
    cmd = str(rec.get("cmdCode", "?"))
    flow = rec.get("flowCode", "?")
    period = str(rec.get("period", "?"))
    value = rec.get("primaryValue") or 0
    flow_label = FLOW_LABEL.get(flow, flow)
    short = SEMICON_COMMODITIES.get(cmd, cmd)

    url = f"comtrade://{reporter_iso}/{cmd}/{flow}/{period}"
    title = (
        f"{reporter_iso} {flow_label} HS{cmd} ({short}) {period}: "
        f"${value / 1e6:,.1f}M"
    )
    body = (
        f"{rec.get('reporterDesc', reporter_iso)} {flow_label.lower()} of "
        f"HS {cmd} — {rec.get('cmdDesc', short)}. "
        f"Partner: {rec.get('partnerDesc', 'World')}. Period {period}. "
        f"Trade value: ${value:,.0f}."
    )
    if rec.get("netWgt"):
        body += f" Net weight: {rec['netWgt']:,.0f} kg."

    year = rec.get("refYear")
    month = rec.get("refMonth")
    published_at = (
        f"{year:04d}-{month:02d}-01T00:00:00" if year and month else None
    )
    content_hash = hashlib.sha256(f"{url}|{value}".encode()).hexdigest()
    return url, title, body, published_at, content_hash


def ingest_comtrade(
    conn: "sqlite3.Connection",  # type: ignore[name-defined]
    *,
    periods: list[str] | None = None,
    reporters: list[int] | None = None,
    commodities: list[str] | None = None,
    flows: list[str] | None = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    client: httpx.Client | None = None,
) -> ComtradeResult:
    """Fetch monthly semiconductor trade flows; store as raw_documents."""
    result = ComtradeResult()
    periods = periods or recent_periods()
    reporters = reporters or list(SEMICON_REPORTERS)
    commodities = commodities or list(SEMICON_COMMODITIES)
    flows = flows or ["M", "X"]
    result.periods = periods

    source_id = ensure_source(conn)

    _own_client = client is None
    if _own_client:
        client = httpx.Client(
            headers={"User-Agent": "pathosphere/0.1 OSINT research"}
        )

    logger.info(
        f"Comtrade: periods={periods} reporters={len(reporters)} "
        f"commodities={commodities} flows={flows}"
    )

    try:
        for i, period in enumerate(periods):
            if i > 0 and request_delay > 0:
                time.sleep(request_delay)   # space out calls to dodge 429
            try:
                records = _fetch_period(
                    client, reporters, commodities, flows, period
                )
            except Exception as exc:
                msg = f"{period}: {exc}"
                result.errors.append(msg)
                logger.warning(f"Comtrade fetch error {msg}")
                continue

            result.records_fetched += len(records)
            with conn:
                for rec in records:
                    url, title, body, pub, chash = _synthesize_doc(rec)
                    conn.execute(
                        """INSERT OR IGNORE INTO raw_documents
                           (origin, source_id, url, title, body, published_at,
                            language, content_hash, embedded)
                           VALUES ('comtrade', ?, ?, ?, ?, ?, 'en', ?, 0)""",
                        (source_id, url, title, body, pub, chash),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        result.docs_inserted += 1
                    else:
                        result.docs_skipped += 1

                    # numeric detail, keyed to the synthetic document
                    doc_row = conn.execute(
                        "SELECT id FROM raw_documents WHERE url = ?", (url,)
                    ).fetchone()
                    if doc_row is not None:
                        conn.execute(
                            """INSERT OR IGNORE INTO comtrade_flows
                               (document_id, reporter_code, reporter_iso,
                                partner_code, cmd_code, flow_code, period,
                                primary_value, net_weight)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                doc_row["id"],
                                rec.get("reporterCode"),
                                rec.get("reporterISO"),
                                rec.get("partnerCode"),
                                str(rec.get("cmdCode")) if rec.get("cmdCode") is not None else None,
                                rec.get("flowCode"),
                                str(rec.get("period")) if rec.get("period") is not None else None,
                                rec.get("primaryValue"),
                                rec.get("netWgt"),
                            ),
                        )
                        if conn.execute("SELECT changes()").fetchone()[0]:
                            result.flows_upserted += 1
    finally:
        if _own_client:
            client.close()

    logger.info(
        f"Comtrade complete: {result.records_fetched} records | "
        f"+{result.docs_inserted} docs ({result.docs_skipped} skipped) | "
        f"{result.flows_upserted} flows | {len(result.errors)} errors"
    )
    return result
