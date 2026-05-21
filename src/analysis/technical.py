import pandas as pd
import numpy as np
from src.utils import get_logger

log = get_logger("technical")


class TechnicalAnalyzer:
    """Compute technical indicators on OHLCV data."""

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 20:
            return df

        df = df.copy()

        # Trend
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
        df["SMA20"] = df["Close"].rolling(20).mean()

        # RSI
        df["RSI"] = self._rsi(df["Close"], 14)

        # MACD
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

        # Bollinger Bands
        df["BB_Mid"] = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        df["BB_Upper"] = df["BB_Mid"] + 2 * std
        df["BB_Lower"] = df["BB_Mid"] - 2 * std
        df["BB_Pct"] = (df["Close"] - df["BB_Lower"]) / (df["BB_Upper"] - df["BB_Lower"])

        # ATR
        df["ATR"] = self._atr(df, 14)

        # Volume
        df["Volume_SMA20"] = df["Volume"].rolling(20).mean()
        df["Volume_Ratio"] = df["Volume"] / df["Volume_SMA20"]

        # Momentum
        df["ROC10"] = df["Close"].pct_change(10) * 100
        df["ROC20"] = df["Close"].pct_change(20) * 100

        # Stochastic
        low14 = df["Low"].rolling(14).min()
        high14 = df["High"].rolling(14).max()
        df["Stoch_K"] = (df["Close"] - low14) / (high14 - low14) * 100
        df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()

        # Support / Resistance levels (recent 20 bars)
        df["Support"] = df["Low"].rolling(20).min()
        df["Resistance"] = df["High"].rolling(20).max()

        # Trend strength
        df["ADX"] = self._adx(df, 14)

        # Price position
        df["Pct_From_52W_High"] = (df["Close"] / df["High"].rolling(252).max() - 1) * 100
        df["Pct_From_52W_Low"] = (df["Close"] / df["Low"].rolling(252).min() - 1) * 100

        return df

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl = df["High"] - df["Low"]
        hc = (df["High"] - df["Close"].shift()).abs()
        lc = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def _adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        atr = self._atr(df, period)
        up = df["High"].diff()
        down = -df["Low"].diff()
        plus_dm = up.where((up > down) & (up > 0), 0)
        minus_dm = down.where((down > up) & (down > 0), 0)
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
        return dx.rolling(period).mean()

    def get_signal_summary(self, df: pd.DataFrame) -> dict:
        if df.empty:
            return {}
        row = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else row

        signals = {
            "rsi": float(row.get("RSI", 50)),
            "rsi_signal": "oversold" if row.get("RSI", 50) < 30 else "overbought" if row.get("RSI", 50) > 70 else "neutral",
            "macd_bullish": bool(row.get("MACD_Hist", 0) > 0 and prev.get("MACD_Hist", 0) <= 0),
            "macd_bearish": bool(row.get("MACD_Hist", 0) < 0 and prev.get("MACD_Hist", 0) >= 0),
            "above_ema20": bool(row.get("Close", 0) > row.get("EMA20", 0)),
            "above_ema50": bool(row.get("Close", 0) > row.get("EMA50", 0)),
            "above_ema200": bool(row.get("Close", 0) > row.get("EMA200", 0)),
            "volume_surge": bool(row.get("Volume_Ratio", 1) > 1.5),
            "bb_pct": float(row.get("BB_Pct", 0.5)),
            "roc10": float(row.get("ROC10", 0)),
            "roc20": float(row.get("ROC20", 0)),
            "adx": float(row.get("ADX", 20)),
            "trend_strong": bool(row.get("ADX", 0) > 25),
            "stoch_k": float(row.get("Stoch_K", 50)),
            "atr": float(row.get("ATR", 0)),
            "close": float(row.get("Close", 0)),
            "support": float(row.get("Support", 0)),
            "resistance": float(row.get("Resistance", 0)),
            "pct_from_52w_high": float(row.get("Pct_From_52W_High", 0)),
            "pct_from_52w_low": float(row.get("Pct_From_52W_Low", 0)),
        }

        # Composite score (0-100)
        score = 50
        if signals["above_ema20"]: score += 5
        if signals["above_ema50"]: score += 5
        if signals["above_ema200"]: score += 10
        if signals["rsi"] < 30: score += 10
        if signals["rsi"] > 70: score -= 10
        if signals["macd_bullish"]: score += 10
        if signals["macd_bearish"]: score -= 10
        if signals["volume_surge"]: score += 5
        if signals["roc10"] > 0: score += 5
        if signals["roc20"] > 0: score += 5
        if signals["trend_strong"]: score += 5
        signals["composite_score"] = max(0, min(100, score))

        return signals
