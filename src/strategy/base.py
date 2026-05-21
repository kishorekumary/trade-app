from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class TradeSignal:
    symbol: str
    action: str          # BUY | SELL | HOLD
    confidence: float    # 0.0 to 1.0
    entry_price: float
    stop_loss: float
    target: float
    reason: str
    indicators: dict


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def analyze(self, symbol: str, df: pd.DataFrame, signals: dict) -> Optional[TradeSignal]:
        """Analyze a symbol and return a trade signal or None."""
        ...

    def _get_latest(self, df: pd.DataFrame, col: str, default=0):
        try:
            return float(df[col].iloc[-1])
        except (KeyError, IndexError):
            return default
