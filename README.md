# Trade Agent — AI-Powered NSE Swing Trading

An autonomous trading agent that scans NIFTY 50/100 stocks, uses OpenAI GPT to make buy/sell decisions, and executes trades on your Zerodha account via Kite Connect.

**Strategy:** Swing trading (3–5 day holds) targeting 3–5% monthly returns  
**Risk:** Max 2% capital per trade | Hard stop losses | Daily loss circuit breaker  
**Default mode:** Paper trading (safe simulation — no real money until you flip the switch)

---

## Prerequisites

- Python 3.10+
- Zerodha account with [Kite Connect API subscription](https://developers.kite.trade/) (₹2,000/month)
- [OpenAI API key](https://platform.openai.com/api-keys)

---

## Installation

```bash
# 1. Clone / navigate to the project
cd trade-app

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

```bash
# Copy the example env file
cp .env.example .env
```

Open `.env` and fill in your credentials:

```env
# --- Zerodha Kite Connect ---
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
KITE_ACCESS_TOKEN=             # Leave blank — filled automatically by auth command

# --- OpenAI ---
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini       # gpt-4o-mini (cheap) | gpt-4o (more reasoning)

# --- Trading Safety ---
PAPER_TRADING=true             # KEEP TRUE until you are confident
MAX_CAPITAL_PER_TRADE=10000    # Max INR per single trade
MAX_DAILY_LOSS=5000            # Agent stops trading if daily loss exceeds this
MAX_OPEN_POSITIONS=5           # Max simultaneous open trades
RISK_PER_TRADE_PCT=2.0         # % of capital risked per trade

# --- Strategy ---
STRATEGY=momentum              # momentum | mean_reversion
UNIVERSE=NIFTY50               # NIFTY50 | NIFTY100
HOLD_PERIOD_DAYS=3             # Target hold duration

# --- Optional: Telegram alerts ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## One-Time Setup: Zerodha Redirect URL

Before running the auth command, you must register the callback URL once in the Zerodha developer console:

1. Go to [https://developers.kite.trade/apps](https://developers.kite.trade/apps)
2. Click your app → **Edit**
3. Set **Redirect URL** to: `http://127.0.0.1:5000/callback`
4. Save

This only needs to be done once.

---

## Daily Workflow

### Step 1 — Authenticate (every morning before 9:00 AM IST)

Zerodha access tokens expire at midnight IST every day. Run this each morning:

```bash
source venv/bin/activate
python main.py auth
```

- Your browser opens automatically to the Zerodha login page
- Log in with your Zerodha credentials
- Token is saved to `.env` automatically
- Server shuts down on its own

### Step 2 — Run the agent

**Option A: One-shot scan** (run manually whenever you want)
```bash
python main.py scan
```
Scans all stocks in the universe, asks GPT to evaluate signals, executes qualifying trades.

**Option B: Automated scheduler** (runs daily without manual intervention)
```bash
python main.py schedule
```
Schedules automatically:
- `09:00 IST` — Morning market scan + trade execution
- `Every 30 min` — Monitor open positions for SL/target hits
- `15:40 IST` — End-of-day report

> Keep the terminal open (or run in background with `nohup python main.py schedule &`)

---

## All Commands

```bash
python main.py auth             # Zerodha OAuth login — run every morning
python main.py scan             # One-shot market scan + trade execution
python main.py schedule         # Start automated daily scheduler
python main.py status           # Show portfolio + open positions + today's PnL
python main.py history          # Show recent trade history
python main.py history --limit 50   # Show last 50 trades
python main.py report           # Monthly P&L report (current month)
python main.py report --month 2025-12   # Report for a specific month
python main.py export           # Export all trades to CSV
python main.py export --output my_trades.csv
python main.py backtest         # Backtest strategy on historical data (365 days)
python main.py backtest --days 180 --strategy mean_reversion
```

---

## Strategies

### Momentum (default)
Buys stocks in a confirmed uptrend with momentum building.

**Entry signals required (all must align):**
- Price above EMA20 and EMA50
- RSI between 40–65 (not overbought)
- MACD histogram turning positive
- Volume above 20-day average
- ADX > 20 (trend strength present)

### Mean Reversion
Buys oversold stocks near support for a bounce.

**Entry signals required:**
- RSI below 35 (oversold)
- Price near lower Bollinger Band
- Stochastic K below 20
- Price near 20-day support level

Switch strategy in `.env`:
```env
STRATEGY=mean_reversion
```

---

## Risk Management

The agent enforces these rules automatically — they cannot be overridden:

| Rule | Default | Setting |
|---|---|---|
| Max capital per trade | ₹10,000 | `MAX_CAPITAL_PER_TRADE` |
| Max daily loss before halt | ₹5,000 | `MAX_DAILY_LOSS` |
| Max open positions | 5 | `MAX_OPEN_POSITIONS` |
| Risk per trade | 2% of capital | `RISK_PER_TRADE_PCT` |
| Stop loss | 2× ATR below entry | Automatic |
| Target | 4× ATR above entry (2:1 R:R) | Automatic |
| Time stop | Exit after 5 days regardless | `HOLD_PERIOD_DAYS` |
| Min Risk:Reward | 1.5:1 | Hard-coded |

---

## Going Live (Paper → Real Money)

1. Run paper trading for at least **2–4 weeks**
2. Check `python main.py report` — ensure win rate > 50% and profit factor > 1.2
3. Review individual trade reasoning: `python main.py history`
4. When confident, set in `.env`:
   ```env
   PAPER_TRADING=false
   ```
5. Start with small capital. Scale up only after 1–2 profitable live months.

> **Warning:** Trading involves real financial risk. Past backtest performance does not guarantee future profits. Never trade money you cannot afford to lose.

---

## Project Structure

```
trade-app/
├── main.py                        # CLI entry point
├── requirements.txt
├── .env                           # Your credentials (never commit this)
├── .env.example                   # Template
├── config/
│   └── settings.py                # All configuration
├── src/
│   ├── agent/
│   │   ├── trading_agent.py       # GPT-powered decision engine
│   │   └── prompts.py             # System prompt + market context templates
│   ├── broker/
│   │   ├── kite_client.py         # Zerodha Kite Connect wrapper
│   │   └── order_manager.py       # Order execution + SL/target monitoring
│   ├── analysis/
│   │   └── technical.py           # RSI, MACD, EMA, ATR, Bollinger, ADX, Stochastic
│   ├── strategy/
│   │   ├── momentum.py            # Momentum swing strategy
│   │   └── mean_reversion.py      # Oversold bounce strategy
│   ├── risk/
│   │   └── manager.py             # Position sizing, daily limits, trade validation
│   ├── backtest/
│   │   ├── engine.py              # Bar-by-bar backtester (no look-ahead bias)
│   │   └── cost_analyzer.py       # Brokerage + tax cost breakdown
│   ├── database/
│   │   ├── models.py              # SQLAlchemy models (trades, PnL, decisions)
│   │   └── repository.py          # Database queries
│   ├── scheduler/
│   │   └── runner.py              # Automated daily scheduler
│   ├── notifications/
│   │   └── telegram.py            # Optional Telegram trade alerts
│   ├── reports/
│   │   └── generator.py           # Rich CLI reports + CSV export
│   └── auth/
│       └── kite_auth.py           # Zerodha OAuth login server
└── data/
    └── trades.db                  # SQLite database (auto-created)
```

---

## Optional: Telegram Alerts

Get notified on your phone for every trade entry, exit, and daily summary.

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your Chat ID
3. Add to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_CHAT_ID=987654321
   ```

---

## Troubleshooting

**`Invalid API key` from Zerodha**
- Run `python main.py auth` again — access tokens expire daily at midnight IST

**`OPENAI_API_KEY is required`**
- Make sure `.env` exists and has `OPENAI_API_KEY=sk-...`

**No trades executed after scan**
- Normal — the agent only trades when all strategy conditions align
- Check `python main.py status` to confirm the agent is running
- Market may be in a sideways/choppy phase — both strategies favour trending markets

**Backtest shows losses**
- Strategy may not suit current market conditions
- Try switching: `STRATEGY=mean_reversion` in `.env`
- Increase `HOLD_PERIOD_DAYS` for longer swing trades

**`ModuleNotFoundError`**
- Make sure the venv is activated: `source venv/bin/activate`
- Reinstall: `pip install -r requirements.txt`
