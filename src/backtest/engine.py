"""
Simple vectorized backtester — no look-ahead bias.
Simulates daily bar-by-bar strategy evaluation on historical data.
"""
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from src.analysis import TechnicalAnalyzer
from src.strategy import get_strategy, TradeSignal
from src.risk import RiskManager
from src.broker import KiteClient
from config.settings import settings
from src.utils import get_logger

log = get_logger("backtest")
console = Console()


@dataclass
class BacktestTrade:
    symbol: str
    entry_date: datetime
    entry_price: float
    stop_loss: float
    target: float
    quantity: int
    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    hold_days: int = 0


@dataclass
class BacktestResult:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    avg_hold_days: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    trades: list = field(default_factory=list)


def run_backtest(days: int = 365, strategy: str = "momentum") -> BacktestResult:
    log.info(f"Starting backtest: {days} days | strategy={strategy}")

    kite = KiteClient(
        api_key=settings.KITE_API_KEY,
        api_secret=settings.KITE_API_SECRET,
        access_token=settings.KITE_ACCESS_TOKEN,
    )
    analyzer = TechnicalAnalyzer()
    strat = get_strategy(strategy)
    risk = RiskManager()
    universe = settings.get_universe()[:20]  # Limit for speed

    initial_capital = 200000.0
    capital = initial_capital
    all_trades: list[BacktestTrade] = []
    open_trades: list[BacktestTrade] = []

    console.print(f"[cyan]Backtesting {len(universe)} symbols over {days} days...[/cyan]")
    progress_symbols = 0

    for symbol in universe:
        progress_symbols += 1
        console.print(f"  [{progress_symbols}/{len(universe)}] {symbol}", end="\r")

        full_df = kite.get_historical_data(symbol, days=days + 200)
        if full_df.empty or len(full_df) < 100:
            continue

        full_df = analyzer.compute_all(full_df)
        test_start_idx = 100  # Skip first 100 bars (indicator warmup)

        for i in range(test_start_idx, len(full_df)):
            bar_date = full_df.index[i]
            bar_close = float(full_df["Close"].iloc[i])
            bar_high = float(full_df["High"].iloc[i])
            bar_low = float(full_df["Low"].iloc[i])

            # Check open trades for this symbol
            to_close = []
            for trade in open_trades:
                if trade.symbol != symbol:
                    continue
                hold = (bar_date - trade.entry_date).days
                exit_price = None
                exit_reason = ""

                if bar_low <= trade.stop_loss:
                    exit_price = trade.stop_loss
                    exit_reason = "SL"
                elif bar_high >= trade.target:
                    exit_price = trade.target
                    exit_reason = "Target"
                elif hold >= settings.HOLD_PERIOD_DAYS + 2:
                    exit_price = bar_close
                    exit_reason = "Time"

                if exit_price:
                    trade.exit_date = bar_date
                    trade.exit_price = exit_price
                    trade.pnl = (exit_price - trade.entry_price) * trade.quantity
                    trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100
                    trade.hold_days = hold
                    trade.exit_reason = exit_reason
                    capital += trade.pnl
                    all_trades.append(trade)
                    to_close.append(trade)

            for t in to_close:
                open_trades.remove(t)

            # Only scan for new entries if we have capacity
            open_symbol_positions = sum(1 for t in open_trades if t.symbol == symbol)
            if open_symbol_positions > 0 or len(open_trades) >= settings.MAX_OPEN_POSITIONS:
                continue
            if capital < 5000:
                continue

            # Use only past data (no look-ahead)
            hist_slice = full_df.iloc[:i + 1]
            signals = analyzer.get_signal_summary(hist_slice)
            signal = strat.analyze(symbol, hist_slice, signals)

            if signal and signal.action == "BUY" and signal.confidence >= 0.55:
                qty, cap_needed = risk.calculate_position_size(
                    available_cash=min(capital, settings.MAX_CAPITAL_PER_TRADE),
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    atr=signals.get("atr", 0),
                )
                if qty > 0 and cap_needed <= capital:
                    bt_trade = BacktestTrade(
                        symbol=symbol,
                        entry_date=bar_date,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        target=signal.target,
                        quantity=qty,
                    )
                    open_trades.append(bt_trade)
                    capital -= cap_needed

    # Close any remaining open trades at last price
    for trade in open_trades:
        df = kite.get_historical_data(trade.symbol, days=10)
        if not df.empty:
            last_price = float(df["Close"].iloc[-1])
            trade.exit_price = last_price
            trade.pnl = (last_price - trade.entry_price) * trade.quantity
            trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100
            trade.exit_reason = "Open"
            all_trades.append(trade)

    # Compute results
    result = BacktestResult(trades=all_trades)
    result.total_trades = len(all_trades)
    closed = [t for t in all_trades if t.exit_price]
    winners = [t for t in closed if t.pnl > 0]
    losers = [t for t in closed if t.pnl <= 0]

    result.winning_trades = len(winners)
    result.losing_trades = len(losers)
    result.total_pnl = sum(t.pnl for t in closed)
    result.win_rate = (len(winners) / len(closed) * 100) if closed else 0
    result.avg_win = sum(t.pnl for t in winners) / len(winners) if winners else 0
    result.avg_loss = sum(t.pnl for t in losers) / len(losers) if losers else 0
    result.best_trade = max((t.pnl for t in closed), default=0)
    result.worst_trade = min((t.pnl for t in closed), default=0)
    result.avg_hold_days = sum(t.hold_days for t in closed) / len(closed) if closed else 0
    total_gain = sum(t.pnl for t in winners)
    total_loss = abs(sum(t.pnl for t in losers))
    result.profit_factor = total_gain / total_loss if total_loss > 0 else float("inf")

    _print_backtest_results(result, initial_capital, days, strategy)
    return result


