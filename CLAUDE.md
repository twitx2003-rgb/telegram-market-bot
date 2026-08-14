# telegram-market-bot — Project Guide

## What This Is
A personal **Market Learning Agent** (לומד מהשוק). It runs on GitHub Actions and publishes a single-page Hebrew PWA to GitHub Pages every 30 minutes. Each build the agent studies:
- **📺 מיכה סטוקס — היום** — the latest YouTube videos of Micha Stocks (`@Micha.Stocks`, "CNBC in Hebrew"): fetches the transcript, summarizes the key insights + tickers in Hebrew.
- **📈 מה ה-X אומר היום** — the significant finance tweets rising on X: sentiment split, most-mentioned tickers, and the top ranked tweets.
- **🎯 קונצנזוס** — tickers that appear in BOTH Micha's video and the day's tweets (shared conviction).

**Design system:** `.claude/skills/dashboard-design/SKILL.md` + `design.md` — "Financial Broadsheet" (serif headlines via Frank Ruhl Libre, hybrid dark Bloomberg / FT-salmon light, amber accent). Read before touching HTML/CSS in `build_html()`.

## Files

| File | Purpose |
|---|---|
| `learn_agent.py` | **Main generator** — YouTube ingestion + tweet synthesis + builds `docs/index.html` |
| `news_dashboard.py` | **Internal library only** — reused low-level helpers (Nitter tweet fetch, sentiment/ticker classifiers, importance ranking). Not run directly; not deployed. |
| `learn_config.json` | **User-editable** — YouTube handle, fallback channel id, transcript languages |
| `docs/index.html` | Auto-generated learning page (GitHub Pages) |
| `docs/learn.json` | Auto-generated data layer (video summaries + tweet synthesis + consensus) |
| `docs/manifest.json` / `docs/sw.js` | PWA manifest + service worker (network-first) |

## Video Summaries — engine ladder

YouTube blocks transcript fetches from GitHub Actions' cloud IPs (documented in the
live logs). The agent handles this with a graded ladder (all optional secrets):

| Secret | Engine | Quality |
|---|---|---|
| `GEMINI_API_KEY` | **Gemini watches the video directly** (YouTube URL, free tier — no IP-block) | ⭐ best — true video comprehension |
| `WEBSHARE_PROXY_USERNAME`/`PASSWORD` or `YT_PROXY_URL` | residential proxy → transcript → Claude/free summary | high (paid proxy) |
| `ANTHROPIC_API_KEY` | Claude over a transcript (only when a transcript is reachable) | high |
| *(none)* | extractive summary from the video's **full description** + `sentiment_he()` | basic, free |

**Recommended free path:** get a free key at Google AI Studio → add `GEMINI_API_KEY` at
**Settings → Secrets and variables → Actions**. Free tier allows 8h of YouTube video/day
(Micha's ~3 videos are far under). Model override: `GEMINI_MODEL` (default `gemini-2.5-flash`).

Tweet synthesis is always free (rule-based). Set secrets at **Settings → Secrets and variables → Actions**.

## How It Works (pipeline in `learn_agent.py:main()`)

1. `resolve_channel_id(handle)` — scrapes the channel page for `channelId` (robust to id ambiguity; falls back to the pinned id in `learn_config.json`).
2. `fetch_channel_videos(channel_id)` — latest uploads via the channel RSS feed (no API key).
3. `fetch_transcript(video_id)` — via `youtube-transcript-api`. **Caveat:** YouTube sometimes blocks transcript fetches from datacenter IPs / videos may lack captions → falls back to the video title + description (an in-page note is shown when this happens).
4. `summarize_video_*` — free extractive or Claude.
5. `fetch_twitter_feeds()` + `synthesize_tweets()` — reused from the library.
6. Consensus = intersection of video tickers and top tweet tickers.
7. Video summaries are cached by `video_id` in `docs/learn.json` (a video is summarized once, persists across 30-min builds).

## Edit the Channel / Handles
- YouTube channel: edit `learn_config.json` (`handle` or `fallback_channel_id`).
- Twitter handles: `TWITTER_HANDLES` in `news_dashboard.py`.

## Workflow

| Workflow | Schedule | What it does |
|---|---|---|
| `hourly_dashboard.yml` (name: *Market Learning Agent*) | Every 30 min + on push | Runs `learn_agent.py` → commits `docs/index.html` + `docs/learn.json` |

## PWA — Install on Mobile
Open the GitHub Pages URL in Chrome/Safari; "Add to Home Screen". Works offline (last cached page).

## Do Not Commit
- Any file containing API keys or credentials in plaintext.
- `.bat` files with hardcoded passwords.
