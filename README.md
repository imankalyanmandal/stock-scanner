# Swing-setup stock scanner (NSE)

Scans **Nifty 50 + Nifty Next 50 + Nifty Midcap 150** (~250 stocks) every day at **3:00 AM IST**
and sends the top 3–5 swing setups (stage-2 uptrend leaders only) with a buy-stop trigger, stop,
first target, position size and exit plan. Every pick is logged to a journal and replayed against
real prices, and `backtest.py` runs the exact same rules over 5 years of history.

## Quick start

```bash
pip install -r requirements.txt
python run_scan.py --limit 20 --no-news   # quick smoke test on 20 stocks
python run_scan.py                        # full scan now
python scheduler.py                       # keep running; scans at 3:00 AM IST
python scheduler.py --run-now             # scan once immediately, then keep the schedule
python backtest.py                        # 5-year walk-forward test of the same rules
python backtest.py --limit 50 --start 2023-01-01   # quicker partial run
python -m pytest -q                       # offline tests on synthetic data
```

Reports land in `reports/scan_<session-date>.{html,csv,json}` and
`reports/backtest_<start>_<end>.{html,json}` (+ `_trades.csv`). Logs go to `logs/scanner.log`.
The journal is `data/journal.csv`. A **Backtest** workflow (Actions tab → Run workflow) runs the
backtest on GitHub and uploads the report as an artifact.

### Scheduling options

**Docker (recommended for a server):**
```bash
docker build -t stock-scanner .
docker run -d --name scanner --restart unless-stopped \
  -v $PWD/reports:/app/reports -v $PWD/logs:/app/logs -v $PWD/data:/app/data stock-scanner
```

**Plain cron instead of `scheduler.py`:**
```
CRON_TZ=Asia/Kolkata
0 3 * * 2-6  cd /path/to/stock-scanner && /usr/bin/python3 run_scan.py >> logs/cron.log 2>&1
```

The default runs **Tue–Sat**, so each run scans the previous Mon–Fri session. Set
`schedule.day_of_week: "*"` in `config.yaml` to run literally every day. The Sunday and
Monday runs would just re-scan Friday's session.

## Telegram notifications

After each scan you get a summary message (regime, size multiplier, picks with buy-stop, stop, T1
and quantity, live journal stats) plus the full HTML report as an attachment. If a scan fails, you get an alert with a
link to the logs.

**One-time setup:**
1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the
   **bot token** it gives you (looks like `123456789:AAH...`).
2. Open a chat with your new bot and send it any message (e.g. `hi`). Bots can't message you
   until you've messaged them first.
3. Get your **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find
   `"chat":{"id":123456789,...}`. That number is your chat id.
4. Add both as GitHub secrets: repo → **Settings → Secrets and variables → Actions → New
   repository secret**:
   - `TELEGRAM_BOT_TOKEN` = the token
   - `TELEGRAM_CHAT_ID` = the chat id

For local runs, export the same two variables in your shell instead. If they're missing,
the scan still runs and just skips the messages. Use `--no-notify` to skip them on purpose.
Message settings are under `notify.telegram` in `config.yaml`.

## Measure first: backtest and journal

Change rules in `config.yaml`, run `python backtest.py`, and keep the change only if expectancy
(average R per trade, after costs) improves. Look at the **by setup** and **by regime** tables:
they show which rules actually make money.

- **Backtest** (`scanner/backtest.py`): every day in the period, applies the regime, gates, scoring
  and selection, then replays each pick bar by bar with the exit rules below, with at most
  `max_open_positions` at once. Limits: today's constituents are used for the whole period
  (survivorship bias), and earnings, delivery %, sector flags and the exclusion list are not applied
  historically.
- **Journal** (`data/journal.csv`): each scan logs its picks (trigger, skip-above price, stop). Each
  later run replays them against new prices with the same simulator, so the live win rate, average
  win/loss and expectancy in the report and Telegram message use exactly the backtest's rules.
  Re-running the same session doesn't add duplicates or resend the alert (`--force` overrides).

## News check

