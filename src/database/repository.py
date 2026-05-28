from datetime import datetime, date
from typing import Optional
from sqlalchemy import func, and_
from .models import Trade, DailyPnL, AgentDecision, TradeStatus, get_session
from src.utils import get_logger

log = get_logger("repository")


class TradeRepository:
    def save_trade(self, trade: Trade) -> Trade:
        with get_session() as session:
            session.add(trade)
            session.commit()
            session.refresh(trade)
            log.info(f"Trade saved: {trade.symbol} {trade.direction} x{trade.quantity}")
            return trade

    def get_open_trades(self) -> list[Trade]:
        with get_session() as session:
            trades = session.query(Trade).filter(Trade.status == TradeStatus.OPEN).all()
            session.expunge_all()
            return trades

    def get_trade_by_id(self, trade_id: int) -> Optional[Trade]:
        with get_session() as session:
            trade = session.query(Trade).filter(Trade.id == trade_id).first()
            if trade:
                session.expunge(trade)
            return trade

    def close_trade(self, trade_id: int, exit_price: float, exit_order_id: str = None) -> Optional[Trade]:
        with get_session() as session:
            trade = session.query(Trade).filter(Trade.id == trade_id).first()
            if not trade:
                log.warning(f"Trade {trade_id} not found")
                return None
            trade.exit_price = exit_price
            trade.exit_time = datetime.utcnow()
            trade.status = TradeStatus.CLOSED
            trade.kite_exit_order_id = exit_order_id
            if trade.direction == "BUY":
                trade.pnl = (exit_price - trade.entry_price) * trade.quantity
            else:
                trade.pnl = (trade.entry_price - exit_price) * trade.quantity
            trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100
            hold = (trade.exit_time - trade.entry_time).days
            trade.hold_days = hold
            session.commit()
            session.refresh(trade)
            session.expunge(trade)
            log.info(f"Trade closed: {trade.symbol} PnL={trade.pnl:.2f} ({trade.pnl_pct:.2f}%)")
            return trade

    def update_trade_order_id(self, trade_id: int, order_id: str):
        with get_session() as session:
            trade = session.query(Trade).filter(Trade.id == trade_id).first()
            if trade:
                trade.kite_order_id = order_id
                trade.status = TradeStatus.OPEN
                session.commit()

    def update_trade_levels(self, trade_id: int, stop_loss: float, target: float):
        with get_session() as session:
            trade = session.query(Trade).filter(Trade.id == trade_id).first()
            if trade:
                trade.stop_loss = stop_loss
                trade.target = target
                session.commit()
                log.info(f"Trade {trade_id} ({trade.symbol}) levels updated: SL={stop_loss:.2f} T={target:.2f}")

    def get_today_pnl(self) -> float:
        today = date.today().isoformat()
        with get_session() as session:
            result = session.query(func.sum(Trade.pnl)).filter(
                and_(
                    Trade.status == TradeStatus.CLOSED,
                    func.date(Trade.exit_time) == today
                )
            ).scalar()
            return result or 0.0

    def get_open_position_count(self) -> int:
        with get_session() as session:
            return session.query(Trade).filter(Trade.status == TradeStatus.OPEN).count()

    def upsert_daily_pnl(self, daily: DailyPnL):
        with get_session() as session:
            existing = session.query(DailyPnL).filter(DailyPnL.date == daily.date).first()
            if existing:
                existing.realized_pnl = daily.realized_pnl
                existing.unrealized_pnl = daily.unrealized_pnl
                existing.total_pnl = daily.total_pnl
                existing.trades_opened = daily.trades_opened
                existing.trades_closed = daily.trades_closed
                existing.winning_trades = daily.winning_trades
                existing.losing_trades = daily.losing_trades
                existing.capital_deployed = daily.capital_deployed
            else:
                session.add(daily)
            session.commit()

    def get_monthly_summary(self, year: int, month: int) -> dict:
        with get_session() as session:
            start = f"{year}-{month:02d}-01"
            end = f"{year}-{month:02d}-31"
            trades = session.query(Trade).filter(
                and_(
                    Trade.status == TradeStatus.CLOSED,
                    func.date(Trade.exit_time) >= start,
                    func.date(Trade.exit_time) <= end
                )
            ).all()

            total_pnl = sum(t.pnl for t in trades)
            winners = [t for t in trades if t.pnl > 0]
            losers = [t for t in trades if t.pnl <= 0]
            win_rate = (len(winners) / len(trades) * 100) if trades else 0

            return {
                "total_trades": len(trades),
                "total_pnl": total_pnl,
                "winning_trades": len(winners),
                "losing_trades": len(losers),
                "win_rate": win_rate,
                "avg_win": sum(t.pnl for t in winners) / len(winners) if winners else 0,
                "avg_loss": sum(t.pnl for t in losers) / len(losers) if losers else 0,
                "best_trade": max(trades, key=lambda t: t.pnl).pnl if trades else 0,
                "worst_trade": min(trades, key=lambda t: t.pnl).pnl if trades else 0,
                "avg_hold_days": sum(t.hold_days for t in trades) / len(trades) if trades else 0,
            }

    def save_agent_decision(self, decision: AgentDecision) -> AgentDecision:
        with get_session() as session:
            session.add(decision)
            session.commit()
            session.refresh(decision)
            session.expunge(decision)
            return decision

    def get_all_trades(self, limit: int = 100) -> list[Trade]:
        with get_session() as session:
            trades = session.query(Trade).order_by(Trade.entry_time.desc()).limit(limit).all()
            session.expunge_all()
            return trades
