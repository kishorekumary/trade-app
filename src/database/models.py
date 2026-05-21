from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, Enum, Index
)
from sqlalchemy.orm import DeclarativeBase, Session
import enum
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "trades.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class TradeStatus(str, enum.Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradeDirection(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    exchange = Column(String(10), default="NSE")
    direction = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target = Column(Float, nullable=True)
    status = Column(String(20), default=TradeStatus.PENDING)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    strategy = Column(String(50), nullable=True)
    agent_reasoning = Column(Text, nullable=True)
    kite_order_id = Column(String(50), nullable=True)
    kite_exit_order_id = Column(String(50), nullable=True)
    paper_trade = Column(Boolean, default=True)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)
    hold_days = Column(Integer, default=0)
    tags = Column(String(200), nullable=True)

    __table_args__ = (
        Index("idx_symbol_status", "symbol", "status"),
        Index("idx_entry_time", "entry_time"),
    )

    def __repr__(self):
        return f"<Trade {self.symbol} {self.direction} qty={self.quantity} status={self.status}>"


class DailyPnL(Base):
    __tablename__ = "daily_pnl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    trades_opened = Column(Integer, default=0)
    trades_closed = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    capital_deployed = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String(50), nullable=False)
    action = Column(String(20), nullable=False)  # BUY / SELL / HOLD / SKIP
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    market_context = Column(Text, nullable=True)
    executed = Column(Boolean, default=False)
    trade_id = Column(Integer, nullable=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, unique=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String(200), nullable=True)
    active = Column(Boolean, default=True)


def init_db():
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
