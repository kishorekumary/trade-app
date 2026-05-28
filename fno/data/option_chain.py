"""
NSE Option Chain Fetcher.

NSE website blocks automated bots, so we mimic a browser session by:
1. First visiting the NSE homepage to get session cookies
2. Then hitting the option chain API endpoint with those cookies + browser headers

If NSE is down or blocks us:
- We fall back to estimating ATM premium from yfinance Nifty spot (0.5% of spot)
- This fallback is rough but prevents the agent from crashing

How to read an option chain (for beginners):
- The NSE option chain is a table of all available strikes for an index/stock
- Each row has a strike price, and two sides: CE (Call) and PE (Put)
- LTP = Last Traded Price = current market price of that option
- OI = Open Interest = how many contracts are open (market sentiment indicator)
- High PE OI vs CE OI → more people are buying Puts → bearish sentiment
"""
import re
import time
import requests
from datetime import datetime
from typing import Optional
from src.utils import get_logger

log = get_logger("fno.option_chain")

# Browser-like headers to avoid NSE's bot detection
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

NSE_HOME = "https://www.nseindia.com"
NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices"

# Cache: avoid hammering NSE — reuse data for up to 2 minutes
_cache: dict = {}
_cache_time: float = 0
_CACHE_TTL = 120  # seconds


