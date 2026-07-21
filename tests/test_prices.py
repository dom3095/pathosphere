"""Tests for pathosphere.market.prices."""

from unittest.mock import patch

import pandas as pd
import pytest

from pathosphere.market.prices import fetch_price


def _make_hist(close: float) -> pd.DataFrame:
    return pd.DataFrame({"Close": [close]})


def test_fetch_price_returns_float():
    with patch("pathosphere.market.prices.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = _make_hist(123.45)
        price = fetch_price("USO")
    assert price == pytest.approx(123.45)


def test_fetch_price_empty_history_returns_none():
    with patch("pathosphere.market.prices.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = pd.DataFrame()
        price = fetch_price("INVALID_TICKER_XYZ")
    assert price is None


def test_fetch_price_exception_returns_none():
    with patch("pathosphere.market.prices.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.side_effect = RuntimeError("network error")
        price = fetch_price("USO")
    assert price is None


def test_fetch_price_empty_ticker_returns_none():
    assert fetch_price("") is None
    assert fetch_price("   ") is None


def test_fetch_price_normalises_ticker():
    with patch("pathosphere.market.prices.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = _make_hist(50.0)
        fetch_price("  spy  ")
    mock_ticker.assert_called_once_with("SPY")
