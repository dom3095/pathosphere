"""Tests for pathosphere.market.technicals — all yfinance calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pathosphere.market.technicals import (
    TechnicalsSnapshot,
    _annualized_vol,
    _max_drawdown,
    _rsi,
    _trailing_return,
    _volume_ratio,
    _vs_sma,
    fetch_technicals,
    render_technicals_text,
)


def _hist(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-14", periods=len(closes))
    data = {"Close": closes}
    if volumes is not None:
        data["Volume"] = volumes
    return pd.DataFrame(data, index=index)


def _mock_ticker(hist: pd.DataFrame | Exception) -> MagicMock:
    tk = MagicMock()
    if isinstance(hist, Exception):
        tk.history.side_effect = hist
    else:
        tk.history.return_value = hist
    return tk


# ── metric helpers ────────────────────────────────────────────────────────────

def test_trailing_return_basic():
    close = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
    assert _trailing_return(close, 5) == pytest.approx(0.10)


def test_trailing_return_window_not_covered():
    close = pd.Series([100.0, 110.0])
    assert _trailing_return(close, 5) is None


def test_rsi_all_gains_is_100():
    close = pd.Series([float(i) for i in range(1, 40)])
    assert _rsi(close) == 100.0


def test_rsi_needs_period_plus_one_bars():
    close = pd.Series([float(i) for i in range(10)])
    assert _rsi(close) is None


def test_rsi_mixed_series_in_bounds():
    closes = [100 + (3 if i % 2 else -2) * (i % 5) for i in range(60)]
    rsi = _rsi(pd.Series([float(c) for c in closes]))
    assert rsi is not None
    assert 0 <= rsi <= 100


def test_annualized_vol_flat_series_is_zero():
    close = pd.Series([100.0] * 30)
    assert _annualized_vol(close) == pytest.approx(0.0)


def test_annualized_vol_too_short():
    close = pd.Series([100.0, 101.0])
    assert _annualized_vol(close) is None


def test_vs_sma_above_average():
    close = pd.Series([100.0] * 19 + [120.0])
    assert _vs_sma(close, 20) == pytest.approx(120.0 / 101.0 - 1)


def test_vs_sma_window_not_covered():
    assert _vs_sma(pd.Series([100.0] * 10), 20) is None


def test_max_drawdown():
    close = pd.Series([100.0, 120.0, 90.0, 110.0])
    assert _max_drawdown(close) == pytest.approx(90.0 / 120.0 - 1)


def test_volume_ratio_none_when_absent():
    assert _volume_ratio(None) is None
    assert _volume_ratio(pd.Series([0.0] * 100)) is None


def test_volume_ratio_computed():
    vol = pd.Series([100.0] * 42 + [200.0] * 21)  # 63 bars, last 21 doubled
    ratio = _volume_ratio(vol)
    expected = 200.0 / ((42 * 100.0 + 21 * 200.0) / 63)
    assert ratio == pytest.approx(expected)


# ── fetch_technicals ──────────────────────────────────────────────────────────

def test_fetch_empty_ticker_returns_none():
    assert fetch_technicals("") is None
    assert fetch_technicals("   ") is None


def test_fetch_history_exception_returns_none():
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(RuntimeError("boom"))):
        assert fetch_technicals("FAIL") is None


def test_fetch_empty_history_returns_none():
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(pd.DataFrame())):
        assert fetch_technicals("EMPTY") is None


def test_fetch_single_bar_returns_none():
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(_hist([100.0]))):
        assert fetch_technicals("ONEBAR") is None


def test_fetch_full_history():
    closes = [100.0 + i * 0.1 for i in range(252)]
    volumes = [1000.0] * 252
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(_hist(closes, volumes))):
        snap = fetch_technicals("full")

    assert snap is not None
    assert snap.ticker == "FULL"  # normalized upper
    assert snap.bars == 252
    assert snap.data_quality == "full"
    assert snap.as_of == "2026-07-14"
    assert snap.last_close == pytest.approx(closes[-1])
    assert snap.return_1w == pytest.approx(closes[-1] / closes[-6] - 1)
    assert snap.return_1y == pytest.approx(closes[-1] / closes[0] - 1)
    assert snap.vs_sma_200 is not None
    # monotonically rising series: at 52w high, max above low, no drawdown
    assert snap.pct_from_52w_high == pytest.approx(0.0)
    assert snap.pct_above_52w_low == pytest.approx(closes[-1] / closes[0] - 1)
    assert snap.max_drawdown_1y == pytest.approx(0.0)
    assert snap.rsi_14 == 100.0
    assert snap.volume_ratio_21_63 == pytest.approx(1.0)


def test_fetch_partial_history():
    closes = [100.0] * 100
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(_hist(closes))):
        snap = fetch_technicals("PART")

    assert snap is not None
    assert snap.data_quality == "partial"
    assert snap.vs_sma_200 is None
    assert snap.return_1y is None  # window too short to call it a 1y return
    assert any("daily bars" in w for w in snap.warnings)


def test_fetch_minimal_history():
    closes = [100.0] * 30
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(_hist(closes))):
        snap = fetch_technicals("TINY")

    assert snap is not None
    assert snap.data_quality == "minimal"
    assert snap.return_3m is None
    assert any("recent listing" in w for w in snap.warnings)


def test_fetch_no_volume_column_warns():
    closes = [100.0] * 252
    with patch("pathosphere.market.technicals.yf.Ticker",
               return_value=_mock_ticker(_hist(closes))):
        snap = fetch_technicals("NOVOL")

    assert snap is not None
    assert snap.volume_ratio_21_63 is None
    assert any("volume" in w for w in snap.warnings)


# ── render_technicals_text ────────────────────────────────────────────────────

def test_render_full_snapshot():
    snap = TechnicalsSnapshot(
        ticker="BZ=F",
        as_of="2026-07-14",
        last_close=78.12,
        bars=251,
        return_1w=0.021,
        return_1m=-0.034,
        return_3m=0.08,
        return_1y=0.123,
        volatility_21d=0.32,
        rsi_14=61.0,
        vs_sma_20=0.012,
        vs_sma_50=0.034,
        vs_sma_200=-0.008,
        pct_from_52w_high=-0.042,
        pct_above_52w_low=0.189,
        max_drawdown_1y=-0.153,
        volume_ratio_21_63=1.25,
        data_quality="full",
    )
    text = render_technicals_text(snap)
    assert "TECHNICALS — BZ=F" in text
    assert "as of 2026-07-14" in text
    assert "1w +2.1%" in text
    assert "RSI(14) 61" in text
    assert "SMA200 -0.8%" in text
    assert "-4.2% from 52w high" in text
    assert "1.25x" in text
    assert "Caveat" in text
    assert "not a trading signal" in text


def test_render_sparse_snapshot_uses_nd():
    snap = TechnicalsSnapshot(ticker="X", bars=5, data_quality="minimal")
    text = render_technicals_text(snap)
    assert "n/d" in text
    assert "Volume: n/d" in text
