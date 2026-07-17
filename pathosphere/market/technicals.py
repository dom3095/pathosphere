"""
Technical / price-action enrichment via yfinance — context layer for the LLM agent.

Fetches one year of daily EOD history for a ticker and computes descriptive
price-action metrics (returns, volatility, RSI, moving-average distances,
52-week range, max drawdown, volume trend) plus a short prompt-ready
interpretive text. Like fundamentals.py this is an ENRICHMENT layer: it never
decides anything (no signals, no buy/sell thresholds) — the LLM agent and the
human read the data and reason over it.

Complementary to fundamentals.py by design: fundamentals only apply to EQUITY
quote types and degrade to minimal for ETF/futures/FX — exactly the
instruments (BZ=F, ITA, FRO...) a geopolitical desk proposes most. Price
history exists for all of them, so technicals cover the gap.

Degradation contract (same as fundamentals.fetch_fundamentals):
  - returns None only on total failure (empty ticker, no price history);
  - short history → TechnicalsSnapshot with None fields + warnings list;
  - never raises: any yfinance/network error is caught and logged.

No-lookahead by construction: everything is computed from EOD bars up to the
fetch time, snapshotted once at thesis-generation time, never refreshed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from loguru import logger

# trading-day offsets for the momentum ladder (1y is the full window instead —
# a fixed 252 offset would never be covered by a "1y" yfinance fetch)
_RETURN_WINDOWS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126}

_RSI_PERIOD = 14
_VOL_WINDOW = 21          # ~1 trading month
_TRADING_DAYS_YEAR = 252
_FULL_YEAR_BARS = 240     # ~1 trading year, tolerant of market holidays
_SMA_WINDOWS = (20, 50, 200)


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class TechnicalsSnapshot:
    ticker: str
    as_of: str | None = None              # date of the last daily bar
    last_close: float | None = None
    bars: int = 0                         # daily bars available in the window
    # momentum (fractions, e.g. 0.05 = +5%)
    return_1w: float | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    return_1y: float | None = None
    # volatility / oscillator
    volatility_21d: float | None = None   # annualized stdev of daily returns, fraction
    rsi_14: float | None = None           # 0-100, Wilder smoothing
    # trend (price distance from SMA, fraction; None if SMA window not covered)
    vs_sma_20: float | None = None
    vs_sma_50: float | None = None
    vs_sma_200: float | None = None
    # range
    pct_from_52w_high: float | None = None  # <= 0
    pct_above_52w_low: float | None = None  # >= 0
    max_drawdown_1y: float | None = None    # <= 0, worst peak-to-trough in window
    # volume
    volume_ratio_21_63: float | None = None  # 21d avg volume / 63d avg volume
    # meta
    data_quality: str = "none"            # full | partial | minimal | none
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── metric helpers ────────────────────────────────────────────────────────────

def _trailing_return(close: pd.Series, bars_back: int) -> float | None:
    """Return over the last *bars_back* trading days. None if not covered."""
    if len(close) <= bars_back:
        return None
    past = close.iloc[-1 - bars_back]
    if not past:
        return None
    return float(close.iloc[-1] / past - 1)


def _rsi(close: pd.Series, period: int = _RSI_PERIOD) -> float | None:
    """RSI with Wilder smoothing. None if fewer than period+1 bars.

    A perfectly flat series (zero gains AND zero losses — halted/illiquid
    listing) has no defined RSI: returning 100 there would feed a fake
    'max overbought' reading into the review prompt, so it degrades to None.
    """
    if len(close) <= period:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else None
    rs = last_gain / last_loss
    return round(100 - 100 / (1 + rs), 1)


def _annualized_vol(close: pd.Series, window: int = _VOL_WINDOW) -> float | None:
    """Annualized stdev of daily returns over the last *window* days."""
    returns = close.pct_change().dropna().tail(window)
    if len(returns) < 2:
        return None
    return float(returns.std() * math.sqrt(_TRADING_DAYS_YEAR))


def _vs_sma(close: pd.Series, window: int) -> float | None:
    """Price distance from its *window*-day SMA, as a fraction."""
    if len(close) < window:
        return None
    sma = float(close.tail(window).mean())
    if not sma:
        return None
    return float(close.iloc[-1] / sma - 1)


def _max_drawdown(close: pd.Series) -> float | None:
    """Worst peak-to-trough decline in the window (<= 0)."""
    if len(close) < 2:
        return None
    return float((close / close.cummax() - 1).min())


def _volume_ratio(volume: pd.Series | None) -> float | None:
    """21d avg volume / 63d avg volume. None if volume data absent (FX...)."""
    if volume is None:
        return None
    volume = volume.dropna()
    if len(volume) < _VOL_WINDOW:
        return None
    long_avg = float(volume.tail(63).mean())
    if not long_avg:
        return None
    return float(volume.tail(_VOL_WINDOW).mean() / long_avg)


# ── public API ────────────────────────────────────────────────────────────────

def fetch_technicals(ticker: str) -> TechnicalsSnapshot | None:
    """Fetch price-action technicals for *ticker*, or None on total failure.

    Never raises. Short history (recent IPO, illiquid listing) is the
    expected case: uncovered windows become None fields plus a warning.
    """
    if not ticker or not ticker.strip():
        return None

    ticker = ticker.strip().upper()
    snap = TechnicalsSnapshot(
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
    except Exception as exc:
        logger.warning(f"TECHNICALS: history fetch failed for {ticker}: {exc}")
        return None

    if hist is None or hist.empty or "Close" not in hist:
        logger.warning(f"TECHNICALS: no price history for {ticker}")
        return None

    close = hist["Close"].dropna()
    if len(close) < 2:
        logger.warning(f"TECHNICALS: <2 usable bars for {ticker}")
        return None

    snap.bars = len(close)
    snap.last_close = float(close.iloc[-1])
    snap.as_of = str(pd.Timestamp(close.index[-1]).date())

    snap.return_1w = _trailing_return(close, _RETURN_WINDOWS["1w"])
    snap.return_1m = _trailing_return(close, _RETURN_WINDOWS["1m"])
    snap.return_3m = _trailing_return(close, _RETURN_WINDOWS["3m"])
    snap.return_6m = _trailing_return(close, _RETURN_WINDOWS["6m"])
    # a "1y" period rarely yields exactly 252 bars — use the full window,
    # but only when it is close enough to a year to be honest about the label
    if snap.bars >= _FULL_YEAR_BARS:
        snap.return_1y = float(close.iloc[-1] / close.iloc[0] - 1) if close.iloc[0] else None

    snap.volatility_21d = _annualized_vol(close)
    snap.rsi_14 = _rsi(close)

    snap.vs_sma_20 = _vs_sma(close, 20)
    snap.vs_sma_50 = _vs_sma(close, 50)
    snap.vs_sma_200 = _vs_sma(close, 200)

    high = float(close.max())
    low = float(close.min())
    if high:
        snap.pct_from_52w_high = float(close.iloc[-1] / high - 1)
    if low:
        snap.pct_above_52w_low = float(close.iloc[-1] / low - 1)
    snap.max_drawdown_1y = _max_drawdown(close)

    snap.volume_ratio_21_63 = _volume_ratio(hist.get("Volume"))
    if snap.volume_ratio_21_63 is None:
        snap.warnings.append("volume data absent or too short (normal for FX/indices)")

    if snap.bars >= 200:
        snap.data_quality = "full"
        if snap.bars < _FULL_YEAR_BARS:
            snap.warnings.append(
                f"{snap.bars} daily bars (< ~1 trading year) — 1y return unavailable, "
                "high/low/drawdown cover this shorter window"
            )
    elif snap.bars >= 60:
        snap.data_quality = "partial"
        snap.warnings.append(
            f"only {snap.bars} daily bars — 52w range/drawdown cover a shorter window, "
            "SMA200 and 1y return unavailable"
        )
    else:
        snap.data_quality = "minimal"
        snap.warnings.append(
            f"only {snap.bars} daily bars (recent listing?) — most windows uncovered"
        )

    logger.debug(
        f"TECHNICALS: {ticker} quality={snap.data_quality} bars={snap.bars} "
        f"1m={snap.return_1m} rsi={snap.rsi_14} vol={snap.volatility_21d}"
    )
    return snap


# ── interpretive text (deterministic template, LLM-prompt-ready) ──────────────

def _fmt_pct(value: float | None) -> str:
    return f"{value:+.1%}" if value is not None else "n/d"


def _fmt(value: float | None, pattern: str = "{:.2f}") -> str:
    return pattern.format(value) if value is not None else "n/d"


def render_technicals_text(snap: TechnicalsSnapshot) -> str:
    """Short prompt-ready interpretive block. Deterministic — no LLM call.

    Injected into the market-context review prompt alongside fundamentals and
    shown to the human during thesis approval. Descriptive only: it states
    where price stands, never what to do about it.
    """
    header = f"TECHNICALS — {snap.ticker}"
    if snap.as_of:
        header += f" (as of {snap.as_of}, last close {_fmt(snap.last_close)})"

    momentum = (
        f"Momentum: 1w {_fmt_pct(snap.return_1w)}, 1m {_fmt_pct(snap.return_1m)}, "
        f"3m {_fmt_pct(snap.return_3m)}, 6m {_fmt_pct(snap.return_6m)}, "
        f"1y {_fmt_pct(snap.return_1y)}"
    )
    vol_bits = [
        f"21d annualized {_fmt_pct(snap.volatility_21d)}"
        if snap.volatility_21d is not None else "21d annualized n/d"
    ]
    if snap.rsi_14 is not None:
        vol_bits.append(f"RSI(14) {snap.rsi_14:.0f} (70/30 = conventional overbought/oversold bands)")
    volatility = "Volatility: " + "; ".join(vol_bits)

    trend = (
        f"Trend: price vs SMA20 {_fmt_pct(snap.vs_sma_20)}, "
        f"SMA50 {_fmt_pct(snap.vs_sma_50)}, SMA200 {_fmt_pct(snap.vs_sma_200)}"
    )
    # honest label: "52w" only when the window actually covers ~a year
    range_label = "52w" if snap.bars >= _FULL_YEAR_BARS else f"{snap.bars}-bar window"
    range_line = (
        f"Range: {_fmt_pct(snap.pct_from_52w_high)} from {range_label} high, "
        f"{_fmt_pct(snap.pct_above_52w_low)} above {range_label} low; "
        f"max drawdown (window) {_fmt_pct(snap.max_drawdown_1y)}"
    )
    volume = (
        f"Volume: 21d avg = {snap.volume_ratio_21_63:.2f}x the 63d avg"
        if snap.volume_ratio_21_63 is not None
        else "Volume: n/d"
    )

    lines = [
        header,
        momentum,
        volatility,
        trend,
        range_line,
        volume,
        f"Data quality: {snap.data_quality} ({snap.bars} daily bars, source: yfinance EOD)",
        (
            "Caveat: descriptive price action only, EOD data — not a trading signal. "
            "An event-driven thesis can invalidate any trend or level instantly."
        ),
    ]
    return "\n".join(lines)
