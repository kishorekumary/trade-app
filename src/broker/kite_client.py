import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
from src.utils import get_logger

log = get_logger("kite_client")


class KiteClient:
    """Wrapper around Zerodha Kite Connect with graceful fallback for missing credentials."""

    def __init__(self, api_key: str, api_secret: str, access_token: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self._kite = None
        self._connected = False
        self._init_kite()

    def _init_kite(self):
        if not self.api_key:
            log.warning("No Kite API key provided — running in simulation mode")
            return
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self.api_key)
            if self.access_token:
                self._kite.set_access_token(self.access_token)
                self._connected = True
                log.info("Kite Connect initialized with access token")
        except ImportError:
            log.error("kiteconnect package not installed. Run: pip install kiteconnect")
        except Exception as e:
            log.error(f"Kite init failed: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_login_url(self) -> str:
        if self._kite:
            return self._kite.login_url()
        return ""

    def generate_session(self, request_token: str) -> str:
        """Exchange request_token for access_token."""
        data = self._kite.generate_session(request_token, api_secret=self.api_secret)
        self.access_token = data["access_token"]
        self._kite.set_access_token(self.access_token)
        self._connected = True
        log.info("Kite session established")
        return self.access_token

    def get_profile(self) -> dict:
        if not self._kite:
            return {"user_name": "Simulation User", "email": "sim@example.com"}
        return self._kite.profile()

    def get_funds(self) -> dict:
        if not self._kite:
            return {"equity": {"available": {"live_balance": 100000.0}}}
        try:
            return self._kite.margins()
        except Exception as e:
            log.error(f"Failed to fetch funds: {e}")
            return {}

    def get_available_cash(self) -> float:
        funds = self.get_funds()
        try:
            return funds["equity"]["available"]["live_balance"]
        except (KeyError, TypeError):
            return 0.0

    def get_historical_data(
        self,
        symbol: str,
        interval: str = "day",
        days: int = 100,
        exchange: str = "NSE"
    ) -> pd.DataFrame:
        if not self._kite or not self._connected:
            return self._fetch_yfinance(symbol, days)

        try:
            instrument_token = self._get_instrument_token(symbol, exchange)
            if not instrument_token:
                return self._fetch_yfinance(symbol, days)

            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            data = self._kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                "close": "Close", "volume": "Volume"}, inplace=True)
            return df
        except Exception as e:
            if "permission" in str(e).lower():
                log.warning(f"Kite historical data permission denied for {symbol}, falling back to yfinance")
                return self._fetch_yfinance(symbol, days)
            log.error(f"Historical data fetch failed for {symbol}: {e}")
            return pd.DataFrame()

    _YF_SYMBOL_MAP = {
        "M&M": "M%26M",
    }

    def _fetch_yfinance(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            base = self._YF_SYMBOL_MAP.get(symbol, symbol)
            for suffix in (".NS", ".BO"):
                ticker = f"{base}{suffix}"
                df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False, auto_adjust=True)
                if not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df.index = pd.to_datetime(df.index)
                    df = df[["Open", "High", "Low", "Close", "Volume"]]
                    log.info(f"yfinance data fetched for {symbol} ({ticker}): {len(df)} rows")
                    return df
            log.warning(f"yfinance returned no data for {symbol}, using simulation")
            return self._simulate_historical_data(symbol, days)
        except Exception as e:
            log.error(f"yfinance fallback failed for {symbol}: {e}")
            return self._simulate_historical_data(symbol, days)

    def _get_instrument_token(self, symbol: str, exchange: str = "NSE") -> Optional[int]:
        try:
            instruments = self._kite.instruments(exchange)
            for inst in instruments:
                if inst["tradingsymbol"] == symbol:
                    return inst["instrument_token"]
        except Exception as e:
            log.error(f"Instrument lookup failed for {symbol}: {e}")
        return None

    def get_quote(self, symbols: list[str], exchange: str = "NSE") -> dict:
        if not self._kite or not self._connected:
            return {f"{exchange}:{s}": {"last_price": self._fetch_ltp_yfinance(s)} for s in symbols}
        try:
            formatted = [f"{exchange}:{s}" for s in symbols]
            return self._kite.quote(formatted)
        except Exception as e:
            if "permission" in str(e).lower():
                return {f"{exchange}:{s}": {"last_price": self._fetch_ltp_yfinance(s)} for s in symbols}
            log.error(f"Quote fetch failed: {e}")
            return {}

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        quote = self.get_quote([symbol], exchange)
        key = f"{exchange}:{symbol}"
        try:
            return quote[key]["last_price"]
        except (KeyError, TypeError):
            return 0.0

    def _fetch_ltp_yfinance(self, symbol: str) -> float:
        try:
            import yfinance as yf
            base = self._YF_SYMBOL_MAP.get(symbol, symbol)
            for suffix in (".NS", ".BO"):
                df = yf.download(f"{base}{suffix}", period="2d", interval="1d",
                                 progress=False, auto_adjust=True)
                if not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    return float(df["Close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    def get_positions(self) -> dict:
        if not self._kite or not self._connected:
            return {"net": [], "day": []}
        try:
            return self._kite.positions()
        except Exception as e:
            log.error(f"Positions fetch failed: {e}")
            return {"net": [], "day": []}

    def get_orders(self) -> list:
        if not self._kite or not self._connected:
            return []
        try:
            return self._kite.orders()
        except Exception as e:
            log.error(f"Orders fetch failed: {e}")
            return []

    def place_order(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0,
        trigger_price: float = 0,
        exchange: str = "NSE",
        product: str = "MIS",
        validity: str = "DAY",
        tag: str = "agent"
    ) -> Optional[str]:
        if not self._kite or not self._connected:
            sim_id = f"SIM_{int(time.time())}_{symbol}"
            log.info(f"[SIMULATION] Order placed: {transaction_type} {quantity} {symbol} @ {order_type}")
            return sim_id

        try:
            from kiteconnect import KiteConnect
            order_id = self._kite.place_order(
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=order_type,
                price=price if order_type == "LIMIT" else None,
                trigger_price=trigger_price if order_type in ("SL", "SL-M") else None,
                product=product,
                validity=validity,
                tag=tag,
                variety=KiteConnect.VARIETY_REGULAR
            )
            log.info(f"Order placed: {transaction_type} {quantity} {symbol} → order_id={order_id}")
            return str(order_id)
        except Exception as e:
            log.error(f"Order placement failed for {symbol}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        if not self._kite or not self._connected:
            log.info(f"[SIMULATION] Order cancelled: {order_id}")
            return True
        try:
            from kiteconnect import KiteConnect
            self._kite.cancel_order(variety=KiteConnect.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception as e:
            log.error(f"Cancel order failed {order_id}: {e}")
            return False

    def _simulate_historical_data(self, symbol: str, days: int) -> pd.DataFrame:
        import numpy as np
        np.random.seed(hash(symbol) % 1000)
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        base = 1000 + hash(symbol) % 3000
        prices = [base]
        for _ in range(days - 1):
            change = np.random.normal(0.0005, 0.015)
            prices.append(prices[-1] * (1 + change))
        prices = np.array(prices)
        df = pd.DataFrame({
            "Open": prices * (1 + np.random.uniform(-0.005, 0.005, days)),
            "High": prices * (1 + np.random.uniform(0.005, 0.02, days)),
            "Low": prices * (1 - np.random.uniform(0.005, 0.02, days)),
            "Close": prices,
            "Volume": np.random.randint(100000, 5000000, days),
        }, index=dates)
        return df
