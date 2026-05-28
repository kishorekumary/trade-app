"""
FnoAgent — the main F&O trading agent.

This class orchestrates:
1. Fetching Nifty spot + technicals
2. Fetching NSE option chain (CE/PE premiums, OI)
3. Checking risk limits
4. Asking GPT-4o for direction (BUY_CE / BUY_PE / SKIP)
5. Placing orders via Kite NFO exchange (or simulating if paper=True)
6. Monitoring open positions for SL/target exits
7. Square-off all positions at 3:15 PM
"""
import json
import time
from datetime import datetime
from typing import Optional
import pytz

from openai import OpenAI

from fno.config import fno_settings
from fno.models import FnoTrade, FnoTradeStatus, init_db
from fno.repository import FnoRepository
from fno.risk.manager import FnoRiskManager
from fno.data.option_chain import (
    fetch_option_chain,
    get_atm_strike,
    get_nearest_expiry,
    get_option_ltp,
    get_chain_summary,
    build_kite_symbol,
    estimate_atm_premium_fallback,
)
from fno.data.nifty import get_nifty_technicals, get_nifty_spot_price
from fno.agent.prompts import FNO_SYSTEM_PROMPT, FNO_ANALYSIS_TEMPLATE
from src.broker.kite_client import KiteClient
from src.utils import get_logger

log = get_logger("fno.agent")
IST = pytz.timezone("Asia/Kolkata")


