import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Zerodha
    KITE_API_KEY: str = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET: str = os.getenv("KITE_API_SECRET", "")
    KITE_ACCESS_TOKEN: str = os.getenv("KITE_ACCESS_TOKEN", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Trading mode
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"

    # Risk controls
    MAX_CAPITAL_PER_TRADE: float = float(os.getenv("MAX_CAPITAL_PER_TRADE", "10000"))
    MAX_DAILY_LOSS: float = float(os.getenv("MAX_DAILY_LOSS", "5000"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "2.0"))

    # Strategy
    STRATEGY: str = os.getenv("STRATEGY", "momentum")
    UNIVERSE: str = os.getenv("UNIVERSE", "NIFTY50")
    HOLD_PERIOD_DAYS: int = int(os.getenv("HOLD_PERIOD_DAYS", "3"))

    # Notifications
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Market timing (IST)
    MARKET_OPEN = "09:15"
    MARKET_CLOSE = "15:30"
    PRE_MARKET_SCAN = "09:00"   # Run analysis before open

    # Database
    DB_PATH: str = os.path.join(os.path.dirname(__file__), "..", "data", "trades.db")

    # Nifty 50 symbols (NSE)
    NIFTY50_SYMBOLS = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
        "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "HCLTECH",
        "SUNPHARMA", "TITAN", "BAJFINANCE", "WIPRO", "ULTRACEMCO",
        "ONGC", "NTPC", "POWERGRID", "TATAMOTORS", "TECHM",
        "ADANIPORTS", "BAJAJFINSV", "DIVISLAB", "DRREDDY", "EICHERMOT",
        "GRASIM", "HEROMOTOCO", "HINDALCO", "JSWSTEEL", "M&M",
        "NESTLEIND", "SBILIFE", "TATACONSUM", "TATASTEEL", "UPL",
        "INDUSINDBK", "COALINDIA", "BPCL", "CIPLA", "BRITANNIA",
        "APOLLOHOSP", "ADANIENT", "LTIM", "HDFCLIFE", "BAJAJ-AUTO"
    ]

    NIFTY100_SYMBOLS = NIFTY50_SYMBOLS + [
        "PIDILITIND", "SIEMENS", "HAVELLS", "MUTHOOTFIN", "TORNTPHARM",
        "CHOLAFIN", "SBICARD", "NAUKRI", "BERGEPAINT", "GODREJCP",
        "MARICO", "DABUR", "COLPAL", "LUPIN", "AUROPHARMA",
        "BIOCON", "PGHH", "BATAINDIA", "PAGEIND", "MCDOWELL-N",
        "TVSMOTOR", "BALKRISIND", "CUMMINSIND", "PERSISTENT", "LTTS",
        "MPHASIS", "COFORGE", "MINDTREE", "ICICIGI", "ICICIPRULI"
    ]

    @classmethod
    def get_universe(cls) -> list[str]:
        if cls.UNIVERSE == "NIFTY50":
            return cls.NIFTY50_SYMBOLS
        elif cls.UNIVERSE == "NIFTY100":
            return cls.NIFTY100_SYMBOLS
        return cls.NIFTY50_SYMBOLS

    @classmethod
    def validate(cls) -> None:
        if not cls.PAPER_TRADING:
            if not cls.KITE_API_KEY or not cls.KITE_ACCESS_TOKEN:
                raise ValueError("KITE_API_KEY and KITE_ACCESS_TOKEN required for live trading")
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")


settings = Settings()
