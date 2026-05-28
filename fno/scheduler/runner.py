"""
F&O Scheduler — runs the FnoAgent on a timed schedule.

Schedule (all times IST):
  09:15, 09:45, 10:15, 10:45, 11:15, 11:45, 12:15, 12:45, 13:15, 13:45
    → run_scan() — check if we should buy a CE or PE option

  Every 5 minutes between 09:15–15:15
    → monitor_positions() — check if any open positions hit SL or target

  15:15  → squareoff_all() — force-close everything before Zerodha's auto close
  15:30  → daily_summary() — print/send today's P&L summary

The scheduler also sends Telegram messages for:
  - Agent startup
  - Trade entered
  - Trade exited (SL/target/squareoff)
  - Daily summary
"""
import time
import schedule
from datetime import datetime
from typing import Optional
import pytz
import requests

from fno.config import fno_settings
from fno.agent.fno_agent import FnoAgent
from src.utils import get_logger

log = get_logger("fno.scheduler")
IST = pytz.timezone("Asia/Kolkata")

# Scan times: every 30 minutes from 9:15 to 14:00 (last entry cutoff)
SCAN_SLOTS = [
    "09:15", "09:45",
    "10:15", "10:45",
    "11:15", "11:45",
    "12:15", "12:45",
    "13:15", "13:45",
]


# ── Telegram helper (lightweight, no class needed) ────────────────────────────

def _telegram_send(message: str):
    """Send a Telegram message. Silently fails if token/chat not configured."""
    token = fno_settings.TELEGRAM_BOT_TOKEN.strip()
    chat_id = fno_settings.TELEGRAM_CHAT_ID.strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.debug(f"Telegram send failed: {e}")


# ── Time helpers ──────────────────────────────────────────────────────────────

def ist_now() -> datetime:
    return datetime.now(IST)


def is_weekday() -> bool:
    return ist_now().weekday() < 5


def is_market_hours() -> bool:
    """True if current IST time is between 09:15 and 15:30 on a weekday."""
    if not is_weekday():
        return False
    now = ist_now()
    open_h, open_m = map(int, fno_settings.MARKET_OPEN.split(":"))
    close_h, close_m = map(int, fno_settings.MARKET_CLOSE.split(":"))
    market_open = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    market_close = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return market_open <= now <= market_close


