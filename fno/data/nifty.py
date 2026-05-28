"""
Nifty 50 spot price and technical indicators.

Uses yfinance to fetch ^NSEI (Nifty 50 index) historical data
and computes technical indicators used by the F&O agent for direction.

Technical indicators explained (for beginners):
  RSI  = Relative Strength Index (0-100)
         > 70 = overbought (price may drop), < 30 = oversold (price may rise)
         50-65 = healthy uptrend, 35-50 = mild downtrend

  EMA  = Exponential Moving Average
         Price above EMA → uptrend, below EMA → downtrend
         EMA20 = short-term trend, EMA50 = medium-term trend

  MACD = Moving Average Convergence Divergence
         Bullish when MACD line crosses above Signal line (buy signal)
         Bearish when MACD line crosses below Signal line (sell signal)

  ADX  = Average Directional Index (trend strength)
         > 25 = strong trend, < 20 = no clear trend (choppy)
"""
import pandas as pd
import numpy as np
from typing import Optional
from src.utils import get_logger

log = get_logger("fno.nifty")


def _compute_rsi(series: pd.Series, period: int = 14) -> float:
    """
    RSI = 100 - (100 / (1 + RS))
    RS = average gain over period / average loss over period
    """
    if len(series) < period + 1:
        return 50.0  # neutral default

    delta = series.diff().dropna()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)

    avg_gain = gains.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    avg_loss = losses.ewm(com=period - 1, min_periods=period).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _compute_ema(series: pd.Series, span: int) -> float:
    """Compute EMA and return the last value."""
    if len(series) < span:
        return float(series.iloc[-1]) if len(series) > 0 else 0.0
    return round(float(series.ewm(span=span, adjust=False).mean().iloc[-1]), 2)


def _compute_macd(series: pd.Series) -> dict:
    """
    MACD = EMA12 - EMA26
    Signal line = EMA9 of MACD
    Bullish: MACD > Signal AND both recently crossed
    Bearish: MACD < Signal
    """
    if len(series) < 26:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0, "bullish": False}

    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    macd_val = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])

    # Bullish = MACD line is above signal line
    bullish = macd_val > signal_val

    # Check for recent crossover (more meaningful signal)
    if len(macd_line) >= 2 and len(signal_line) >= 2:
        prev_diff = float(macd_line.iloc[-2]) - float(signal_line.iloc[-2])
        curr_diff = macd_val - signal_val
        fresh_crossover = (prev_diff < 0 and curr_diff > 0) or (prev_diff > 0 and curr_diff < 0)
    else:
        fresh_crossover = False

    return {
        "macd": round(macd_val, 4),
        "signal": round(signal_val, 4),
        "histogram": round(hist_val, 4),
        "bullish": bullish,
        "bearish": not bullish,
        "fresh_crossover": fresh_crossover,
        "direction": "BULLISH" if bullish else "BEARISH",
    }


