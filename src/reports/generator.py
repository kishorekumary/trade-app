from datetime import datetime, date
from tabulate import tabulate
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from src.database.repository import TradeRepository
from src.database.models import TradeStatus
from src.utils import get_logger

log = get_logger("reports")
console = Console()


class ReportGenerator:
    def __init__(self):
        self.repo = TradeRepository()

    def print_portfolio_status(self, status: dict):
        mode = "[red]LIVE TRADING[/red]" if not status["paper_trading"] else "[yellow]PAPER TRADING[/yellow]"

        panel_content = (
            f"Mode: {mode}\n"
            f"Cash Available: ₹{status['available_cash']:,.2f}\n"
            f"Open Positions: {status['open_positions']}\n"
            f"Today Realized: ₹{status['today_realized_pnl']:+,.2f}\n"
            f"Today Unrealized: ₹{status['today_unrealized_pnl']:+,.2f}\n"
            f"Today Total: ₹{status['today_total_pnl']:+,.2f}"
        )
        console.print(Panel(panel_content, title="[bold blue]Portfolio Status[/bold blue]", box=box.ROUNDED))

        if status["positions"]:
            table = Table(title="Open Positions", box=box.SIMPLE)
            table.add_column("Symbol", style="cyan")
            table.add_column("LTP", justify="right")
            table.add_column("Entry", justify="right")
            table.add_column("Unrealized PnL", justify="right")
            table.add_column("PnL %", justify="right")

            for pos in status["positions"]:
                pnl_color = "green" if pos["unrealized_pnl"] >= 0 else "red"
                table.add_row(
                    pos["symbol"],
                    f"₹{pos['ltp']:,.2f}",
                    f"₹{pos['entry']:,.2f}",
                    f"[{pnl_color}]₹{pos['unrealized_pnl']:+,.2f}[/{pnl_color}]",
                    f"[{pnl_color}]{pos['pnl_pct']:+.2f}%[/{pnl_color}]",
                )
            console.print(table)

    def print_trade_history(self, limit: int = 20):
        trades = self.repo.get_all_trades(limit)
        if not trades:
            console.print("[yellow]No trades found[/yellow]")
            return

        table = Table(title=f"Recent {limit} Trades", box=box.SIMPLE)
        table.add_column("#", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Dir")
        table.add_column("Qty", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Exit", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("PnL%", justify="right")
        table.add_column("Status")
        table.add_column("Days", justify="right")

        for t in trades:
            pnl_color = "green" if t.pnl >= 0 else "red"
            status_color = "green" if t.status == TradeStatus.CLOSED else "yellow" if t.status == TradeStatus.OPEN else "dim"
            table.add_row(
                str(t.id),
                t.symbol,
                t.direction,
                str(t.quantity),
                f"₹{t.entry_price:,.2f}",
                f"₹{t.exit_price:,.2f}" if t.exit_price else "-",
                f"[{pnl_color}]₹{t.pnl:+,.2f}[/{pnl_color}]" if t.status == TradeStatus.CLOSED else "-",
                f"[{pnl_color}]{t.pnl_pct:+.2f}%[/{pnl_color}]" if t.status == TradeStatus.CLOSED else "-",
                f"[{status_color}]{t.status}[/{status_color}]",
                str(t.hold_days) if t.hold_days else "-",
            )
        console.print(table)

    def print_monthly_report(self, year: int = None, month: int = None):
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        summary = self.repo.get_monthly_summary(year, month)

        month_name = datetime(year, month, 1).strftime("%B %Y")
        pnl_color = "green" if summary["total_pnl"] >= 0 else "red"

        content = (
            f"Period: {month_name}\n"
            f"Total PnL: [{pnl_color}]₹{summary['total_pnl']:+,.2f}[/{pnl_color}]\n\n"
            f"Total Trades: {summary['total_trades']}\n"
            f"Winners: {summary['winning_trades']} | Losers: {summary['losing_trades']}\n"
            f"Win Rate: {summary['win_rate']:.1f}%\n\n"
            f"Avg Win: ₹{summary['avg_win']:+,.2f}\n"
            f"Avg Loss: ₹{summary['avg_loss']:+,.2f}\n"
            f"Best Trade: ₹{summary['best_trade']:+,.2f}\n"
            f"Worst Trade: ₹{summary['worst_trade']:+,.2f}\n"
            f"Avg Hold: {summary['avg_hold_days']:.1f} days"
        )
        console.print(Panel(content, title=f"[bold blue]Monthly Report — {month_name}[/bold blue]", box=box.ROUNDED))

    def export_csv(self, filepath: str = None):
        import csv
        trades = self.repo.get_all_trades(limit=10000)
        filepath = filepath or f"trades_export_{date.today().isoformat()}.csv"
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Symbol", "Direction", "Qty", "Entry", "Exit", "SL", "Target",
                              "PnL", "PnL%", "Status", "Strategy", "Entry Time", "Exit Time", "Hold Days"])
            for t in trades:
                writer.writerow([
                    t.id, t.symbol, t.direction, t.quantity,
                    t.entry_price, t.exit_price, t.stop_loss, t.target,
                    t.pnl, t.pnl_pct, t.status, t.strategy,
                    t.entry_time, t.exit_time, t.hold_days
                ])
        log.info(f"Exported {len(trades)} trades to {filepath}")
        return filepath
