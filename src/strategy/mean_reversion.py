from typing import Optional
import pandas as pd
from .base import BaseStrategy, TradeSignal
from src.utils import get_logger
from config.settings import settings

log = get_logger("mean_reversion_strategy")


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy: buy oversold stocks near support.
    Entry conditions:
      - RSI < 35 (oversold)
      - Price near lower Bollinger Band (BB% < 0.2)
      - Stochastic K < 20
      - Price near 20-day support level
      - Volume not collapsing
    """
    name = "mean_reversion"

    def analyze(self, symbol: str, df: pd.DataFrame, signals: dict) -> Optional[TradeSignal]:
        if not signals or df.empty:
            return None

        close = signals.get("close", 0)
        rsi = signals.get("rsi", 50)
        atr = signals.get("atr", close * 0.02)
        bb_pct = signals.get("bb_pct", 0.5)
        stoch_k = signals.get("stoch_k", 50)
        support = signals.get("support", 0)
        above_ema200 = signals.get("above_ema200", False)
        volume_ratio = self._get_latest(df, "Volume_Ratio", 1.0)

        score = 0
        reasons = []

        if rsi < 35:
            score += 30
            reasons.append(f"RSI={rsi:.1f} (oversold)")

        if bb_pct < 0.2:
            score += 25
            reasons.append(f"BB%={bb_pct:.2f} (near lower band)")

        if stoch_k < 20:
            score += 20
            reasons.append(f"Stoch={stoch_k:.1f} (oversold)")

        if support > 0 and close <= support * 1.02:
            score += 15
            reasons.append(f"Near support @ {support:.2f}")

        if above_ema200:
            score += 10
            reasons.append("above EMA200 (long-term uptrend)")

        if 0.5 <= volume_ratio <= 1.5:
            score += 5
            reasons.append("normal volume (no panic selling)")

        if score < 55:
            return None

        if settings.INTRADAY_MODE:
            sl_dist = round(close * (settings.INTRADAY_SL_PCT / 100), 2)
            tp_dist = round(close * (settings.INTRADAY_TARGET_PCT / 100), 2)
            stop_loss = round(close - sl_dist, 2)
            target = round(close + tp_dist, 2)
        else:
            stop_loss = round(close - (atr * 1.5), 2)
            target = round(close + (atr * 3), 2)

        confidence = min(score / 100, 0.90)

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
                "bb_pct": bb_pct,
                "stoch_k": stoch_k,
                "support": support,
                "score": score,
            }
        )
