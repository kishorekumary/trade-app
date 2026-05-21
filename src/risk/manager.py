from src.utils import get_logger
from config.settings import settings

log = get_logger("risk_manager")


class RiskManager:
    """Enforces position sizing, daily loss limits, and trade safety rules."""

    def __init__(
        self,
        max_capital_per_trade: float = None,
        max_daily_loss: float = None,
        max_open_positions: int = None,
        risk_per_trade_pct: float = None,
    ):
        self.max_capital_per_trade = max_capital_per_trade or settings.MAX_CAPITAL_PER_TRADE
        self.max_daily_loss = max_daily_loss or settings.MAX_DAILY_LOSS
        self.max_open_positions = max_open_positions or settings.MAX_OPEN_POSITIONS
        self.risk_per_trade_pct = risk_per_trade_pct or settings.RISK_PER_TRADE_PCT

    def can_trade(self, today_pnl: float, open_positions: int, available_cash: float) -> tuple[bool, str]:
        if today_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit hit: {today_pnl:.2f} INR (limit={self.max_daily_loss})"
        if open_positions >= self.max_open_positions:
            return False, f"Max positions reached: {open_positions}/{self.max_open_positions}"
        if available_cash < 5000:
            return False, f"Insufficient cash: {available_cash:.2f} INR"
        return True, "OK"

    def calculate_position_size(
        self,
        available_cash: float,
        entry_price: float,
        stop_loss: float,
        atr: float = 0.0
    ) -> tuple[int, float]:
        """
        Returns (quantity, capital_at_risk).
        Uses ATR-based position sizing: risk 2% of capital per trade,
        capped by MAX_CAPITAL_PER_TRADE.
        """
        risk_amount = available_cash * (self.risk_per_trade_pct / 100)
        sl_distance = abs(entry_price - stop_loss) if stop_loss else (atr * 2 if atr else entry_price * 0.02)

        if sl_distance <= 0:
            sl_distance = entry_price * 0.02

        qty_by_risk = int(risk_amount / sl_distance)
        qty_by_capital = int(self.max_capital_per_trade / entry_price)

        quantity = max(1, min(qty_by_risk, qty_by_capital))
        capital_deployed = quantity * entry_price

        log.debug(
            f"Position size: qty={quantity} | risk={risk_amount:.2f} | "
            f"sl_dist={sl_distance:.2f} | capital={capital_deployed:.2f}"
        )
        return quantity, capital_deployed

    def calculate_stop_loss(self, entry_price: float, atr: float, direction: str = "BUY") -> float:
        """ATR-based stop loss (2x ATR from entry)."""
        sl_distance = atr * 2
        if direction == "BUY":
            return round(entry_price - sl_distance, 2)
        return round(entry_price + sl_distance, 2)

    def calculate_target(self, entry_price: float, stop_loss: float, rr_ratio: float = 2.0) -> float:
        """Target based on Risk:Reward ratio (default 2:1)."""
        risk = abs(entry_price - stop_loss)
        return round(entry_price + (risk * rr_ratio), 2)

    def validate_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        target: float,
        quantity: int
    ) -> tuple[bool, str]:
        if entry_price <= 0:
            return False, "Invalid entry price"
        if quantity < 1:
            return False, "Quantity must be >= 1"
        if stop_loss >= entry_price:
            return False, f"Stop loss ({stop_loss}) must be below entry ({entry_price})"
        if target <= entry_price:
            return False, f"Target ({target}) must be above entry ({entry_price})"

        risk = entry_price - stop_loss
        reward = target - entry_price
        rr = reward / risk if risk > 0 else 0

        if rr < 1.5:
            return False, f"Risk:Reward {rr:.2f} below minimum 1.5"

        capital = entry_price * quantity
        if capital > self.max_capital_per_trade * 1.1:
            return False, f"Capital {capital:.2f} exceeds limit {self.max_capital_per_trade}"

        return True, f"Valid | R:R={rr:.2f} | Capital={capital:.2f}"