def _next_scan_slot() -> str:
    """Return the label of the next upcoming scan slot."""
    now = ist_now()
    for slot in SCAN_SLOTS:
        h, m = map(int, slot.split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < slot_dt:
            return f"{slot} IST"
    return "No more scans today — resumes tomorrow at 09:15 IST"


# ── Job functions ─────────────────────────────────────────────────────────────

def job_scan(agent: FnoAgent):
    """Run a market scan and optionally enter a trade."""
    if not is_weekday():
        return
    if not is_market_hours():
        log.info("Scan skipped — market not open")
        return

    try:
        time_str = ist_now().strftime("%H:%M IST")
        log.info(f"Running F&O scan at {time_str}...")

        result = agent.run_scan()
        if not result:
            log.info("Scan returned no result")
            _telegram_send(
                f"F&O Scan ({time_str})\n"
                f"No trade — could not fetch market data."
            )
            return

        action = result.get("action", "SKIP")
        trade = result.get("trade")
        decision = result.get("decision", {})
        reasoning = result.get("reasoning", decision.get("reasoning", ""))

        if trade:
            # Trade was entered
            sl_prem, tgt_prem = agent.risk.calculate_exit_levels(trade.entry_premium)
            cost_per_lot = trade.entry_premium * trade.lot_size
            _telegram_send(
                f"F&O TRADE ENTERED ({time_str})\n\n"
                f"Symbol: <b>{trade.symbol}</b>\n"
                f"Type: {'CALL (CE) — bullish bet' if trade.option_type == 'CE' else 'PUT (PE) — bearish bet'}\n"
                f"Strike: {trade.strike}\n"
                f"Lots: {trade.lots} (= {trade.lots * trade.lot_size} shares)\n"
                f"Entry premium: ₹{trade.entry_premium:.2f} per share\n"
                f"Total cost: ₹{trade.total_cost:.0f}\n"
                f"Stop-loss at: ₹{sl_prem:.2f} (lose -{fno_settings.FNO_SL_PCT:.0f}%)\n"
                f"Target at: ₹{tgt_prem:.2f} (gain +{fno_settings.FNO_TARGET_PCT:.0f}%)\n"
                f"Mode: {'PAPER' if trade.paper_trade else 'LIVE'}\n\n"
                f"Reason: {reasoning}"
            )
        elif action == "SKIP":
            confidence = decision.get("confidence", 0)
            _telegram_send(
                f"F&O Scan ({time_str})\n"
                f"Action: SKIP | Confidence: {confidence:.0%}\n"
                f"Reason: {reasoning}"
            )
        elif action == "BLOCKED":
            _telegram_send(
                f"F&O Scan ({time_str})\n"
                f"Trade BLOCKED by risk rules:\n"
                f"{result.get('reason', 'Unknown reason')}"
            )

    except Exception as e:
        log.error(f"Scan job error: {e}")
        _telegram_send(f"F&O Scan ERROR:\n{str(e)[:300]}")


def job_monitor(agent: FnoAgent):
    """Monitor open positions for SL/target exits."""
    if not is_market_hours():
        return

    try:
        exited = agent.monitor_positions()
        for ex in exited:
            trade = ex.get("trade")
            if not trade:
                continue
            pnl = ex.get("pnl", 0)
            pnl_pct = ex.get("pnl_pct", 0)
            reason = ex.get("exit_reason", "")
            emoji = "PROFIT" if pnl >= 0 else "LOSS"
            _telegram_send(
                f"F&O EXIT — {emoji}\n\n"
                f"Symbol: <b>{trade.symbol}</b>\n"
                f"Exit premium: ₹{ex.get('exit_premium', 0):.2f}\n"
                f"P&L: <b>{'+'if pnl>=0 else ''}₹{pnl:.2f} ({pnl_pct:+.1f}%)</b>\n"
                f"Reason: {reason}"
            )
    except Exception as e:
        log.error(f"Monitor job error: {e}")


def job_squareoff(agent: FnoAgent):
    """Force-close all open positions at 3:15 PM."""
    if not is_weekday():
        return

    log.info("Initiating F&O square-off (3:15 PM)...")
    try:
        closed = agent.squareoff_all()
        if not closed:
            log.info("Square-off: no open positions")
            _telegram_send("F&O Square-off complete — no open positions.")
            return

        total_pnl = sum(c.get("pnl", 0) for c in closed)
        lines = ["F&O Square-off Complete (3:15 PM)\n"]
        for c in closed:
            trade = c.get("trade")
            if trade:
                pnl = c.get("pnl", 0)
                lines.append(
                    f"  {trade.symbol}: ₹{c.get('exit_premium', 0):.2f} | "
                    f"PnL={'+'if pnl>=0 else ''}₹{pnl:.2f}"
                )
        lines.append(f"\nTotal square-off PnL: {'+'if total_pnl>=0 else ''}₹{total_pnl:.2f}")
        _telegram_send("\n".join(lines))

    except Exception as e:
        log.error(f"Square-off error: {e}")
        _telegram_send(f"F&O Square-off ERROR:\n{str(e)[:300]}")


def job_daily_summary(agent: FnoAgent):
    """Print and send the end-of-day summary at 3:30 PM."""
    if not is_weekday():
        return

    log.info("Sending F&O daily summary...")
    try:
        today_pnl = agent.repo.get_today_pnl()
        all_today = [
            t for t in agent.repo.get_all_trades(limit=20)
            if t.entry_time and t.entry_time.date() == datetime.utcnow().date()
        ]
        closed_today = [t for t in all_today if t.status == "CLOSED"]

        winners = [t for t in closed_today if (t.pnl or 0) > 0]
        losers = [t for t in closed_today if (t.pnl or 0) <= 0]
        win_rate = (len(winners) / len(closed_today) * 100) if closed_today else 0

        pnl_sign = "+" if today_pnl >= 0 else ""

        summary = (
            f"F&O Daily Summary\n\n"
            f"Trades today: {len(closed_today)}\n"
            f"Winners: {len(winners)} | Losers: {len(losers)}\n"
            f"Win rate: {win_rate:.0f}%\n"
            f"Today's P&L: <b>{pnl_sign}₹{today_pnl:.2f}</b>\n"
            f"Mode: {'PAPER' if agent.paper_trading else 'LIVE'}"
        )
        log.info(summary.replace("\n", " | "))
        _telegram_send(summary)

    except Exception as e:
        log.error(f"Daily summary error: {e}")


# ── Main scheduler ────────────────────────────────────────────────────────────

def run_fno_scheduler():
    """
    Start the F&O agent scheduler.
    This is the main entry point — called from fno/main.py.
    """
    agent = FnoAgent()
    now = ist_now()
    day_name = now.strftime("%A")
    time_str = now.strftime("%H:%M IST")
    weekday = is_weekday()
    market_open = is_market_hours()

    # ── Startup log ──────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info(f"F&O Agent Scheduler Started — {day_name}  {time_str}")
    log.info("=" * 60)
    log.info(f"Mode: {'PAPER TRADING' if fno_settings.PAPER_TRADING else 'LIVE TRADING'}")
    log.info(f"Market: {'OPEN' if market_open else 'CLOSED'}")
    log.info(f"Max lots: {fno_settings.FNO_MAX_LOTS} (lot size: {fno_settings.NIFTY_LOT_SIZE} shares/lot)")
    log.info(f"Max premium: ₹{fno_settings.FNO_MAX_PREMIUM:.0f} per lot")
    log.info(f"Stop-loss: -{fno_settings.FNO_SL_PCT:.0f}% | Target: +{fno_settings.FNO_TARGET_PCT:.0f}%")
    log.info(f"Entry cutoff: {fno_settings.ENTRY_CUTOFF} IST")
    log.info(f"Square-off: {fno_settings.SQUAREOFF_TIME} IST")
    log.info("-" * 60)
    log.info("Schedule (IST):")
    log.info(f"  Scans:      {', '.join(SCAN_SLOTS)}")
    log.info(f"  Monitor:    every 5 min during market hours")
    log.info(f"  Square-off: {fno_settings.SQUAREOFF_TIME}")
    log.info(f"  Summary:    {fno_settings.MARKET_CLOSE}")
    log.info("-" * 60)

    # ── Startup Telegram message ─────────────────────────────────────────────
    next_scan = _next_scan_slot()
    _telegram_send(
        f"F&O Agent Started\n\n"
        f"{day_name}  {time_str}\n"
        f"Market: {'Open' if market_open else 'Closed'}\n"
        f"Mode: {'PAPER' if fno_settings.PAPER_TRADING else 'LIVE'}\n"
        f"Strategy: Buy ATM CE (bullish) or PE (bearish)\n"
        f"Next scan: {next_scan}\n\n"
        f"Max lots: {fno_settings.FNO_MAX_LOTS} | "
        f"Max premium: ₹{fno_settings.FNO_MAX_PREMIUM:.0f}\n"
        f"SL: -{fno_settings.FNO_SL_PCT:.0f}% | Target: +{fno_settings.FNO_TARGET_PCT:.0f}%"
    )

    # ── Run immediate catch-up scan if started mid-market ────────────────────
    if weekday and market_open:
        log.info("Market is open now — running immediate scan...")
        job_scan(agent)

    # ── Register all scheduled jobs ───────────────────────────────────────────
    # Scan every 30 minutes from 09:15 to 13:45
    for slot_time in SCAN_SLOTS:
        schedule.every().day.at(slot_time).do(job_scan, agent)

    # Monitor positions every 5 minutes
    schedule.every(5).minutes.do(job_monitor, agent)

    # Square-off at 3:15 PM
    schedule.every().day.at(fno_settings.SQUAREOFF_TIME).do(job_squareoff, agent)

    # Daily summary at 3:30 PM
    schedule.every().day.at(fno_settings.MARKET_CLOSE).do(job_daily_summary, agent)

    log.info("All jobs registered. Scheduler running...")
    log.info(f"Next scan: {next_scan}")
    log.info("Press Ctrl+C to stop.")
    log.info("=" * 60)

    # ── Main loop ─────────────────────────────────────────────────────────────
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds
        except KeyboardInterrupt:
            log.info("F&O Scheduler stopped by user (Ctrl+C)")
            _telegram_send("F&O Agent stopped (manual interrupt).")
            break
        except Exception as e:
            log.error(f"Scheduler loop error: {e}")
            _telegram_send(f"F&O Scheduler error:\n{str(e)[:300]}")
            time.sleep(60)  # Brief pause before retrying
