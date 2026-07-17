"""
System health check (`pathos doctor`).

Read-only diagnostics, no LLM calls, no paid APIs. Five areas:

  1. Prerequisites — `claude` CLI on PATH (CP-001), Ollama reachable with the
     configured model pulled (CP-003), spaCy NER model installed.
  2. Config — presence (NEVER the value) of optional API keys, backend name.
  3. Data freshness — most recent row per recurring ingest source vs its
     expected cadence (one-off backfill sources like UCDP are excluded).
  4. Pipeline backlog — docs waiting for embedding/dedup/NER, events waiting
     for geolocation/geocoding, entities waiting for Wikidata linking.
     Filters mirror the pipeline queries exactly (embedder/dedup/extract).
  5. Agent state — portfolios initialized, pending theses, open trades past
     their thesis horizon, open predictions past horizon_date, active
     scenario sets past horizon, age of the latest brief.

Every DB query is defensive: a table/column added by a later migration may
be missing on a pre-migration DB — that yields a SKIP result, never a crash.
The only network touched by default is the local Ollama socket (3s timeout);
`--network` adds a yfinance price probe.

Exit code contract (enforced by the CLI): 0 = no FAIL (warnings allowed),
1 = at least one FAIL.
"""

import importlib.util
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from pathosphere.config import Settings
from pathosphere.semantic.embedder import NON_PROSE_ORIGINS

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

OLLAMA_PROBE_TIMEOUT_S = 3.0
STALE_BRIEF_DAYS = 3

# Backlog size above which a pending count becomes a WARN instead of an OK.
# Small backlogs are normal between cycle phases; these flag a pipeline that
# has silently stopped draining (CP-023 class: degradation nobody notices).
BACKLOG_WARN_AT = {
    "embedding": 500,
    "dedup": 500,
    "ner": 500,
    "rss geolocation": 200,
    "geocoding": 200,
    "wikidata linking": 500,
}

# Sentinel for "table/column missing" (sqlite3.OperationalError), which must
# render as SKIP — distinct from "table exists but has no rows" (None → WARN).
_MISSING = object()

# (name, SQL returning one MAX timestamp, warn threshold hours, fix hint,
#  settings attr whose presence gates the source, or None)
_FRESHNESS_SPECS = [
    ("rss",
     "SELECT MAX(fetched_at) FROM raw_documents WHERE origin = 'rss'",
     48, "pathos ingest rss", None),
    ("gdelt",
     "SELECT MAX(downloaded_at) FROM gdelt_file_log WHERE status = 'ok'",
     48, "pathos ingest gdelt", None),
    ("portwatch",
     "SELECT MAX(fetched_at) FROM chokepoint_metrics",
     72, "pathos ingest portwatch", None),
    ("firms",
     "SELECT MAX(fetched_at) FROM fire_metrics",
     72, "pathos ingest firms", "firms_map_key"),
    ("ioda",
     "SELECT MAX(fetched_at) FROM internet_metrics",
     72, "pathos ingest ioda", None),
    ("usgs",
     "SELECT MAX(created_at) FROM events WHERE origin = 'usgs'",
     72, "pathos ingest usgs", None),
    # Comtrade publishes monthly with a multi-week lag — a stale fetch only
    # matters on a much longer horizon than the daily sources.
    ("comtrade",
     "SELECT MAX(fetched_at) FROM raw_documents WHERE origin = 'comtrade'",
     45 * 24, "pathos ingest comtrade", None),
]


@dataclass
class CheckResult:
    section: str  # prerequisites | config | database | freshness | backlog | agent | network
    name: str
    status: str   # ok | warn | fail | skip
    detail: str


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    """First column of the first row; _MISSING on OperationalError
    (table/column absent on a pre-migration DB)."""
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return _MISSING
    return row[0] if row else None


