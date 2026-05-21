import time
import schedule
from datetime import datetime
import pytz
from src.utils import get_logger
from src.agent import TradingAgent
from src.reports import ReportGenerator
from src.notifications import TelegramNotifier
from config.settings import settings

log = get_logger("scheduler")
IST = pytz.timezone("Asia/Kolkata")

SCAN_SLOTS = [
    ("09:00", "Morning"),
    ("11:30", "Mid-morning"),
    ("13:30", "Post-lunch"),
]


def ist_now() -> datetime:
    return datetime.now(IST)


def is_market_hours() -> bool:
    now = ist_now()
    if now.weekday() >= 5:
        return False
    oh, om = map(int, settings.MARKET_OPEN.split(":"))
    ch, cm = map(int, settings.MARKET_CLOSE.split(":"))
    market_open  = now.replace(hour=oh, minute=om, second=0, microsecond=0)
    market_close = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_weekday() -> bool:
    return ist_now().weekday() < 5


def _next_scan_slot() -> str:
    """Return label of the next scan slot that hasn't passed yet today."""
    now = ist_now()
    for slot_time, label in SCAN_SLOTS:
        h, m = map(int, slot_time.split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < slot_dt:
            return f"{label} scan at {slot_time} IST"
    return "No more scans today — next scan tomorrow at 09:00 IST"


def _missed_scans_today() -> list[str]:
    """Return labels of scan slots that already passed today."""
    now = ist_now()
    missed = []
    for slot_time, label in SCAN_SLOTS:
        h, m = map(int, slot_time.split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now > slot_dt:
            missed.append(label)
    return missed


# ── Job functions ──────────────────────────────────────────────────────────────

def job_auth_reminder(notifier: TelegramNotifier):
    if not is_weekday():
        return
    log.info("Sending auth reminder...")
    notifier.notify_auth_reminder()


def job_morning_scan(agent: TradingAgent, notifier: TelegramNotifier):
    if not is_weekday():
        return
    _run_scan(agent, notifier, "Morning", full_universe=True)


def job_intraday_scan(agent: TradingAgent, notifier: TelegramNotifier, label: str):
    if not is_market_hours() or not is_weekday():
        return
    # Intraday: top 25 most liquid stocks only (less time left in day)
    _run_scan(agent, notifier, label, full_universe=False)


def _run_scan(agent: TradingAgent, notifier: TelegramNotifier, label: str, full_universe: bool):
    if getattr(agent, "_halted", False):
        log.warning("Scan skipped — agent halted")
        return
    log.info(f"Running {label} scan...")
    original_universe = agent.universe
    try:
        if not full_universe:
            agent.universe = settings.get_universe()[:25]

        status = agent.get_portfolio_status()
        notifier.notify_morning_digest(
            available_cash=status["available_cash"],
            open_positions=status["open_positions"],
            strategy=settings.STRATEGY,
            universe_size=len(agent.universe),
        )
        results = agent.run_daily_scan()
        notifier.notify_scan_result(
            signals_found=len(results),
            trades_executed=len([r for r in results if r]),
        )
    except Exception as e:
        log.error(f"{label} scan error: {e}")
        notifier.notify_error(str(e))
    finally:
        agent.universe = original_universe


def job_position_monitor(agent: TradingAgent, notifier: TelegramNotifier):
    if not is_market_hours():
        return
    if getattr(agent, "_halted", False):
        return
    log.info("Monitoring positions...")
    try:
        open_trades = agent.repo.get_open_trades()
        exited = agent.orders.check_stop_loss_targets(open_trades)
        for t in exited:
            notifier.notify_trade_exit(t.symbol, t.pnl, t.pnl_pct, "SL/Target hit")
    except Exception as e:
        log.error(f"Monitor error: {e}")


def job_eod_report(agent: TradingAgent, notifier: TelegramNotifier):
    if not is_weekday():
        return
    log.info("Sending end-of-day summary...")
    try:
        status = agent.get_portfolio_status()
        notifier.notify_daily_summary(
            realized=status["today_realized_pnl"],
            unrealized=status["today_unrealized_pnl"],
            trades=status["open_positions"],
            win_rate=0,
        )
    except Exception as e:
        log.error(f"EOD report error: {e}")


def job_weekly_report(agent: TradingAgent, notifier: TelegramNotifier):
    log.info("Sending weekly report...")
    try:
        now = datetime.now()
        summary = agent.repo.get_monthly_summary(now.year, now.month)
        notifier.notify_weekly_report(summary)
    except Exception as e:
        log.error(f"Weekly report error: {e}")


# ── Main scheduler ─────────────────────────────────────────────────────────────

def run_scheduler():
    agent = TradingAgent()
    notifier = TelegramNotifier()
    now = ist_now()

    day_name   = now.strftime("%A")          # e.g. Thursday
    time_ist   = now.strftime("%H:%M IST")
    weekday    = is_weekday()
    mkt_open   = is_market_hours()
    missed     = _missed_scans_today() if weekday else []
    next_scan  = _next_scan_slot() if weekday else "Markets closed (weekend)"

    # ── Startup log ───────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info(f"Trade Agent Scheduler — {day_name}  {time_ist}")
    log.info("=" * 60)
    log.info(f"Market hours: {'OPEN' if mkt_open else 'CLOSED'}")
    if missed:
        log.info(f"Missed scans today (already past): {', '.join(missed)}")
        log.info("→ Run /scan on Telegram to scan right now")
    log.info(f"Next scheduled scan: {next_scan}")
    log.info("-" * 60)
    log.info("Full schedule (IST):")
    log.info("  08:45  — Auth reminder via Telegram")
    log.info("  09:00  — Morning scan      (50 stocks)")
    log.info("  11:30  — Mid-morning scan  (25 stocks)")
    log.info("  13:30  — Post-lunch scan   (25 stocks)")
    log.info("  15:30  — End-of-day report")
    log.info("  Fri 16:00 — Weekly report")
    log.info("  Every 15m — SL/target auto-exit monitor")
    log.info("-" * 60)
    log.info("Telegram commands: /scan /status /report /history /halt /resume")
    log.info("=" * 60)

    # ── Telegram startup message ───────────────────────────────────────────────
    if notifier.enabled:
        missed_note = ""
        if missed:
            missed_note = (
                f"\n\n⚠️ Missed today: {', '.join(missed)} scan(s)\n"
                f"Send /scan to run one now."
            )
        notifier.send(
            f"🤖 <b>Trade Agent started</b>\n\n"
            f"📅 {day_name}  •  {time_ist}\n"
            f"📊 Market: {'🟢 Open' if mkt_open else '🔴 Closed'}\n"
            f"🎯 Strategy: {settings.STRATEGY.title()}\n"
            f"💼 Mode: {'PAPER' if settings.PAPER_TRADING else '🔴 LIVE'}\n"
            f"⏭ Next scan: {next_scan}"
            f"{missed_note}\n\n"
            f"Send /help for commands."
        )
        notifier.start_command_listener(agent)

    # ── Auto-run scan if started mid-market and missed morning slot ───────────
    if weekday and mkt_open and "Morning" in missed:
        log.info("Started after 09:00 — running catch-up scan now...")
        _run_scan(agent, notifier, "Catch-up", full_universe=True)

    # ── Register all jobs ─────────────────────────────────────────────────────
    schedule.every().day.at("08:45").do(job_auth_reminder, notifier)
    schedule.every().day.at("09:00").do(job_morning_scan, agent, notifier)
    schedule.every().day.at("11:30").do(job_intraday_scan, agent, notifier, "Mid-morning")
    schedule.every().day.at("13:30").do(job_intraday_scan, agent, notifier, "Post-lunch")
    schedule.every(15).minutes.do(job_position_monitor, agent, notifier)
    schedule.every().day.at("15:30").do(job_eod_report, agent, notifier)
    schedule.every().friday.at("16:00").do(job_weekly_report, agent, notifier)

    while True:
        schedule.run_pending()
        time.sleep(30)
