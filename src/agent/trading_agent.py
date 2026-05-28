import json
from datetime import datetime
from typing import Optional
from openai import OpenAI
from src.utils import get_logger
from src.analysis import TechnicalAnalyzer
from src.strategy import TradeSignal, get_strategy
from src.risk import RiskManager
from src.broker import KiteClient, OrderManager
from src.database import AgentDecision, init_db
from src.database.repository import TradeRepository
from .prompts import SYSTEM_PROMPT, MARKET_CONTEXT_TEMPLATE, EXIT_ANALYSIS_TEMPLATE, MARKET_REGIME_PROMPT
from config.settings import settings

log = get_logger("trading_agent")


class TradingAgent:
    """Claude-powered trading agent that scans the market and executes trades."""

    def __init__(self):
        init_db()
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.analyzer = TechnicalAnalyzer()
        self.strategy = get_strategy(settings.STRATEGY)
        self.risk = RiskManager()
        self.repo = TradeRepository()
        self.kite = KiteClient(
            api_key=settings.KITE_API_KEY,
            api_secret=settings.KITE_API_SECRET,
            access_token=settings.KITE_ACCESS_TOKEN,
        )
        self.orders = OrderManager(self.kite, self.repo, settings.PAPER_TRADING)
        self.universe = settings.get_universe()
        self._halted = False   # can be toggled via Telegram /halt command
        log.info(
            f"Agent initialized | strategy={settings.STRATEGY} | "
            f"paper={settings.PAPER_TRADING} | universe={len(self.universe)} stocks"
        )

    def analyze_market_regime(self) -> dict:
        """Fetch Nifty + VIX data and ask GPT-4o to recommend intraday SL%/Target%."""
        try:
            import yfinance as yf
            import numpy as np
            import pandas as pd

            nifty = yf.download("^NSEI", period="10d", interval="1d", progress=False, auto_adjust=True)
            vix   = yf.download("^INDIAVIX", period="10d", interval="1d", progress=False, auto_adjust=True)

            if isinstance(nifty.columns, pd.MultiIndex):
                nifty.columns = nifty.columns.get_level_values(0)
            if isinstance(vix.columns, pd.MultiIndex):
                vix.columns = vix.columns.get_level_values(0)

            if nifty.empty:
                log.warning("Nifty data unavailable — keeping current SL/Target settings")
                return {}

            nifty_close   = float(nifty["Close"].iloc[-1])
            nifty_prev    = float(nifty["Close"].iloc[-2])
            nifty_change  = ((nifty_close - nifty_prev) / nifty_prev) * 100
            nifty_5d      = nifty.tail(5)
            nifty_5d_high = float(nifty_5d["High"].max())
            nifty_5d_low  = float(nifty_5d["Low"].min())
            ema20         = float(nifty["Close"].ewm(span=20).mean().iloc[-1])
            atr_pct       = float((nifty_5d["High"] - nifty_5d["Low"]).mean() / nifty_close * 100)

            vix_current = float(vix["Close"].iloc[-1]) if not vix.empty else 15.0
            vix_avg     = float(vix["Close"].tail(5).mean()) if not vix.empty else 15.0
            if vix_current > 20:
                vix_signal = "High fear — expect wide swings"
            elif vix_current < 13:
                vix_signal = "Low volatility — tight orderly moves"
            else:
                vix_signal = "Normal volatility"

            prompt = MARKET_REGIME_PROMPT.format(
                date=datetime.now().strftime("%Y-%m-%d"),
                nifty_close=nifty_close,
                nifty_change=nifty_change,
                nifty_5d_high=nifty_5d_high,
                nifty_5d_low=nifty_5d_low,
                nifty_above_ema20=nifty_close > ema20,
                nifty_atr_pct=atr_pct,
                vix_current=vix_current,
                vix_avg=vix_avg,
                vix_signal=vix_signal,
            )

            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            result = json.loads(response.choices[0].message.content)

            sl_pct     = float(result.get("sl_pct", settings.INTRADAY_SL_PCT))
            target_pct = float(result.get("target_pct", settings.INTRADAY_TARGET_PCT))
            regime     = result.get("market_regime", "unknown")
            reasoning  = result.get("reasoning", "")

            # Apply bounds — never let GPT go too wide or too tight
            sl_pct     = max(0.2, min(sl_pct, 1.0))
            target_pct = max(0.4, min(target_pct, 2.0))

            settings.INTRADAY_SL_PCT     = sl_pct
            settings.INTRADAY_TARGET_PCT = target_pct

            log.info(
                f"Market regime: {regime.upper()} | "
                f"SL={sl_pct:.1f}% | Target={target_pct:.1f}% | "
                f"Nifty={nifty_change:+.2f}% | VIX={vix_current:.1f} | {reasoning}"
            )
            return result

        except Exception as e:
            log.error(f"Market regime analysis failed: {e}")
            return {}

    def recalculate_open_trade_levels(self):
        """Update SL/Target for all open trades to match current intraday settings."""
        if not settings.INTRADAY_MODE:
            return
        open_trades = self.repo.get_open_trades()
        if not open_trades:
            return
        for trade in open_trades:
            new_sl     = round(trade.entry_price * (1 - settings.INTRADAY_SL_PCT / 100), 2)
            new_target = round(trade.entry_price * (1 + settings.INTRADAY_TARGET_PCT / 100), 2)
            if new_sl != trade.stop_loss or new_target != trade.target:
                self.repo.update_trade_levels(trade.id, new_sl, new_target)
                log.info(
                    f"Recalculated {trade.symbol}: "
                    f"SL {trade.stop_loss:.2f}→{new_sl:.2f} | "
                    f"T {trade.target:.2f}→{new_target:.2f}"
                )

    def run_daily_scan(self) -> list[dict]:
        """Full market scan: analyze universe, ask Claude, execute best trades."""
        if self._halted:
            log.warning("Scan skipped — agent is halted. Send /resume via Telegram to restart.")
            return []

        log.info("=" * 60)
        log.info(f"Daily scan started at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        log.info("=" * 60)

        # Check existing positions first
        self._manage_open_positions()

        available_cash = self.kite.get_available_cash()
        today_pnl = self.repo.get_today_pnl()
        open_count = self.repo.get_open_position_count()

        can_trade, reason = self.risk.can_trade(today_pnl, open_count, available_cash)
        if not can_trade:
            log.warning(f"Trading halted: {reason}")
            return []

        # Scan universe
        signals_found = []
        log.info(f"Scanning {len(self.universe)} symbols...")

        for symbol in self.universe:
            try:
                signal = self._analyze_symbol(symbol)
                if signal and signal.action == "BUY" and signal.confidence >= 0.55:
                    signals_found.append(signal)
                    log.info(f"Signal: {symbol} | confidence={signal.confidence:.2f} | {signal.reason[:60]}")
            except Exception as e:
                log.debug(f"Error analyzing {symbol}: {e}")

        # Sort by confidence, take top candidates
        signals_found.sort(key=lambda s: s.confidence, reverse=True)
        top_signals = signals_found[:10]

        log.info(f"Found {len(signals_found)} signals, sending top {len(top_signals)} to Claude")

        # Ask Claude to pick the best trades
        executed = []
        for signal in top_signals:
            if open_count >= settings.MAX_OPEN_POSITIONS:
                break
            available_cash = self.kite.get_available_cash()
            today_pnl = self.repo.get_today_pnl()

            can_trade, reason = self.risk.can_trade(today_pnl, open_count, available_cash)
            if not can_trade:
                log.warning(f"Stopping: {reason}")
                break

            result = self._evaluate_with_claude(signal, available_cash, today_pnl, open_count)
            if result:
                executed.append(result)
                open_count += 1

        log.info(f"Scan complete | {len(executed)} trades executed")
        return executed

    def _analyze_symbol(self, symbol: str) -> Optional[TradeSignal]:
        df = self.kite.get_historical_data(symbol, days=200)
        if df.empty or len(df) < 50:
            return None
        df = self.analyzer.compute_all(df)
        signals = self.analyzer.get_signal_summary(df)
        return self.strategy.analyze(symbol, df, signals)

    def _evaluate_with_claude(
        self,
        signal: TradeSignal,
        available_cash: float,
        today_pnl: float,
        open_positions: int,
    ) -> Optional[dict]:
        df = self.kite.get_historical_data(signal.symbol, days=200)
        if df.empty:
            return None

        df = self.analyzer.compute_all(df)
        tech = self.analyzer.get_signal_summary(df)

        row = df.iloc[-1]
        ema20 = float(row.get("EMA20", 0))
        ema50 = float(row.get("EMA50", 0))
        ema200 = float(row.get("EMA200", 0))
        volume_ratio = float(row.get("Volume_Ratio", 1))

        risk = signal.entry_price - signal.stop_loss
        reward = signal.target - signal.entry_price
        rr = reward / risk if risk > 0 else 0

        prompt = MARKET_CONTEXT_TEMPLATE.format(
            symbol=signal.symbol,
            price=signal.entry_price,
            rsi=tech.get("rsi", 50),
            rsi_signal=tech.get("rsi_signal", "neutral"),
            macd_signal="Bullish" if tech.get("macd_bullish") else "Bearish" if tech.get("macd_bearish") else "Neutral",
            ema20=ema20,
            ema20_pos="ABOVE" if tech.get("above_ema20") else "BELOW",
            ema50=ema50,
            ema50_pos="ABOVE" if tech.get("above_ema50") else "BELOW",
            ema200=ema200,
            ema200_pos="ABOVE" if tech.get("above_ema200") else "BELOW",
            adx=tech.get("adx", 0),
            adx_trend="Strong trend" if tech.get("adx", 0) > 25 else "Weak trend",
            bb_pct=tech.get("bb_pct", 0.5),
            bb_zone="Upper zone" if tech.get("bb_pct", 0.5) > 0.8 else "Lower zone" if tech.get("bb_pct", 0.5) < 0.2 else "Mid zone",
            stoch_k=tech.get("stoch_k", 50),
            atr=tech.get("atr", 0),
            volume_ratio=volume_ratio,
            volume_status="Above avg" if volume_ratio > 1.2 else "Normal" if volume_ratio > 0.8 else "Low",
            roc10=tech.get("roc10", 0),
            roc20=tech.get("roc20", 0),
            pct_52w_high=tech.get("pct_from_52w_high", 0),
            pct_52w_low=tech.get("pct_from_52w_low", 0),
            strategy_name=self.strategy.name,
            strategy_signal=signal.action,
            strategy_confidence=signal.confidence,
            strategy_reason=signal.reason,
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            target=signal.target,
            risk=risk,
            reward=reward,
            rr=rr,
            available_cash=available_cash,
            open_positions=open_positions,
            max_positions=settings.MAX_OPEN_POSITIONS,
            today_pnl=today_pnl,
            daily_loss_limit=settings.MAX_DAILY_LOSS,
            composite_score=tech.get("composite_score", 50),
        )

        try:
            log.info(f"Asking {settings.OPENAI_MODEL} about {signal.symbol}...")
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            decision = json.loads(raw)
            action = decision.get("action", "SKIP").upper()
            confidence = float(decision.get("confidence", 0))
            reasoning = decision.get("reasoning", "")

            log.info(f"Claude: {signal.symbol} → {action} (confidence={confidence:.2f})")
            log.info(f"Reasoning: {reasoning}")

            # Save decision
            agent_decision = AgentDecision(
                symbol=signal.symbol,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                market_context=prompt[:500],
                executed=False,
            )
            self.repo.save_agent_decision(agent_decision)

            if action == "BUY" and confidence >= 0.60:
                qty, capital = self.risk.calculate_position_size(
                    available_cash=available_cash,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    atr=tech.get("atr", 0),
                )

                valid, msg = self.risk.validate_trade(
                    signal.symbol, signal.entry_price, signal.stop_loss, signal.target, qty
                )
                if not valid:
                    log.warning(f"Trade validation failed for {signal.symbol}: {msg}")
                    return None

                trade = self.orders.enter_long(
                    symbol=signal.symbol,
                    quantity=qty,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    target=signal.target,
                    strategy=self.strategy.name,
                    reasoning=reasoning,
                )
                if trade:
                    log.info(f"TRADE EXECUTED: {signal.symbol} x{qty} @ ₹{signal.entry_price:.2f}")
                    return {"trade": trade, "decision": decision}

        except json.JSONDecodeError as e:
            log.error(f"JSON parse error for {signal.symbol}: {e}")
        except Exception as e:
            log.error(f"Error evaluating {signal.symbol}: {e}")

        return None

    def _manage_open_positions(self):
        """Check stop losses, targets, and time stops on open trades."""
        open_trades = self.repo.get_open_trades()
        if not open_trades:
            return

        log.info(f"Managing {len(open_trades)} open positions...")

        for trade in open_trades:
            ltp = self.kite.get_ltp(trade.symbol, trade.exchange)
            if ltp <= 0:
                continue

            days_held = (datetime.utcnow() - trade.entry_time).days
            exit_reason = None

            if trade.stop_loss and ltp <= trade.stop_loss:
                exit_reason = f"SL hit @ ₹{ltp:.2f}"
            elif trade.target and ltp >= trade.target:
                exit_reason = f"Target hit @ ₹{ltp:.2f}"
            elif days_held >= settings.HOLD_PERIOD_DAYS + 2:
                exit_reason = f"Time stop: {days_held} days held"

            if exit_reason:
                self.orders.exit_trade(trade, ltp, exit_reason)
            else:
                unrealized = (ltp - trade.entry_price) * trade.quantity
                log.info(
                    f"  {trade.symbol}: ₹{ltp:.2f} | "
                    f"PnL: ₹{unrealized:+.2f} | "
                    f"SL: ₹{trade.stop_loss:.2f} | "
                    f"T: ₹{trade.target:.2f} | "
                    f"Days: {days_held}"
                )

    def get_portfolio_status(self) -> dict:
        open_trades = self.repo.get_open_trades()
        pnl_data = self.orders.get_current_pnl(open_trades)
        today_realized = self.repo.get_today_pnl()
        available_cash = self.kite.get_available_cash()

        return {
            "open_positions": len(open_trades),
            "available_cash": available_cash,
            "today_realized_pnl": today_realized,
            "today_unrealized_pnl": pnl_data["total_unrealized"],
            "today_total_pnl": today_realized + pnl_data["total_unrealized"],
            "positions": pnl_data["positions"],
            "paper_trading": settings.PAPER_TRADING,
        }
