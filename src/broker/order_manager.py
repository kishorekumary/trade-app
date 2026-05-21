from datetime import datetime
from typing import Optional
from src.utils import get_logger
from src.database.models import Trade, TradeStatus
from src.database.repository import TradeRepository
from .kite_client import KiteClient

log = get_logger("order_manager")


class OrderManager:
    def __init__(self, kite: KiteClient, repo: TradeRepository, paper_trading: bool = True):
        self.kite = kite
        self.repo = repo
        self.paper_trading = paper_trading

    def enter_long(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        target: float,
        strategy: str = "",
        reasoning: str = "",
        exchange: str = "NSE"
    ) -> Optional[Trade]:
        trade = Trade(
            symbol=symbol,
            exchange=exchange,
            direction="BUY",
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            status=TradeStatus.PENDING,
            strategy=strategy,
            agent_reasoning=reasoning,
            paper_trade=self.paper_trading,
            entry_time=datetime.utcnow(),
        )
        saved = self.repo.save_trade(trade)

        order_id = self.kite.place_order(
            symbol=symbol,
            transaction_type="BUY",
            quantity=quantity,
            order_type="MARKET",
            exchange=exchange,
            tag="agent_buy"
        )

        if order_id:
            self.repo.update_trade_order_id(saved.id, order_id)
            log.info(f"Entered LONG: {symbol} x{quantity} @ {entry_price:.2f} | SL={stop_loss:.2f} | T={target:.2f}")
            return self.repo.get_trade_by_id(saved.id)
        else:
            log.error(f"Order placement failed for {symbol}")
            return None

    def exit_trade(self, trade: Trade, exit_price: float, reason: str = "") -> Optional[Trade]:
        order_id = self.kite.place_order(
            symbol=trade.symbol,
            transaction_type="SELL",
            quantity=trade.quantity,
            order_type="MARKET",
            exchange=trade.exchange,
            tag="agent_exit"
        )

        closed = self.repo.close_trade(trade.id, exit_price, order_id)
        if closed:
            log.info(
                f"Exited {trade.symbol} @ {exit_price:.2f} | "
                f"PnL={closed.pnl:.2f} ({closed.pnl_pct:.2f}%) | Reason: {reason}"
            )
        return closed

    def check_stop_loss_targets(self, open_trades: list[Trade]) -> list[Trade]:
        """Check and auto-exit trades that hit SL or target."""
        exited = []
        for trade in open_trades:
            ltp = self.kite.get_ltp(trade.symbol, trade.exchange)
            if ltp <= 0:
                continue

            reason = None
            exit_price = ltp

            if trade.stop_loss and ltp <= trade.stop_loss:
                reason = f"Stop loss hit @ {ltp:.2f} (SL={trade.stop_loss:.2f})"
            elif trade.target and ltp >= trade.target:
                reason = f"Target hit @ {ltp:.2f} (T={trade.target:.2f})"

            if reason:
                closed = self.exit_trade(trade, exit_price, reason)
                if closed:
                    exited.append(closed)
        return exited

    def get_current_pnl(self, open_trades: list[Trade]) -> dict:
        total_unrealized = 0.0
        breakdown = []
        for trade in open_trades:
            ltp = self.kite.get_ltp(trade.symbol, trade.exchange)
            if ltp > 0:
                unrealized = (ltp - trade.entry_price) * trade.quantity
                pct = (unrealized / (trade.entry_price * trade.quantity)) * 100
                total_unrealized += unrealized
                breakdown.append({
                    "symbol": trade.symbol,
                    "ltp": ltp,
                    "entry": trade.entry_price,
                    "unrealized_pnl": unrealized,
                    "pnl_pct": pct,
                })
        return {"total_unrealized": total_unrealized, "positions": breakdown}
