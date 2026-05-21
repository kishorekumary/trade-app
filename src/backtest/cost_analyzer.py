"""
Shows real impact of brokerage + taxes on your strategy.
Zerodha charges: ₹20/order or 0.03% whichever is lower + STT + exchange fees.
"""
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def zerodha_cost(price: float, qty: int, is_sell: bool = False) -> dict:
    """Calculate total Zerodha charges for a CNC equity trade."""
    turnover = price * qty

    brokerage = min(20, turnover * 0.0003)       # ₹20 flat or 0.03%

    # STT: 0.1% on sell side only (CNC delivery)
    stt = turnover * 0.001 if is_sell else 0

    exchange_txn = turnover * 0.0000345           # NSE exchange fee
    sebi_charge = turnover * 0.000001             # SEBI regulatory fee
    gst = (brokerage + exchange_txn + sebi_charge) * 0.18
    stamp_duty = turnover * 0.00015 if not is_sell else 0  # 0.015% on buy

    total = brokerage + stt + exchange_txn + sebi_charge + gst + stamp_duty
    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange": exchange_txn,
        "sebi": sebi_charge,
        "gst": gst,
        "stamp": stamp_duty,
        "total": total,
    }


def analyze_trade_viability(
    capital: float = 10000,
    expected_gain_pct: float = 2.0,
    stop_loss_pct: float = 1.5,
):
    console.print("\n[bold cyan]Trade Cost vs Profit Analysis[/bold cyan]")
    console.print(f"Capital per trade: ₹{capital:,.0f} | Target gain: {expected_gain_pct}% | SL: {stop_loss_pct}%\n")

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Stock Price", justify="right", style="cyan")
    table.add_column("Qty", justify="right")
    table.add_column("Total Costs", justify="right")
    table.add_column("Cost %", justify="right")
    table.add_column("Gross Gain", justify="right", style="green")
    table.add_column("Net Gain", justify="right")
    table.add_column("Gross Loss", justify="right", style="red")
    table.add_column("Net Loss", justify="right")
    table.add_column("Breakeven %", justify="right", style="yellow")

    for price in [100, 250, 500, 1000, 2500, 5000]:
        qty = max(1, int(capital / price))
        actual_capital = price * qty

        buy_cost = zerodha_cost(price, qty, is_sell=False)
        exit_price_target = price * (1 + expected_gain_pct / 100)
        exit_price_sl = price * (1 - stop_loss_pct / 100)
        sell_cost_win = zerodha_cost(exit_price_target, qty, is_sell=True)
        sell_cost_loss = zerodha_cost(exit_price_sl, qty, is_sell=True)

        total_costs_win = buy_cost["total"] + sell_cost_win["total"]
        total_costs_loss = buy_cost["total"] + sell_cost_loss["total"]
        avg_costs = (total_costs_win + total_costs_loss) / 2

        gross_gain = actual_capital * (expected_gain_pct / 100)
        gross_loss = actual_capital * (stop_loss_pct / 100)
        net_gain = gross_gain - total_costs_win
        net_loss = -(gross_loss + total_costs_loss)
        cost_pct = (avg_costs / actual_capital) * 100
        breakeven_pct = (avg_costs / actual_capital) * 100

        net_color = "green" if net_gain > 0 else "red"

        table.add_row(
            f"₹{price:,}",
            str(qty),
            f"₹{avg_costs:.2f}",
            f"{cost_pct:.2f}%",
            f"₹{gross_gain:.2f}",
            f"[{net_color}]₹{net_gain:.2f}[/{net_color}]",
            f"₹{gross_loss:.2f}",
            f"₹{abs(net_loss):.2f}",
            f"{breakeven_pct:.2f}%",
        )

    console.print(table)
    console.print(
        "\n[yellow]Key insight:[/yellow] For low-priced stocks (₹100-₹250), "
        "costs eat 0.5-1% of each trade. You need >1% move just to break even.\n"
        "Stick to [bold]mid/large caps (₹500+)[/bold] where cost % is manageable."
    )


if __name__ == "__main__":
    analyze_trade_viability(capital=10000, expected_gain_pct=2.0, stop_loss_pct=1.5)
