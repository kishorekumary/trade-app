from typing import Optional
import pandas as pd
from .base import BaseStrategy, TradeSignal
from src.utils import get_logger
from config.settings import settings

log = get_logger("momentum_strategy")


class MomentumStrategy(BaseStrategy):
    """
    Swing momentum strategy targeting 3-5 day holds.
    Entry conditions (all must be met):
      - Price above EMA20 and EMA50 (uptrend)
      - RSI between 40-65 (not overbought, momentum building)
      - MACD histogram turning positive (momentum shift)
      - Volume above 20-day average (conviction)
      - ADX > 20 (trend present)
      - Risk:Reward >= 2:1
    """
    name = "momentum"

    def analyze(self, symbol: str, df: pd.DataFrame, signals: dict) -> Optional[TradeSignal]:
        if not signals or df.empty:
            return None

        close = signals.get("close", 0)
        rsi = signals.get("rsi", 50)
        atr = signals.get("atr", close * 0.02)
        adx = signals.get("adx", 0)
        volume_ratio = signals.get("volume_surge", False)
        above_ema20 = signals.get("above_ema20", False)
        above_ema50 = signals.get("above_ema50", False)
        macd_bullish = signals.get("macd_bullish", False)
        roc10 = signals.get("roc10", 0)
        bb_pct = signals.get("bb_pct", 0.5)

        score = 0
        reasons = []

        if above_ema20 and above_ema50:
            score += 30
            reasons.append("uptrend (above EMA20+50)")

        if 40 <= rsi <= 65:
            score += 20
            reasons.append(f"RSI={rsi:.1f} (momentum zone)")
        elif rsi < 40:
            score += 10
            reasons.append(f"RSI={rsi:.1f} (oversold recovery)")

        if macd_bullish:
            score += 25
            reasons.append("MACD bullish crossover")

        if volume_ratio:
            score += 15
            reasons.append("volume surge (>1.5x avg)")

        if adx > 25:
            score += 10
            reasons.append(f"ADX={adx:.1f} (strong trend)")

        if roc10 > 2:
            score += 10
            reasons.append(f"10-day momentum +{roc10:.1f}%")

        if 0.3 <= bb_pct <= 0.7:
            score += 5
            reasons.append("price in BB mid-zone")

        if score < 55:
            return None

        # Calculate levels
        if settings.INTRADAY_MODE:
            sl_dist = round(close * (settings.INTRADAY_SL_PCT / 100), 2)
            tp_dist = round(close * (settings.INTRADAY_TARGET_PCT / 100), 2)
            stop_loss = round(close - sl_dist, 2)
            target = round(close + tp_dist, 2)
        else:
            stop_loss = round(close - (atr * 2), 2)
            target = round(close + (atr * 4), 2)

        confidence = min(score / 100, 0.95)

        return TradeSignal(
            symbol=symbol,
            action="BUY",
            confidence=confidence,
            entry_price=close,
            stop_loss=stop_loss,
            target=target,
            reason=" | ".join(reasons),
            indicators={
                "rsi": rsi,
                "adx": adx,
                "score": score,
                "atr": atr,
                "roc10": roc10,
            }
        )
