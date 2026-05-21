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
from .prompts import SYSTEM_PROMPT, MARKET_CONTEXT_TEMPLATE, EXIT_ANALYSIS_TEMPLATE
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
            above_ema20=tech.get("above_ema20", False),
            ema50=ema50,
            above_ema50=tech.get("above_ema50", False),
            ema200=ema200,
            above_ema200=tech.get("above_ema200", False),
            adx=tech.get("adx", 0),
            bb_pct=tech.get("bb_pct", 0.5),
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
