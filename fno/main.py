"""
F&O Agent Entry Point.

Run with:
    python -m fno.main

This starts the F&O scheduler which will:
  - Scan Nifty every 30 min from 9:15 to 13:45
  - Monitor positions every 5 minutes
  - Square-off everything at 3:15 PM
  - Send Telegram alerts for all trades

Before running:
  1. Make sure your .env file has OPENAI_API_KEY set
  2. For live trading: also set KITE_API_KEY and KITE_ACCESS_TOKEN
  3. For paper trading (safe mode): set PAPER_TRADING=true (already default)
  4. Optional: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for alerts

F&O-specific settings (optional .env vars):
  FNO_MAX_LOTS=1          # Buy 1 lot at a time (75 shares = 1 Nifty lot)
  FNO_MAX_PREMIUM=15000   # Don't buy if option costs more than ₹15,000 per lot
  FNO_SL_PCT=40.0         # Exit if option loses 40% of entry price
  FNO_TARGET_PCT=80.0     # Exit if option gains 80% of entry price
  FNO_EXPIRY=weekly       # Trade weekly options (nearest expiry)
"""
import sys
import os

# Ensure we can import from project root (needed for src.utils, src.broker, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from fno.config import fno_settings
    from src.utils import get_logger

    log = get_logger("fno.main")

    log.info("=" * 60)
    log.info("  F&O Trading Agent — Nifty Options")
    log.info("=" * 60)
    log.info(f"  Mode:        {'PAPER TRADING (safe)' if fno_settings.PAPER_TRADING else 'LIVE TRADING'}")
    log.info(f"  Strategy:    Buy ATM CE (Nifty up) or PE (Nifty down)")
    log.info(f"  Max lots:    {fno_settings.FNO_MAX_LOTS} lot ({fno_settings.FNO_MAX_LOTS * fno_settings.NIFTY_LOT_SIZE} shares)")
    log.info(f"  Max cost:    ₹{fno_settings.FNO_MAX_PREMIUM:,.0f} per lot")
    log.info(f"  Stop-loss:   -{fno_settings.FNO_SL_PCT:.0f}% of entry premium")
    log.info(f"  Target:      +{fno_settings.FNO_TARGET_PCT:.0f}% of entry premium")
    log.info(f"  Expiry:      {fno_settings.FNO_EXPIRY}")
    log.info(f"  DB:          {fno_settings.DB_PATH}")
    log.info("=" * 60)

    if not fno_settings.PAPER_TRADING:
        log.warning("LIVE TRADING MODE — real money will be used!")
        log.warning("Make sure KITE_ACCESS_TOKEN is fresh (renewed today)")
        log.warning("Press Ctrl+C in the next 5 seconds to abort...")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Aborted by user.")
            sys.exit(0)

    from fno.scheduler.runner import run_fno_scheduler
    run_fno_scheduler()


if __name__ == "__main__":
    main()
