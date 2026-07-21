"""Tests for pathosphere.market.fundamentals — all yfinance calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest

from pathosphere.market.fundamentals import (
    FundamentalsSnapshot,
    _altman_z,
    _piotroski_f,
    fetch_fundamentals,
    render_fundamentals_text,
)

_COLS = ["2025-12-31", "2024-12-31"]


def _df(rows: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [rows[k] for k in rows], index=list(rows), columns=_COLS
    )


_INFO_FULL = {
    "quoteType": "EQUITY",
    "sector": "Technology",
    "industry": "Semiconductors",
    "country": "United States",
    "marketCap": 120.0,
    "trailingPE": 30.0,
    "forwardPE": 25.0,
    "priceToBook": 10.0,
    "enterpriseToEbitda": 20.0,
    "debtToEquity": 40.0,
    "returnOnEquity": 0.30,
    "currentRatio": 2.5,
    "revenueGrowth": 0.20,
    "earningsGrowth": 0.30,
    "profitMargins": 0.25,
}

_BALANCE = _df({
    "Total Assets": [100.0, 80.0],
    "Total Liabilities Net Minority Interest": [40.0, 35.0],
    "Current Assets": [50.0, 40.0],
    "Current Liabilities": [20.0, 18.0],
    "Retained Earnings": [30.0, 25.0],
    "Long Term Debt": [10.0, 12.0],
    "Ordinary Shares Number": [1000.0, 1000.0],
})

_INCOME = _df({
    "Total Revenue": [90.0, 70.0],
    "EBIT": [25.0, 18.0],
    "Net Income": [20.0, 12.0],
    "Gross Profit": [50.0, 35.0],
})

_CASHFLOW = _df({
    "Operating Cash Flow": [28.0, 20.0],
})


def _mock_ticker(info=None, balance=None, income=None, cashflow=None):
    tk = MagicMock()
    tk.info = info if info is not None else {}
    tk.balance_sheet = balance if balance is not None else pd.DataFrame()
    tk.financials = income if income is not None else pd.DataFrame()
    tk.cashflow = cashflow if cashflow is not None else pd.DataFrame()
    return tk


# ── fetch: full equity ────────────────────────────────────────────────────────

def test_fetch_full_equity():
    tk = _mock_ticker(_INFO_FULL, _BALANCE, _INCOME, _CASHFLOW)
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("NVDA")

    assert snap is not None
    assert snap.quote_type == "EQUITY"
    assert snap.pe_trailing == pytest.approx(30.0)
    assert snap.roe == pytest.approx(0.30)
    # Z = 1.2*0.30 + 1.4*0.30 + 3.3*0.25 + 0.6*(120/40) + 1.0*0.90 = 4.305
    assert snap.altman_z == pytest.approx(4.31, abs=0.01)
    assert snap.altman_zone == "safe"
    # every Piotroski test passes on this dataset
    assert snap.piotroski_f == 9
    assert snap.piotroski_testable == 9
    assert snap.data_quality == "full"


def test_fetch_normalises_ticker():
    tk = _mock_ticker(_INFO_FULL, _BALANCE, _INCOME, _CASHFLOW)
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk) as mock_cls:
        fetch_fundamentals("  nvda  ")
    mock_cls.assert_called_once_with("NVDA")


# ── fetch: degradation paths ──────────────────────────────────────────────────

def test_fetch_empty_ticker_returns_none():
    assert fetch_fundamentals("") is None
    assert fetch_fundamentals("   ") is None


def test_fetch_no_info_returns_none():
    tk = _mock_ticker(info={})
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        assert fetch_fundamentals("BOGUS") is None


def test_fetch_info_exception_returns_none():
    tk = MagicMock()
    type(tk).info = PropertyMock(side_effect=RuntimeError("rate limited"))
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        assert fetch_fundamentals("NVDA") is None


def test_fetch_statements_failure_degrades_to_minimal():
    tk = MagicMock()
    tk.info = dict(_INFO_FULL)
    type(tk).balance_sheet = PropertyMock(side_effect=RuntimeError("paywall"))
    tk.financials = pd.DataFrame()
    tk.cashflow = pd.DataFrame()
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("TSM")

    assert snap is not None
    assert snap.altman_z is None
    assert snap.altman_zone == "unavailable"
    assert snap.piotroski_f is None
    assert snap.data_quality == "minimal"
    assert any("statements fetch partially failed" in w for w in snap.warnings)


def test_fetch_empty_statements_expected_for_non_us():
    tk = _mock_ticker(dict(_INFO_FULL, country="Taiwan"))
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("2330.TW")

    assert snap is not None
    assert snap.altman_zone == "unavailable"
    assert snap.data_quality == "minimal"
    assert any("statements empty" in w for w in snap.warnings)


# ── fetch: instrument-type handling ───────────────────────────────────────────

def test_fetch_etf_minimal_snapshot():
    tk = _mock_ticker({"quoteType": "ETF", "marketCap": 5e9})
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("USO")

    assert snap is not None
    assert snap.quote_type == "ETF"
    assert snap.data_quality == "minimal"
    assert snap.altman_zone == "not_applicable"
    assert snap.pe_trailing is None
    assert any("not applicable" in w for w in snap.warnings)


def test_fetch_financial_sector_skips_altman():
    info = dict(_INFO_FULL, sector="Financial Services", industry="Banks - Diversified")
    tk = _mock_ticker(info, _BALANCE, _INCOME, _CASHFLOW)
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("JPM")

    assert snap is not None
    assert snap.altman_z is None
    assert snap.altman_zone == "not_applicable"
    # Piotroski still computed — it is robust cross-sector
    assert snap.piotroski_f == 9


# ── score unit tests ──────────────────────────────────────────────────────────

def test_altman_distress_zone():
    balance = _df({
        "Total Assets": [100.0, 100.0],
        "Total Liabilities Net Minority Interest": [90.0, 85.0],
        "Current Assets": [10.0, 12.0],
        "Current Liabilities": [20.0, 18.0],
        "Retained Earnings": [-20.0, -10.0],
    })
    income = _df({
        "Total Revenue": [50.0, 55.0],
        "EBIT": [-5.0, -2.0],
    })
    z, zone = _altman_z(balance, income, market_cap=10.0)
    assert zone == "distress"
    assert z < 1.81


def test_altman_missing_components_unavailable():
    z, zone = _altman_z(pd.DataFrame(), pd.DataFrame(), market_cap=100.0)
    assert z is None
    assert zone == "unavailable"


def test_piotroski_partial_data():
    # Only income statement available → only tests with income-only inputs run
    score, testable = _piotroski_f(None, _INCOME, None)
    assert testable < 9
    score_none, testable_zero = _piotroski_f(None, None, None)
    assert score_none is None
    assert testable_zero == 0


# ── rendered text ─────────────────────────────────────────────────────────────

def test_render_text_full():
    tk = _mock_ticker(_INFO_FULL, _BALANCE, _INCOME, _CASHFLOW)
    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("NVDA")
    text = render_fundamentals_text(snap)

    assert "FUNDAMENTALS — NVDA" in text
    assert "safe zone" in text
    assert "9/9" in text
    assert "intra" in text.lower() or "same sector" in text


def test_render_text_missing_values_show_nd():
    snap = FundamentalsSnapshot(ticker="XYZ", quote_type="EQUITY", data_quality="minimal")
    text = render_fundamentals_text(snap)
    assert "n/d" in text
    assert "not computed" in text


def test_render_text_etf():
    snap = FundamentalsSnapshot(
        ticker="USO", quote_type="ETF", market_cap=5e9, data_quality="minimal"
    )
    text = render_fundamentals_text(snap)
    assert "do not apply" in text
    assert "underlying exposure" in text


# ── CP-023: retry/backoff on transient yfinance failures ─────────────────────

def test_info_fetch_retries_then_succeeds(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("pathosphere.market.fundamentals._sleep", delays.append)
    tk = _mock_ticker(_INFO_FULL, _BALANCE, _INCOME, _CASHFLOW)
    outcomes = iter([Exception("rate limited"), Exception("rate limited"), tk])

    def flaky_ticker(_t):
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("pathosphere.market.fundamentals.yf.Ticker", side_effect=flaky_ticker):
        snap = fetch_fundamentals("NVDA")

    assert snap is not None
    assert snap.quote_type == "EQUITY"
    assert delays == [2.0, 4.0]  # exponential backoff between the 3 attempts


def test_info_fetch_gives_up_after_all_attempts(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("pathosphere.market.fundamentals._sleep", delays.append)
    with patch(
        "pathosphere.market.fundamentals.yf.Ticker",
        side_effect=Exception("yahoo down"),
    ):
        assert fetch_fundamentals("NVDA") is None
    assert len(delays) == 2  # no sleep after the final attempt


def test_statements_failure_retries_then_degrades(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("pathosphere.market.fundamentals._sleep", delays.append)
    tk = MagicMock()
    tk.info = dict(_INFO_FULL)
    type(tk).balance_sheet = PropertyMock(side_effect=Exception("boom"))
    tk.financials = pd.DataFrame()
    tk.cashflow = pd.DataFrame()

    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("NVDA")

    assert snap is not None  # degrades, never raises — same contract as before
    assert any("statements fetch partially failed" in w for w in snap.warnings)
    assert len(delays) == 2


def test_statements_all_fail_no_duplicate_contradictory_warning(monkeypatch):
    """Regression: the 'financial statements empty on yfinance' guard used
    to string-match the OLD 'statements fetch failed' warning text; once the
    per-statement fix reworded it to 'statements fetch partially failed',
    the guard silently stopped matching and both warnings (one saying
    'failed', one implying 'just empty') got appended together whenever all
    three statements failed. Only the specific per-statement failure message
    should appear."""
    monkeypatch.setattr("pathosphere.market.fundamentals._sleep", lambda _delay: None)
    tk = MagicMock()
    tk.info = dict(_INFO_FULL)
    type(tk).balance_sheet = PropertyMock(side_effect=Exception("down"))
    type(tk).financials = PropertyMock(side_effect=Exception("down"))
    type(tk).cashflow = PropertyMock(side_effect=Exception("down"))

    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("NVDA")

    assert snap is not None
    assert any("statements fetch partially failed" in w for w in snap.warnings)
    assert not any("financial statements empty on yfinance" in w for w in snap.warnings)


def test_statements_partial_failure_keeps_successful_statement(monkeypatch):
    """CP-032 review fix regression test: balance_sheet keeps fetching fine
    on every attempt while financials permanently fails — the pre-fix code
    bundled all three into one retry unit and discarded balance_sheet too;
    each statement must now retry independently so a successful sibling
    survives a permanently-failing one."""
    monkeypatch.setattr("pathosphere.market.fundamentals._sleep", lambda _delay: None)
    tk = MagicMock()
    tk.info = dict(_INFO_FULL)
    tk.balance_sheet = _BALANCE
    type(tk).financials = PropertyMock(side_effect=Exception("permanently broken"))
    tk.cashflow = _CASHFLOW

    with patch("pathosphere.market.fundamentals.yf.Ticker", return_value=tk):
        snap = fetch_fundamentals("NVDA")

    assert snap is not None
    assert any(
        "statements fetch partially failed" in w and "financials: permanently broken" in w
        for w in snap.warnings
    )
    # balance_sheet/cashflow survived — enough for a real Piotroski/Altman
    # score instead of the "minimal" degradation a fully-discarded retry
    # would have produced.
    assert snap.piotroski_f is not None
