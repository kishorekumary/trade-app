"""
F&O Risk Manager.

This module enforces all safety rules before the agent places a trade.
Options trading has defined risk (you can never lose more than the premium paid),
but we still add layers of protection to prevent overtrading or panic trading.

Risk rules:
1. Daily loss limit: if today's realized losses exceed 3x max premium, stop trading
2. Position limit: max 2 open F&O positions at once
3. Premium cap: don't buy options that cost more than FNO_MAX_PREMIUM per lot
4. Time cutoff: no new entries after 2:00 PM (options lose value fast near close)
"""
from datetime import datetime
from typing import Tuple
import pytz

from fno.config import fno_settings
from src.utils import get_logger

log = get_logger("fno.risk")

IST = pytz.timezone("Asia/Kolkata")


class FnoRiskManager:
    """Enforces pre-trade and position-level risk rules for F&O."""

    def __init__(self):
        self.max_lots = fno_settings.FNO_MAX_LOTS
        self.max_premium = fno_settings.FNO_MAX_PREMIUM
        self.sl_pct = fno_settings.FNO_SL_PCT
        self.target_pct = fno_settings.FNO_TARGET_PCT
        self.max_open = fno_settings.MAX_OPEN_POSITIONS
        self.max_daily_loss = fno_settings.get_max_daily_loss()
        self.entry_cutoff = fno_settings.ENTRY_CUTOFF  # "14:00"

        log.info(
            f"FnoRiskManager initialized: "
            f"max_lots={self.max_lots} | "
            f"max_premium=₹{self.max_premium:.0f} | "
            f"SL={self.sl_pct}% | Target={self.target_pct}% | "
            f"daily_loss_limit=₹{self.max_daily_loss:.0f} | "
            f"entry_cutoff={self.entry_cutoff}"
        )

    def can_trade(
        self,
        today_pnl: float,
        open_positions: int,
        premium_per_share: float,
        lots: int = 1,
    ) -> Tuple[bool, str]:
        """
        Check all pre-trade risk conditions.

        Parameters:
          today_pnl         : today's total realized P&L in INR (negative = loss)
          open_positions    : current number of open F&O positions
          premium_per_share : the option premium per share (e.g. 180.50)
          lots              : number of lots we want to buy (default 1)

        Returns:
          (True, "OK") if all checks pass
          (False, "reason") if any check fails
        """
        # 1. Daily loss limit check
        if today_pnl <= -self.max_daily_loss:
            reason = (
                f"Daily loss limit hit: today's loss=₹{abs(today_pnl):.0f} "
                f"exceeds limit=₹{self.max_daily_loss:.0f}. "
                f"No more trades today."
            )
            log.warning(f"RISK BLOCK: {reason}")
            return False, reason

        # 2. Maximum open positions check
        if open_positions >= self.max_open:
            reason = (
                f"Max open positions reached: {open_positions}/{self.max_open}. "
                f"Wait for existing positions to close."
            )
            log.warning(f"RISK BLOCK: {reason}")
            return False, reason

        # 3. Premium too expensive check
        total_cost = premium_per_share * lots * fno_settings.NIFTY_LOT_SIZE
        if total_cost > self.max_premium:
            reason = (
                f"Option too expensive: premium=₹{premium_per_share:.2f} × {lots} lot × "
                f"{fno_settings.NIFTY_LOT_SIZE} shares = ₹{total_cost:.0f} "
                f"exceeds max=₹{self.max_premium:.0f}."
            )
            log.warning(f"RISK BLOCK: {reason}")
            return False, reason

        # 4. Time cutoff check
        now_ist = datetime.now(IST)
        cutoff_h, cutoff_m = map(int, self.entry_cutoff.split(":"))
        cutoff_time = now_ist.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
        if now_ist >= cutoff_time:
            reason = (
                f"Past entry cutoff time ({self.entry_cutoff} IST). "
                f"Current time: {now_ist.strftime('%H:%M')} IST. "
                f"No new F&O entries — options near expiry lose value quickly."
            )
            log.warning(f"RISK BLOCK: {reason}")
            return False, reason

        # All checks passed
        log.info(
            f"Risk checks PASSED: "
            f"today_pnl=₹{today_pnl:+.0f} | "
            f"open_pos={open_positions}/{self.max_open} | "
            f"cost=₹{total_cost:.0f} (max=₹{self.max_premium:.0f})"
        )
        return True, "OK"

    def calculate_exit_levels(self, entry_premium: float) -> Tuple[float, float]:
        """
        Compute stop-loss and target premiums for an options position.

        For option BUYERS (which is our strategy):
          Stop-loss: exit if premium falls below (entry × (1 - SL_PCT/100))
          Target:    exit if premium rises above (entry × (1 + TARGET_PCT/100))

        Example with entry=₹180, SL_PCT=40%, TARGET_PCT=80%:
          sl_premium     = 180 × (1 - 0.40) = ₹108  (exit if option drops to ₹108)
          target_premium = 180 × (1 + 0.80) = ₹324  (exit if option rises to ₹324)

        Why these numbers?
          - Options can be very volatile. A 40% drop is not unusual in a losing trade.
          - An 80% gain is achievable on good days and gives excellent R:R (2:1).
        """
        sl_premium = round(entry_premium * (1 - self.sl_pct / 100), 2)
        target_premium = round(entry_premium * (1 + self.target_pct / 100), 2)

        # Safety: SL can't be below 1 rupee (option can't be worth negative)
        sl_premium = max(sl_premium, 1.0)

        log.debug(
            f"Exit levels: entry=₹{entry_premium:.2f} | "
            f"SL=₹{sl_premium:.2f} (-{self.sl_pct}%) | "
            f"Target=₹{target_premium:.2f} (+{self.target_pct}%)"
        )
        return sl_premium, target_premium

    def should_exit(self, current_premium: float, entry_premium: float) -> Tuple[bool, str]:
        """
        Check if a current open position should be exited based on premium movement.

        Returns (True, "SL_HIT") or (True, "TARGET_HIT") or (False, "HOLD")
        """
        sl_premium, target_premium = self.calculate_exit_levels(entry_premium)
        pnl_pct = ((current_premium / entry_premium) - 1) * 100 if entry_premium > 0 else 0

        if current_premium <= sl_premium:
            reason = (
                f"STOP_LOSS: current=₹{current_premium:.2f} <= SL=₹{sl_premium:.2f} "
                f"({pnl_pct:+.1f}%)"
            )
            log.info(f"Exit triggered: {reason}")
            return True, "SL_HIT"

        if current_premium >= target_premium:
            reason = (
                f"TARGET: current=₹{current_premium:.2f} >= target=₹{target_premium:.2f} "
                f"({pnl_pct:+.1f}%)"
            )
            log.info(f"Exit triggered: {reason}")
            return True, "TARGET_HIT"

        log.debug(
            f"Hold: current=₹{current_premium:.2f} | "
            f"SL=₹{sl_premium:.2f} | Target=₹{target_premium:.2f} | "
            f"PnL={pnl_pct:+.1f}%"
        )
        return False, "HOLD"

    def is_past_squareoff_time(self) -> bool:
        """Check if it's past the square-off time (3:15 PM IST)."""
        now_ist = datetime.now(IST)
        sqoff_h, sqoff_m = map(int, fno_settings.SQUAREOFF_TIME.split(":"))
        squareoff_time = now_ist.replace(hour=sqoff_h, minute=sqoff_m, second=0, microsecond=0)
        return now_ist >= squareoff_time

    def is_market_open(self) -> bool:
        """Check if market is currently open (9:15 AM – 3:30 PM IST, weekdays only)."""
        now_ist = datetime.now(IST)
        # Skip weekends
        if now_ist.weekday() >= 5:
            return False
        open_h, open_m = map(int, fno_settings.MARKET_OPEN.split(":"))
        close_h, close_m = map(int, fno_settings.MARKET_CLOSE.split(":"))
        market_open = now_ist.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        market_close = now_ist.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        return market_open <= now_ist <= market_close
