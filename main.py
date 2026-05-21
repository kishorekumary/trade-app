#!/usr/bin/env python3
"""
Trade Agent — AI-powered Zerodha swing trading system.

Usage:
  python main.py scan          # Run one-shot market scan
  python main.py schedule      # Start automated daily scheduler
  python main.py status        # Show portfolio status
  python main.py history       # Show trade history
  python main.py report        # Show monthly P&L report
  python main.py export        # Export trades to CSV
  python main.py auth          # Start Zerodha OAuth login server
  python main.py backtest      # Backtest strategy on historical data
"""
import sys
import argparse
from rich.console import Console
from rich.panel import Panel
from rich import box

console = Console()

BANNER = """
╔══════════════════════════════════════════════════════════╗
║          TRADE AGENT — Powered by Claude AI              ║
║          NSE Swing Trading | Zerodha Kite Connect        ║
╚══════════════════════════════════════════════════════════╝
"""


def cmd_scan(args):
    from src.agent import TradingAgent
    from src.reports import ReportGenerator
    agent = TradingAgent()
    reporter = ReportGenerator()

    console.print("[bold cyan]Running market scan...[/bold cyan]")
    results = agent.run_daily_scan()

    console.print(f"\n[green]Scan complete: {len(results)} trade(s) executed[/green]")
    status = agent.get_portfolio_status()
    reporter.print_portfolio_status(status)


def cmd_schedule(args):
    from src.scheduler import run_scheduler
    console.print("[bold cyan]Starting trading scheduler...[/bold cyan]")
    run_scheduler()


def cmd_status(args):
    from src.agent import TradingAgent
    from src.reports import ReportGenerator
    agent = TradingAgent()
    reporter = ReportGenerator()
    status = agent.get_portfolio_status()
    reporter.print_portfolio_status(status)


def cmd_history(args):
    from src.reports import ReportGenerator
    reporter = ReportGenerator()
    reporter.print_trade_history(limit=args.limit)


def cmd_report(args):
    from src.reports import ReportGenerator
    reporter = ReportGenerator()
    if args.month:
        year, month = map(int, args.month.split("-"))
        reporter.print_monthly_report(year, month)
    else:
        reporter.print_monthly_report()


def cmd_export(args):
    from src.reports import ReportGenerator
    reporter = ReportGenerator()
    path = reporter.export_csv(args.output)
    console.print(f"[green]Exported to: {path}[/green]")


def cmd_auth(args):
    from src.auth import run_auth_server
    console.print("[bold cyan]Starting Zerodha OAuth server...[/bold cyan]")
    console.print("Visit [link]http://localhost:5000[/link] to authenticate")
    run_auth_server(port=args.port)


def cmd_backtest(args):
    from src.backtest import run_backtest
    console.print(f"[bold cyan]Running backtest: {args.days} days | strategy={args.strategy}[/bold cyan]")
    run_backtest(days=args.days, strategy=args.strategy)


def main():
    console.print(BANNER, style="bold blue")

    parser = argparse.ArgumentParser(description="Trade Agent — AI-powered NSE swing trading")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # scan
    scan_p = subparsers.add_parser("scan", help="Run one-shot market scan and execute trades")
    scan_p.set_defaults(func=cmd_scan)

    # schedule
    sch_p = subparsers.add_parser("schedule", help="Start automated daily trading scheduler")
    sch_p.set_defaults(func=cmd_schedule)

    # status
    stat_p = subparsers.add_parser("status", help="Show current portfolio status")
    stat_p.set_defaults(func=cmd_status)

    # history
    hist_p = subparsers.add_parser("history", help="Show trade history")
    hist_p.add_argument("--limit", type=int, default=20, help="Number of trades to show")
    hist_p.set_defaults(func=cmd_history)

    # report
    rep_p = subparsers.add_parser("report", help="Show monthly P&L report")
    rep_p.add_argument("--month", type=str, help="Month in YYYY-MM format (default: current month)")
    rep_p.set_defaults(func=cmd_report)

    # export
    exp_p = subparsers.add_parser("export", help="Export trades to CSV")
    exp_p.add_argument("--output", type=str, help="Output file path")
    exp_p.set_defaults(func=cmd_export)

    # auth
    auth_p = subparsers.add_parser("auth", help="Start Zerodha OAuth login server")
    auth_p.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    auth_p.set_defaults(func=cmd_auth)

    # backtest
    bt_p = subparsers.add_parser("backtest", help="Backtest strategy on historical data")
    bt_p.add_argument("--days", type=int, default=365, help="Days of history to backtest")
    bt_p.add_argument("--strategy", type=str, default="momentum", help="Strategy to test")
    bt_p.set_defaults(func=cmd_backtest)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise


if __name__ == "__main__":
    main()
