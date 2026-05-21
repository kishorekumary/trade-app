SYSTEM_PROMPT = """You are an expert Indian stock market trading agent specializing in NSE equity swing trading.

Your role is to make disciplined, data-driven trading decisions for a retail trader targeting monthly returns of 3-5% through swing trades (2-5 day holds) in NIFTY 50/100 stocks.

## Your Decision Framework

**ENTRY criteria (ALL must align):**
1. Clear uptrend: Price above EMA20 and EMA50
2. Momentum confirmation: MACD bullish crossover OR RSI recovering from oversold (35-65)
3. Volume conviction: Volume above 20-day average
4. Risk:Reward >= 2:1 (minimum)
5. No major earnings within hold period

**POSITION SIZING:**
- Never risk more than 2% of capital per trade
- Maximum ₹10,000 per position unless specified
- Maximum 5 open positions at once

**EXIT rules:**
- Stop loss: 2x ATR below entry (hard stop)
- Target: 4x ATR above entry (2:1 R:R)
- Time stop: Exit after 5 trading days regardless

**RISK rules you must enforce:**
- Never trade against the primary trend (price below 200 EMA)
- Avoid stocks with RSI > 75 (overbought)
- Skip if daily loss limit is hit
- Prefer liquid stocks (high volume)

## Response Format

Always respond in valid JSON:
```json
{
  "action": "BUY" | "HOLD" | "SKIP",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence explanation",
  "key_factors": ["factor1", "factor2"],
  "risks": ["risk1", "risk2"],
  "entry_price": 1234.50,
  "stop_loss": 1210.00,
  "target": 1280.00,
  "hold_days": 3
}
```

Be conservative. Protect capital first. A good month is 8-10 quality trades with 60%+ win rate."""


MARKET_CONTEXT_TEMPLATE = """
## Market Analysis Request

**Symbol:** {symbol}
**Current Price:** ₹{price:.2f}
**Exchange:** NSE

## Technical Indicators
- RSI (14): {rsi:.1f} → {rsi_signal}
- MACD: {macd_signal}
- EMA20: ₹{ema20:.2f} | Price {'ABOVE' if above_ema20 else 'BELOW'} EMA20
- EMA50: ₹{ema50:.2f} | Price {'ABOVE' if above_ema50 else 'BELOW'} EMA50
- EMA200: ₹{ema200:.2f} | Price {'ABOVE' if above_ema200 else 'BELOW'} EMA200
- ADX: {adx:.1f} ({'Strong trend' if adx > 25 else 'Weak trend'})
- Bollinger Band %: {bb_pct:.2f} ({'Upper zone' if bb_pct > 0.8 else 'Lower zone' if bb_pct < 0.2 else 'Mid zone'})
- Stochastic K: {stoch_k:.1f}
- ATR (14): ₹{atr:.2f}
- Volume Ratio: {volume_ratio:.2f}x ({volume_status})
- 10-day ROC: {roc10:+.2f}%
- 20-day ROC: {roc20:+.2f}%
- 52W High: {pct_52w_high:+.1f}%
- 52W Low: {pct_52w_low:+.1f}%

## Strategy Signal
- Strategy: {strategy_name}
- Signal: {strategy_signal}
- Strategy Confidence: {strategy_confidence:.1%}
- Strategy Reason: {strategy_reason}

## Proposed Trade Levels
- Entry: ₹{entry:.2f}
- Stop Loss: ₹{stop_loss:.2f} (Risk: ₹{risk:.2f} per share)
- Target: ₹{target:.2f} (Reward: ₹{reward:.2f} per share)
- Risk:Reward = 1:{rr:.1f}

## Portfolio Context
- Available Cash: ₹{available_cash:.0f}
- Open Positions: {open_positions}/{max_positions}
- Today's PnL: ₹{today_pnl:+.2f}
- Daily Loss Limit: ₹{daily_loss_limit:.0f}

## Composite Technical Score: {composite_score}/100

Analyze this trade setup and provide your decision in the specified JSON format.
"""


EXIT_ANALYSIS_TEMPLATE = """
## Exit Decision Request

**Symbol:** {symbol}
**Direction:** {direction}
**Entry Price:** ₹{entry_price:.2f}
**Current Price:** ₹{current_price:.2f}
**Unrealized PnL:** ₹{unrealized_pnl:+.2f} ({pnl_pct:+.2f}%)
**Days Held:** {days_held}
**Stop Loss:** ₹{stop_loss:.2f}
**Target:** ₹{target:.2f}

## Current Technicals
- RSI: {rsi:.1f}
- MACD: {macd_signal}
- ADX: {adx:.1f}
- Volume Ratio: {volume_ratio:.2f}x

Should we exit this position? Respond in JSON:
{{
  "action": "EXIT" | "HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "explanation",
  "urgency": "immediate" | "end_of_day" | "can_wait"
}}
"""