def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    ADX = trend strength indicator (0-100).
    Computed from True Range and Directional Movement.
    > 25 = strong trend (good for directional options trading)
    < 20 = sideways/choppy (avoid)
    """
    if len(df) < period + 1:
        return 20.0

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM and -DM
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # Smoothed averages
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr

    # DX and ADX
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(span=period, adjust=False).mean()

    return round(float(adx.iloc[-1]), 2)


def get_nifty_technicals() -> dict:
    """
    Fetch Nifty 50 daily data and compute technical indicators.

    Returns a dictionary with:
      close         : latest closing price of Nifty 50
      prev_close    : previous day's close
      day_change_pct: % change today (positive = up, negative = down)
      rsi_14        : RSI(14) value
      ema20         : 20-day EMA value
      ema50         : 50-day EMA value
      above_ema20   : True if Nifty is above its 20-day EMA
      above_ema50   : True if Nifty is above its 50-day EMA
      macd_direction: "BULLISH" or "BEARISH"
      macd_signal   : full MACD data dict
      adx           : ADX trend strength (>25 = strong trend)
      trend_strength: "STRONG" / "MODERATE" / "WEAK"
      overall_bias  : "BULLISH" / "BEARISH" / "NEUTRAL"

    Returns an empty dict if data fetch fails.
    """
    try:
        import yfinance as yf

        log.info("Fetching Nifty 50 (^NSEI) data from yfinance...")
        df = yf.download("^NSEI", period="60d", interval="1d", progress=False, auto_adjust=True)

        if df.empty:
            log.error("yfinance returned empty DataFrame for ^NSEI")
            return {}

        # Flatten MultiIndex columns if present (yfinance quirk)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Ensure we have required columns
        if "Close" not in df.columns:
            log.error(f"Missing 'Close' column. Got: {list(df.columns)}")
            return {}

        df = df.dropna(subset=["Close"])
        if len(df) < 20:
            log.error(f"Insufficient data: only {len(df)} rows")
            return {}

        close_series = df["Close"]

        # Current and previous close
        close = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else close
        day_change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0

        # Technical indicators
        rsi = _compute_rsi(close_series, period=14)
        ema20 = _compute_ema(close_series, span=20)
        ema50 = _compute_ema(close_series, span=50)
        macd_data = _compute_macd(close_series)
        adx = _compute_adx(df, period=14)

        above_ema20 = close > ema20
        above_ema50 = close > ema50

        # Trend strength from ADX
        if adx >= 30:
            trend_strength = "STRONG"
        elif adx >= 20:
            trend_strength = "MODERATE"
        else:
            trend_strength = "WEAK"

        # Overall directional bias (simple scoring)
        bullish_score = 0
        bearish_score = 0

        if day_change_pct > 0:
            bullish_score += 1
        else:
            bearish_score += 1

        if above_ema20:
            bullish_score += 1
        else:
            bearish_score += 1

        if above_ema50:
            bullish_score += 1
        else:
            bearish_score += 1

        if macd_data["bullish"]:
            bullish_score += 2  # MACD gets extra weight
        else:
            bearish_score += 2

        if rsi > 55:
            bullish_score += 1
        elif rsi < 45:
            bearish_score += 1

        if bullish_score > bearish_score + 1:
            overall_bias = "BULLISH"
        elif bearish_score > bullish_score + 1:
            overall_bias = "BEARISH"
        else:
            overall_bias = "NEUTRAL"

        result = {
            "close": round(close, 2),
            "prev_close": round(prev_close, 2),
            "day_change_pct": round(day_change_pct, 2),
            "rsi_14": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "above_ema20": above_ema20,
            "above_ema50": above_ema50,
            "macd_direction": macd_data["direction"],
            "macd_bullish": macd_data["bullish"],
            "macd_fresh_crossover": macd_data.get("fresh_crossover", False),
            "macd_histogram": macd_data["histogram"],
            "adx": adx,
            "trend_strength": trend_strength,
            "overall_bias": overall_bias,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
        }

        log.info(
            f"Nifty technicals: spot=₹{close:.2f} ({day_change_pct:+.2f}%) | "
            f"RSI={rsi:.1f} | EMA20={'above' if above_ema20 else 'below'} | "
            f"MACD={macd_data['direction']} | ADX={adx:.1f} | "
            f"Bias={overall_bias}"
        )
        return result

    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        return {}
    except Exception as e:
        log.error(f"Nifty technicals fetch failed: {e}")
        return {}


def get_nifty_spot_price() -> float:
    """
    Quick fetch of just the current Nifty 50 spot price.
    Used as a fallback when full technical data isn't needed.
    """
    try:
        import yfinance as yf
        df = yf.download("^NSEI", period="2d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return 0.0
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return float(df["Close"].iloc[-1])
    except Exception as e:
        log.error(f"Spot price fetch failed: {e}")
        return 0.0