class FnoAgent:
    """GPT-4o powered Nifty F&O trading agent."""

    def __init__(self):
        # Initialize DB (creates table if needed)
        init_db()

        # OpenAI client for GPT-4o analysis
        self.openai = OpenAI(api_key=fno_settings.OPENAI_API_KEY)

        # Kite broker (reuse from src.broker — NFO exchange for F&O)
        self.kite = KiteClient(
            api_key=fno_settings.KITE_API_KEY,
            api_secret=fno_settings.KITE_API_SECRET,
            access_token=fno_settings.KITE_ACCESS_TOKEN,
        )

        # Risk manager and repository
        self.risk = FnoRiskManager()
        self.repo = FnoRepository()

        # Paper trading flag
        self.paper_trading = fno_settings.PAPER_TRADING

        mode = "PAPER" if self.paper_trading else "LIVE"
        log.info(
            f"FnoAgent initialized | mode={mode} | "
            f"model={fno_settings.OPENAI_MODEL} | "
            f"max_lots={fno_settings.FNO_MAX_LOTS} | "
            f"max_premium=₹{fno_settings.FNO_MAX_PREMIUM:.0f}"
        )

    # ── Main scan ─────────────────────────────────────────────────────────────

    def run_scan(self) -> Optional[dict]:
        """
        Main scan cycle — runs every 30 minutes during market hours.

        Steps:
          1. Fetch Nifty technicals
          2. Fetch NSE option chain
          3. Get ATM strike and nearest expiry
          4. Extract CE/PE premiums and OI
          5. Check risk rules (daily loss, positions, premium cap, time)
          6. Ask GPT-4o for direction
          7. If confident enough, place order and save to DB
          8. Return result dict (or None if skipped)
        """
        log.info("=" * 60)
        log.info(f"F&O Scan — {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
        log.info("=" * 60)

        # Step 1: Nifty technicals
        technicals = get_nifty_technicals()
        if not technicals:
            log.warning("Could not fetch Nifty technicals — skipping scan")
            return None

        spot = technicals["close"]
        log.info(
            f"Nifty spot: ₹{spot:.2f} ({technicals['day_change_pct']:+.2f}%) | "
            f"Bias: {technicals['overall_bias']}"
        )

        # Step 2: Option chain
        chain_data = fetch_option_chain("NIFTY")

        # Step 3: ATM strike and expiry
        atm_strike = get_atm_strike(spot, step=50)
        log.info(f"ATM strike: {atm_strike}")

        if chain_data:
            expiry = get_nearest_expiry(chain_data)
            chain_summary = get_chain_summary(chain_data, atm_strike, expiry) if expiry else None
        else:
            log.warning("NSE option chain unavailable — using fallback premium estimates")
            expiry = None
            chain_summary = None

        # Determine premiums (from chain or fallback)
        if chain_summary and chain_summary["ce_ltp"] > 0:
            ce_ltp = chain_summary["ce_ltp"]
            pe_ltp = chain_summary["pe_ltp"]
            ce_oi = chain_summary["ce_oi"]
            pe_oi = chain_summary["pe_oi"]
            ce_iv = chain_summary["ce_iv"]
            pe_iv = chain_summary["pe_iv"]
            oi_sentiment = chain_summary["oi_sentiment"]
            log.info(f"ATM CE: ₹{ce_ltp:.2f} | ATM PE: ₹{pe_ltp:.2f} | OI: {oi_sentiment}")
        else:
            # Fallback: estimate premiums from spot price
            ce_ltp = estimate_atm_premium_fallback(spot, "CE")
            pe_ltp = estimate_atm_premium_fallback(spot, "PE")
            ce_oi = 0.0
            pe_oi = 0.0
            ce_iv = 0.0
            pe_iv = 0.0
            oi_sentiment = "UNKNOWN (NSE API unavailable)"
            expiry = expiry or "NEAREST_WEEKLY"

        # Step 4: Risk checks
        today_pnl = self.repo.get_today_pnl()
        open_count = self.repo.get_open_count()

        # We'll check based on CE premium (conservative — check the more expensive one)
        check_premium = max(ce_ltp, pe_ltp)
        can_trade, reason = self.risk.can_trade(
            today_pnl=today_pnl,
            open_positions=open_count,
            premium_per_share=check_premium,
            lots=fno_settings.FNO_MAX_LOTS,
        )
        if not can_trade:
            log.info(f"Trade not allowed: {reason}")
            return {"action": "BLOCKED", "reason": reason}

        # Step 5: Ask GPT-4o for direction
        lot_size = fno_settings.NIFTY_LOT_SIZE
        rsi = technicals["rsi_14"]

        # Interpret RSI for the prompt
        if rsi >= 70:
            rsi_signal = "Overbought — risk of reversal"
        elif rsi >= 55:
            rsi_signal = "Bullish momentum"
        elif rsi >= 45:
            rsi_signal = "Neutral"
        elif rsi >= 30:
            rsi_signal = "Bearish momentum"
        else:
            rsi_signal = "Oversold — risk of bounce"

        # OI interpretation for the prompt
        higher_oi_side = "CE" if ce_oi >= pe_oi else "PE"
        oi_interpretation = (
            "more bulls active" if higher_oi_side == "CE"
            else "more bears active"
        )

        prompt = FNO_ANALYSIS_TEMPLATE.format(
            datetime=datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
            spot=spot,
            day_change_pct=technicals["day_change_pct"],
            day_direction="UP" if technicals["day_change_pct"] >= 0 else "DOWN",
            prev_close=technicals["prev_close"],
            rsi_14=rsi,
            rsi_signal=rsi_signal,
            ema20=technicals["ema20"],
            ema20_position="ABOVE" if technicals["above_ema20"] else "BELOW",
            ema50=technicals["ema50"],
            ema50_position="ABOVE" if technicals["above_ema50"] else "BELOW",
            macd_direction=technicals["macd_direction"],
            macd_histogram=technicals["macd_histogram"],
            adx=technicals["adx"],
            trend_strength=technicals["trend_strength"],
            overall_bias=technicals["overall_bias"],
            atm_strike=atm_strike,
            expiry=expiry,
            ce_ltp=ce_ltp,
            ce_lot_cost=ce_ltp * fno_settings.FNO_MAX_LOTS * lot_size,
            pe_ltp=pe_ltp,
            pe_lot_cost=pe_ltp * fno_settings.FNO_MAX_LOTS * lot_size,
            ce_oi=ce_oi,
            pe_oi=pe_oi,
            oi_sentiment=oi_sentiment,
            higher_oi_side=higher_oi_side,
            oi_interpretation=oi_interpretation,
            ce_iv=ce_iv,
            pe_iv=pe_iv,
            max_premium=fno_settings.FNO_MAX_PREMIUM,
            sl_pct=fno_settings.FNO_SL_PCT,
            target_pct=fno_settings.FNO_TARGET_PCT,
            today_pnl=today_pnl,
            open_positions=open_count,
            max_positions=fno_settings.MAX_OPEN_POSITIONS,
        )

        decision = self._ask_gpt(prompt)
        if not decision:
            log.error("GPT-4o did not return a valid decision")
            return None

        action = decision.get("action", "SKIP").upper()
        confidence = float(decision.get("confidence", 0.0))
        reasoning = decision.get("reasoning", "")
        preferred_strike = int(decision.get("preferred_strike", atm_strike))

        log.info(
            f"GPT-4o decision: {action} | confidence={confidence:.2f} | "
            f"strike={preferred_strike}"
        )
        log.info(f"Reasoning: {reasoning}")

        # Step 6: Execute trade if action is BUY_CE or BUY_PE with sufficient confidence
        if action in ("BUY_CE", "BUY_PE") and confidence >= 0.70:
            option_type = "CE" if action == "BUY_CE" else "PE"
            entry_premium = ce_ltp if option_type == "CE" else pe_ltp

            if entry_premium <= 0:
                log.warning(f"Entry premium is 0 for {option_type} — cannot place order")
                return {"action": action, "decision": decision, "trade": None}

            # Final check: premium cap for this specific option
            total_cost = entry_premium * fno_settings.FNO_MAX_LOTS * lot_size
            if total_cost > fno_settings.FNO_MAX_PREMIUM:
                log.warning(
                    f"Premium too high: ₹{total_cost:.0f} > max ₹{fno_settings.FNO_MAX_PREMIUM:.0f}"
                )
                return {"action": "BLOCKED", "reason": f"Premium too high: ₹{total_cost:.0f}"}

            # Build the Kite NFO trading symbol
            if expiry and expiry != "NEAREST_WEEKLY":
                kite_symbol = build_kite_symbol("NIFTY", expiry, preferred_strike, option_type)
            else:
                # Fallback symbol name (won't place real order but logs cleanly)
                kite_symbol = f"NIFTY_ATM_{preferred_strike}{option_type}"

            log.info(
                f"Placing order: {kite_symbol} | "
                f"lots={fno_settings.FNO_MAX_LOTS} | "
                f"premium=₹{entry_premium:.2f} | "
                f"total_cost=₹{total_cost:.0f}"
            )

            # Place order (paper or live)
            order_id = self._place_order(
                kite_symbol=kite_symbol,
                entry_premium=entry_premium,
                lots=fno_settings.FNO_MAX_LOTS,
            )

            if order_id is None:
                log.error("Order placement failed")
                return {"action": action, "decision": decision, "trade": None}

            # Save to database
            trade = FnoTrade(
                symbol=kite_symbol,
                underlying="NIFTY",
                option_type=option_type,
                strike=preferred_strike,
                expiry=expiry or "UNKNOWN",
                lots=fno_settings.FNO_MAX_LOTS,
                lot_size=lot_size,
                entry_premium=entry_premium,
                total_cost=total_cost,
                status=FnoTradeStatus.OPEN,
                entry_time=datetime.utcnow(),
                kite_order_id=order_id,
                paper_trade=self.paper_trading,
                agent_reasoning=reasoning,
                nifty_spot_at_entry=spot,
                iv_at_entry=ce_iv if option_type == "CE" else pe_iv,
            )
            saved_trade = self.repo.save_trade(trade)

            log.info(
                f"TRADE ENTERED: {kite_symbol} | "
                f"lots={fno_settings.FNO_MAX_LOTS} | "
                f"premium=₹{entry_premium:.2f} | "
                f"order_id={order_id}"
            )

            return {
                "action": action,
                "decision": decision,
                "trade": saved_trade,
                "kite_symbol": kite_symbol,
                "entry_premium": entry_premium,
                "total_cost": total_cost,
                "order_id": order_id,
            }

        else:
            # SKIP or low confidence
            if action in ("BUY_CE", "BUY_PE") and confidence < 0.70:
                log.info(
                    f"Skipping trade: confidence {confidence:.2f} < 0.70 threshold. "
                    f"Action was {action}."
                )
            else:
                log.info(f"GPT-4o says SKIP: {reasoning}")

            return {"action": "SKIP", "decision": decision, "reasoning": reasoning}

    # ── Position monitoring ───────────────────────────────────────────────────

    def monitor_positions(self) -> list[dict]:
        """
        Check all open positions and exit those that hit SL or target.
        Called every 5 minutes during market hours.

        Returns a list of dicts for positions that were exited.
        """
        open_trades = self.repo.get_open_trades()
        if not open_trades:
            return []

        log.info(f"Monitoring {len(open_trades)} open F&O positions...")

        # Fetch fresh option chain data once for all positions
        chain_data = fetch_option_chain("NIFTY")
        exited = []

        for trade in open_trades:
            try:
                # Get current LTP for this option
                current_ltp = self._get_current_ltp(trade, chain_data)

                if current_ltp <= 0:
                    log.warning(f"Could not get LTP for {trade.symbol} — skipping check")
                    continue

                pnl_pct = ((current_ltp / trade.entry_premium) - 1) * 100 if trade.entry_premium > 0 else 0
                log.info(
                    f"  {trade.symbol}: "
                    f"entry=₹{trade.entry_premium:.2f} | "
                    f"current=₹{current_ltp:.2f} | "
                    f"PnL={pnl_pct:+.1f}%"
                )

                # Check exit conditions
                should_exit, exit_reason = self.risk.should_exit(current_ltp, trade.entry_premium)

                if should_exit:
                    result = self._exit_trade(trade, current_ltp, exit_reason)
                    if result:
                        exited.append(result)

            except Exception as e:
                log.error(f"Error monitoring trade {trade.id} ({trade.symbol}): {e}")

        return exited

    def squareoff_all(self) -> list[dict]:
        """
        Force-close ALL open F&O positions at current market price.
        Called at 3:15 PM IST to avoid Zerodha's auto square-off at 3:20 PM.
        """
        open_trades = self.repo.get_open_trades()
        if not open_trades:
            log.info("Square-off: no open positions")
            return []

        log.info(f"Square-off: closing {len(open_trades)} open F&O positions...")
        chain_data = fetch_option_chain("NIFTY")
        closed = []

        for trade in open_trades:
            try:
                current_ltp = self._get_current_ltp(trade, chain_data)
                # If we can't get LTP, use entry price (worst case, no gain/loss from entry)
                if current_ltp <= 0:
                    current_ltp = trade.entry_premium
                    log.warning(
                        f"Could not get LTP for {trade.symbol} during square-off — "
                        f"using entry premium ₹{current_ltp:.2f}"
                    )

                result = self._exit_trade(trade, current_ltp, "SQUAREOFF")
                if result:
                    closed.append(result)

            except Exception as e:
                log.error(f"Square-off failed for {trade.symbol}: {e}")

        log.info(f"Square-off complete: {len(closed)}/{len(open_trades)} positions closed")
        return closed

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Return current portfolio status for display/logging.
        """
        open_trades = self.repo.get_open_trades()
        today_pnl = self.repo.get_today_pnl()

        positions = []
        for trade in open_trades:
            chain_data = fetch_option_chain("NIFTY")
            current_ltp = self._get_current_ltp(trade, chain_data)
            unrealized_pnl = 0.0
            pnl_pct = 0.0
            if current_ltp > 0 and trade.entry_premium > 0:
                unrealized_pnl = (current_ltp - trade.entry_premium) * trade.lots * trade.lot_size
                pnl_pct = ((current_ltp / trade.entry_premium) - 1) * 100

            sl_premium, target_premium = self.risk.calculate_exit_levels(trade.entry_premium)

            positions.append({
                "id": trade.id,
                "symbol": trade.symbol,
                "option_type": trade.option_type,
                "strike": trade.strike,
                "lots": trade.lots,
                "entry_premium": trade.entry_premium,
                "current_ltp": current_ltp,
                "sl_premium": sl_premium,
                "target_premium": target_premium,
                "unrealized_pnl": unrealized_pnl,
                "pnl_pct": pnl_pct,
                "entry_time": trade.entry_time.strftime("%H:%M") if trade.entry_time else "",
                "paper_trade": trade.paper_trade,
            })

        return {
            "open_positions": len(open_trades),
            "today_realized_pnl": today_pnl,
            "positions": positions,
            "paper_trading": self.paper_trading,
            "mode": "PAPER" if self.paper_trading else "LIVE",
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ask_gpt(self, prompt: str) -> Optional[dict]:
        """Call GPT-4o with the analysis prompt and return the parsed JSON decision."""
        try:
            log.info(f"Sending analysis to {fno_settings.OPENAI_MODEL}...")
            response = self.openai.chat.completions.create(
                model=fno_settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": FNO_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=512,
            )
            raw = response.choices[0].message.content.strip()
            decision = json.loads(raw)
            log.debug(f"GPT-4o raw response: {raw}")
            return decision

        except json.JSONDecodeError as e:
            log.error(f"Failed to parse GPT-4o JSON response: {e}")
        except Exception as e:
            log.error(f"GPT-4o API call failed: {e}")
        return None

    def _place_order(self, kite_symbol: str, entry_premium: float, lots: int) -> Optional[str]:
        """
        Place a BUY order for the option.
        - NFO exchange (not NSE — options trade on NFO segment)
        - Product = MIS (intraday, auto-squared off at end of day)
        - Order type = LIMIT at entry_premium (avoid slippage)
        - Quantity = lots × lot_size

        Returns order_id string, or None on failure.
        """
        quantity = lots * fno_settings.NIFTY_LOT_SIZE

        if self.paper_trading:
            order_id = f"PAPER_{int(time.time())}_{kite_symbol[:10]}"
            log.info(
                f"[PAPER] BUY {quantity} {kite_symbol} @ ₹{entry_premium:.2f} "
                f"(NFO, MIS) → {order_id}"
            )
            return order_id

        # Live order via Kite
        order_id = self.kite.place_order(
            symbol=kite_symbol,
            transaction_type="BUY",
            quantity=quantity,
            order_type="LIMIT",
            price=entry_premium,
            exchange="NFO",
            product="MIS",
            validity="DAY",
            tag="fno_agent",
        )
        return order_id

    def _place_sell_order(self, kite_symbol: str, exit_premium: float, lots: int) -> Optional[str]:
        """
        Place a SELL order to exit an option position.
        Uses MARKET order for faster execution on exit.
        """
        quantity = lots * fno_settings.NIFTY_LOT_SIZE

        if self.paper_trading:
            order_id = f"PAPER_EXIT_{int(time.time())}_{kite_symbol[:10]}"
            log.info(
                f"[PAPER] SELL {quantity} {kite_symbol} @ ₹{exit_premium:.2f} "
                f"(NFO, MIS) → {order_id}"
            )
            return order_id

        # Live order via Kite — use MARKET for quick exit
        order_id = self.kite.place_order(
            symbol=kite_symbol,
            transaction_type="SELL",
            quantity=quantity,
            order_type="MARKET",
            exchange="NFO",
            product="MIS",
            validity="DAY",
            tag="fno_exit",
        )
        return order_id

    def _exit_trade(self, trade: FnoTrade, current_ltp: float, exit_reason: str) -> Optional[dict]:
        """
        Exit a trade: place sell order + update DB.
        Returns exit result dict or None if order failed.
        """
        log.info(
            f"Exiting trade {trade.id} ({trade.symbol}): "
            f"exit_price=₹{current_ltp:.2f} | reason={exit_reason}"
        )

        # Place sell order
        exit_order_id = self._place_sell_order(trade.symbol, current_ltp, trade.lots)

        if exit_order_id is None and not self.paper_trading:
            log.error(f"Sell order failed for {trade.symbol}")
            return None

        # Update DB
        closed_trade = self.repo.close_trade(
            trade_id=trade.id,
            exit_premium=current_ltp,
            exit_reason=exit_reason,
            kite_exit_order_id=exit_order_id,
        )

        if closed_trade:
            pnl_sign = "+" if (closed_trade.pnl or 0) >= 0 else ""
            log.info(
                f"Trade closed: {trade.symbol} | "
                f"PnL={pnl_sign}₹{closed_trade.pnl:.2f} "
                f"({pnl_sign}{closed_trade.pnl_pct:.1f}%) | "
                f"Reason: {exit_reason}"
            )
            return {
                "trade": closed_trade,
                "exit_premium": current_ltp,
                "exit_reason": exit_reason,
                "pnl": closed_trade.pnl,
                "pnl_pct": closed_trade.pnl_pct,
            }
        return None

    def _get_current_ltp(self, trade: FnoTrade, chain_data: Optional[dict]) -> float:
        """
        Get the current LTP for an open option position.
        Priority:
          1. From NSE option chain data (most accurate)
          2. From Kite quote API
          3. Returns 0.0 if both fail
        """
        # Try NSE chain first
        if chain_data:
            ltp = get_option_ltp(chain_data, trade.strike, trade.option_type, trade.expiry)
            if ltp > 0:
                return ltp

        # Try Kite quote (works for NFO instruments when connected)
        if self.kite.is_connected:
            try:
                ltp = self.kite.get_ltp(trade.symbol, exchange="NFO")
                if ltp > 0:
                    return ltp
            except Exception as e:
                log.debug(f"Kite LTP failed for {trade.symbol}: {e}")

        log.warning(f"Could not get current LTP for {trade.symbol}")
        return 0.0