def _get_nse_session() -> requests.Session:
    """
    Create a requests Session that mimics a browser visiting NSE.
    NSE requires session cookies from the homepage before the API will respond.
    """
    session = requests.Session()
    try:
        # First hit: homepage to get cookies (NSE uses these for bot detection)
        resp = session.get(NSE_HOME, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        log.debug(f"NSE homepage OK — cookies acquired: {list(session.cookies.keys())}")
        # Small delay to appear human-like
        time.sleep(0.5)
    except Exception as e:
        log.warning(f"NSE homepage visit failed: {e} — will try API without cookies")
    return session


def fetch_option_chain(symbol: str = "NIFTY") -> Optional[dict]:
    """
    Fetch the full NSE option chain for the given index symbol.

    Returns the raw JSON response from NSE, which contains:
      - data["records"]["expiryDates"] → list of available expiry dates
      - data["records"]["data"] → list of option contracts (each has strike, CE, PE data)
      - data["records"]["underlyingValue"] → current spot price

    Returns None if the fetch fails (caller should handle gracefully).
    """
    global _cache, _cache_time

    # Return cached data if fresh enough
    if _cache and (time.time() - _cache_time) < _CACHE_TTL:
        log.debug("Returning cached option chain data")
        return _cache

    log.info(f"Fetching NSE option chain for {symbol}...")
    try:
        session = _get_nse_session()
        resp = session.get(
            NSE_OPTION_CHAIN_URL,
            params={"symbol": symbol},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "records" not in data:
            log.error(f"NSE response missing 'records' key: {list(data.keys())}")
            return None

        _cache = data
        _cache_time = time.time()
        log.info(
            f"Option chain fetched: {len(data['records'].get('data', []))} contracts | "
            f"spot=₹{data['records'].get('underlyingValue', 'N/A')}"
        )
        return data

    except requests.exceptions.Timeout:
        log.error("NSE option chain request timed out")
    except requests.exceptions.ConnectionError:
        log.error("Cannot reach NSE — no internet or NSE is down")
    except requests.exceptions.HTTPError as e:
        log.error(f"NSE returned HTTP error: {e.response.status_code}")
    except Exception as e:
        log.error(f"Option chain fetch failed: {e}")

    return None


def get_atm_strike(spot: float, step: int = 50) -> int:
    """
    Find the ATM (At The Money) strike — the strike closest to current Nifty spot.

    Nifty strikes are in multiples of 50 (e.g. 24500, 24550, 24600).
    'At the money' = the strike closest to where Nifty is trading right now.

    Example:
      spot = 24,527 → ATM = round(24527 / 50) * 50 = round(490.54) * 50 = 491 * 50 = 24550
    """
    return int(round(spot / step) * step)


def get_nearest_expiry(chain_data: dict) -> Optional[str]:
    """
    Extract the nearest (first) expiry date from option chain data.
    NSE returns expiry dates in format like "29-May-2025".
    For weekly strategy, we want the first one (nearest weekly expiry).
    """
    try:
        expiry_dates = chain_data["records"]["expiryDates"]
        if not expiry_dates:
            log.error("No expiry dates found in chain data")
            return None
        nearest = expiry_dates[0]
        log.info(f"Nearest expiry: {nearest} (from {len(expiry_dates)} available)")
        return nearest
    except (KeyError, IndexError, TypeError) as e:
        log.error(f"Failed to extract expiry dates: {e}")
        return None


def get_option_ltp(
    chain_data: dict,
    strike: int,
    option_type: str,
    expiry: str,
) -> float:
    """
    Extract the LTP (Last Traded Price) for a specific option contract.

    Parameters:
      chain_data  : the raw dict from fetch_option_chain()
      strike      : the strike price (e.g. 24500)
      option_type : "CE" (call/bullish) or "PE" (put/bearish)
      expiry      : expiry date string matching NSE format (e.g. "29-May-2025")

    Returns 0.0 if not found.
    """
    try:
        records = chain_data["records"]["data"]
        for record in records:
            if record.get("strikePrice") != strike:
                continue
            if record.get("expiryDate") != expiry:
                continue
            option_data = record.get(option_type, {})
            ltp = option_data.get("lastPrice", 0.0)
            if ltp and ltp > 0:
                return float(ltp)
    except (KeyError, TypeError) as e:
        log.debug(f"LTP extraction failed for {strike}{option_type}: {e}")
    return 0.0


def get_option_oi(
    chain_data: dict,
    strike: int,
    option_type: str,
    expiry: str,
) -> float:
    """
    Extract the Open Interest for a specific option contract.
    OI = number of outstanding contracts = market sentiment indicator.
    High CE OI → lots of call buyers → bullish expectations
    High PE OI → lots of put buyers → bearish expectations
    """
    try:
        records = chain_data["records"]["data"]
        for record in records:
            if record.get("strikePrice") != strike:
                continue
            if record.get("expiryDate") != expiry:
                continue
            option_data = record.get(option_type, {})
            return float(option_data.get("openInterest", 0.0))
    except (KeyError, TypeError):
        pass
    return 0.0


def get_option_iv(
    chain_data: dict,
    strike: int,
    option_type: str,
    expiry: str,
) -> float:
    """
    Extract the Implied Volatility (IV) for a specific option.
    IV = the market's expectation of future volatility.
    High IV → expensive options (premium is high).
    Low IV → cheap options, good time to buy.
    """
    try:
        records = chain_data["records"]["data"]
        for record in records:
            if record.get("strikePrice") != strike:
                continue
            if record.get("expiryDate") != expiry:
                continue
            option_data = record.get(option_type, {})
            return float(option_data.get("impliedVolatility", 0.0))
    except (KeyError, TypeError):
        pass
    return 0.0


def build_kite_symbol(
    underlying: str,
    expiry_str: str,
    strike: int,
    option_type: str,
) -> str:
    """
    Convert NSE expiry format to Zerodha Kite NFO trading symbol.

    NSE expiry format: "29-May-2025"
    Kite NFO format:   "NIFTY29MAY2524500CE"

    Breakdown:
      NIFTY  = underlying index
      29     = day (DD)
      MAY    = month (3-letter uppercase)
      25     = year (2-digit YY)
      24500  = strike price (integer, no decimals)
      CE     = option type

    More examples:
      strike=24550, expiry="05-Jun-2025", type="PE" → "NIFTY05JUN2524550PE"
      strike=24500, expiry="29-May-2025", type="CE" → "NIFTY29MAY2524500CE"
    """
    try:
        # NSE uses "29-May-2025" format
        dt = datetime.strptime(expiry_str, "%d-%b-%Y")
        day = dt.strftime("%d")      # "29"
        mon = dt.strftime("%b").upper()  # "MAY"
        yr = dt.strftime("%y")       # "25"
        symbol = f"{underlying}{day}{mon}{yr}{int(strike)}{option_type}"
        log.debug(f"Kite symbol built: '{expiry_str}' → '{symbol}'")
        return symbol
    except ValueError as e:
        log.error(f"Could not parse expiry '{expiry_str}': {e}")
        # Fallback: try alternate formats
        for fmt in ("%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(expiry_str, fmt)
                day = dt.strftime("%d")
                mon = dt.strftime("%b").upper()
                yr = dt.strftime("%y")
                return f"{underlying}{day}{mon}{yr}{int(strike)}{option_type}"
            except ValueError:
                continue
        # Last resort: return a placeholder (trade won't execute but agent won't crash)
        log.error(f"All expiry format attempts failed for '{expiry_str}'")
        return f"{underlying}UNKNOWN{int(strike)}{option_type}"


def get_chain_summary(chain_data: dict, strike: int, expiry: str) -> dict:
    """
    Return a compact summary of ATM option data:
    CE LTP, PE LTP, CE OI, PE OI, CE IV, PE IV, spot price.
    Used to feed the GPT-4o prompt.
    """
    spot = float(chain_data.get("records", {}).get("underlyingValue", 0.0))
    ce_ltp = get_option_ltp(chain_data, strike, "CE", expiry)
    pe_ltp = get_option_ltp(chain_data, strike, "PE", expiry)
    ce_oi = get_option_oi(chain_data, strike, "CE", expiry)
    pe_oi = get_option_oi(chain_data, strike, "PE", expiry)
    ce_iv = get_option_iv(chain_data, strike, "CE", expiry)
    pe_iv = get_option_iv(chain_data, strike, "PE", expiry)

    # OI ratio interpretation:
    # PE OI > CE OI → more put buying → bearish sentiment
    # CE OI > PE OI → more call buying → bullish sentiment
    oi_diff = ce_oi - pe_oi
    oi_sentiment = "BULLISH" if oi_diff > 0 else "BEARISH" if oi_diff < 0 else "NEUTRAL"

    return {
        "spot": spot,
        "atm_strike": strike,
        "expiry": expiry,
        "ce_ltp": ce_ltp,
        "pe_ltp": pe_ltp,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_iv": ce_iv,
        "pe_iv": pe_iv,
        "oi_diff": oi_diff,
        "oi_sentiment": oi_sentiment,
    }


def estimate_atm_premium_fallback(spot: float, option_type: str) -> float:
    """
    Rough fallback premium estimate when NSE chain is unavailable.
    Uses 0.5% of spot as a proxy for ATM option price.
    This is a very crude estimate — actual premium depends on IV, DTE, etc.
    Only used when NSE API fails.
    """
    estimated = round(spot * 0.005, 2)
    log.warning(
        f"[FALLBACK] Estimating {option_type} premium as 0.5% of spot "
        f"(spot=₹{spot:.2f} → est. premium=₹{estimated:.2f})"
    )
    return estimated
