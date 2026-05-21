from .base import BaseStrategy, TradeSignal
from .momentum import MomentumStrategy
from .mean_reversion import MeanReversionStrategy


def get_strategy(name: str) -> BaseStrategy:
    strategies = {
        "momentum": MomentumStrategy(),
        "mean_reversion": MeanReversionStrategy(),
    }
    return strategies.get(name, MomentumStrategy())


__all__ = ["BaseStrategy", "TradeSignal", "MomentumStrategy", "MeanReversionStrategy", "get_strategy"]
