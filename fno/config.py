"""
F&O agent configuration.
Reads from the same .env file as the equity agent.
New F&O-specific variables are prefixed with FNO_.
"""
import os
from dotenv import load_dotenv

# Load .env from project root (parent of this fno/ directory)
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, ".env"))


class FnoSettings:
    # ── Zerodha Kite Connect ──────────────────────────────────────────────────
    KITE_API_KEY: str = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET: str = os.getenv("KITE_API_SECRET", "")
    KITE_ACCESS_TOKEN: str = os.getenv("KITE_ACCESS_TOKEN", "")

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # ── Trading mode ──────────────────────────────────────────────────────────
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"

    # ── Notifications ─────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── F&O-specific settings ─────────────────────────────────────────────────
    # Maximum number of lots to buy in a single trade (1 lot = 75 shares for Nifty)
    FNO_MAX_LOTS: int = int(os.getenv("FNO_MAX_LOTS", "1"))

    # Maximum premium (INR) per lot allowed — prevents buying very expensive options
    # e.g. if ATM CE costs ₹200/share, total cost = 200 × 75 = ₹15,000
    FNO_MAX_PREMIUM: float = float(os.getenv("FNO_MAX_PREMIUM", "15000"))

    # Exit if option loses this % of entry premium (stop-loss)
    FNO_SL_PCT: float = float(os.getenv("FNO_SL_PCT", "40.0"))

    # Exit if option gains this % of entry premium (target)
    FNO_TARGET_PCT: float = float(os.getenv("FNO_TARGET_PCT", "80.0"))

    # Which expiry to trade: "weekly" or "monthly"
    FNO_EXPIRY: str = os.getenv("FNO_EXPIRY", "weekly")

    # ── Lot size ──────────────────────────────────────────────────────────────
    # Nifty 50 lot size (SEBI-mandated, changes periodically — verify before trading)
    NIFTY_LOT_SIZE: int = 75

    # ── Database ──────────────────────────────────────────────────────────────
    # Separate DB from equity agent to keep them fully independent
    DB_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fno_trades.db")

    # ── Market timing (IST) ───────────────────────────────────────────────────
    MARKET_OPEN: str = "09:15"
    MARKET_CLOSE: str = "15:30"
    SQUAREOFF_TIME: str = "15:15"    # Force-exit all F&O positions before Zerodha auto square-off
    ENTRY_CUTOFF: str = "14:00"      # No new option buys after 2 PM (low liquidity + theta decay)

    # ── Risk limits ───────────────────────────────────────────────────────────
    # Daily loss limit = 3x max premium per lot
    @classmethod
    def get_max_daily_loss(cls) -> float:
        return cls.FNO_MAX_PREMIUM * 3

    # Maximum simultaneous open F&O positions
    MAX_OPEN_POSITIONS: int = 2

    @classmethod
    def validate(cls) -> None:
        if not cls.PAPER_TRADING:
            if not cls.KITE_API_KEY or not cls.KITE_ACCESS_TOKEN:
                raise ValueError("KITE_API_KEY and KITE_ACCESS_TOKEN required for live F&O trading")
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required for F&O agent")


fno_settings = FnoSettings()
