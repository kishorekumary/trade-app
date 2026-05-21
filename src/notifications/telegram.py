import threading
import requests
from src.utils import get_logger
from config.settings import settings

log = get_logger("telegram")


class TelegramNotifier:
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN.strip()
        self.chat_id = settings.TELEGRAM_CHAT_ID.strip()
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            log.info("Telegram notifications enabled")

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            log.error(f"Telegram send failed: {e}")
            return False

    # ── Trade alerts ──────────────────────────────────────────────────────────

    def notify_trade_entry(self, symbol, qty, price, sl, target, paper):
        mode = "PAPER" if paper else "LIVE"
        risk = (price - sl) * qty
        reward = (target - price) * qty
        self.send(
            f"{'📄' if paper else '💰'} <b>{mode} TRADE ENTRY</b>\n\n"
            f"📈 <b>{symbol}</b>\n"
            f"Qty: {qty} shares @ ₹{price:.2f}\n"
            f"Stop Loss: ₹{sl:.2f}  (risk ₹{risk:.0f})\n"
            f"Target:    ₹{target:.2f}  (reward ₹{reward:.0f})\n"
            f"R:R = 1:{reward/risk:.1f}"
        )

    def notify_trade_exit(self, symbol, pnl, pnl_pct, reason):
        emoji = "✅" if pnl >= 0 else "❌"
        self.send(
            f"{emoji} <b>TRADE EXIT — {symbol}</b>\n\n"
            f"PnL: <b>₹{pnl:+.2f}</b> ({pnl_pct:+.2f}%)\n"
            f"Reason: {reason}"
        )

    def notify_daily_summary(self, realized, unrealized, trades, win_rate):
        total = realized + unrealized
        emoji = "📈" if total >= 0 else "📉"
        self.send(
            f"{emoji} <b>Daily Summary</b>\n\n"
            f"Realized P&L:   ₹{realized:+.2f}\n"
            f"Unrealized P&L: ₹{unrealized:+.2f}\n"
            f"Total:          <b>₹{total:+.2f}</b>\n\n"
            f"Open trades: {trades}  |  Win rate: {win_rate:.0f}%"
        )

    def notify_morning_digest(self, available_cash, open_positions, strategy, universe_size):
        self.send(
            f"☀️ <b>Good Morning — Market opens in 15 min</b>\n\n"
            f"Strategy:   {strategy.title()}\n"
            f"Universe:   {universe_size} stocks\n"
            f"Cash ready: ₹{available_cash:,.0f}\n"
            f"Open positions: {open_positions}\n\n"
            f"Running market scan now..."
        )

    def notify_weekly_report(self, summary: dict):
        pnl = summary["total_pnl"]
        emoji = "📈" if pnl >= 0 else "📉"
        self.send(
            f"{emoji} <b>Weekly Report</b>\n\n"
            f"Total P&L:  <b>₹{pnl:+.2f}</b>\n"
            f"Trades:     {summary['total_trades']}\n"
            f"Win rate:   {summary['win_rate']:.1f}%\n"
            f"Best trade: ₹{summary['best_trade']:+.2f}\n"
            f"Avg hold:   {summary['avg_hold_days']:.1f} days"
        )

    def notify_auth_reminder(self):
        self.send(
            "⏰ <b>Auth Reminder</b>\n\n"
            "Market opens in 30 minutes.\n"
            "Run the daily auth to refresh your Zerodha token:\n\n"
            "<code>python main.py auth</code>\n\n"
            "Then the agent will scan and trade automatically."
        )

    def notify_scan_result(self, signals_found, trades_executed):
        if trades_executed:
            self.send(
                f"🔍 <b>Scan Complete</b>\n\n"
                f"Signals found: {signals_found}\n"
                f"Trades executed: <b>{trades_executed}</b>"
            )
        else:
            self.send(
                f"🔍 <b>Scan Complete — No trades today</b>\n\n"
                f"Signals found: {signals_found}\n"
                f"Market conditions did not meet entry criteria.\n"
                f"Capital preserved."
            )

    def notify_error(self, error: str):
        self.send(f"⚠️ <b>Agent Error</b>\n\n{error[:300]}")

    def notify_halt(self, reason: str):
        self.send(f"🛑 <b>Trading Halted</b>\n\n{reason}")

    # ── Telegram Bot command listener ─────────────────────────────────────────

    def start_command_listener(self, agent):
        """Listen for Telegram commands and act on them."""
        thread = threading.Thread(target=self._poll_commands, args=(agent,), daemon=True)
        thread.start()
        log.info("Telegram command listener started")

    def _poll_commands(self, agent):
        import time
        offset = None
        log.info("Listening for Telegram commands...")

        while True:
            try:
                params = {"timeout": 30}
                if offset:
                    params["offset"] = offset

                r = requests.get(
                    f"https://api.telegram.org/bot{self.token}/getUpdates",
                    params=params,
                    timeout=35,
                )
                data = r.json()
                if not data.get("ok"):
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip().lower()
                    from_id = str(msg.get("chat", {}).get("id", ""))

                    # Only respond to the configured chat
                    if from_id != self.chat_id:
                        continue

                    self._handle_command(text, agent)

            except Exception as e:
                log.error(f"Telegram poll error: {e}")
                time.sleep(10)

    def _handle_command(self, text: str, agent):
        log.info(f"Telegram command received: {text}")

        if text in ("/start", "/help"):
            self.send(
                "🤖 <b>Trade Agent Commands</b>\n\n"
                "/status   — Portfolio + open positions\n"
                "/scan     — Run market scan now\n"
                "/report   — Monthly P&L report\n"
                "/history  — Last 10 trades\n"
                "/halt     — Stop trading for today\n"
                "/resume   — Resume trading\n"
                "/help     — Show this menu"
            )

        elif text == "/status":
            try:
                status = agent.get_portfolio_status()
                pos = status["open_positions"]
                lines = [
                    f"💼 <b>Portfolio Status</b>\n",
                    f"Mode: {'PAPER' if status['paper_trading'] else 'LIVE'}",
                    f"Cash: ₹{status['available_cash']:,.0f}",
                    f"Open positions: {pos}",
                    f"Today realized: ₹{status['today_realized_pnl']:+.2f}",
                    f"Today unrealized: ₹{status['today_unrealized_pnl']:+.2f}",
                    f"Today total: <b>₹{status['today_total_pnl']:+.2f}</b>",
                ]
                if status["positions"]:
                    lines.append("\n<b>Positions:</b>")
                    for p in status["positions"]:
                        emoji = "🟢" if p["unrealized_pnl"] >= 0 else "🔴"
                        lines.append(f"{emoji} {p['symbol']}: ₹{p['ltp']:.2f} ({p['pnl_pct']:+.2f}%)")
                self.send("\n".join(lines))
            except Exception as e:
                self.send(f"Error fetching status: {e}")

        elif text == "/scan":
            self.send("🔍 Starting market scan... (this takes 1–2 minutes)")
            try:
                results = agent.run_daily_scan()
                self.notify_scan_result(
                    signals_found=len(results),
                    trades_executed=len([r for r in results if r])
                )
            except Exception as e:
                self.send(f"Scan failed: {e}")

        elif text == "/report":
            try:
                from datetime import datetime
                from src.database.repository import TradeRepository
                repo = TradeRepository()
                now = datetime.now()
                summary = repo.get_monthly_summary(now.year, now.month)
                self.notify_weekly_report(summary)
            except Exception as e:
                self.send(f"Report error: {e}")

        elif text == "/history":
            try:
                from src.database.repository import TradeRepository
                from src.database.models import TradeStatus
                repo = TradeRepository()
                trades = repo.get_all_trades(limit=10)
                if not trades:
                    self.send("No trades yet.")
                    return
                lines = ["📋 <b>Last 10 Trades</b>\n"]
                for t in trades:
                    emoji = "✅" if t.pnl > 0 else "❌" if t.status == TradeStatus.CLOSED else "🟡"
                    pnl_str = f"₹{t.pnl:+.2f}" if t.status == TradeStatus.CLOSED else "open"
                    lines.append(f"{emoji} {t.symbol} x{t.quantity} — {pnl_str}")
                self.send("\n".join(lines))
            except Exception as e:
                self.send(f"History error: {e}")

        elif text == "/halt":
            agent._halted = True
            self.send("🛑 Trading halted for today. Send /resume to restart.")

        elif text == "/resume":
            agent._halted = False
            self.send("✅ Trading resumed.")

        else:
            self.send(f"Unknown command: {text}\nSend /help to see available commands.")