For the final candidates only (up to 15 per scan), `scanner/news.py` collects the last 3 days of
**BSE corporate filings** (official, by ISIN → BSE scrip code) and **Google News** headlines, then
classifies them with **Gemini**, falling back to **Groq**, then to keyword matching:

- **negative** (SEBI/ED/tax action, fraud, default, results miss, downgrade, promoter selling or
  pledging, block sale, management/auditor exit, plant shutdown, USFDA warning…) → **rejected**, and
  the next-best stock takes its place. Vetoed trades are still logged in the journal (`vetoed=True`)
  so you can see whether the veto saves money.
- **positive** → flag only; news never rescues a stock that failed a technical rule.
- If the LLM says non-negative but risk keywords appear, the pick gets a `news_keyword_alert` flag.

**Pre-market recheck** (`premarket.py`, workflow at ~8:20 AM IST Mon–Fri): re-checks news from the
last 8 hours for the pending picks and sends a Telegram message: all clear, or which buy-stop to
cancel.

### Global (international) risk

Each scan also runs `scanner/macro.py`:
- **Overnight market moves:** S&P 500, Nasdaq, Brent crude, USD/INR, US 10-year yield, dollar index, Nikkei, Hang Seng.
- **Global headlines from the last 36 hours:** the Fed, Wall Street, crude, US tariffs, wars, FII outflows, the rupee, China, recession.

Gemini (then Groq) judges the risk of opening new longs in India and lists the NSE sectors directly
hit. Rules on the market moves set a **floor**: e.g. S&P −3% or Brent +6% overnight is "high" even
if the LLM says otherwise.

| Global risk | Effect |
|---|---|
| low | nothing |
| elevated | position size × 0.5 |
| high | no new longs today (watchlist only) |
| sector marked negative | stocks in that sector rejected (`global_sector_headwind`) |

The pre-market check repeats this with news from the last 8 hours plus the Asian open, and tells you
to hold off on buy-stops if global risk is high. None of this can be backtested (no headline
archive), so it is used only to reduce risk, never to add trades.

### Secrets

| GitHub secret | Used for | Required |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot | yes |
| `TELEGRAM_CHAT_ID` | Your chat | yes |
| `GEMINI_API_KEY` | News classification (first choice) | recommended |
| `GROQ_API_KEY` | News classification (fallback) | recommended |

Without either LLM key the news check still runs, on keyword matching only. After adding keys, run
the **Setup check** workflow: `reports/news_check.json` shows which sources and models work (never
the keys). Model names are in `config.yaml` under `news.llm`.

## Experiments: compare variants

`experiments.yaml` lists rule variants as overrides of `config.yaml`. `python experiments.py` (or the
**Experiments** workflow) downloads prices once, runs every variant on the same data and writes
`reports/experiments_<date>.md` (also shown on the workflow run page). It covers:

- **Swing variants:** expectancy, win rate and drawdown per variant, plus expectancy for each half of
  the period. Trust a change only if it helps in both halves.
- **Monthly momentum rotation** (`scanner/momentum.py`): top 30 by NSE-style momentum score
  (6- and 12-month return / volatility), rebalanced monthly at the next open, optional cash when
  Nifty 50 is under its 200 DMA, costs and liquid-fund yield on cash included, compared with
  Nifty 50 / Nifty 500 buy-and-hold.

Pullbacks are switched off (`setups.pullback.enabled: false`) after losing money in the backtest.

## Rules