def _parse_ts(value) -> datetime | None:
    """Parse ISO timestamps as naive UTC. Handles both sqlite datetime('now')
    ('YYYY-MM-DD HH:MM:SS', already UTC) and tz-aware ISO strings."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _age_hours(ts: datetime) -> float:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return max((now - ts).total_seconds() / 3600.0, 0.0)


def _fmt_age(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f}min ago"
    if hours < 48:
        return f"{hours:.1f}h ago"
    return f"{hours / 24:.1f}d ago"


# ── prerequisites ────────────────────────────────────────────────────────────

def _check_claude_cli(settings: Settings) -> CheckResult:
    path = shutil.which("claude")
    if path:
        return CheckResult("prerequisites", "claude CLI", OK, f"found at {path}")
    if settings.reasoning_model == "claude":
        return CheckResult(
            "prerequisites", "claude CLI", FAIL,
            "not on PATH with reasoning_model=claude — brief/thesis/scenario "
            "tasks will fail (CP-001)",
        )
    return CheckResult(
        "prerequisites", "claude CLI", WARN,
        "not on PATH — required when reasoning_model=claude",
    )


def _check_ollama(settings: Settings) -> CheckResult:
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        resp = httpx.get(url, timeout=OLLAMA_PROBE_TIMEOUT_S)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError) as exc:
        return CheckResult(
            "prerequisites", "ollama", WARN,
            f"unreachable at {settings.ollama_base_url} ({type(exc).__name__}) "
            "— debate/geoloc-qwen unavailable; start with `ollama serve` (CP-003)",
        )
    target = settings.ollama_llm_model
    if any(name == target or name.split(":")[0] == target for name in models):
        return CheckResult(
            "prerequisites", "ollama", OK, f"reachable, model {target} available"
        )
    return CheckResult(
        "prerequisites", "ollama", WARN,
        f"reachable but model {target} not pulled — `ollama pull {target}`",
    )


def _check_spacy_model() -> CheckResult:
    if importlib.util.find_spec("xx_ent_wiki_sm") is not None:
        return CheckResult("prerequisites", "spaCy NER model", OK,
                           "xx_ent_wiki_sm installed")
    return CheckResult(
        "prerequisites", "spaCy NER model", WARN,
        "xx_ent_wiki_sm not installed — `pathos extract` will fail (`uv sync`)",
    )


def _check_prerequisites(settings: Settings) -> list[CheckResult]:
    return [
        _check_claude_cli(settings),
        _check_ollama(settings),
        _check_spacy_model(),
    ]


# ── config ───────────────────────────────────────────────────────────────────

def _check_config(settings: Settings) -> list[CheckResult]:
    # SECURITY (CLAUDE.md): report only the PRESENCE of a key, never its value.
    results = []
    if settings.reasoning_model in ("claude", "qwen-local"):
        results.append(CheckResult("config", "reasoning_model", OK,
                                   settings.reasoning_model))
    else:
        results.append(CheckResult(
            "config", "reasoning_model", FAIL,
            f"'{settings.reasoning_model}' is not a valid backend "
            "(expected claude | qwen-local)",
        ))
    for attr, consumer in (
        ("firms_map_key", "pathos ingest firms"),
        ("reliefweb_appname", "pathos ingest reliefweb"),
    ):
        if not hasattr(settings, attr):
            continue  # field added by a branch not merged yet — same defensive
            # posture as missing DB tables
        if bool(getattr(settings, attr)):
            results.append(CheckResult("config", attr, OK, "set"))
        else:
            results.append(CheckResult(
                "config", attr, WARN,
                f"not set — `{consumer}` is skipped (free registration)",
            ))
    return results


# ── database ─────────────────────────────────────────────────────────────────

def _check_database(settings: Settings) -> list[CheckResult]:
    if not settings.db_path.exists():
        return [CheckResult("database", "db file", SKIP,
                            f"not found at {settings.db_path}")]
    size_mb = settings.db_path.stat().st_size / 1e6
    return [CheckResult("database", "db file", OK,
                        f"{settings.db_path} ({size_mb:.0f} MB)")]


# ── data freshness ───────────────────────────────────────────────────────────

def _check_freshness(conn: sqlite3.Connection, settings: Settings) -> list[CheckResult]:
    results = []
    for name, sql, warn_hours, hint, key_attr in _FRESHNESS_SPECS:
        if key_attr and not bool(getattr(settings, key_attr, None)):
            results.append(CheckResult(
                "freshness", name, SKIP,
                f"{key_attr.upper()} not set — source disabled",
            ))
            continue
        value = _scalar(conn, sql)
        if value is _MISSING:
            results.append(CheckResult("freshness", name, SKIP,
                                       "table missing — run `pathos db init`"))
        elif value is None:
            results.append(CheckResult("freshness", name, WARN,
                                       f"no data yet — `{hint}`"))
        else:
            ts = _parse_ts(value)
            if ts is None:
                results.append(CheckResult(
                    "freshness", name, WARN, f"unparseable timestamp {value!r}"
                ))
                continue
            age = _age_hours(ts)
            if age > warn_hours:
                results.append(CheckResult(
                    "freshness", name, WARN,
                    f"last data {_fmt_age(age)} — `{hint}`",
                ))
            else:
                results.append(CheckResult("freshness", name, OK,
                                           f"last data {_fmt_age(age)}"))
    return results


# ── pipeline backlog ─────────────────────────────────────────────────────────

def _check_backlog(conn: sqlite3.Connection) -> list[CheckResult]:
    placeholders = ", ".join("?" for _ in NON_PROSE_ORIGINS)
    # Each SQL mirrors the corresponding pipeline query so counts match what
    # the phase itself would process (embedder.py / dedup.py / extract.py).
    specs = [
        ("embedding",
         f"""SELECT COUNT(*) FROM raw_documents
             WHERE embedded = 0
               AND (origin IS NULL OR origin NOT IN ({placeholders}))""",
         NON_PROSE_ORIGINS, "pathos embed"),
        ("dedup",
         """SELECT COUNT(*) FROM raw_documents
            WHERE embedded = 1 AND is_duplicate = 0 AND dedup_checked = 0""",
         (), "pathos embed"),
        ("ner",
         f"""SELECT COUNT(*) FROM raw_documents
             WHERE embedded = 1 AND is_duplicate = 0 AND ner_done = 0
               AND (origin IS NULL OR origin NOT IN ({placeholders}))""",
         NON_PROSE_ORIGINS, "pathos extract"),
        ("rss geolocation",
         """SELECT COUNT(*) FROM events
            WHERE origin = 'rss' AND location_name IS NULL
              AND geoloc_checked = 0""",
         (), "pathos extract --geolocate-qwen --geoloc-limit 200"),
        ("geocoding",
         """SELECT COUNT(*) FROM events
            WHERE location_name IS NOT NULL AND lat IS NULL""",
         (), "pathos extract"),
        ("wikidata linking",
         """SELECT COUNT(*) FROM entities
            WHERE wikidata_checked = 0 AND wikidata_qid IS NULL""",
         (), "pathos extract"),
    ]
    results = []
    for name, sql, params, hint in specs:
        count = _scalar(conn, sql, params)
        if count is _MISSING:
            results.append(CheckResult(
                "backlog", name, SKIP,
                "column/table missing — migration not applied on this DB",
            ))
        elif count and count >= BACKLOG_WARN_AT[name]:
            results.append(CheckResult(
                "backlog", name, WARN, f"{count} pending — `{hint}`"
            ))
        else:
            results.append(CheckResult("backlog", name, OK,
                                       f"{count or 0} pending"))
    return results


# ── agent state ──────────────────────────────────────────────────────────────

def _check_agent_state(conn: sqlite3.Connection) -> list[CheckResult]:
    results = []

    n_portfolios = _scalar(conn, "SELECT COUNT(*) FROM portfolios")
    if n_portfolios is _MISSING:
        results.append(CheckResult("agent", "portfolios", SKIP, "table missing"))
    elif not n_portfolios:
        results.append(CheckResult(
            "agent", "portfolios", WARN,
            "not initialized — `pathos portfolio init`",
        ))
    else:
        results.append(CheckResult("agent", "portfolios", OK,
                                   f"{n_portfolios} initialized"))

    pending = _scalar(
        conn, "SELECT COUNT(*) FROM theses WHERE status = 'pending'"
    )
    if pending is _MISSING:
        results.append(CheckResult("agent", "theses", SKIP, "table missing"))
    elif pending:
        results.append(CheckResult(
            "agent", "theses", WARN,
            f"{pending} pending review — `pathos thesis list --status pending`",
        ))
    else:
        results.append(CheckResult("agent", "theses", OK, "none pending"))

    open_trades = _scalar(
        conn, "SELECT COUNT(*) FROM trades WHERE closed_at IS NULL"
    )
    # date() on both sides: opened_at may be date-only or full timestamp
    # (same mixed-format pitfall fixed in scenario review, 2026-07-16).
    overdue_trades = _scalar(conn, """
        SELECT COUNT(*) FROM trades t
        JOIN theses th ON th.id = t.thesis_id
        WHERE t.closed_at IS NULL AND th.horizon_days IS NOT NULL
          AND date(t.opened_at, '+' || th.horizon_days || ' days') < date('now')
    """)
    if open_trades is _MISSING:
        results.append(CheckResult("agent", "open trades", SKIP, "table missing"))
    elif overdue_trades not in (_MISSING, None) and overdue_trades:
        results.append(CheckResult(
            "agent", "open trades", WARN,
            f"{overdue_trades} of {open_trades} past thesis horizon — "
            "review and `pathos trade close <id>`",
        ))
    else:
        results.append(CheckResult("agent", "open trades", OK,
                                   f"{open_trades or 0} open, none past horizon"))

    open_preds = _scalar(
        conn, "SELECT COUNT(*) FROM predictions WHERE resolved = 0"
    )
    overdue_preds = _scalar(conn, """
        SELECT COUNT(*) FROM predictions
        WHERE resolved = 0 AND date(horizon_date) < date('now')
    """)
    if open_preds is _MISSING:
        results.append(CheckResult("agent", "predictions", SKIP, "table missing"))
    elif overdue_preds not in (_MISSING, None) and overdue_preds:
        results.append(CheckResult(
            "agent", "predictions", WARN,
            f"{overdue_preds} of {open_preds} past horizon — "
            "`pathos predict resolve <id> --outcome-eventual true|false`",
        ))
    else:
        results.append(CheckResult("agent", "predictions", OK,
                                   f"{open_preds or 0} open, none past horizon"))

    active_sets = _scalar(
        conn, "SELECT COUNT(*) FROM scenario_sets WHERE status = 'active'"
    )
    overdue_sets = _scalar(conn, """
        SELECT COUNT(*) FROM scenario_sets
        WHERE status = 'active' AND date(horizon_date) < date('now')
    """)
    if active_sets is _MISSING:
        results.append(CheckResult(
            "agent", "conflict scenarios", SKIP,
            "scenario tables absent (feature not merged/migrated)",
        ))
    elif overdue_sets not in (_MISSING, None) and overdue_sets:
        results.append(CheckResult(
            "agent", "conflict scenarios", WARN,
            f"{overdue_sets} of {active_sets} active sets past horizon — "
            "`pathos scenario resolve <id> --winner X`",
        ))
    else:
        results.append(CheckResult("agent", "conflict scenarios", OK,
                                   f"{active_sets or 0} active sets"))

    last_brief = _scalar(conn, "SELECT MAX(date) FROM briefs")
    if last_brief is _MISSING:
        results.append(CheckResult("agent", "brief", SKIP, "table missing"))
    elif last_brief is None:
        results.append(CheckResult("agent", "brief", WARN,
                                   "no briefs yet — `pathos brief`"))
    else:
        ts = _parse_ts(last_brief)
        age_days = _age_hours(ts) / 24 if ts else None
        if age_days is None:
            results.append(CheckResult("agent", "brief", WARN,
                                       f"unparseable date {last_brief!r}"))
        elif age_days > STALE_BRIEF_DAYS:
            results.append(CheckResult(
                "agent", "brief", WARN,
                f"latest is {last_brief} ({age_days:.0f}d old) — `pathos brief`",
            ))
        else:
            results.append(CheckResult("agent", "brief", OK,
                                       f"latest is {last_brief}"))
    return results


# ── network (opt-in) ─────────────────────────────────────────────────────────

def _check_market_data(network: bool) -> CheckResult:
    if not network:
        return CheckResult("network", "market data", SKIP,
                           "probe disabled — rerun with --network")
    try:
        import yfinance as yf

        price = yf.Ticker("SPY").fast_info["last_price"]
    except Exception as exc:  # yfinance raises assorted types — probe, not logic
        return CheckResult(
            "network", "market data", WARN,
            f"yfinance probe failed ({type(exc).__name__}) — price fetch / "
            "EOD update may be degraded (CP-023)",
        )
    if price:
        return CheckResult("network", "market data", OK,
                           "yfinance reachable (SPY quote fetched)")
    return CheckResult("network", "market data", WARN,
                       "yfinance returned no price for SPY")


def has_failures(results: list[CheckResult]) -> bool:
    return any(r.status == FAIL for r in results)


def run_doctor(
    conn: sqlite3.Connection, settings: Settings, *, network: bool = False
) -> list[CheckResult]:
    """Run all health checks; read-only, safe to run anytime."""
    results: list[CheckResult] = []
    results += _check_prerequisites(settings)
    results += _check_config(settings)
    results += _check_database(settings)
    results += _check_freshness(conn, settings)
    results += _check_backlog(conn)
    results += _check_agent_state(conn)
    results.append(_check_market_data(network))
    return results
