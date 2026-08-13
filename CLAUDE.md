# telegram-market-bot — Project Guide

## What This Is
A personal stock market dashboard. It runs on GitHub Actions and publishes a Hebrew-language PWA to GitHub Pages every 30 minutes. Four tabs:
- **📰 חדשות** — significance-ranked news feed (authoritative RSS + top tweets, cleaned of reply/RT artifacts, newest-first)
- **📊 דופק השוק** — infographic aggregating ALL fetched tweets: sentiment split, most-mentioned tickers, topic breakdown, bull-vs-bear leaderboard (`build_market_pulse()`)
- **💡 רעיונות** — stocks flagged trade-worthy from tweet chatter (mentions + trade-signal language), cross-referenced with TA indicators (`build_trade_ideas()`)
- **🎯 הזדמנויות** — technical analysis: support/resistance levels, indicators, Hebrew recommendations with rationale (watchlist + StockTwits trending, max 15 tickers), plus macro widgets (indices, Fear & Greed, sector heatmap)

**Design system:** `.claude/skills/dashboard-design/SKILL.md` — read it before touching any HTML/CSS in `build_html()` or the builder functions.

## GitHub Secrets — Free vs Premium Mode

The system runs **100% free by default**. `ANTHROPIC_API_KEY` is optional:

| Secret | Required | Purpose |
|---|---|---|
| `FINNHUB_API_KEY` | Yes (free tier) | Real-time stock quotes — sign up free at https://finnhub.io |
| `EMAIL_FROM` | Yes | Gmail address to send alerts from |
| `EMAIL_TO` | Yes | Email address to receive alerts |
| `EMAIL_PASSWORD` | Yes | Gmail app password (not your login password) |
| `ANTHROPIC_API_KEY` | **Optional (paid)** | Premium mode — see below |

**Free mode (no ANTHROPIC_API_KEY):**
- Opportunities tab: rule-based recommendations (`rule_based_analysis()` — same rulebook, deterministic, Hebrew rationale templates citing real numbers)
- News: `build_free_news()` blends authoritative US RSS (Reuters/CNBC/MarketWatch/Yahoo via `fetch_us_news_rss()`) with finance tweets into one curated feed — clean-sentence Google translation, keyword tags + sentiment, de-dup by headline, importance ranking (`_importance_score()`), newest-first, capped
- No article images (clean text cards — a professional-presentation choice)
- Daily improvement agent: skips gracefully (green no-op)

**Premium mode (with ANTHROPIC_API_KEY):**
- Analyst-voice Hebrew news commentary (Claude Sonnet)
- Claude interpretation of the technical indicators (richer rationale)
- Daily AI improvement agent active
- Approximate cost at 30-min cadence: $2-4/day

Switching modes = adding/removing the secret. No code changes.

Set secrets at: **Settings → Secrets and variables → Actions → New repository secret**

## Workflow Schedule (all times in UTC)

| Workflow | Schedule | What it does |
|---|---|---|
| `hourly_dashboard.yml` | Every 30 min | Generates `docs/index.html` + `docs/data.json` |
| `market_alerts.yml` | Every 20 min | Checks RSS + watchlist price alerts, sends email |
| `daily_summary.yml` | 09:00 UTC (12:00 IDT) | Daily digest email + `docs/report.html` |
| `daily_improvement.yml` | 09:30 UTC (12:30 IDT) | AI self-improvement pass on index.html |

## Files Overview

| File | Purpose |
|---|---|
| `news_dashboard.py` | Main generator — fetches data, calls Claude, builds HTML |
| `market_news.py` | Alert bot — RSS polling + watchlist price alerts via email |
| `improvement_agent.py` | Daily AI improvement agent (reads live HTML, injects enhancements) |
| `watchlist.json` | **User-editable** — your personal stock watchlist + alert config |
| `docs/index.html` | Auto-generated dashboard (GitHub Pages) |
| `docs/data.json` | Auto-generated intermediate data layer (used by alert bot) |
| `docs/manifest.json` | PWA manifest (static, committed once) |
| `docs/sw.js` | Service worker for offline support (static, committed once) |

## Model Strategy (cost vs quality)

- **Claude Sonnet** (`ENRICHMENT_MODEL`) — analyst-voice interpretation of market tweets. High quality, runs once per 30-min cycle.
- **Claude Sonnet** (`TA_MODEL`, defaults to `ENRICHMENT_MODEL`) — second independent call: technical-analysis interpretation of Python-computed indicators. Claude never invents numbers — Python computes SMA/RSI/support/resistance and always wins on levels.
- **Claude Haiku** (`MODEL`) — HTML template generation in `improvement_agent.py`. Fast and cheap.
- Override via env vars: `ENRICHMENT_MODEL=claude-opus-4-8`, `TA_MODEL=...`, `DASHBOARD_MODEL=claude-haiku-4-5-20251001`

## How to Edit Your Watchlist

Edit `watchlist.json` — no code changes needed:

```json
{
  "stocks": [
    {"ticker": "AAPL", "name": "Apple"},
    {"ticker": "NVDA", "name": "Nvidia"}
  ],
  "il_stocks": [
    {"ticker": "TEVA", "name": "טבע"}
  ],
  "alerts": {
    "price_move_pct": 5,
    "on_earnings_day": true
  }
}
```

- `ticker` must match the exact symbol used on US markets (Yahoo Finance / Finnhub)
- `price_move_pct` — sends an email alert when a stock moves more than this % in one day
- `on_earnings_day` — sends an alert on the day of an earnings report (requires Finnhub)

## data.json Schema

```json
{
  "generated_at": "2026-07-14T12:00:00Z",
  "watchlist": [
    {
      "ticker": "AAPL", "name": "Apple",
      "price": 185.50, "change_pct": 2.3,
      "direction": "up", "change_str": "+2.3%",
      "earnings_date": "2026-07-28",
      "is_earnings_today": false,
      "alert_triggered": false
    }
  ],
  "opportunities": [
    {
      "ticker": "NVDA", "price": 208.51, "setup_type": "מומנטום",
      "levels": {"support": 195.0, "resistance": 220.0, "entry": 198.0, "stop": 191.0},
      "recommendation": "קנייה", "confidence": 4,
      "rationale_he": "...", "indicators": {"rsi14": 61.2, "sma20": 201.3},
      "analyzed": true, "stale": false
    }
  ],
  "ta_generated_at": "2026-07-15T12:00:00Z",
  "market_us": [], "commodities": [], "market_il": [], "sectors": [],
  "fear_greed": {"score": 65, "rating": "Greed"},
  "us_news": [], "il_news": [],
  "alert_config": {"price_move_pct": 5, "on_earnings_day": true}
}
```

Notes:
- `us_news`/`il_news` items carry `published_at` (ISO-8601) and `src_id` for source traceability.
- The watchlist prices are still fetched (the email alert bot needs them) even though they're no longer displayed as cards.

## PWA — Install on Mobile

Open the GitHub Pages URL in Chrome/Safari on your phone. You'll see an "Add to Home Screen" or install prompt. Once installed, the app works offline (shows the last cached dashboard).

## Do Not Commit

- `.bat` files with hardcoded passwords (e.g. `run_summary.bat`)
- `seen_news.json`, `seen_alerts.json` — these are runtime cache files, auto-gitignored
- Any file containing API keys or credentials in plaintext
