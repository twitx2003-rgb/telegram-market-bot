#!/usr/bin/env python3
"""
לוח חדשות יומי — שוק ההון האמריקאי + חדשות ישראל
Personal News Dashboard: Wall Street + Israel News
v4 — StockTwits Wall Street news, Ticker Tape, Fear & Greed, Heatmap, Calendar, Sparklines, WhatsApp, Dark Mode
"""

import anthropic
import concurrent.futures
import json
import math
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# תמיכה בעברית ב-Windows terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Configuration ──────────────────────────────────────────────────────────────

if os.environ.get("GITHUB_ACTIONS"):
    OUTPUT_PATH      = Path("docs/index.html")
    DATA_PATH        = Path("docs/data.json")
    MODEL            = os.environ.get("DASHBOARD_MODEL",    "claude-haiku-4-5-20251001")
    ENRICHMENT_MODEL = os.environ.get("ENRICHMENT_MODEL",   "claude-sonnet-4-6")
else:
    IDOP_DIR         = Path("C:/Users/idoph/OneDrive/IDOP")
    OUTPUT_PATH      = IDOP_DIR / "reports/docs/index.html"
    DATA_PATH        = IDOP_DIR / "reports/docs/data.json"
    MODEL            = os.environ.get("DASHBOARD_MODEL",    "claude-opus-4-6")
    ENRICHMENT_MODEL = os.environ.get("ENRICHMENT_MODEL",   "claude-opus-4-6")

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

MAX_TOKENS = 12000

_now   = datetime.now()
TODAY  = _now.strftime("%d.%m.%Y")
TIME   = _now.strftime("%H:%M")

HEBREW_MONTHS = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]
TODAY_HE = f"{_now.day} ב{HEBREW_MONTHS[_now.month]} {_now.year}"

# ── Twitter Handles to Follow ─────────────────────────────────────────────────
# הוסף כאן את שמות המשתמש שאתה עוקב אחריהם (ללא @)
TWITTER_HANDLES = [
    "unusual_whales",
    "RyanDetrick",
    "wallstengine",
    "StockSavvyShay",
    "cperruna",
    "bespokeinvest",
    "MikeZaccardi",
    "DeItaone",
    "gurgavin",
    "DivesTech",
    "garyblack00",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.unixfox.eu",
]

# ── RSS Feed Definitions ───────────────────────────────────────────────────────