def _print_backtest_results(result: BacktestResult, initial_capital: float, days: int, strategy: str):
    console.print()
    pnl_color = "green" if result.total_pnl >= 0 else "red"
    pnl_pct = (result.total_pnl / initial_capital) * 100

    content = (
        f"Strategy: {strategy} | Period: {days} days\n"
        f"Initial Capital: ₹{initial_capital:,.0f}\n"
        f"Final Capital: ₹{initial_capital + result.total_pnl:,.0f}\n"
        f"Total PnL: [{pnl_color}]₹{result.total_pnl:+,.2f} ({pnl_pct:+.1f}%)[/{pnl_color}]\n\n"
        f"Total Trades: {result.total_trades}\n"
        f"Winners: {result.winning_trades} | Losers: {result.losing_trades}\n"
        f"Win Rate: {result.win_rate:.1f}%\n"
        f"Profit Factor: {result.profit_factor:.2f}\n\n"
        f"Avg Win: ₹{result.avg_win:+,.2f}\n"
        f"Avg Loss: ₹{result.avg_loss:+,.2f}\n"
        f"Best Trade: ₹{result.best_trade:+,.2f}\n"
        f"Worst Trade: ₹{result.worst_trade:+,.2f}\n"
        f"Avg Hold: {result.avg_hold_days:.1f} days"
    )
    console.print(Panel(content, title=f"[bold blue]Backtest Results[/bold blue]", box=box.ROUNDED))

    # Show last 10 trades
    recent = sorted(result.trades, key=lambda t: t.entry_date or datetime.min, reverse=True)[:10]
    if recent:
        table = Table(title="Recent Backtest Trades", box=box.SIMPLE)
        table.add_column("Symbol", style="cyan")
        table.add_column("Entry Date")
        table.add_column("Entry", justify="right")
        table.add_column("Exit", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("PnL%", justify="right")
        table.add_column("Reason")
        table.add_column("Days", justify="right")

        for t in recent:
            pnl_color = "green" if t.pnl >= 0 else "red"
            table.add_row(
                t.symbol,
                t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "-",
                f"₹{t.entry_price:,.2f}",
                f"₹{t.exit_price:,.2f}" if t.exit_price else "-",
                f"[{pnl_color}]₹{t.pnl:+,.2f}[/{pnl_color}]",
                f"[{pnl_color}]{t.pnl_pct:+.2f}%[/{pnl_color}]",
                t.exit_reason,
                str(t.hold_days),
            )
        console.print(table)
