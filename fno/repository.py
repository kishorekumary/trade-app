"""
Database CRUD operations for the F&O agent.
All operations are on the fno_trades table (separate from equity trades.db).
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import func, and_

from fno.models import FnoTrade, FnoTradeStatus, get_session
from src.utils import get_logger

log = get_logger("fno.repository")


class FnoRepository:
    """All database operations for F&O trades."""

    # ── Write operations ──────────────────────────────────────────────────────

    def save_trade(self, trade: FnoTrade) -> FnoTrade:
        """Persist a new trade to the database. Returns the saved trade with its ID."""
        with get_session() as session:
            session.add(trade)
            session.commit()
            session.refresh(trade)
            # Detach from session so it can be used outside
            session.expunge(trade)
            log.info(
                f"Trade saved: {trade.symbol} x{trade.lots} lots "
                f"@ ₹{trade.entry_premium:.2f} | total_cost=₹{trade.total_cost:.2f}"
            )
            return trade

    def close_trade(
        self,
        trade_id: int,
        exit_premium: float,
        exit_reason: str,
        kite_exit_order_id: str = None,
    ) -> Optional[FnoTrade]:
        """
        Mark a trade as CLOSED, compute P&L, and save.

        P&L formula (for bought options):
          pnl = (exit_premium - entry_premium) * lots * lot_size
          pnl_pct = ((exit_premium / entry_premium) - 1) * 100

        Example:
          Entry: ₹180 | Exit: ₹324 | Lots: 1 | Lot size: 75
          pnl = (324 - 180) * 1 * 75 = ₹10,800
          pnl_pct = (324/180 - 1) * 100 = +80%
        """
        with get_session() as session:
            trade = session.query(FnoTrade).filter(FnoTrade.id == trade_id).first()
            if not trade:
                log.warning(f"Trade ID {trade_id} not found for closing")
                return None

            trade.exit_premium = exit_premium
            trade.exit_time = datetime.utcnow()
            trade.status = FnoTradeStatus.CLOSED
            trade.exit_reason = exit_reason
            trade.kite_exit_order_id = kite_exit_order_id

            # Compute P&L
            trade.pnl = (exit_premium - trade.entry_premium) * trade.lots * trade.lot_size
            if trade.entry_premium > 0:
                trade.pnl_pct = ((exit_premium / trade.entry_premium) - 1) * 100
            else:
                trade.pnl_pct = 0.0

            session.commit()
            session.refresh(trade)
            session.expunge(trade)

            pnl_sign = "+" if trade.pnl >= 0 else ""
            log.info(
                f"Trade closed: {trade.symbol} | "
                f"Exit=₹{exit_premium:.2f} | "
                f"PnL={pnl_sign}₹{trade.pnl:.2f} ({pnl_sign}{trade.pnl_pct:.1f}%) | "
                f"Reason: {exit_reason}"
            )
            return trade

    # ── Read operations ───────────────────────────────────────────────────────

    def get_open_trades(self) -> list[FnoTrade]:
        """Return all trades with status=OPEN."""
        with get_session() as session:
            trades = (
                session.query(FnoTrade)
                .filter(FnoTrade.status == FnoTradeStatus.OPEN)
                .order_by(FnoTrade.entry_time.asc())
                .all()
            )
            session.expunge_all()
            return trades

    def get_open_count(self) -> int:
        """Return number of currently open F&O positions."""
        with get_session() as session:
            return (
                session.query(FnoTrade)
                .filter(FnoTrade.status == FnoTradeStatus.OPEN)
                .count()
            )

    def get_today_pnl(self) -> float:
        """
        Return today's total realized P&L (sum of closed trades exited today).
        Negative = loss, Positive = profit.
        """
        today = date.today().isoformat()
        with get_session() as session:
            result = session.query(func.sum(FnoTrade.pnl)).filter(
                and_(
                    FnoTrade.status == FnoTradeStatus.CLOSED,
                    func.date(FnoTrade.exit_time) == today,
                )
            ).scalar()
            return result or 0.0

    def get_trade_by_id(self, trade_id: int) -> Optional[FnoTrade]:
        """Fetch a single trade by its database ID."""
        with get_session() as session:
            trade = session.query(FnoTrade).filter(FnoTrade.id == trade_id).first()
            if trade:
                session.expunge(trade)
            return trade

    def get_all_trades(self, limit: int = 50) -> list[FnoTrade]:
        """Return the most recent trades (both open and closed)."""
        with get_session() as session:
            trades = (
                session.query(FnoTrade)
                .order_by(FnoTrade.entry_time.desc())
                .limit(limit)
                .all()
            )
            session.expunge_all()
            return trades

    def get_monthly_summary(self, year: int, month: int) -> dict:
        """Compute monthly trading statistics."""
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-31"
        with get_session() as session:
            trades = session.query(FnoTrade).filter(
                and_(
                    FnoTrade.status == FnoTradeStatus.CLOSED,
                    func.date(FnoTrade.exit_time) >= start,
                    func.date(FnoTrade.exit_time) <= end,
                )
            ).all()

            if not trades:
                return {
                    "total_trades": 0,
                    "total_pnl": 0.0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "best_trade": 0.0,
                    "worst_trade": 0.0,
                    "total_ce_trades": 0,
                    "total_pe_trades": 0,
                }

            winners = [t for t in trades if (t.pnl or 0) > 0]
            losers = [t for t in trades if (t.pnl or 0) <= 0]

            return {
                "total_trades": len(trades),
                "total_pnl": sum(t.pnl or 0 for t in trades),
                "winning_trades": len(winners),
                "losing_trades": len(losers),
                "win_rate": (len(winners) / len(trades) * 100) if trades else 0.0,
                "avg_win": (sum(t.pnl for t in winners) / len(winners)) if winners else 0.0,
                "avg_loss": (sum(t.pnl for t in losers) / len(losers)) if losers else 0.0,
                "best_trade": max(t.pnl or 0 for t in trades),
                "worst_trade": min(t.pnl or 0 for t in trades),
                "total_ce_trades": sum(1 for t in trades if t.option_type == "CE"),
                "total_pe_trades": sum(1 for t in trades if t.option_type == "PE"),
            }