| Step | Rule | Where |
|---|---|---|
| Market regime | Strong: Nifty 50 & 500 above 20 and 50 DMA and ≥60% of universe above its 50 DMA → full size. Neutral → half size. Weak (Nifty 50 under 50 DMA or breadth <40%) → **no new longs** (watchlist only). India VIX ≥20 halves size; ≥28 blocks new longs. | `market.py` |
| Hygiene | Price ≥ ₹50, 20-day median turnover ≥ ₹10 Cr, avg volume ≥ 1 lakh, no locked-circuit day in 20 sessions, not in `data/exclude.csv` | `filters.py` |
| Trend template | Close > 50 DMA > 200 DMA, both rising, within 25% of the 52-week high, ≥30% above the 52-week low | `filters.py` |
| Leadership | RS composite (weighted 3/6/9/12-month returns) ranked across the universe; top 30% only. Scored on beating Nifty 500 over 1 and 3 months. | `filters.py` |
| Setup (required) | **Breakout:** day 1 or 2 of a close above the prior 20-bar high; base ≤15% deep; breakout volume ≥1.5× 50-day avg; close in top 25% of range. **Pullback:** ≥2 bars down from a recent high on below-average volume, low within −0.75…+0.5 ATR of a rising 20 DMA, then a green reversal day closing above the 20 DMA in the top 40% of its range. | `filters.py` |
| Not chasing | RSI ≤ 75, ≤2.5 ATR above the 20 DMA, 2-day gain ≤ 8% | `filters.py` |
| Entry | **Buy-stop** 0.1% above the signal-day high (breakouts: above the breakout high), valid 1 session. **Skip** if the stock opens more than 1.5% above the trigger. | `filters.py` |
| Stop / room | Stop under the setup low (breakout days or pullback low) − 0.1 ATR, at least 1 ATR, at most 3 ATR from the trigger. Nearest overhead resistance (any swing high, including recent ones) must be ≥2R away. | `filters.py` |
| Events | Earnings within 14 days → reject. Ex-dividend within 7 days → flag. | `pipeline.py` |
| Selection | Rank by score, max 2 per sector, top 5 | `filters.py` |
| Sizing | Qty = 1% of capital × regime/VIX multiplier ÷ (trigger − stop), capped at 20% of capital | `trade.py` |
| Exits | Book 50% at +2R and move the stop to entry; trail the rest on a close below the 20 EMA; exit if not +1R after 7 sessions; hard exit at 30 sessions. Costs: 0.25% round trip. | `trade.py` |

The score (0–100) only ranks stocks that passed every gate: RS rank, volume (expansion on
breakouts, dry-up on pullbacks), base tightness / clean 20 DMA touch, close location, nearness to
the 52-week high, beating the benchmark, room to resistance and delivery %.

## Exclusion list

`data/exclude.csv` (`symbol,reason`) is for stocks you won't trade: ASM/GSM stages, F&O ban list,
heavy promoter pledge. NSE serves those lists behind anti-bot protection, so they are maintained by
hand until a broker API is plugged in.

## Tuning

Every threshold is in `config.yaml`; the scheduler re-reads it on each run. The HTML report has a
**"Why stocks were rejected"** section. Pullbacks are often rejected by `resistance_too_close`
because the prior high is usually less than 2R away; if the backtest shows pullbacks still have a
positive expectancy, set `setups.pullback.min_room_r` lower (e.g. 1.5) rather than loosening the
global gate.

## Not covered yet

- **ASM/GSM lists, F&O ban list, promoter pledge.** Behind NSE's anti-bot protection; use
  `data/exclude.csv` meanwhile. Locked-circuit days (high = low) in the last 20 sessions are rejected.
- **Bid-ask spread / impact cost.** Not in EOD data; needs a broker API.
- **Sector indices.** Sector breadth is computed from peers in the same NSE industry within the
  universe (only for sectors with ≥5 stocks), not from official sector index prices.
- **News.** Off by default (`news.enabled`); when on it is shown for information only and never
  affects selection. `enrich.classify_news()` is a stub for an LLM classifier.
- **Regulatory / litigation / fundraising disclosures.** These need NSE corporate
  announcements, which sit behind the same anti-bot protection as above.

## Data sources

- Prices: Yahoo Finance via `yfinance` (`.NS` tickers, adjusted). It's free and unofficial, so
  occasional gaps happen and missing symbols are logged. For production, swap `data.py` to
  Kite/Upstox.
- Delivery %: NSE full bhavcopy for the session date. If unavailable, the delivery score is set
  to neutral and a warning is logged.
- India VIX: `^INDIAVIX` on Yahoo. If it's missing, the VIX rule is skipped.

Screening output only, not investment advice. Entry, stop and target are mechanical estimates.
