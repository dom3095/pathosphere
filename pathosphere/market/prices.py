"""
Current-price fetcher via yfinance.

Returns the last available closing price for a ticker — EOD granularity,
no real-time feed needed. Returns None on any failure (bad ticker,
network error, market closed) so callers can decide how to handle missing
price data without crashing the thesis pipeline.
"""

from __future__ import annotations

import yfinance as yf
from loguru import logger


def fetch_price(ticker: str) -> float | None:
    """Return the last closing price for *ticker*, or None on any failure.

    Uses a 5-day window to handle weekends / holidays gracefully.
    """
    if not ticker or not ticker.strip():
        return None

    ticker = ticker.strip().upper()
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            logger.warning(f"PRICES: no history for {ticker}")
            return None
        price = float(hist["Close"].iloc[-1])
        logger.debug(f"PRICES: {ticker} = {price:.4f}")
        return price
    except Exception as exc:
        logger.warning(f"PRICES: fetch failed for {ticker}: {exc}")
        return None