US_FEEDS = [
    {"name": "Reuters Business", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Markets",     "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch",      "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance",    "url": "https://finance.yahoo.com/news/rssindex"},
]

ISRAEL_FEEDS = [
    {"name": "Jerusalem Post",  "url": "https://www.jpost.com/Rss/RssFeedsHeadlines.aspx"},
    {"name": "Times of Israel", "url": "https://www.timesofisrael.com/feed/"},
    {"name": "Haaretz",         "url": "https://www.haaretz.com/cmlink/1.628765"},
    {"name": "Walla News",      "url": "https://rss.walla.co.il/feed/1"},
]

# ── Relevance Filters ──────────────────────────────────────────────────────────

US_HIGH_IMPACT = [
    "federal reserve","rate hike","rate cut","interest rate","fomc",
    "merger","acquisition","buyout","takeover",
    "ipo","bankruptcy","bankrupt","default",
    "earnings beat","earnings miss","beats estimates","misses estimates",
    "layoffs","crash","plunge","surge","soar",
    "trade war","tariff","sanctions","jobs report","gdp",
]
US_MEDIUM_IMPACT = [
    "earnings","revenue","profit","loss","guidance",
    "fed ","inflation","recession",
    "s&p 500","nasdaq","dow jones",
    "apple","microsoft","google","alphabet","amazon","tesla",
    "meta","nvidia","openai","jpmorgan","goldman sachs",
    "oil","gold","bitcoin","rally","drop",
    "netflix","amd","intel","visa","mastercard","paypal","salesforce",
    "pfizer","moderna","disney","boeing","bank of america","wells fargo",
]
US_NOISE = [
    "should i","what do you think","advice","help me","my portfolio",
    "is it worth","eli5","how do i","first time","noob","beginner",
    "what should","anyone else","opinion",
]
IL_KEYWORDS_EN = [
    "israel","gaza","hamas","netanyahu","knesset","idf","hezbollah",
    "tel aviv","jerusalem","hostage","ceasefire","war","operation",
    "shekel","bank of israel","economy","government","coalition",
    "west bank","iran","trump","minister","protest","reform",
    "judicial","democracy","election","security",
]
IL_KEYWORDS_HE = [
    "ממשלה","ביטחון","כלכלה","מלחמה","בורסה","שקל","נתניהו",
    "כנסת","צבא","עזה","חמאס","חטופים","הפגנה","רפורמה",
    "משפטית","איראן","בנק","ריבית","אינפלציה","תקציב",
]


def is_us_relevant(title: str) -> bool:
    t = title.lower()
    if any(n in t for n in US_NOISE): return False
    if any(kw in t for kw in US_HIGH_IMPACT): return True
    return sum(1 for kw in US_MEDIUM_IMPACT if kw in t) >= 2

def is_il_relevant(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in IL_KEYWORDS_EN) or any(kw in title for kw in IL_KEYWORDS_HE)

# ── RSS Fetching ────────────────────────────────────────────────────────────────

def fetch_rss(feeds: list, max_per_feed: int = 10) -> list:
    if not HAS_FEEDPARSER:
        print("⚠  feedparser not installed"); return []
    cutoff  = datetime.now(timezone.utc) - timedelta(hours=36)
    results = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"])
            count  = 0
            for entry in parsed.entries:
                if count >= max_per_feed: break
                title = (entry.get("title") or "").strip()
                link  = entry.get("link", "")
                if not title: continue
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                published_at = ""
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff: continue
                    published_at = pub_dt.isoformat()
                results.append({"title": title, "link": link, "source": feed["name"],
                                "published_at": published_at})
                count += 1
            print(f"  ✓ {feed['name']}: {count} articles")
        except Exception as e:
            print(f"  ✗ {feed['name']}: {e}")
    return results

def filter_headlines(items: list, filter_fn, max_out: int) -> list:
    relevant = [i for i in items if filter_fn(i["title"])]
    if len(relevant) < 4: relevant = items
    return relevant[:max_out]

# ── Yahoo Finance — Market Data ────────────────────────────────────────────────

_YF_SYMBOLS = [
    # Main indices
    ("^GSPC",   "S&P 500",           "market_us",   "index"),
    ("^IXIC",   "Nasdaq",            "market_us",   "index"),
    ("^DJI",    "Dow Jones",         "market_us",   "index"),
    ("^RUT",    "Russell 2000",      "market_us",   "index"),
    ("^VIX",    "VIX",               "market_us",   "vix"),
    # Commodities
    ("GC=F",    "זהב",               "commodities", "commodity"),
    ("BZ=F",    "נפט ברנט",          "commodities", "commodity"),
    ("BTC-USD", "ביטקוין",           "commodities", "btc"),
    ("^TNX",    'אג"ח ארה"ב 10Y',   "commodities", "tnx"),
    # Israel
    ("TA35.TA", 'ת"א 35',            "market_il",   "index"),
    ("ILS=X",   "דולר/שקל",          "market_il",   "ils"),
    # Sector ETFs
    ("XLK",  "טכנולוגיה",     "sectors", "sector"),
    ("XLF",  "פיננסים",       "sectors", "sector"),
    ("XLE",  "אנרגיה",        "sectors", "sector"),
    ("XLV",  "בריאות",        "sectors", "sector"),
    ("XLI",  "תעשייה",        "sectors", "sector"),
    ("XLY",  "צריכה שיקולית", "sectors", "sector"),
    ("XLP",  "צריכה בסיסית",  "sectors", "sector"),
    ("XLRE", 'נדל"ן',         "sectors", "sector"),
    ("XLU",  "תשתיות",        "sectors", "sector"),
    ("XLB",  "חומרים",        "sectors", "sector"),
    ("XLC",  "תקשורת",        "sectors", "sector"),
]

# Sparkline: fetch 5-day data for main indices only
_SPARKLINE_SYMS = ["^GSPC", "^IXIC", "^DJI", "^RUT", "GC=F", "BTC-USD"]

_YF_BASE    = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
_YF_5D_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _format_yf(price: float, pct: float, fmt: str) -> tuple:
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    change = f"{pct:+.2f}%"
    if fmt == "index":   value = f"{price:,.0f}"
    elif fmt == "vix":   value = f"{price:.2f}"
    elif fmt == "commodity": value = f"${price:,.1f}"
    elif fmt == "btc":   value = f"${price:,.0f}"
    elif fmt == "tnx":   value = f"{price:.2f}%"
    elif fmt == "ils":   value = f"{price:.3f}"
    elif fmt == "sector":value = f"{price:.2f}"
    else:                value = f"{price:,.2f}"
    return value, change, direction


def _fetch_yf_one(args: tuple) -> dict:
    sym, name, group, fmt = args
    placeholder = {"name": name, "value": "—", "change": "—", "direction": "flat", "group": group, "pct_raw": 0.0}
    if not HAS_REQUESTS: return placeholder
    try:
        url  = _YF_BASE.format(sym=urllib.parse.quote(sym))
        resp = _requests.get(url, headers=_YF_HEADERS, timeout=8)
        resp.raise_for_status()
        meta  = resp.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        pct   = float(meta.get("regularMarketChangePercent", 0.0))
        # If API returns 0, calculate from previous close
        if abs(pct) < 0.001:
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
            if prev > 0:
                pct = (price - prev) / prev * 100
        value, change, direction = _format_yf(price, pct, fmt)
        return {"name": name, "value": value, "change": change, "direction": direction, "group": group, "pct_raw": pct}
    except Exception as e:
        print(f"  ✗ {sym}: {e}")
        return placeholder


def _fetch_sparkline_one(sym: str) -> tuple:
    """Fetch 5-day closing prices. Returns (sym, [prices])."""
    if not HAS_REQUESTS: return sym, []
    try:
        url  = _YF_5D_BASE.format(sym=urllib.parse.quote(sym))
        resp = _requests.get(url, headers=_YF_HEADERS, timeout=8)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        prices = [p for p in closes if p is not None][-5:]
        return sym, prices
    except Exception:
        return sym, []


def fetch_market_data() -> dict:
    """Fetch all market data from Yahoo Finance in parallel."""
    if not HAS_REQUESTS:
        na = lambda n, g: {"name": n, "value": "N/A", "change": "—", "direction": "flat", "group": g, "pct_raw": 0.0}
        return {
            "market_us":   [na(n, "market_us")   for _, n, g, _ in _YF_SYMBOLS if g == "market_us"],
            "commodities": [na(n, "commodities")  for _, n, g, _ in _YF_SYMBOLS if g == "commodities"],
            "market_il":   [na(n, "market_il")    for _, n, g, _ in _YF_SYMBOLS if g == "market_il"],
            "sectors":     [na(n, "sectors")      for _, n, g, _ in _YF_SYMBOLS if g == "sectors"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_yf_one, _YF_SYMBOLS))

    ok = sum(1 for r in results if r["value"] != "—")
    print(f"  ✓ שוק: {ok}/{len(_YF_SYMBOLS)} סמלים נטענו")
    return {
        "market_us":   [r for r in results if r["group"] == "market_us"],
        "commodities": [r for r in results if r["group"] == "commodities"],
        "market_il":   [r for r in results if r["group"] == "market_il"],
        "sectors":     [r for r in results if r["group"] == "sectors"],
    }


def fetch_sparklines() -> dict:
    """Fetch 5-day price history for main symbols. Returns {sym: [prices]}."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_fetch_sparkline_one, _SPARKLINE_SYMS))
    ok = sum(1 for _, p in results if len(p) >= 2)
    print(f"  ✓ sparklines: {ok}/{len(_SPARKLINE_SYMS)} נטענו")
    return {sym: prices for sym, prices in results}


# ── Fear & Greed Index ─────────────────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    """Fetch Fear & Greed Index from alternative.me (crypto/stock sentiment).
    Returns {'score': int, 'rating': str} or None."""
    if not HAS_REQUESTS: return None
    he_labels = {
        "extreme fear": "פחד קיצוני",
        "fear":         "פחד",
        "neutral":      "ניטרלי",
        "greed":        "חמדנות",
        "extreme greed":"חמדנות קיצונית",
    }
    # Try alternative.me (crypto F&G — widely used as general market sentiment)
    try:
        url  = "https://api.alternative.me/fng/"
        resp = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        resp.raise_for_status()
        item   = resp.json()["data"][0]
        score  = int(item["value"])
        rating = item["value_classification"]
        rating_he = he_labels.get(rating.lower(), rating)
        print(f"  ✓ Fear & Greed: {score} ({rating_he})")
        return {"score": score, "rating": rating_he, "rating_en": rating.lower()}
    except Exception as e:
        print(f"  ✗ Fear & Greed: {e}")
        return None

# ── Economic Calendar ──────────────────────────────────────────────────────────

ECONOMIC_EVENTS = [
    # ── אפריל 2026 ──
    {"date": "2026-04-09", "name": "CPI מרץ",          "type": "CPI",      "emoji": "📊"},
    {"date": "2026-04-10", "name": "פרוטוקול Fed",      "type": "FED",      "emoji": "🏦"},
    {"date": "2026-04-17", "name": "NFP / תעסוקה",      "type": "NFP",      "emoji": "💼"},
    {"date": "2026-04-23", "name": "PMI אפריל",         "type": "PMI",      "emoji": "🏭"},
    {"date": "2026-04-29", "name": "GDP Q1 2026",       "type": "GDP",      "emoji": "📈"},
    {"date": "2026-04-30", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
    # ── מאי 2026 ──
    {"date": "2026-05-08", "name": "NFP אפריל",         "type": "NFP",      "emoji": "💼"},
    {"date": "2026-05-12", "name": "CPI אפריל",         "type": "CPI",      "emoji": "📊"},
    {"date": "2026-05-27", "name": "PMI מאי",           "type": "PMI",      "emoji": "🏭"},
    # ── יוני 2026 ──
    {"date": "2026-06-05", "name": "NFP מאי",           "type": "NFP",      "emoji": "💼"},
    {"date": "2026-06-10", "name": "CPI מאי",           "type": "CPI",      "emoji": "📊"},
    {"date": "2026-06-17", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
    # ── יולי 2026 ──
    {"date": "2026-07-02", "name": "NFP יוני",          "type": "NFP",      "emoji": "💼"},
    {"date": "2026-07-10", "name": "CPI יוני",          "type": "CPI",      "emoji": "📊"},
    {"date": "2026-07-14", "name": "עונת דוחות Q2",     "type": "EARNINGS", "emoji": "💹"},
    {"date": "2026-07-29", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
    # ── אוגוסט 2026 ──
    {"date": "2026-08-07", "name": "NFP יולי",          "type": "NFP",      "emoji": "💼"},
    {"date": "2026-08-12", "name": "CPI יולי",          "type": "CPI",      "emoji": "📊"},
    # ── ספטמבר 2026 ──
    {"date": "2026-09-04", "name": "NFP אוגוסט",        "type": "NFP",      "emoji": "💼"},
    {"date": "2026-09-10", "name": "CPI אוגוסט",        "type": "CPI",      "emoji": "📊"},
    {"date": "2026-09-16", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
    # ── אוקטובר 2026 ──
    {"date": "2026-10-02", "name": "NFP ספטמבר",        "type": "NFP",      "emoji": "💼"},
    {"date": "2026-10-13", "name": "CPI ספטמבר",        "type": "CPI",      "emoji": "📊"},
    {"date": "2026-10-14", "name": "עונת דוחות Q3",     "type": "EARNINGS", "emoji": "💹"},
    {"date": "2026-11-05", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
    {"date": "2026-12-16", "name": "ישיבת FOMC",        "type": "FED",      "emoji": "🏦"},
]

EVENT_COLORS = {
    "FED":      ("var(--red)",    "rgba(239,68,68,0.15)"),
    "CPI":      ("var(--accent)", "rgba(14,165,233,0.15)"),
    "NFP":      ("var(--gold)",   "rgba(245,158,11,0.15)"),
    "EARNINGS": ("var(--green)",  "rgba(34,197,94,0.15)"),
    "GDP":      ("var(--purple)", "rgba(168,85,247,0.15)"),
    "PMI":      ("var(--muted)",  "rgba(100,116,139,0.15)"),
}

def get_upcoming_events(n: int = 5) -> list:
    today = date.today()
    upcoming = []
    for e in ECONOMIC_EVENTS:
        d = date.fromisoformat(e["date"])
        if d >= today:
            delta = (d - today).days
            e = dict(e)
            e["days_left"]  = delta
            e["days_label"] = "היום!" if delta == 0 else ("מחר" if delta == 1 else f"בעוד {delta} ימים")
            upcoming.append(e)
    return upcoming[:n]

# ── Sparkline SVG ──────────────────────────────────────────────────────────────

def sparkline_svg(prices: list, direction: str) -> str:
    """Render a tiny 52×22px sparkline SVG from a list of prices."""
    if len(prices) < 2:
        return ""
    mn, mx = min(prices), max(prices)
    if mx == mn: mx = mn + 1
    W, H, PAD = 52, 22, 2
    def px(i): return PAD + i * (W - 2*PAD) / (len(prices) - 1)
    def py(p): return H - PAD - (p - mn) / (mx - mn) * (H - 2*PAD)
    pts = " ".join(f"{px(i):.1f},{py(p):.1f}" for i, p in enumerate(prices))
    color = "#22c55e" if direction == "up" else "#ef4444"
    return (
        f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'style="display:block;overflow:visible">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )

# ── Image Fetching ─────────────────────────────────────────────────────────────

_FINANCE_IMG_KW = {
    "EARNINGS": "stock-market,earnings,finance",
    "MACRO":    "economy,federal-reserve,finance",
    "FED":      "federal-reserve,interest-rate,economy",
    "TECH":     "technology,nasdaq,silicon-valley",
    "M&A":      "business,merger,corporate",
    "ENERGY":   "energy,oil,pipeline",
    "CRYPTO":   "bitcoin,cryptocurrency,blockchain",
    "BANKS":    "bank,finance,wall-street",
    "NEWS":     "wall-street,stock-market,trading",
}

def _finance_img(tag: str = "", ticker: str = "", seed_text: str = "") -> str:
    """Return a relevant, unique finance image URL based on tag/ticker/title."""
    kw = _FINANCE_IMG_KW.get(tag, "wall-street,finance,stock-market")
    # Use full seed_text (title) for uniqueness — each article gets a different image
    seed = abs(hash(seed_text or ticker or tag or "finance")) % 9000 + 1000
    return f"https://loremflickr.com/400/220/{kw}?random={seed}"

def _picsum_url(seed_text: str) -> str:
    h = abs(hash(seed_text)) % 1000
    return f"https://picsum.photos/seed/{h}/400/200"

_URL_RE    = re.compile(r'https?://(?!nitter\.|twitter\.com|t\.co)[^\s\])"\']+', re.IGNORECASE)
_TICKER_RE = re.compile(r'(\$[A-Z]{1,5})\b')


def bold_tickers(text: str) -> str:
    """Wrap $TICKER patterns in a highlighted span."""
    return _TICKER_RE.sub(r'<strong class="ticker-hl">\1</strong>', text)
_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_ORIG_RE = re.compile(r'href=["\'](/pic/orig/[^"\']+)["\']', re.IGNORECASE)

def _extract_nitter_images(summary_html: str, instance: str) -> list:
    """Extract media image URLs from Nitter RSS entry HTML.
    Prefers /pic/orig/ (full-res) links; falls back to <img src>.
    Returns list of absolute URLs (may be empty)."""
    result = []
    # 1. Try full-resolution links first (/pic/orig/...)
    for path in _ORIG_RE.findall(summary_html):
        url = instance + path
        if url not in result:
            result.append(url)
    if result:
        return result
    # 2. Fall back to <img src="...">
    for src in _IMG_RE.findall(summary_html):
        # Skip profile avatars and tiny icons
        low = src.lower()
        if any(skip in low for skip in ("avatar", "profile_image", "emoji", "icon")):
            continue
        if src.startswith("/"):
            src = instance + src
        if src.startswith("http") and src not in result:
            result.append(src)
    return result

def fetch_og_image(url: str) -> str:
    if not HAS_REQUESTS or not url or url == "#":
        return _picsum_url(url or "default")
    try:
        resp = _requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsDashbot/1.0)"},
            timeout=5, stream=True,
        )
        html = b""
        for chunk in resp.iter_content(chunk_size=4096):
            html += chunk
            if len(html) >= 51200: break
        html_str = html.decode("utf-8", errors="replace")
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content="([^"]+)"',
        ]
        for pat in patterns:
            m = re.search(pat, html_str, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith("http"): return img
    except Exception:
        pass
    return _picsum_url(url)

def _best_image_url(item: dict) -> str:
    """Pick the best URL to fetch an og:image from (used when no direct tweet_image)."""
    # Prefer article URL extracted from tweet body
    article = item.get("article_url", "")
    if article:
        return article
    # Fall back to item link (works well for IL news from RSS)
    return item.get("link", "")

def fetch_all_images(news_items: list) -> list:
    """Fetch images for all news items.
    Priority: (1) tweet_image (actual photo from tweet), (2) og:image from article,
    (3) unique finance fallback based on title+tag+ticker.
    """
    if not news_items:
        return []

    # Separate items that already have a direct tweet image from those that need fetching
    need_fetch_idx = []
    need_fetch_urls = []
    for i, item in enumerate(news_items):
        if not item.get("tweet_image"):
            need_fetch_idx.append(i)
            need_fetch_urls.append(_best_image_url(item))

    # Parallel og:image fetch only for items without tweet_image
    fetched_map: dict = {}
    if need_fetch_urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            og_results = list(ex.map(fetch_og_image, need_fetch_urls))
        fetched_map = dict(zip(need_fetch_idx, og_results))

    final = []
    tweet_img_count = 0
    article_img_count = 0
    fallback_count = 0

    for i, item in enumerate(news_items):
        tweet_img = item.get("tweet_image", "")
        if tweet_img:
            # Use the actual photo/chart from the tweet
            final.append(tweet_img)
            tweet_img_count += 1
        else:
            img = fetched_map.get(i, "")
            if img and "picsum" not in img:
                final.append(img)
                article_img_count += 1
            else:
                # Unique fallback: combine title + ticker + tag as seed
                seed_text = (item.get("title_he") or item.get("title") or
                             item.get("ticker") or item.get("body", ""))[:120]
                img = _finance_img(item.get("tag", ""), item.get("ticker", ""), seed_text)
                final.append(img)
                fallback_count += 1

    print(f"  ✓ תמונות: {tweet_img_count} tweet · {article_img_count} מאמרים · {fallback_count} fallback")
    return final

# ── Twitter / Nitter RSS ──────────────────────────────────────────────────────

def _parse_tweet_date(date_str: str):
    """Parse Nitter RSS date string to an aware UTC datetime. Returns None on failure."""
    if not date_str:
        return None
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def _relative_time(date_str: str) -> str:
    """Convert a date string (RFC-2822 or ISO-8601) to a Hebrew relative time label."""
    dt = _parse_tweet_date(date_str)
    if not dt and date_str:
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if not dt:
        return ""
    diff = datetime.now(timezone.utc) - dt
    total_sec = diff.total_seconds()
    if total_sec < 0:
        return "עכשיו"
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)
    if h == 0:
        return f"לפני {m}ד׳" if m > 0 else "עכשיו"
    if h < 24:
        return f"לפני {h}ש׳"
    return f"לפני {h // 24}י׳"


def _try_nitter_feed(handle: str, instance: str) -> list:
    """Try fetching RSS for one handle from one Nitter instance. Returns [] on failure."""
    if not HAS_FEEDPARSER:
        return []
    try:
        url    = f"{instance}/{handle}/rss"
        parsed = feedparser.parse(url)
        if not parsed.entries:
            return []
        tweets = []
        for entry in parsed.entries[:5]:
            body = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            date = entry.get("published", "")
            if body and len(body) > 5:
                # Extract article URL and tweet images from summary HTML
                summary_html = entry.get("summary", "") or entry.get("description", "")
                combined = body + " " + summary_html
                # Article URL (non-twitter/nitter links in tweet body)
                article_urls = _URL_RE.findall(combined)
                article_url = article_urls[0][:300] if article_urls else ""
                # Tweet media images (charts, tables, photos)
                tweet_images = _extract_nitter_images(summary_html, instance)
                tweet_image = tweet_images[0] if tweet_images else ""
                pub_dt = _parse_tweet_date(date)
                tweets.append({
                    "handle":        handle,
                    "body":          body[:500],
                    "url":           link,
                    "date":          date,
                    "published_at":  pub_dt.isoformat() if pub_dt else "",
                    "article_url":   article_url,
                    "tweet_image":   tweet_image,
                    "relative_time": _relative_time(date),
                })
        return tweets
    except Exception:
        return []


def _fetch_one_handle(handle: str) -> list:
    """Try all Nitter instances for a handle until one succeeds."""
    for instance in NITTER_INSTANCES:
        tweets = _try_nitter_feed(handle, instance)
        if tweets:
            return tweets
    return []


def fetch_twitter_feeds(handles: list) -> list:
    """Fetch tweets for all handles in parallel via Nitter RSS. Filters to last 24h, sorted newest first."""
    if not handles:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_one_handle, handles))
    all_tweets = [t for sub in results for t in sub]
    ok = sum(1 for sub in results if sub)

    # Filter: keep only tweets from last 24 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = []
    for t in all_tweets:
        dt = _parse_tweet_date(t.get("date", ""))
        if dt is None or dt >= cutoff:
            recent.append(t)

    # Sort newest first
    def _dt_key(t):
        dt = _parse_tweet_date(t.get("date", ""))
        return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)
    recent.sort(key=_dt_key, reverse=True)

    print(f"  ✓ Twitter/Nitter: {ok}/{len(handles)} חשבונות · {len(recent)}/{len(all_tweets)} ציוצים (24ש׳)")
    return recent


# ── StockTwits & Ticker News ───────────────────────────────────────────────────

_ST_FALLBACK = ["AAPL","NVDA","TSLA","MSFT","AMZN","META","GOOGL","JPM","AMD","NFLX"]

def fetch_stocktwits_trending() -> list:
    """Get trending tickers from StockTwits. Falls back to popular list."""
    if not HAS_REQUESTS:
        return _ST_FALLBACK
    try:
        url  = "https://api.stocktwits.com/api/2/streams/trending.json"
        resp = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        resp.raise_for_status()
        symbols = [s["symbol"] for s in resp.json().get("symbols", [])][:10]
        if symbols:
            print(f"  ✓ StockTwits trending: {', '.join(symbols)}")
            return symbols
    except Exception as e:
        print(f"  ✗ StockTwits trending: {e}")
    return _ST_FALLBACK


def _fetch_st_posts_one(ticker: str) -> list:
    if not HAS_REQUESTS:
        return []
    try:
        url  = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        resp = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        messages.sort(key=lambda m: m.get("likes", {}).get("total", 0), reverse=True)
        posts = []
        for msg in messages[:3]:
            body = (msg.get("body") or "").strip()
            if body and len(body) > 20:
                posts.append({
                    "ticker": f"${ticker}",
                    "body":   body[:300],
                    "url":    f"https://stocktwits.com/message/{msg.get('id','')}",
                    "source": "StockTwits",
                })
        return posts
    except Exception:
        return []


def fetch_stocktwits_posts(tickers: list) -> list:
    """Fetch StockTwits posts for multiple tickers in parallel."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_st_posts_one, tickers))
    all_posts = [p for sub in results for p in sub]
    print(f"  ✓ StockTwits posts: {len(all_posts)} פוסטים")
    return all_posts


def _fetch_ticker_news_one(ticker: str) -> list:
    if not HAS_FEEDPARSER:
        return []
    try:
        url    = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        parsed = feedparser.parse(url)
        news   = []
        for entry in parsed.entries[:2]:
            title = (entry.get("title") or "").strip()
            link  = entry.get("link", "")
            if title:
                pub_dt = _parse_tweet_date(entry.get("published", ""))
                news.append({
                    "ticker":       f"${ticker}",
                    "title":        title,
                    "url":          link,
                    "published":    entry.get("published", ""),
                    "published_at": pub_dt.isoformat() if pub_dt else "",
                    "source":       "Yahoo Finance",
                })
        return news
    except Exception:
        return []


def fetch_ticker_news(tickers: list) -> list:
    """Fetch Yahoo Finance RSS headlines for specific tickers in parallel."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_ticker_news_one, tickers))
    all_news = [n for sub in results for n in sub]
    print(f"  ✓ Ticker news: {len(all_news)} כתבות")
    return all_news


# ── Watchlist (Finnhub) ────────────────────────────────────────────────────────

def load_watchlist() -> dict:
    """Load watchlist config from watchlist.json. Returns empty config on failure."""
    wl_path = Path(__file__).parent / "watchlist.json"
    try:
        with open(wl_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": [], "il_stocks": [], "alerts": {"price_move_pct": 5, "on_earnings_day": True}}


def _fetch_finnhub_one(stock: dict) -> dict:
    """Fetch real-time quote from Finnhub for one ticker. Returns stock dict enriched with price data."""
    ticker = stock.get("ticker", "")
    if not FINNHUB_KEY or not ticker or not HAS_REQUESTS:
        return {**stock, "price": 0, "change_pct": 0, "direction": "flat",
                "change_str": "N/A", "earnings_date": "", "is_earnings_today": False, "alert_triggered": False}
    try:
        r = _requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": FINNHUB_KEY},
            timeout=5,
        )
        d = r.json()
        price     = d.get("c", 0) or 0
        prev      = d.get("pc", 0) or 0
        pct       = round((price - prev) / prev * 100, 2) if prev else 0
        direction = "up" if pct > 0 else ("down" if pct < 0 else "flat")
        change_str = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
        return {**stock, "price": round(price, 2), "change_pct": pct,
                "direction": direction, "change_str": change_str,
                "earnings_date": "", "is_earnings_today": False, "alert_triggered": False}
    except Exception as e:
        print(f"  ✗ Finnhub {ticker}: {e}")
        return {**stock, "price": 0, "change_pct": 0, "direction": "flat",
                "change_str": "N/A", "earnings_date": "", "is_earnings_today": False, "alert_triggered": False}


def fetch_watchlist_data(stocks: list) -> list:
    """Parallel Finnhub quote fetch for all watchlist tickers."""
    if not stocks:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_finnhub_one, stocks))
    ok = sum(1 for r in results if r.get("price", 0) > 0)
    print(f"  ✓ Watchlist: {ok}/{len(results)} ציטוטים מ-Finnhub")
    return results


# ── Finance Relevance Filter ───────────────────────────────────────────────────

_FINANCE_KW = [
    # מניות / וול סטריט
    "stock","stocks","share","shares","market","markets","wall street","nasdaq","s&p","dow",
    "nyse","ipo","earnings","revenue","profit","loss","guidance","dividend","buyback",
    "short","long","rally","crash","surge","plunge","bull","bear","volatility","vix",
    "etf","fund","index","sector","upgrade","downgrade","price target","analyst",
    # טיקרים — סימן $
    "$",
    # קריפטו
    "bitcoin","btc","ethereum","eth","crypto","blockchain","defi","altcoin","coinbase","binance",
    "solana","sol","xrp","ripple","token","nft","web3",
    # מאקרו ארה"ב
    "fed","federal reserve","fomc","interest rate","rate hike","rate cut","inflation","cpi",
    "gdp","recession","unemployment","jobs report","nfp","payroll","treasury","yield","bond",
    "deficit","debt ceiling","tariff","trade war","sanctions","economy","economic",
    # מיקרו / חברות
    "merger","acquisition","m&a","ipo","spinoff","layoffs","ceo","cfo","earnings beat",
    "earnings miss","quarterly","revenue","forecast","outlook","guidance",
]

def is_finance_tweet(body: str) -> bool:
    """Return True if tweet is related to markets, stocks, crypto, or US macro/micro."""
    t = body.lower()
    return any(kw in t for kw in _FINANCE_KW)


def select_finance_tweets(tw_tweets: list, limit: int = 30) -> list:
    """Deterministic finance filter + cap. Used by both enrichment and source merging
    so [tN] ids in the prompt always refer to the same items."""
    finance = [t for t in tw_tweets if is_finance_tweet(t.get("body", ""))]
    if not finance:
        finance = tw_tweets
    return finance[:limit]


# ── Claude Enrichment Agent ────────────────────────────────────────────────────

def run_enrichment_agent(client: anthropic.Anthropic, tw_tweets: list, il_raw: list,
                          ticker_news_raw: list = None) -> dict:
    il_titles = "\n".join(f"[il{i+1}] [{item['source']}] {item['title']}" for i, item in enumerate(il_raw))

    ticker_section = ""
    if ticker_news_raw:
        tn_lines = "\n".join(
            f"[n{i+1}] [{n['ticker']}] {n['title']} ({n['source']})"
            for i, n in enumerate(ticker_news_raw[:15])
        )
        ticker_section = f"\n\nחדשות מניות ספציפיות ({len(ticker_news_raw[:15])}):\n{tn_lines}"

    system = f"""אתה אנליסט שוק הון בכיר — 20 שנה ב-Goldman Sachs ו-Morgan Stanley, כעת מגיש פינת שוק ב-Bloomberg בעברית.
היום: {TODAY_HE} ({TODAY}).
החזר אובייקט JSON בלבד — ללא markdown, ללא טקסט נוסף.

══════════════════════════════════════════════════════════
חלק א — פינת השוק שלך (us_news)
══════════════════════════════════════════════════════════

אתה לא מתרגם ולא מסכם. אתה מדבר בקול שלך — כמו אנליסט שנותן פרשנות חיה בשידור.

סנן קודם: כלול רק ציוצים עם מניות ספציפיות, נתונים קונקרטיים, אירועים שמזיזים שוק.
דלג על: דעות ללא עובדות, שאלות, "מה אתם חושבים", ניתוחים עמומים.
בחר עד 10 פריטים.

לכל פריט — כתוב כפי שהיית אומר בשידור חי:

── title_he ─────────────────────────────────────────────
הכותרת היא המשפט הפותח שלך בשידור. לא "מה קרה" — "מה אני חושב על זה".
דבר בגוף ראשון של הניתוח — אבל ללא "אני" — כמו chyron של Bloomberg:

  דוגמאות:
  ✓ "$NVDA: המספרים לא משקרים — ה-AI cycle עוד רחוק מפיק"
  ✓ "Fed היה hawk היום, אבל השוק החליט לשמוע מה שהוא רוצה"
  ✓ "$TSLA: שוליים שמתכווצים שוב — זה לא רק בעיה של מחירים"
  ✓ "ביטקוין שובר $70K ולזה יש סיבה אחת: המוסדיים חזרו"
  ✓ "$AAPL: מחיר יעד חדש $220 — האנליסטים עוקבים אחרי ה-services"
  ✗ "נבידיה פרסמה דוח רבעוני" ← זה כותרת עיתון, לא ניתוח
  ✗ "הפד קיים ישיבה" ← trivial, ללא עמדה

── summary_he ───────────────────────────────────────────
2-3 משפטים בקולך האנליטי. כאילו אתה מסביר לצופה חכם מה הסיפור האמיתי:
  • הכנס את כל המספרים (EPS, הכנסות, %, מחיר יעד, תחזית)
  • אמור מה זה אומר לשוק — לא רק מה קרה אלא למה זה חשוב עכשיו
  • אם יש סיכון או הזדמנות ספציפית — ציין אותה
  • שפה נגישה אבל מקצועית: "זה בדיוק מה ש...", "מה שמעניין כאן...", "הסיפור האמיתי הוא..."
  • אל תחזור על הכותרת — הרחב אותה

── שדות טכניים ──────────────────────────────────────────
src_id:  חובה! ה-id של פריט המקור ([t3] / [n2] / [il1]) — העתק אותו בדיוק
body_en: הציוץ המקורי מילה במילה, עד 200 תווים (ציטוט ישיר, לא תרגום)
ticker:  $AAPL / $BTC / ריק אם אין
tag:     EARNINGS / MACRO / FED / TECH / M&A / ENERGY / CRYPTO / BANKS / NEWS
link, source: כתובת + "@handle"

══════════════════════════════════════════════════════════
חלק ב — חדשות ישראל (israel_news)
══════════════════════════════════════════════════════════
כאן אתה עיתונאי רגיל, לא אנליסט פיננסי.
כותרת עברית נקייה + סיכום 2-3 משפטים + תגית: ביטחון / פוליטיקה / כלכלה / חברה / דיפלומטיה.

{{
  "us_news": [
    {{"src_id":"t3","title_he":"הקול האנליטי שלך","summary_he":"ניתוח עם מספרים ומשמעות","body_en":"verbatim tweet...","source":"@handle","link":"...","tag":"TECH","ticker":"$AAPL"}}
  ],
  "israel_news": [
    {{"src_id":"il1","title_he":"...","summary_he":"...","source":"...","link":"...","tag":"ביטחון"}}
  ]
}}
JSON בלבד."""

    # Pre-filter: keep only finance-relevant tweets before sending to Claude
    tw_sample = select_finance_tweets(tw_tweets)
    print(f"  ✓ פילטר פיננסי: {len(tw_sample)}/{len(tw_tweets)} ציוצים רלוונטיים")
    # Compact tweet lines with stable ids Claude must echo back as src_id
    tw_lines = "\n".join(
        f"[t{i+1}] @{t.get('handle','')}: {t.get('body','')} | link: {t.get('url','')}"
        for i, t in enumerate(tw_sample)
    )
    messages = [{"role": "user", "content":
        f"עבד נתונים ל-{TODAY_HE}. לכל פריט יש id בסוגריים — החזר אותו כ-src_id.\n\n"
        f"Twitter ({len(tw_sample)} ציוצים):\n{tw_lines}\n\n"
        f"ישראל ({len(il_raw)}):\n{il_titles}"
        + (ticker_section if ticker_section else "")
        + "\n\nהחזר JSON."}]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל Claude ({ENRICHMENT_MODEL})...")
    response = client.messages.create(model=ENRICHMENT_MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] stop_reason={response.stop_reason}")

    text_blocks = [b for b in response.content if b.type == "text"]
    if text_blocks:
        raw = text_blocks[-1].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw.strip())
    raise ValueError("לא התקבלה תגובה")


def merge_enriched_with_sources(news_data: dict, tw_sample: list, il_raw: list,
                                 ticker_news_raw: list = None) -> dict:
    """Join Claude's enriched items back to their source items by src_id.
    Recovers published_at, tweet_image, article_url, relative_time, link.
    Falls back to legacy URL/handle matching when src_id is missing/unknown."""
    source_index = {}
    for i, t in enumerate(tw_sample):
        source_index[f"t{i+1}"] = t
    for i, n in enumerate((ticker_news_raw or [])[:15]):
        source_index[f"n{i+1}"] = n
    for i, item in enumerate(il_raw):
        source_index[f"il{i+1}"] = item

    # Legacy fallback maps (url → tweet, handle → most recent tweet)
    url_to_tweet, handle_to_tweet = {}, {}
    for t in tw_sample:
        if t.get("url") and t["url"] not in url_to_tweet:
            url_to_tweet[t["url"]] = t
        h = f'@{t.get("handle","")}'.lower()
        if h not in handle_to_tweet:
            handle_to_tweet[h] = t

    matched = 0
    for n in news_data.get("us_news", []):
        src = source_index.get(n.get("src_id", ""))
        if not src:
            # Legacy matching by link/handle
            src = url_to_tweet.get(n.get("link", "")) or handle_to_tweet.get(n.get("source", "").lower())
        if src:
            matched += 1
            if not n.get("published_at"):  n["published_at"]  = src.get("published_at", "")
            if not n.get("tweet_image"):   n["tweet_image"]   = src.get("tweet_image", "")
            if not n.get("article_url"):   n["article_url"]   = src.get("article_url", "")
            if not n.get("relative_time"): n["relative_time"] = src.get("relative_time", "") or _relative_time(src.get("published_at", ""))
            if not n.get("link") or n["link"] == "#":
                n["link"] = src.get("url", "") or src.get("link", "")

    for n in news_data.get("israel_news", []):
        src = source_index.get(n.get("src_id", ""))
        if src:
            if not n.get("published_at"): n["published_at"] = src.get("published_at", "")
            if not n.get("link"):         n["link"]         = src.get("link", "")
    # Positional fallback for Israel items without src_id (legacy behavior)
    for i, n in enumerate(news_data.get("israel_news", [])):
        if not n.get("link") and i < len(il_raw):
            n["link"] = il_raw[i].get("link", "")
            if not n.get("published_at"):
                n["published_at"] = il_raw[i].get("published_at", "")

    print(f"  ✓ שיוך מקורות: {matched}/{len(news_data.get('us_news', []))} פריטי US")
    return news_data

# ── Fallback: RSS-only mode ────────────────────────────────────────────────────

def fallback_data(tw_tweets: list, il_raw: list) -> dict:
    def tr(text):
        if HAS_TRANSLATOR:
            try: return GoogleTranslator(source="en", target="iw").translate(text)
            except Exception: pass
        return text
    return {
        "us_news": [
            {
                "title_he":    tr(t["body"][:120]),
                "summary_he":  "",
                "ticker":      "",
                "source":      f'@{t["handle"]}',
                "link":        t.get("url", "#"),
                "tag":         "NEWS",
                "article_url": t.get("article_url", ""),
                "tweet_image": t.get("tweet_image", ""),
                "relative_time": t.get("relative_time", ""),
                "published_at": t.get("published_at", ""),
                "sentiment":   "neutral",
            }
            for t in tw_tweets if is_finance_tweet(t.get("body", ""))
        ],
        "israel_news": [{"title_he": tr(i["title"]), "summary_he": "", "source": i["source"],
                         "link": i["link"], "tag": "כללי",
                         "published_at": i.get("published_at", "")} for i in il_raw],
    }

# ── Tag Colors ─────────────────────────────────────────────────────────────────

TAG_COLORS_US = {
    "EARNINGS": ("var(--green)",  "rgba(34,197,94,0.12)"),
    "FED":      ("var(--red)",    "rgba(239,68,68,0.12)"),
    "TECH":     ("var(--purple)", "rgba(168,85,247,0.12)"),
    "M&A":      ("var(--gold)",   "rgba(245,158,11,0.12)"),
    "MACRO":    ("var(--accent)", "rgba(14,165,233,0.12)"),
    "ENERGY":   ("var(--gold)",   "rgba(245,158,11,0.12)"),
    "CRYPTO":   ("var(--purple)", "rgba(168,85,247,0.12)"),
    "BANKS":    ("var(--accent)", "rgba(14,165,233,0.12)"),
    "NEWS":     ("var(--muted)",  "rgba(100,116,139,0.12)"),
}
TAG_COLORS_IL = {
    "ביטחון":    ("var(--red)",    "rgba(239,68,68,0.12)"),
    "פוליטיקה":  ("var(--accent)", "rgba(14,165,233,0.12)"),
    "כלכלה":     ("var(--green)",  "rgba(34,197,94,0.12)"),
    "חברה":      ("var(--purple)", "rgba(168,85,247,0.12)"),
    "דיפלומטיה": ("var(--gold)",   "rgba(245,158,11,0.12)"),
    "כללי":      ("var(--muted)",  "rgba(100,116,139,0.12)"),
}

_WA_ICON = ('<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" '
            'fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099'
            '-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.'
            '255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134'
            '-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149'
            '-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-'
            '.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.'
            '198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571'
            '-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347'
            'z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.124.558 4.121 1.533 5.851L.057 23.215a.75'
            '.75 0 00.921.921l5.404-1.476A11.945 11.945 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 '
            '12 0zm0 21.75a9.732 9.732 0 01-5.1-1.439l-.366-.217-3.795 1.035 1.027-3.706-.238-.38'
            'A9.75 9.75 0 1112 21.75z"/></svg>')

def _arrow(d): return "▲" if d == "up" else "▼" if d == "down" else "–"
def _css(d):   return "up" if d == "up" else "down" if d == "down" else "flat"

# ── HTML Builders ──────────────────────────────────────────────────────────────

def build_ticker_items(data: dict) -> str:
    parts = []
    all_items = (data.get("market_us", []) + data.get("commodities", []) +
                 data.get("market_il", []))
    for m in all_items:
        d = m.get("direction", "flat")
        css = _css(d)
        parts.append(
            f'<span class="tick-item">'
            f'<span class="tick-name">{m["name"]}</span>'
            f'<span class="tick-val">{m["value"]}</span>'
            f'<span class="tick-chg {css}">{_arrow(d)} {m["change"]}</span>'
            f'</span>'
        )
    items_html = "".join(parts)
    # Double for seamless loop
    return items_html + items_html


def build_market_card(m: dict, sparkline: str = "") -> str:
    css = _css(m.get("direction", "flat"))
    arrow = _arrow(m.get("direction", "flat"))
    spark_html = f'<div class="spark">{sparkline}</div>' if sparkline else ""
    return (
        f'<div class="mkt-card">'
        f'<div class="mkt-name">{m["name"]}</div>'
        f'<div class="mkt-value">{m["value"]}</div>'
        f'<div class="mkt-row">'
        f'<div class="mkt-change {css}">{arrow} {m["change"]}</div>'
        f'{spark_html}'
        f'</div>'
        f'</div>'
    )


def build_fear_greed_card(fg: dict) -> str:
    if not fg: return ""
    score = fg["score"]
    label = fg["rating"]
    rating_en = fg.get("rating_en", "neutral")

    # Color based on rating
    if score <= 25:   needle_color, fill_color = "#ef4444", "rgba(239,68,68,0.15)"
    elif score <= 45: needle_color, fill_color = "#f97316", "rgba(249,115,22,0.15)"
    elif score <= 55: needle_color, fill_color = "#eab308", "rgba(234,179,8,0.15)"
    elif score <= 75: needle_color, fill_color = "#84cc16", "rgba(132,204,22,0.15)"
    else:             needle_color, fill_color = "#22c55e", "rgba(34,197,94,0.15)"

    # SVG gauge: half-circle arc, needle at angle
    # Score 0=left(-180deg), 100=right(0deg)
    # Angle: -180 + score*1.8 degrees from positive x-axis
    angle_deg = -180 + score * 1.8
    angle_rad = math.radians(angle_deg)
    cx, cy, r = 60, 55, 42
    nx = cx + (r - 8) * math.cos(angle_rad)
    ny = cy + (r - 8) * math.sin(angle_rad)

    svg = (
        f'<svg viewBox="0 0 120 65" width="120" height="65">'
        # Background arc segments
        f'<path d="M 18 55 A 42 42 0 0 1 102 55" fill="none" stroke="rgba(239,68,68,0.3)" stroke-width="8" stroke-linecap="round"/>'
        f'<path d="M 18 55 A 42 42 0 0 1 60 13" fill="none" stroke="rgba(234,179,8,0.3)" stroke-width="8" stroke-linecap="round"/>'
        f'<path d="M 60 13 A 42 42 0 0 1 102 55" fill="none" stroke="rgba(34,197,94,0.3)" stroke-width="8" stroke-linecap="round"/>'
        # Active needle
        f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
        f'stroke="{needle_color}" stroke-width="2.5" stroke-linecap="round"/>'
        f'<circle cx="{cx}" cy="{cy}" r="3" fill="{needle_color}"/>'
        # Score text
        f'<text x="{cx}" y="{cy+14}" text-anchor="middle" font-size="13" font-weight="700" '
        f'fill="#f1f5f9">{score}</text>'
        f'</svg>'
    )

    return (
        f'<div class="fg-card" style="background:{fill_color};border-color:{needle_color}40">'
        f'<div class="fg-title">😱 פחד &amp; חמדנות</div>'
        f'{svg}'
        f'<div class="fg-label" style="color:{needle_color}">{label}</div>'
        f'</div>'
    )


def build_heatmap(sectors: list) -> str:
    if not sectors: return ""
    cells = []
    for s in sectors:
        pct = s.get("pct_raw", 0.0)
        # Color intensity
        if pct >= 2:    bg, tc = "rgba(34,197,94,0.45)",  "#bbf7d0"
        elif pct >= 1:  bg, tc = "rgba(34,197,94,0.25)",  "#86efac"
        elif pct >= 0:  bg, tc = "rgba(34,197,94,0.10)",  "#4ade80"
        elif pct >= -1: bg, tc = "rgba(239,68,68,0.10)",  "#fca5a5"
        elif pct >= -2: bg, tc = "rgba(239,68,68,0.25)",  "#f87171"
        else:           bg, tc = "rgba(239,68,68,0.45)",  "#ef4444"
        chg_css = "up" if pct >= 0 else "down"
        cells.append(
            f'<div class="heat-cell" style="background:{bg}">'
            f'<div class="heat-name">{s["name"]}</div>'
            f'<div class="heat-chg {chg_css}">{s["change"]}</div>'
            f'</div>'
        )
    return "".join(cells)


def build_calendar_strip(events: list) -> str:
    if not events: return ""
    pills = []
    for e in events:
        color, bg = EVENT_COLORS.get(e["type"], ("var(--muted)", "rgba(100,116,139,0.15)"))
        urgent = ' style="border-color:currentColor;opacity:1"' if e["days_left"] <= 1 else ""
        pills.append(
            f'<div class="cal-pill"{urgent} style="color:{color};background:{bg}">'
            f'<span class="cal-emoji">{e["emoji"]}</span>'
            f'<div class="cal-info">'
            f'<div class="cal-name">{e["name"]}</div>'
            f'<div class="cal-days">{e["days_label"]}</div>'
            f'</div>'
            f'</div>'
        )
    return "".join(pills)


def _wa_link(title: str, link: str) -> str:
    text = urllib.parse.quote(f"{title}\n{link}")
    return (
        f'<a href="https://wa.me/?text={text}" target="_blank" class="wa-btn" title="שתף ב-WhatsApp">'
        f'{_WA_ICON}</a>'
    )


def build_us_news_card(n: dict, idx: int) -> str:
    tag       = n.get("tag", "NEWS")
    color, bg = TAG_COLORS_US.get(tag, TAG_COLORS_US["NEWS"])
    link      = n.get("link", "#")
    ticker    = n.get("ticker", "")
    body_en   = n.get("body_en", "")
    sentiment = n.get("sentiment", "neutral")
    image     = n.get("image", "")
    rel_time  = n.get("relative_time", "")

    ticker_badge = f'<span class="ticker-badge">{ticker}</span>' if ticker else ""
    en_block     = (f'<blockquote class="news-en" dir="ltr">{body_en[:200]}</blockquote>' if body_en else "")
    read_more    = f'<a href="{link}" target="_blank" class="read-more">מקור ←</a>' if link and link != "#" else ""
    wa           = _wa_link(n.get("title_he",""), link) if link and link != "#" else ""
    time_span    = f'<span class="card-time">{rel_time}</span>' if rel_time else ""

    img_html = ""
    if image:
        img_html = (
            f'<div class="card-img-wrap">'
            f'<img class="news-img" src="{image}" alt="" onerror="this.style.display=\'none\'" loading="lazy"/>'
            f'<div class="card-img-overlay"></div>'
            f'<span class="card-num">{idx:02d}</span>'
            f'</div>'
        )

    title_rendered   = bold_tickers(n.get("title_he", ""))
    summary_rendered = bold_tickers(n.get("summary_he", "")) if n.get("summary_he") else ""

    return (
        f'<article class="news-card" data-sentiment="{sentiment}">'
        + img_html
        + f'<div class="card-content">'
        + f'<div class="card-top"><span class="news-tag" style="color:{color};background:{bg}">{tag}</span>{ticker_badge}<span class="sentiment-dot {sentiment}"></span></div>'
        + f'<h3 class="news-title" dir="rtl">{title_rendered}</h3>'
        + (f'<p class="news-summary" dir="rtl">{summary_rendered}</p>' if summary_rendered else "")
        + en_block
        + f'<div class="card-meta"><span class="card-source">{n.get("source","")}</span>{time_span}{read_more}{wa}</div>'
        + f'</div>'
        + f'</article>'
    )


def build_il_news_card(n: dict) -> str:
    tag = n.get("tag", "כללי")
    color, bg = TAG_COLORS_IL.get(tag, TAG_COLORS_IL["כללי"])
    link  = n.get("link", "#")
    image = n.get("image", "")
    img_html = ""
    if image:
        img_html = (
            f'<div class="card-img-wrap">'
            f'<img class="news-img" src="{image}" alt="" onerror="this.style.display=\'none\'" loading="lazy"/>'
            f'<div class="card-img-overlay"></div>'
            f'</div>'
        )
    read_more = f'<a href="{link}" target="_blank" class="read-more">קרא עוד ←</a>' if link and link != "#" else ""
    wa        = _wa_link(n.get("title_he",""), link) if link and link != "#" else ""
    return (
        f'<div class="news-card il-card">'
        + img_html
        + f'<div class="card-content">'
        + f'<div class="card-top"><span class="news-tag" style="color:{color};background:{bg}">{tag}</span></div>'
        + f'<h3 class="news-title">{n["title_he"]}</h3>'
        + (f'<p class="news-summary">{n["summary_he"]}</p>' if n.get("summary_he") else "")
        + f'<div class="card-meta"><span class="card-source">{n.get("source","")}</span>{read_more}{wa}</div>'
        + f'</div>'
        + f'</div>'
    )


def build_twitter_card(tweet: dict) -> str:
    handle   = tweet.get("handle", "")
    body     = tweet.get("body", "")
    url      = tweet.get("url", "#")
    rel_time = tweet.get("relative_time", "")
    read_more = f'<a href="{url}" target="_blank" class="read-more">X ←</a>' if url and url != "#" else ""
    wa        = _wa_link(body, url) if url and url != "#" else ""
    time_span = f'<span class="card-time">{rel_time}</span>' if rel_time else ""
    return (
        f'<div class="news-card tw-card">'
        f'<div class="card-content">'
        f'<div class="card-top"><span class="tw-handle">@{handle}</span></div>'
        f'<h3 class="news-title" dir="ltr">{body}</h3>'
        f'<div class="card-meta">{time_span}{read_more}{wa}</div>'
        f'</div>'
        f'</div>'
    )


def _news_sort_key(n: dict) -> str:
    """Sort key: ISO published_at desc; items without a timestamp sink to the bottom."""
    return n.get("published_at") or "0000"


def build_news_feed_tab(us_news: list, il_news: list) -> str:
    """Tab 1: merged news feed sorted newest-first + Israel secondary section."""
    feed = sorted(us_news, key=_news_sort_key, reverse=True)
    feed_cards = "".join(build_us_news_card(n, i + 1) for i, n in enumerate(feed))
    il_sorted  = sorted(il_news, key=_news_sort_key, reverse=True)
    il_cards   = "".join(build_il_news_card(n) for n in il_sorted)
    il_section = (
        f'<section class="section il-section" id="israel">'
        f'<div class="section-label">🇮🇱 חדשות ישראל</div>'
        f'<div class="news-list">{il_cards}</div>'
        f'</section>'
    ) if il_cards else ""
    empty = '<div class="feed-empty">אין חדשות זמינות כרגע — נסה שוב בריצה הבאה</div>'
    return (
        f'<section class="section" id="feed">'
        f'<div class="section-label">📰 פיד חדשות — מהחדש לישן</div>'
        f'<div class="news-list feed-list">{feed_cards or empty}</div>'
        f'</section>'
        + il_section
    )


def build_opportunities_tab(data: dict, ta_cards_html: str = "") -> str:
    """Tab 2: technical opportunities + macro widgets (calendar, markets, F&G, heatmap)."""
    sparks  = data.get("sparklines", {})
    sym_map = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow Jones": "^DJI",
               "Russell 2000": "^RUT", "זהב": "GC=F", "ביטקוין": "BTC-USD"}

    def mkt_card(m):
        svg = sparkline_svg(sparks.get(sym_map.get(m["name"], ""), []), m.get("direction", "flat"))
        return build_market_card(m, svg)

    us_cards   = "".join(mkt_card(m) for m in data.get("market_us", []))
    comm_cards = "".join(mkt_card(m) for m in data.get("commodities", []))
    il_cards   = "".join(build_market_card(m) for m in data.get("market_il", []))
    fg_card    = build_fear_greed_card(data.get("fear_greed"))
    heat       = build_heatmap(data.get("sectors", []))
    cal        = build_calendar_strip(data.get("events", []))

    ta_section = (
        f'<section class="section" id="ta">'
        f'<div class="section-label">🎯 הזדמנויות טכניות</div>'
        f'{ta_cards_html}'
        f'</section>'
    ) if ta_cards_html else ""

    return (
        ta_section
        + (f'<div class="section-label">📅 אירועים כלכליים קרובים</div><div class="cal-strip">{cal}</div>' if cal else "")
        + f'<section class="section" id="us">'
        + f'<div class="section-label">📈 מדדים אמריקאיים</div>'
        + f'<div class="mkt-strip">{us_cards}</div>'
        + f'<div class="mkt-strip" style="margin-top:.75rem">{comm_cards}</div>'
        + f'</section>'
        + f'<div class="fg-il-row">{fg_card}'
        + f'<div style="flex:1"><div class="section-label" style="margin-bottom:.7rem">🏦 שוק ישראלי</div>'
        + f'<div class="mkt-strip il">{il_cards}</div></div></div>'
        + (f'<section class="section" id="heatmap"><div class="section-label">🌡 מפת מגזרים — S&P 500</div><div class="heatmap-grid">{heat}</div></section>' if heat else "")
    )


def build_watchlist_card(stock: dict) -> str:
    ticker     = stock.get("ticker", "")
    name       = stock.get("name", ticker)
    price      = stock.get("price", 0)
    change_pct = stock.get("change_pct", 0)
    change_str = stock.get("change_str", "N/A")
    direction  = stock.get("direction", "flat")
    is_earn    = stock.get("is_earnings_today", False)
    earn_date  = stock.get("earnings_date", "")
    alerted    = stock.get("alert_triggered", False)

    css      = _css(direction)
    arrow    = _arrow(direction)
    price_str = f"${price:,.2f}" if price else "—"

    earnings_badge = ""
    if is_earn:
        earnings_badge = '<span class="earn-badge earn-today">דוח היום</span>'
    elif earn_date:
        try:
            ed = datetime.strptime(earn_date, "%Y-%m-%d").date()
            days = (ed - date.today()).days
            if 0 < days <= 14:
                earnings_badge = f'<span class="earn-badge">דוח בעוד {days} ימים</span>'
        except Exception:
            pass

    alert_dot = '<span class="alert-dot" title="התראה הופעלה"></span>' if alerted else ""

    return (
        f'<div class="wl-card">'
        f'<div class="wl-top">'
        f'<span class="wl-ticker">{ticker}</span>'
        f'{alert_dot}'
        f'<span class="wl-name">{name}</span>'
        f'</div>'
        f'<div class="wl-price">{price_str}</div>'
        f'<div class="wl-change {css}">{arrow} {change_str}</div>'
        f'{earnings_badge}'
        f'</div>'
    )


def build_watchlist_tab(watchlist: list) -> str:
    if not watchlist:
        return '<div class="wl-empty">הגדר מניות ב-watchlist.json כדי לראות נתונים כאן</div>'
    cards = "".join(build_watchlist_card(s) for s in watchlist)
    return f'<div class="wl-grid">{cards}</div>'


def build_html(data: dict) -> str:
    ticker_items = build_ticker_items(data)
    news_tab     = build_news_feed_tab(data.get("us_news", []), data.get("israel_news", []))
    opps_tab     = build_opportunities_tab(data, data.get("ta_cards_html", ""))

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>לוח חדשות — {TODAY}</title>
<link rel="manifest" href="manifest.json"/>
<meta name="theme-color" content="#080c10"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;700;800;900&family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
  :root {{
    --bg:      #080c10;
    --surface: #0d1117;
    --card:    #111820;
    --border:  #1c2a38;
    --accent:  #06b6d4;
    --green:   #10b981;
    --red:     #f43f5e;
    --gold:    #f59e0b;
    --purple:  #8b5cf6;
    --text:    #94a3b8;
    --muted:   #475569;
    --white:   #f0f6ff;
  }}
  body.light {{
    --bg:      #f8fafc;
    --surface: #f1f5f9;
    --card:    #ffffff;
    --border:  #e2e8f0;
    --accent:  #0284c7;
    --green:   #059669;
    --red:     #e11d48;
    --gold:    #d97706;
    --purple:  #7c3aed;
    --text:    #334155;
    --muted:   #94a3b8;
    --white:   #0f172a;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter','Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.65;min-height:100vh;transition:background .3s,color .3s}}

  /* ── Sticky Nav ── */
  .nav{{
    position:sticky;top:0;z-index:100;
    background:rgba(7,11,15,0.93);
    backdrop-filter:blur(14px);
    border-bottom:1px solid var(--border);
    padding:.5rem 1.2rem;
    display:flex;gap:.6rem;align-items:center;justify-content:center;
  }}
  body.light .nav{{background:rgba(248,250,252,0.93)}}
  .nav a{{color:var(--muted);text-decoration:none;font-size:.78rem;font-weight:600;
    padding:.3rem .75rem;border-radius:6px;border:1px solid transparent;transition:all .2s}}
  .nav a:hover{{color:var(--accent);border-color:var(--accent);background:rgba(14,165,233,.08)}}
  .nav-sep{{color:var(--border);font-size:.85rem}}
  .theme-btn{{
    margin-right:auto;background:none;border:1px solid var(--border);border-radius:6px;
    color:var(--muted);cursor:pointer;font-size:.85rem;padding:.28rem .6rem;
    transition:all .2s;
  }}
  body[dir=rtl] .theme-btn{{margin-right:0;margin-left:auto}}
  .theme-btn:hover{{border-color:var(--accent);color:var(--accent)}}

  /* ── Ticker Tape ── */
  .ticker-wrap{{
    overflow:hidden;white-space:nowrap;
    background:rgba(14,165,233,.04);
    border-bottom:1px solid var(--border);
    height:34px;display:flex;align-items:center;
    cursor:default;
  }}
  .ticker-track{{
    display:inline-flex;align-items:center;
    animation:ticker-scroll 55s linear infinite;
    will-change:transform;
  }}
  .ticker-wrap:hover .ticker-track{{animation-play-state:paused}}
  @keyframes ticker-scroll{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}
  .tick-item{{padding:0 1.4rem;display:inline-flex;align-items:center;gap:.45rem;font-size:.75rem;font-weight:500;border-right:1px solid var(--border)}}
  .tick-name{{color:var(--muted)}}
  .tick-val{{color:var(--white);font-weight:700;font-variant-numeric:tabular-nums}}
  .tick-chg{{font-weight:600;font-size:.72rem}}

  /* ── Hero ── */
  .hero{{
    background:linear-gradient(160deg,#070b0f 0%,#0c1929 50%,#070b0f 100%);
    border-bottom:1px solid var(--border);
    padding:2.5rem 2rem 2rem;text-align:center;position:relative;overflow:hidden;
  }}
  body.light .hero{{background:linear-gradient(160deg,#f8fafc 0%,#e0f2fe 50%,#f8fafc 100%)}}
  .hero::before{{content:'';position:absolute;top:-60px;left:50%;transform:translateX(-50%);
    width:600px;height:200px;background:radial-gradient(ellipse,rgba(14,165,233,.12) 0%,transparent 70%);pointer-events:none}}
  .hero-label{{font-size:.65rem;font-weight:700;letter-spacing:.35em;color:var(--accent);text-transform:uppercase;margin-bottom:.6rem}}
  .hero h1{{font-family:'Heebo',sans-serif;font-size:clamp(1.8rem,5vw,3rem);font-weight:900;color:var(--white);letter-spacing:-.04em;line-height:1.1}}
  .hero h1 span{{color:var(--accent)}}
  .hero-sub{{color:var(--muted);font-size:.88rem;margin-top:.5rem}}
  .hero-date{{display:inline-flex;align-items:center;gap:.5rem;margin-top:1rem;
    background:rgba(14,165,233,.1);border:1px solid rgba(14,165,233,.3);
    color:var(--accent);padding:.35rem 1.1rem;border-radius:999px;font-size:.8rem;font-weight:600}}
  .pulse{{width:6px;height:6px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(1.5)}}}}

  /* ── Layout ── */
  .container{{max-width:1140px;margin:0 auto;padding:1.8rem 1.4rem 5rem}}
  .section{{margin-bottom:2.6rem}}
  .section-label{{font-size:.65rem;font-weight:700;letter-spacing:.28em;text-transform:uppercase;
    color:var(--accent);margin-bottom:1.1rem;padding-bottom:.55rem;
    border-bottom:1px solid var(--border);display:flex;align-items:center;gap:.5rem}}

  /* ── Calendar Strip ── */
  .cal-strip{{display:flex;gap:.7rem;flex-wrap:wrap;margin-bottom:2rem}}
  .cal-pill{{display:flex;align-items:center;gap:.6rem;padding:.55rem .9rem;
    border-radius:10px;border:1px solid transparent;min-width:140px;flex:1;}}
  .cal-emoji{{font-size:1.1rem}}
  .cal-name{{font-size:.8rem;font-weight:600;color:var(--white)}}
  .cal-days{{font-size:.7rem;color:var(--muted);margin-top:.1rem}}

  /* ── Market Strips ── */
  .mkt-strip{{display:flex;flex-wrap:wrap;gap:.8rem;margin-bottom:.5rem}}
  .mkt-card{{background:var(--card);border:1px solid var(--border);border-radius:10px;
    padding:.9rem 1.1rem;min-width:120px;flex:1;transition:border-color .2s,background .2s}}
  .mkt-card:hover{{border-color:var(--accent);background:linear-gradient(135deg,var(--card),rgba(6,182,212,.04))}}
  .mkt-name{{font-size:.68rem;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.25rem}}
  .mkt-value{{font-size:1.4rem;font-weight:700;color:var(--white);font-variant-numeric:tabular-nums;font-family:'Inter',monospace}}
  .mkt-row{{display:flex;align-items:center;justify-content:space-between;margin-top:.2rem}}
  .mkt-change{{font-size:.82rem;font-weight:600}}
  .spark{{opacity:.8}}
  .up{{color:var(--green)}}.down{{color:var(--red)}}.flat{{color:var(--muted)}}

  /* ── Fear & Greed + IL row ── */
  .fg-il-row{{display:flex;gap:.8rem;flex-wrap:wrap;margin-bottom:2.6rem;align-items:stretch}}
  .fg-card{{background:var(--card);border:1px solid var(--border);border-radius:10px;
    padding:.9rem 1.1rem;display:flex;flex-direction:column;align-items:center;gap:.3rem;
    min-width:150px;flex:0 0 auto}}
  .fg-title{{font-size:.65rem;font-weight:700;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}}
  .fg-label{{font-size:.8rem;font-weight:700;margin-top:.2rem}}
  .il-strip{{display:flex;gap:.8rem;flex:1;flex-wrap:wrap}}

  /* ── Heatmap ── */
  .heatmap-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:.45rem}}
  @media(max-width:500px){{.heatmap-grid{{grid-template-columns:repeat(3,1fr)}}}}
  .heat-cell{{border-radius:8px;padding:.7rem .5rem;text-align:center;transition:opacity .2s}}
  .heat-cell:hover{{opacity:.85}}
  .heat-name{{font-size:.72rem;font-weight:600;color:var(--white);margin-bottom:.2rem}}
  .heat-chg{{font-size:.78rem;font-weight:700}}

  /* ── News Cards ── */
  .news-list{{display:flex;flex-direction:column;gap:1rem}}
  .news-card{{
    background:var(--card);border:1px solid var(--border);border-radius:14px;
    overflow:hidden;
    transition:transform .2s,border-color .2s,box-shadow .2s;
  }}
  .news-card:hover{{
    transform:translateY(-2px);
    border-color:var(--accent);
    box-shadow:0 8px 32px rgba(6,182,212,.08);
  }}
  /* Sentiment left border (RTL = border-right) */
  .news-card[data-sentiment="bullish"]{{border-right:3px solid var(--green)}}
  .news-card[data-sentiment="bearish"]{{border-right:3px solid var(--red)}}
  .news-card[data-sentiment="neutral"]{{border-right:3px solid var(--border)}}

  .il-card{{border-right:3px solid var(--border)}}

  /* Image with gradient overlay */
  .card-img-wrap{{position:relative;height:165px;overflow:hidden;flex-shrink:0}}
  .news-img{{width:100%;height:100%;object-fit:cover;display:block;background:var(--border)}}
  .card-img-overlay{{
    position:absolute;inset:0;
    background:linear-gradient(to bottom,transparent 40%,var(--card) 100%);
  }}
  .card-num{{
    position:absolute;top:.7rem;left:.8rem;
    font-size:.65rem;font-weight:800;color:rgba(240,246,255,.35);
    font-family:'Inter',monospace;letter-spacing:.12em;
  }}
  .card-content{{padding:1rem 1.3rem 1.1rem}}
  .card-top{{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-bottom:.45rem}}
  .news-tag{{display:inline-block;padding:.12rem .55rem;border-radius:4px;
    font-size:.65rem;font-weight:700;letter-spacing:.05em}}
  .ticker-badge{{
    display:inline-block;
    background:rgba(6,182,212,.15);
    color:#22d3ee;
    border:1px solid rgba(6,182,212,.35);
    border-radius:5px;
    padding:.12rem .55rem;
    font-size:.75rem;
    font-weight:800;
    font-family:'Inter',monospace;
    letter-spacing:.04em;
  }}
  /* Sentiment dot */
  .sentiment-dot{{width:7px;height:7px;border-radius:50%;margin-right:auto;flex-shrink:0}}
  .sentiment-dot.bullish{{background:var(--green);box-shadow:0 0 6px var(--green)}}
  .sentiment-dot.bearish{{background:var(--red);box-shadow:0 0 6px var(--red)}}
  .sentiment-dot.neutral{{background:var(--muted)}}

  .news-title{{font-family:'Heebo',sans-serif;font-size:1.06rem;font-weight:800;
    color:var(--white);line-height:1.42;margin-bottom:.35rem}}
  .news-summary{{font-size:.86rem;color:var(--text);margin-bottom:.5rem;line-height:1.65}}
  .news-en{{
    font-size:.74rem;color:var(--muted);font-style:italic;
    border-right:2px solid var(--border);
    padding:.3rem .65rem;margin:.4rem 0 .55rem;
    line-height:1.5;font-family:'Inter',sans-serif;
    text-align:left;
  }}
  .card-meta{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;font-size:.72rem;color:var(--muted)}}
  .card-source{{font-weight:600;color:var(--accent)}}
  .card-time{{font-variant-numeric:tabular-nums;color:var(--muted)}}
  .news-meta{{font-size:.72rem;color:var(--muted);display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;padding:.9rem 1.3rem}}
  .read-more{{color:var(--accent);text-decoration:none;font-weight:600;font-size:.72rem}}
  .read-more:hover{{text-decoration:underline}}
  .wa-btn{{color:#25d366;display:inline-flex;align-items:center;opacity:.8;transition:opacity .2s}}
  .wa-btn:hover{{opacity:1}}
  /* ── Twitter Cards ── */
  .tw-card{{border-right:3px solid rgba(14,165,233,.35)}}
  .tw-handle{{
    display:inline-block;
    color:var(--accent);
    font-weight:700;
    font-size:.75rem;
    font-family:monospace;
    margin-bottom:.3rem;
    letter-spacing:.02em;
  }}

  /* ── IL strip card (smaller) ── */
  .mkt-strip.il .mkt-card{{min-width:130px;max-width:190px;flex:0 0 auto}}

  /* ── Ticker highlight ── */
  .ticker-hl{{color:#38bdf8;font-weight:800;font-family:'Inter',monospace;font-style:normal}}

  /* ── News feed ── */
  .feed-empty{{color:var(--muted);text-align:center;padding:2.5rem 1rem;font-size:.9rem;
    background:var(--card);border:1px dashed var(--border);border-radius:14px}}
  .il-section{{margin-top:2.6rem;padding-top:1.4rem;border-top:1px solid var(--border)}}

  /* ── Tab Navigation ── */
  .tab-nav{{display:flex;gap:.5rem;margin:1.5rem 0 1rem;border-bottom:1px solid var(--border);padding-bottom:0}}
  .tab-btn{{background:none;border:none;padding:.65rem 1.3rem;font-size:.95rem;font-weight:700;
    color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;
    transition:color .2s,border-color .2s;font-family:'Heebo',sans-serif}}
  .tab-btn.active{{color:var(--accent);border-bottom-color:var(--accent)}}
  .tab-btn:hover:not(.active){{color:var(--white)}}
  .tab-pane{{display:none}}
  .tab-pane.active{{display:block}}

  /* ── Watchlist Grid ── */
  .wl-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.85rem;padding:.5rem 0 1.5rem}}
  .wl-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:1.1rem 1.2rem;transition:border-color .2s;position:relative}}
  .wl-card:hover{{border-color:var(--accent)}}
  .wl-top{{display:flex;align-items:center;gap:.5rem;margin-bottom:.55rem}}
  .wl-ticker{{font-family:'Inter',monospace;font-weight:900;font-size:.95rem;color:var(--white)}}
  .wl-name{{font-size:.72rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .wl-price{{font-size:1.45rem;font-weight:800;color:var(--white);font-family:'Inter',monospace;
    letter-spacing:-.02em;margin-bottom:.3rem}}
  .wl-change{{font-size:.82rem;font-weight:700}}
  .wl-change.up{{color:var(--green)}}
  .wl-change.down{{color:var(--red)}}
  .wl-change.flat{{color:var(--muted)}}
  .earn-badge{{display:inline-block;margin-top:.5rem;font-size:.67rem;font-weight:700;
    padding:.15rem .5rem;border-radius:4px;background:rgba(245,158,11,.15);color:var(--gold)}}
  .earn-badge.earn-today{{background:rgba(239,68,68,.15);color:var(--red)}}
  .alert-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;
    background:var(--red);box-shadow:0 0 6px var(--red);flex-shrink:0}}
  .wl-empty{{color:var(--muted);text-align:center;padding:2rem;font-size:.9rem}}

  /* ── Footer ── */
  footer{{text-align:center;padding:2rem;font-size:.72rem;color:var(--muted);border-top:1px solid var(--border)}}
  footer a{{color:var(--accent);text-decoration:none}}
  footer a:hover{{text-decoration:underline}}

  @media(max-width:600px){{
    .hero h1{{font-size:1.65rem}}
    .mkt-card{{min-width:105px}}
    .card-img-wrap{{height:130px}}
    .heatmap-grid{{grid-template-columns:repeat(3,1fr)}}
    .fg-il-row{{flex-direction:column}}
    .cal-strip{{gap:.5rem}}
    .cal-pill{{min-width:120px}}
    .wl-grid{{grid-template-columns:repeat(2,1fr)}}
  }}
</style>
</head>
<body>

<!-- Sticky Nav -->
<nav class="nav">
  <button class="theme-btn" onclick="toggleTheme()" id="themeBtn" title="החלף מצב">🌙</button>
  <a href="#feed" onclick="showTab('news')">📰 חדשות</a>
  <span class="nav-sep">|</span>
  <a href="#ta" onclick="showTab('opps')">🎯 הזדמנויות</a>
  <span class="nav-sep">|</span>
  <a href="report.html">דוח מלא</a>
</nav>

<!-- Ticker Tape -->
<div class="ticker-wrap">
  <div class="ticker-track">{ticker_items}</div>
</div>

<!-- Hero -->
<div class="hero">
  <div class="hero-label">לוח חדשות אישי</div>
  <h1>שוק ההון <span>&</span> ישראל</h1>
  <div class="hero-sub">וול סטריט · חברות אמריקאיות · חדשות ישראל</div>
  <div class="hero-date">
    <span class="pulse"></span>
    {TODAY_HE} &nbsp;|&nbsp; עודכן {TIME}
  </div>
</div>

<div class="container">

  <!-- Tab Navigation -->
  <div class="tab-nav">
    <button class="tab-btn active" id="tab-news-btn" onclick="showTab('news')">📰 חדשות</button>
    <button class="tab-btn" id="tab-opps-btn" onclick="showTab('opps')">🎯 הזדמנויות</button>
  </div>

  <!-- ── News Tab ── -->
  <div class="tab-pane active" id="tab-news">
    {news_tab}
  </div>

  <!-- ── Opportunities Tab ── -->
  <div class="tab-pane" id="tab-opps">
    {opps_tab}
  </div>

</div>

<footer>
  נוצר ב-{TODAY} בשעה {TIME} &nbsp;·&nbsp;
  <a href="report.html">דוח שוק ההון המלא</a> &nbsp;·&nbsp;
  אינו מהווה ייעוץ השקעות
</footer>

<script>
  // ── Dark/Light Mode ──
  (function(){{
    var saved = localStorage.getItem('theme');
    if(saved === 'light'){{
      document.body.classList.add('light');
      document.getElementById('themeBtn').textContent = '☀️';
    }}
  }})();

  function toggleTheme(){{
    var light = document.body.classList.toggle('light');
    document.getElementById('themeBtn').textContent = light ? '☀️' : '🌙';
    localStorage.setItem('theme', light ? 'light' : 'dark');
  }}

  // ── Tab Navigation ──
  var TABS = ['news', 'opps'];
  function showTab(name) {{
    if (TABS.indexOf(name) === -1) name = 'news';
    document.querySelectorAll('.tab-pane').forEach(function(p) {{ p.classList.remove('active'); }});
    document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
    document.getElementById('tab-' + name).classList.add('active');
    document.getElementById('tab-' + name + '-btn').classList.add('active');
    localStorage.setItem('activeTab', name);
  }}
  (function() {{
    var t = localStorage.getItem('activeTab');
    if (t && t !== 'news' && TABS.indexOf(t) !== -1) showTab(t);
  }})();

  // ── PWA Service Worker ──
  if ('serviceWorker' in navigator) {{
    navigator.serviceWorker.register('sw.js').catch(function() {{}});
  }}
</script>
</body>
</html>"""



# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  לוח חדשות יומי v5 — {TODAY_HE}")
    print(f"{'='*55}\n")

    # 0. Load watchlist config
    wl_config = load_watchlist()
    all_wl_stocks = wl_config.get("stocks", []) + wl_config.get("il_stocks", [])
    print(f"[ 0 ] Watchlist: {len(all_wl_stocks)} מניות")

    # 1. RSS — Israel only (US news comes from StockTwits)
    print("[ 1 ] שולף כותרות RSS ישראל...")
    il_raw_all = fetch_rss(ISRAEL_FEEDS, max_per_feed=12)
    il_raw = filter_headlines(il_raw_all, is_il_relevant, 10)
    print(f"\n  נבחרו: {len(il_raw)} ישראל")

    # 2. Market data + Sparklines + Fear&Greed + Twitter + StockTwits + Watchlist — in parallel
    print("\n[ 2 ] שולף נתוני שוק, sparklines, Fear & Greed, Twitter, StockTwits ו-Watchlist במקביל...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        fut_mkt   = ex.submit(fetch_market_data)
        fut_spark = ex.submit(fetch_sparklines)
        fut_fg    = ex.submit(fetch_fear_greed)
        fut_tw    = ex.submit(fetch_twitter_feeds, TWITTER_HANDLES)
        fut_st    = ex.submit(fetch_stocktwits_trending)
        fut_wl    = ex.submit(fetch_watchlist_data, all_wl_stocks)
        mkt       = fut_mkt.result()
        sparks    = fut_spark.result()
        fg        = fut_fg.result()
        tw_feed   = fut_tw.result()
        trending  = fut_st.result()
        watchlist = fut_wl.result()

    # 2b. Ticker-specific news for StockTwits trending
    print("\n[ 2b ] שולף חדשות מניות ספציפיות...")
    tk_news = fetch_ticker_news(trending[:8])

    # 3. Claude Enrichment — analyst-voice interpretation of tweets
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    news_data = None
    if client:
        try:
            print("\n[ 3 ] מנתח ציוצים עם Claude (analyst voice)...")
            tw_sample = select_finance_tweets(tw_feed)
            news_data = run_enrichment_agent(client, tw_feed, il_raw, ticker_news_raw=tk_news)
            news_data = merge_enriched_with_sources(news_data, tw_sample, il_raw, ticker_news_raw=tk_news)
            has_img = sum(1 for n in news_data.get("us_news", []) if n.get("tweet_image"))
            print(f"✓ ניתוח הושלם — {has_img} ציוצים עם תמונה מקורית")
        except Exception as e:
            print(f"✗ Claude נכשל: {e} — עובר ל-fallback")
    else:
        print("⚠  ANTHROPIC_API_KEY לא מוגדר — fallback")

    if news_data is None:
        news_data = fallback_data(tw_feed, il_raw)

    # 4. Assemble full data dict
    data = {
        **news_data,
        "watchlist":   watchlist,
        "market_us":   mkt["market_us"],
        "commodities": mkt["commodities"],
        "market_il":   mkt["market_il"],
        "sectors":     mkt["sectors"],
        "sparklines":  sparks,
        "fear_greed":  fg,
        "events":      get_upcoming_events(5),
    }

    # 5. Fetch images
    print("\n[ 4 ] שולף תמונות לכתבות...")
    print("  US:");   us_imgs = fetch_all_images(data.get("us_news", []))
    print("  ישראל:"); il_imgs = fetch_all_images(data.get("israel_news", []))
    for i, img in enumerate(us_imgs):
        if i < len(data["us_news"]): data["us_news"][i]["image"] = img
    for i, img in enumerate(il_imgs):
        if i < len(data["israel_news"]): data["israel_news"][i]["image"] = img

    # 6. Write data.json (intermediate data layer)
    print("\n[ 5 ] כותב data.json...")
    import datetime as _dt
    data_export = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "watchlist":    watchlist,
        "market_us":    mkt["market_us"],
        "commodities":  mkt["commodities"],
        "market_il":    mkt["market_il"],
        "sectors":      mkt["sectors"],
        "fear_greed":   fg,
        "us_news":      [{k: v for k, v in n.items() if k != "image"} for n in data.get("us_news", [])],
        "il_news":      [{k: v for k, v in n.items() if k != "image"} for n in data.get("israel_news", [])],
        "alert_config": wl_config.get("alerts", {}),
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data_export, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ data.json נשמר → {DATA_PATH}")

    # 7. Build & Save HTML
    print("\n[ 6 ] בונה HTML...")
    html = build_html(data)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"✓ HTML נשמר → {OUTPUT_PATH}")

    print(f"\n✓ הושלם — {TODAY} {TIME}\n")


if __name__ == "__main__":
    main()
