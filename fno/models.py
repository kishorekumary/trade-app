"""
SQLAlchemy ORM models for the F&O agent.
Uses a separate SQLite database: fno/data/fno_trades.db
This is completely independent from the equity agent's trades.db.
"""
import os
import enum
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, Index
)
from sqlalchemy.orm import DeclarativeBase, Session

from fno.config import fno_settings

# Ensure data directory exists
os.makedirs(os.path.dirname(fno_settings.DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{fno_settings.DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class FnoTradeStatus(str, enum.Enum):
    """
    OPEN  = position is live (option was bought, not yet sold)
    CLOSED = position has been exited (target/SL/squareoff)
    """
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class FnoTrade(Base):
    """
    One row per option trade.

    Example row:
      symbol         = "NIFTY29MAY2524500CE"
      underlying     = "NIFTY"
      option_type    = "CE"    (CE = Call = bullish bet)
      strike         = 24500
      expiry         = "29-May-2025"
      lots           = 1
      lot_size       = 75
      entry_premium  = 180.50  (price per share when we bought)
      total_cost     = 180.50 * 1 * 75 = ₹13,537.50
      exit_premium   = 324.90  (price per share when we sold)
      pnl            = (324.90 - 180.50) * 1 * 75 = ₹10,830
      pnl_pct        = (324.90/180.50 - 1) * 100 = +80%
    """
    __tablename__ = "fno_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Option identity
    symbol = Column(String(30), nullable=False, index=True)
    # e.g. "NIFTY29MAY2524500CE" — the trading symbol used in Kite NFO

    underlying = Column(String(10), nullable=False, default="NIFTY")
    # Always "NIFTY" for now

    option_type = Column(String(2), nullable=False)
    # "CE" = Call (bullish) or "PE" = Put (bearish)

    strike = Column(Integer, nullable=False)
    # e.g. 24500 — the strike price (multiple of 50 for Nifty)

    expiry = Column(String(20), nullable=False)
    # e.g. "29-May-2025" — the option's expiry date (from NSE format)

    # Position sizing
    lots = Column(Integer, nullable=False, default=1)
    # Number of lots bought (1 lot = 75 shares for Nifty)

    lot_size = Column(Integer, nullable=False, default=75)
    # Shares per lot

    # Pricing
    entry_premium = Column(Float, nullable=False)
    # Price per share at entry (what we paid for each of the 75 shares in a lot)

    total_cost = Column(Float, nullable=False)
    # = entry_premium * lots * lot_size — total cash deployed

    exit_premium = Column(Float, nullable=True)
    # Price per share at exit

    # P&L (filled when trade is closed)
    pnl = Column(Float, nullable=True)
    # = (exit_premium - entry_premium) * lots * lot_size
    # Positive = profit, Negative = loss

    pnl_pct = Column(Float, nullable=True)
    # = ((exit_premium / entry_premium) - 1) * 100
    # e.g. +80.0 means option doubled to 80% gain

    # Status
    status = Column(String(10), nullable=False, default=FnoTradeStatus.OPEN)

    # Timestamps (UTC stored, displayed in IST)
    entry_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)

    # Why the trade was closed
    exit_reason = Column(String(100), nullable=True)
    # e.g. "TARGET_HIT", "SL_HIT", "SQUAREOFF", "MANUAL"

    # Order tracking
    kite_order_id = Column(String(50), nullable=True)
    # Entry order ID from Kite (or PAPER_xxx for paper trades)

    kite_exit_order_id = Column(String(50), nullable=True)
    # Exit order ID from Kite

    paper_trade = Column(Boolean, nullable=False, default=True)
    # True = paper/simulation, False = real money

    # GPT reasoning (why the agent took this trade)
    agent_reasoning = Column(Text, nullable=True)

    # Market context snapshot at entry
    nifty_spot_at_entry = Column(Float, nullable=True)
    # What Nifty 50 spot price was when we entered

    iv_at_entry = Column(Float, nullable=True)
    # Implied Volatility of the option at entry (if available)

    __table_args__ = (
        Index("idx_fno_status", "status"),
        Index("idx_fno_entry_time", "entry_time"),
        Index("idx_fno_symbol", "symbol"),
    )

    def __repr__(self):
        return (
            f"<FnoTrade {self.symbol} lots={self.lots} "
            f"entry={self.entry_premium:.2f} status={self.status}>"
        )


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    return Session(engine)
