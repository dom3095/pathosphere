"""
Fundamentals enrichment via yfinance — context layer for the LLM agent.

Fetches valuation ratios, Altman Z-score and Piotroski F-score for a ticker
and renders a short prompt-ready interpretive text. This module is an
ENRICHMENT layer: it never decides anything (no auto-reject thresholds) —
the LLM agent and the human read the data and reason over it.

Degradation contract (same as prices.fetch_price):
  - returns None only on total failure (empty ticker, no data at all);
  - partial data → FundamentalsSnapshot with None fields + warnings list;
  - non-equity (ETF, index, crypto, FX) → minimal snapshot, flagged;
  - financial-sector companies → Altman Z skipped (leverage is their core
    business, the score is meaningless there), flagged as not_applicable;
  - never raises: any yfinance/network error is caught and logged.

Known data caveats (handled as EXPECTED, not exceptional):
  - yfinance statements are often empty or misaligned for non-US/small-cap;
  - Yahoo rate-limits unauthenticated scraping;
  - ratios are only comparable intra-sector (the rendered text says so).

SEC EDGAR cross-check deliberately deferred to v2: ticker→CIK mapping plus
US-filers-only coverage plus ~45d filing delay adds complexity that is not
worth it for a v1 enrichment layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from loguru import logger

# Altman Z-score standard thresholds (original 1968 model)
ALTMAN_SAFE = 2.99
ALTMAN_DISTRESS = 1.81

# quoteType values for which company fundamentals apply
_EQUITY_TYPES = {"EQUITY"}

# Sectors / industry keywords where Altman Z is not applicable
_FINANCIAL_SECTORS = {"financial services", "financial", "financials"}
_FINANCIAL_INDUSTRY_KEYWORDS = ("bank", "insurance", "capital markets", "credit")


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class FundamentalsSnapshot:
    ticker: str
    quote_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    market_cap: float | None = None
    # valuation / balance ratios (from .info)
    pe_trailing: float | None = None
    pe_forward: float | None = None
    price_to_book: float | None = None
    ev_ebitda: float | None = None
    debt_to_equity: float | None = None   # yfinance convention: percent (150 = 1.5x)
    roe: float | None = None              # fraction (0.15 = 15%)
    current_ratio: float | None = None
    revenue_growth: float | None = None   # fraction YoY
    earnings_growth: float | None = None  # fraction YoY
    profit_margin: float | None = None    # fraction
    # composite scores (from statements)
    altman_z: float | None = None
    altman_zone: str | None = None        # safe | grey | distress | not_applicable | unavailable
    piotroski_f: int | None = None
    piotroski_testable: int | None = None # how many of the 9 tests had data
    # meta
    data_quality: str = "none"            # full | partial | minimal | none
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── DataFrame helpers ─────────────────────────────────────────────────────────

def _row(df: pd.DataFrame | None, labels: list[str], col: int = 0) -> float | None:
    """First matching row label at column *col*, as float. None if absent/NaN."""
    if df is None or df.empty or col >= len(df.columns):
        return None
    for label in labels:
        if label in df.index:
            try:
                value = df.loc[label].iloc[col]
            except (IndexError, KeyError):
                continue
            if pd.notna(value):
                return float(value)
    return None


def _num(info: dict, key: str) -> float | None:
    value = info.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and pd.notna(value):
        return float(value)
    return None


def _is_financial(sector: str | None, industry: str | None) -> bool:
    if sector and sector.strip().lower() in _FINANCIAL_SECTORS:
        return True
    if industry:
        low = industry.lower()
        return any(kw in low for kw in _FINANCIAL_INDUSTRY_KEYWORDS)
    return False


# ── composite scores ──────────────────────────────────────────────────────────

def _altman_z(
    balance: pd.DataFrame | None,
    income: pd.DataFrame | None,
    market_cap: float | None,
) -> tuple[float | None, str]:
    """Original 5-factor Altman Z. Returns (score, zone).

    Requires ALL five components — a partial Z is not a Z. Missing data →
    (None, 'unavailable'). Thresholds: >2.99 safe, <1.81 distress, else grey.
    """
    total_assets = _row(balance, ["Total Assets"])
    total_liab = _row(balance, ["Total Liabilities Net Minority Interest", "Total Liab"])
    working_capital = _row(balance, ["Working Capital"])
    if working_capital is None:
        cur_assets = _row(balance, ["Current Assets", "Total Current Assets"])
        cur_liab = _row(balance, ["Current Liabilities", "Total Current Liabilities"])
        if cur_assets is not None and cur_liab is not None:
            working_capital = cur_assets - cur_liab
    retained = _row(balance, ["Retained Earnings"])
    ebit = _row(income, ["EBIT", "Operating Income"])
    revenue = _row(income, ["Total Revenue", "Operating Revenue"])

    components = [total_assets, total_liab, working_capital, retained, ebit, revenue, market_cap]
    if any(c is None for c in components) or not total_assets or not total_liab:
        return None, "unavailable"

    z = (
        1.2 * (working_capital / total_assets)
        + 1.4 * (retained / total_assets)
        + 3.3 * (ebit / total_assets)
        + 0.6 * (market_cap / total_liab)
        + 1.0 * (revenue / total_assets)
    )
    if z > ALTMAN_SAFE:
        zone = "safe"
    elif z < ALTMAN_DISTRESS:
        zone = "distress"
    else:
        zone = "grey"
    return round(z, 2), zone


def _piotroski_f(
    balance: pd.DataFrame | None,
    income: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
) -> tuple[int | None, int]:
    """Piotroski F-score (0-9) over the two most recent annual periods.

    Each of the 9 tests is scored only when its inputs exist; returns
    (score, testable_count). testable_count == 0 → (None, 0).
    """
    ta0 = _row(balance, ["Total Assets"], 0)
    ta1 = _row(balance, ["Total Assets"], 1)
    ni0 = _row(income, ["Net Income", "Net Income Common Stockholders"], 0)
    ni1 = _row(income, ["Net Income", "Net Income Common Stockholders"], 1)
    ocf0 = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], 0)
    rev0 = _row(income, ["Total Revenue", "Operating Revenue"], 0)
    rev1 = _row(income, ["Total Revenue", "Operating Revenue"], 1)

    score = 0
    testable = 0

    def _test(condition_inputs: list[float | None], passed: bool) -> None:
        nonlocal score, testable
        if any(v is None for v in condition_inputs):
            return
        testable += 1
        if passed:
            score += 1

    # ── profitability ──
    roa0 = ni0 / ta0 if ni0 is not None and ta0 else None
    roa1 = ni1 / ta1 if ni1 is not None and ta1 else None
    _test([roa0], roa0 is not None and roa0 > 0)                       # 1. ROA > 0
    _test([ocf0], ocf0 is not None and ocf0 > 0)                       # 2. OCF > 0
    _test([roa0, roa1], None not in (roa0, roa1) and roa0 > roa1)      # 3. ΔROA > 0
    _test([ocf0, ni0], None not in (ocf0, ni0) and ocf0 > ni0)         # 4. accruals: OCF > NI

    # ── leverage / liquidity / dilution ──
    ltd0 = _row(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 0)
    ltd1 = _row(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 1)
    lev0 = ltd0 / ta0 if ltd0 is not None and ta0 else None
    lev1 = ltd1 / ta1 if ltd1 is not None and ta1 else None
    _test([lev0, lev1], None not in (lev0, lev1) and lev0 <= lev1)     # 5. leverage down

    ca0 = _row(balance, ["Current Assets", "Total Current Assets"], 0)
    cl0 = _row(balance, ["Current Liabilities", "Total Current Liabilities"], 0)
    ca1 = _row(balance, ["Current Assets", "Total Current Assets"], 1)
    cl1 = _row(balance, ["Current Liabilities", "Total Current Liabilities"], 1)
    cr0 = ca0 / cl0 if ca0 is not None and cl0 else None
    cr1 = ca1 / cl1 if ca1 is not None and cl1 else None
    _test([cr0, cr1], None not in (cr0, cr1) and cr0 > cr1)            # 6. Δcurrent ratio > 0

    sh0 = _row(balance, ["Ordinary Shares Number", "Share Issued"], 0)
    sh1 = _row(balance, ["Ordinary Shares Number", "Share Issued"], 1)
    _test([sh0, sh1], None not in (sh0, sh1) and sh0 <= sh1)           # 7. no new shares

    # ── operating efficiency ──
    gp0 = _row(income, ["Gross Profit"], 0)
    gp1 = _row(income, ["Gross Profit"], 1)
    gm0 = gp0 / rev0 if gp0 is not None and rev0 else None
    gm1 = gp1 / rev1 if gp1 is not None and rev1 else None
    _test([gm0, gm1], None not in (gm0, gm1) and gm0 > gm1)            # 8. Δgross margin > 0

    at0 = rev0 / ta0 if rev0 is not None and ta0 else None
    at1 = rev1 / ta1 if rev1 is not None and ta1 else None
    _test([at0, at1], None not in (at0, at1) and at0 > at1)            # 9. Δasset turnover > 0

    if testable == 0:
        return None, 0
    return score, testable


# ── public API ────────────────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str) -> FundamentalsSnapshot | None:
    """Fetch fundamentals for *ticker*, or None on total failure.

    Never raises. Partial data is the expected case for non-US/small-cap
    tickers: missing pieces become None fields plus an entry in .warnings.
    """
    if not ticker or not ticker.strip():
        return None

    ticker = ticker.strip().upper()
    snap = FundamentalsSnapshot(
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        tk = yf.Ticker(ticker)
        info: dict = tk.info or {}
    except Exception as exc:
        logger.warning(f"FUNDAMENTALS: info fetch failed for {ticker}: {exc}")
        return None

    if not info or (info.get("quoteType") is None and info.get("marketCap") is None):
        logger.warning(f"FUNDAMENTALS: no info data for {ticker}")
        return None

    snap.quote_type = info.get("quoteType")
    snap.sector = info.get("sector")
    snap.industry = info.get("industry")
    snap.country = info.get("country")
    snap.market_cap = _num(info, "marketCap")

    if snap.quote_type not in _EQUITY_TYPES:
        snap.data_quality = "minimal"
        snap.altman_zone = "not_applicable"
        snap.warnings.append(
            f"quoteType={snap.quote_type}: company fundamentals not applicable "
            "(ETF/index/fund/FX) — only market cap / type reported"
        )
        logger.info(f"FUNDAMENTALS: {ticker} is {snap.quote_type}, minimal snapshot")
        return snap

    # ── ratios from .info ──
    snap.pe_trailing = _num(info, "trailingPE")
    snap.pe_forward = _num(info, "forwardPE")
    snap.price_to_book = _num(info, "priceToBook")
    snap.ev_ebitda = _num(info, "enterpriseToEbitda")
    snap.debt_to_equity = _num(info, "debtToEquity")
    snap.roe = _num(info, "returnOnEquity")
    snap.current_ratio = _num(info, "currentRatio")
    snap.revenue_growth = _num(info, "revenueGrowth")
    snap.earnings_growth = _num(info, "earningsGrowth")
    snap.profit_margin = _num(info, "profitMargins")

    ratio_fields = [
        snap.pe_trailing, snap.pe_forward, snap.price_to_book, snap.ev_ebitda,
        snap.debt_to_equity, snap.roe, snap.current_ratio,
        snap.revenue_growth, snap.earnings_growth, snap.profit_margin,
    ]
    ratios_present = sum(1 for r in ratio_fields if r is not None)
    if ratios_present < len(ratio_fields):
        snap.warnings.append(
            f"{len(ratio_fields) - ratios_present}/{len(ratio_fields)} ratios missing "
            "(expected for non-US / small-cap on yfinance)"
        )

    # ── financial statements (may be empty/stale — expected) ──
    balance = income = cashflow = None
    try:
        balance = tk.balance_sheet
        income = tk.financials
        cashflow = tk.cashflow
    except Exception as exc:
        snap.warnings.append(f"statements fetch failed: {exc}")
        logger.warning(f"FUNDAMENTALS: statements fetch failed for {ticker}: {exc}")

    statements_ok = any(
        df is not None and not df.empty for df in (balance, income, cashflow)
    )
    if not statements_ok and "statements fetch failed" not in " ".join(snap.warnings):
        snap.warnings.append("financial statements empty on yfinance")

    # ── Altman Z (skip for financials — leverage is their core business) ──
    if _is_financial(snap.sector, snap.industry):
        snap.altman_zone = "not_applicable"
        snap.warnings.append(
            "Altman Z not applicable to financial-sector companies (skipped)"
        )
    else:
        snap.altman_z, snap.altman_zone = _altman_z(balance, income, snap.market_cap)
        if snap.altman_zone == "unavailable":
            snap.warnings.append("Altman Z components missing — score not computed")

    # ── Piotroski F ──
    snap.piotroski_f, testable = _piotroski_f(balance, income, cashflow)
    snap.piotroski_testable = testable or None
    if testable < 9:
        snap.warnings.append(f"Piotroski F: only {testable}/9 tests had data")

    # ── data quality ──
    if statements_ok and ratios_present >= 5 and testable >= 7:
        snap.data_quality = "full"
    elif statements_ok:
        snap.data_quality = "partial"
    elif ratios_present > 0:
        snap.data_quality = "minimal"
    else:
        snap.data_quality = "none"

    logger.debug(
        f"FUNDAMENTALS: {ticker} quality={snap.data_quality} "
        f"Z={snap.altman_z} ({snap.altman_zone}) F={snap.piotroski_f}/{snap.piotroski_testable}"
    )
    return snap


# ── interpretive text (deterministic template, LLM-prompt-ready) ──────────────

def _fmt(value: float | None, pattern: str = "{:.2f}") -> str:
    return pattern.format(value) if value is not None else "n/d"


def _fmt_pct(value: float | None) -> str:
    return f"{value:+.0%}" if value is not None else "n/d"


def render_fundamentals_text(snap: FundamentalsSnapshot) -> str:
    """Short prompt-ready interpretive block. Deterministic — no LLM call.

    Meant to be injected into an LLM prompt (fundamentals review pass) and
    shown to the human during thesis approval. States its own caveats so the
    reader never over-trusts the numbers.
    """
    header = f"FUNDAMENTALS — {snap.ticker}"
    meta_bits = [b for b in (snap.quote_type, snap.sector, snap.industry, snap.country) if b]
    if meta_bits:
        header += f" ({', '.join(meta_bits)})"

    if snap.quote_type not in _EQUITY_TYPES:
        return (
            f"{header}\n"
            f"Instrument type {snap.quote_type or 'unknown'}: company fundamentals "
            "(ratios, Altman Z, Piotroski F) do not apply. "
            f"Market cap / AUM: {_fmt(snap.market_cap, '{:,.0f}')}.\n"
            "Judge this instrument on its underlying exposure, not on issuer ratios."
        )

    mcap = f"{snap.market_cap:,.0f}" if snap.market_cap is not None else "n/d"

    if snap.altman_zone == "safe":
        z_line = f"{snap.altman_z} — safe zone (> {ALTMAN_SAFE}), low bankruptcy signal"
    elif snap.altman_zone == "distress":
        z_line = f"{snap.altman_z} — DISTRESS zone (< {ALTMAN_DISTRESS}), elevated bankruptcy signal"
    elif snap.altman_zone == "grey":
        z_line = f"{snap.altman_z} — grey zone ({ALTMAN_DISTRESS}-{ALTMAN_SAFE}), inconclusive"
    elif snap.altman_zone == "not_applicable":
        z_line = "not applicable (financial-sector company — leverage is core business)"
    else:
        z_line = "not computed (missing balance-sheet data)"

    if snap.piotroski_f is not None:
        f_line = (
            f"{snap.piotroski_f}/{snap.piotroski_testable} passed "
            f"({snap.piotroski_testable}/9 tests had data; "
            "high = strong fundamentals momentum, low = weak)"
        )
    else:
        f_line = "not computed (missing statement data)"

    lines = [
        header,
        f"Market cap: {mcap}",
        (
            f"Valuation: P/E {_fmt(snap.pe_trailing)} trailing / {_fmt(snap.pe_forward)} forward; "
            f"P/B {_fmt(snap.price_to_book)}; EV/EBITDA {_fmt(snap.ev_ebitda)}"
        ),
        (
            f"Balance: D/E {_fmt(snap.debt_to_equity)}% ; current ratio {_fmt(snap.current_ratio)}; "
            f"ROE {_fmt_pct(snap.roe)}"
        ),
        (
            f"Growth: revenue {_fmt_pct(snap.revenue_growth)} YoY; "
            f"earnings {_fmt_pct(snap.earnings_growth)} YoY; "
            f"net margin {_fmt_pct(snap.profit_margin)}"
        ),
        f"Altman Z-score: {z_line}",
        f"Piotroski F-score: {f_line}",
        f"Data quality: {snap.data_quality} (source: yfinance, may be stale for non-US/small-cap)",
        (
            "Caveat: ratios are comparable only within the same sector — "
            "never as absolute standalone thresholds."
        ),
    ]
    return "\n".join(lines)
