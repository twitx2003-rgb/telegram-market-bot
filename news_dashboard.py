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
    OUTPUT_PATH = Path("docs/index.html")
    MODEL       = os.environ.get("DASHBOARD_MODEL", "claude-haiku-4-5-20251001")
else:
    IDOP_DIR    = Path("C:/Users/idoph/OneDrive/IDOP")
    OUTPUT_PATH = IDOP_DIR / "reports/docs/index.html"
    MODEL       = os.environ.get("DASHBOARD_MODEL", "claude-opus-4-6")

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
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff: continue
                results.append({"title": title, "link": link, "source": feed["name"]})
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

def _finance_img(tag: str = "", ticker: str = "") -> str:
    """Return a relevant finance image URL based on tag or ticker."""
    kw = _FINANCE_IMG_KW.get(tag, "wall-street,finance,stock-market")
    seed = abs(hash(ticker or tag or "finance")) % 9000 + 1000
    return f"https://loremflickr.com/400/200/{kw}?random={seed}"

def _picsum_url(seed_text: str) -> str:
    h = abs(hash(seed_text)) % 1000
    return f"https://picsum.photos/seed/{h}/400/200"

_URL_RE    = re.compile(r'https?://(?!nitter\.|twitter\.com|t\.co)[^\s\])"\']+', re.IGNORECASE)
_TICKER_RE = re.compile(r'(\$[A-Z]{1,5})\b')


def bold_tickers(text: str) -> str:
    """Wrap $TICKER symbols with a highlighted <strong> span."""
    return _TICKER_RE.sub(r'<strong class="ticker-hl">\1</strong>', text)

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
    """Pick the best URL to fetch an image from for a news item."""
    # Prefer article URL extracted from tweet body
    article = item.get("article_url", "")
    if article:
        return article
    # Fall back to item link (works well for IL news from RSS)
    return item.get("link", "")

def fetch_all_images(news_items: list) -> list:
    if not news_items: return []
    urls = [_best_image_url(item) for item in news_items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(fetch_og_image, urls))
    # Replace picsum fallbacks with finance-themed images
    final = []
    for item, img in zip(news_items, results):
        if "picsum" in img or not img:
            img = _finance_img(item.get("tag",""), item.get("ticker",""))
        final.append(img)
    original = sum(1 for r in final if "loremflickr" not in r and "picsum" not in r)
    print(f"  ✓ תמונות: {original} מאמרים + {len(final)-original} finance")
    return final

# ── Twitter / Nitter RSS ──────────────────────────────────────────────────────

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
        for entry in parsed.entries[:3]:
            body = (entry.get("title") or "").strip()
            link = entry.get("link", "")
            date = entry.get("published", "")
            if body and len(body) > 5:
                # Extract article URL from tweet body or summary (not twitter/nitter links)
                summary_html = entry.get("summary", "") or entry.get("description", "")
                combined = body + " " + summary_html
                article_urls = _URL_RE.findall(combined)
                article_url = article_urls[0][:300] if article_urls else ""
                tweets.append({
                    "handle":      handle,
                    "body":        body[:500],
                    "url":         link,
                    "date":        date,
                    "article_url": article_url,
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
    """Fetch tweets for all handles in parallel via Nitter RSS."""
    if not handles:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_one_handle, handles))
    all_tweets = [t for sub in results for t in sub]
    ok = sum(1 for sub in results if sub)
    print(f"  ✓ Twitter/Nitter: {ok}/{len(handles)} חשבונות · {len(all_tweets)} ציוצים")
    return all_tweets


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
                news.append({
                    "ticker":    f"${ticker}",
                    "title":     title,
                    "url":       link,
                    "published": entry.get("published", ""),
                    "source":    "Yahoo Finance",
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


# ── Claude Enrichment Agent ────────────────────────────────────────────────────

def run_enrichment_agent(client: anthropic.Anthropic, tw_tweets: list, il_raw: list,
                          ticker_news_raw: list = None) -> dict:
    il_titles = "\n".join(f"{i+1}. [{item['source']}] {item['title']}" for i, item in enumerate(il_raw))

    ticker_section = ""
    if ticker_news_raw:
        tn_lines = "\n".join(
            f"- [{n['ticker']}] {n['title']} ({n['source']})" for n in ticker_news_raw[:15]
        )
        ticker_section = f"\n\nחדשות מניות ספציפיות ({len(ticker_news_raw[:15])}):\n{tn_lines}"

    system = f"""אתה עיתונאי פיננסי בכיר ומומחה לחדשות ישראל.
היום: {TODAY_HE} ({TODAY}).
החזר אובייקט JSON בלבד — ללא markdown, ללא טקסט נוסף.

אתה כתב פיננסי בכיר — סגנונך: ה-Wall Street Journal בעברית.

חלק א — ציוצי Twitter + חדשות טיקרים (us_news):
1. סנן אגרסיבי: כלול רק ציוצים ישירות על מניות ספציפיות, קריפטו, נתוני מאקרו קריטיים.
   העדף ציוצים עם: % שינוי, מחיר יעד, EPS, הכנסות, דוחות רבעוניים, שדרוגים/שנמוכים, M&A.
   דלג על דעות כלליות, שאלות, "מה דעתכם", ניתוחים מעורפלים.
2. בחר עד 10 פריטים משמעותיים.
3. לכל פריט — אל תתרגם ישירות. במקום זאת:
   • כותרת עברית (title_he): חדה, ממוקדת. אם יש טיקר ספציפי — פתח בו (לדוג': "$NVDA: רווח Q1 עקף ציפיות ב-18%").
     כלול נתונים כמותיים בכותרת עצמה בכל הזדמנות (%, $, מחיר יעד).
   • סיכום (summary_he): 2-3 משפטים מקצועיים. חובה לחלץ ולכלול כל מספר/סטטיסטיקה מהציוץ
     (EPS, הכנסות, מחיר יעד, % שינוי, שווי שוק, תחזית). הסבר את המשמעות למשקיע.
   • body_en: הטקסט האנגלי המקורי, עד 200 תווים.
4. זהה טיקר ($AAPL, $BTC וכד׳). אם אין — השאר ריק.
5. תגית: EARNINGS / MACRO / FED / TECH / M&A / ENERGY / CRYPTO / BANKS / NEWS
6. link = כתובת המקור. source = "@handle" או שם המקור.

חלק ב — חדשות ישראל (israel_news):
כותרת עברית + סיכום 2-3 משפטים מקצועיים + תגית: ביטחון/פוליטיקה/כלכלה/חברה/דיפלומטיה.

{{
  "us_news": [
    {{"title_he":"כותרת עם נתונים","summary_he":"סיכום עם סטטיסטיקות","body_en":"original text...","source":"@handle","link":"...","tag":"TECH","ticker":"$AAPL"}}
  ],
  "israel_news": [
    {{"title_he":"...","summary_he":"...","source":"...","link":"...","tag":"ביטחון"}}
  ]
}}
JSON בלבד."""

    # Pre-filter: keep only finance-relevant tweets before sending to Claude
    finance_tweets = [t for t in tw_tweets if is_finance_tweet(t.get("body", ""))]
    if not finance_tweets:
        finance_tweets = tw_tweets  # fallback: send all if filter is too aggressive
    tw_sample = finance_tweets[:30]
    print(f"  ✓ פילטר פיננסי: {len(finance_tweets)}/{len(tw_tweets)} ציוצים רלוונטיים")
    messages = [{"role": "user", "content":
        f"עבד נתונים ל-{TODAY_HE}.\n\n"
        f"Twitter ({len(tw_sample)} ציוצים):\n{json.dumps(tw_sample, ensure_ascii=False)}\n\n"
        f"ישראל ({len(il_raw)}):\n{il_titles}"
        + (f"\n\nחדשות מניות ספציפיות:{ticker_section}" if ticker_section else "")
        + "\n\nהחזר JSON."}]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל Claude...")
    response = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] stop_reason={response.stop_reason}")

    text_blocks = [b for b in response.content if b.type == "text"]
    if text_blocks:
        raw = text_blocks[-1].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw.strip())
    raise ValueError("לא התקבלה תגובה")

# ── Fallback: RSS-only mode ────────────────────────────────────────────────────

def fallback_data(tw_tweets: list, il_raw: list) -> dict:
    def tr(text):
        if HAS_TRANSLATOR:
            try: return GoogleTranslator(source="en", target="iw").translate(text)
            except Exception: pass
        return text
    return {
        "us_news": [{"title_he": tr(t["body"][:120]), "summary_he": "", "ticker": "",
                     "source": f'@{t["handle"]}', "link": t.get("url", "#"), "tag": "NEWS"}
                    for t in tw_tweets if is_finance_tweet(t.get("body", ""))],
        "israel_news": [{"title_he": tr(i["title"]), "summary_he": "", "source": i["source"],
                         "link": i["link"], "tag": "כללי"} for i in il_raw],
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
    tag    = n.get("tag", "NEWS")
    color, bg = TAG_COLORS_US.get(tag, TAG_COLORS_US["NEWS"])
    link       = n.get("link", "#")
    ticker     = n.get("ticker", "")
    body_en    = n.get("body_en", "")
    summary_he = n.get("summary_he", "")

    # Bold $TICKER symbols in all text fields
    title_rendered   = bold_tickers(n.get("title_he", ""))
    summary_rendered = bold_tickers(summary_he) if summary_he else ""
    body_rendered    = bold_tickers(body_en) if body_en else ""

    ticker_badge = f'<span class="ticker-badge">{bold_tickers(ticker)}</span>' if ticker else ""
    en_block     = (f'<div class="news-en" dir="ltr">{body_rendered}</div>' if body_en else "")
    read_more    = f'<a href="{link}" target="_blank" class="read-more">מקור ←</a>' if link and link != "#" else ""
    wa           = _wa_link(n.get("title_he", ""), link) if link and link != "#" else ""

    source    = n.get("source", "")
    byline    = f'<div class="news-byline">{source}</div>' if source else ""
    return (
        f'<div class="news-item">'
        f'<div class="news-card il-card">'
        f'<div class="news-body">'
        # ── Header: context row ──
        + f'<div class="news-header">'
        + f'<span class="news-tag" style="color:{color};background:{bg}">{tag}</span>'
        + ticker_badge
        + f'<span class="news-num-badge">#{idx:02d}</span>'
        + f'</div>'
        # ── Main content ──
        + f'<div class="news-title" dir="rtl">{title_rendered}</div>'
        + (f'<div class="news-summary" dir="rtl">{summary_rendered}</div>' if summary_he else "")
        + en_block
        # ── Footer: actions ──
        + f'<div class="news-footer">{read_more} {wa}</div>'
        + f'</div>'
        + f'</div>'
        # ── Byline below card ──
        + byline
        + f'</div>'
    )


def build_il_news_card(n: dict) -> str:
    tag = n.get("tag", "כללי")
    color, bg = TAG_COLORS_IL.get(tag, TAG_COLORS_IL["כללי"])
    link  = n.get("link", "#")
    image = n.get("image", "")
    img_html  = (f'<img class="news-img" src="{image}" alt="" onerror="this.style.display=\'none\'" loading="lazy"/>'
                 if image else "")
    read_more = f'<a href="{link}" target="_blank" class="read-more">קרא עוד ←</a>' if link and link != "#" else ""
    wa        = _wa_link(n.get("title_he",""), link) if link and link != "#" else ""
    source = n.get("source", "")
    byline = f'<div class="news-byline">{source}</div>' if source else ""
    return (
        f'<div class="news-item">'
        f'<div class="news-card il-card">'
        f'<div class="news-body">'
        + img_html
        # ── Header: context row ──
        + f'<div class="news-header">'
        + f'<span class="news-tag" style="color:{color};background:{bg}">{tag}</span>'
        + f'</div>'
        # ── Main content ──
        + f'<div class="news-title">{n["title_he"]}</div>'
        + (f'<div class="news-summary">{n["summary_he"]}</div>' if n.get("summary_he") else "")
        # ── Footer: actions ──
        + f'<div class="news-footer">{read_more} {wa}</div>'
        + f'</div>'
        + f'</div>'
        # ── Byline below card ──
        + byline
        + f'</div>'
    )


def build_twitter_card(tweet: dict) -> str:
    handle = tweet.get("handle", "")
    body   = tweet.get("body", "")
    url    = tweet.get("url", "#")
    date   = tweet.get("date", "")
    # Shorten date: keep only first part (e.g. "Mon, 05 Apr 2026 14:30:00")
    date_short = date[:16] if date else ""
    read_more  = f'<a href="{url}" target="_blank" class="read-more">X ←</a>' if url and url != "#" else ""
    wa         = _wa_link(body, url) if url and url != "#" else ""
    return (
        f'<div class="news-card il-card tw-card">'
        f'<div class="news-body">'
        f'<span class="tw-handle">@{handle}</span>'
        f'<div class="news-title">{body}</div>'
        f'<div class="news-meta">{date_short} {read_more} {wa}</div>'
        f'</div>'
        f'</div>'
    )


def build_html(data: dict) -> str:
    sparks    = data.get("sparklines", {})
    sym_map   = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow Jones": "^DJI",
                 "Russell 2000": "^RUT", "זהב": "GC=F", "ביטקוין": "BTC-USD"}

    def mkt_card(m):
        sym    = sym_map.get(m["name"], "")
        prices = sparks.get(sym, [])
        svg    = sparkline_svg(prices, m.get("direction","flat"))
        return build_market_card(m, svg)

    us_market_cards = "".join(mkt_card(m) for m in data.get("market_us", []))
    comm_cards      = "".join(mkt_card(m) for m in data.get("commodities", []))
    il_market_cards = "".join(build_market_card(m) for m in data.get("market_il", []))
    us_news_cards   = "".join(build_us_news_card(n, i+1) for i, n in enumerate(data.get("us_news", [])))
    il_news_cards   = "".join(build_il_news_card(n) for n in data.get("israel_news", []))
    ticker_items    = build_ticker_items(data)
    fg_card         = build_fear_greed_card(data.get("fear_greed"))
    heatmap_cells   = build_heatmap(data.get("sectors", []))
    cal_strip       = build_calendar_strip(data.get("events", []))

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>לוח חדשות — {TODAY}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
  :root {{
    --bg:      #070b0f;
    --surface: #0e1419;
    --card:    #131a22;
    --border:  #1e2a35;
    --accent:  #0ea5e9;
    --green:   #22c55e;
    --red:     #ef4444;
    --gold:    #f59e0b;
    --purple:  #a855f7;
    --text:    #cbd5e1;
    --muted:   #64748b;
    --white:   #f1f5f9;
  }}
  body.light {{
    --bg:      #f8fafc;
    --surface: #f1f5f9;
    --card:    #ffffff;
    --border:  #e2e8f0;
    --accent:  #0284c7;
    --green:   #16a34a;
    --red:     #dc2626;
    --gold:    #d97706;
    --purple:  #9333ea;
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
  .hero h1{{font-size:2.2rem;font-weight:900;color:var(--white);letter-spacing:-.03em;line-height:1.1}}
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
    padding:.9rem 1.1rem;min-width:120px;flex:1;transition:border-color .2s}}
  .mkt-card:hover{{border-color:var(--accent)}}
  .mkt-name{{font-size:.68rem;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.25rem}}
  .mkt-value{{font-size:1.4rem;font-weight:700;color:var(--white);font-variant-numeric:tabular-nums}}
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
  .news-list{{display:flex;flex-direction:column;gap:.85rem}}
  .news-item{{display:flex;flex-direction:column}}
  .news-byline{{font-size:.68rem;color:var(--muted);padding:.28rem .6rem;text-align:right;letter-spacing:.01em}}
  .news-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:1.2rem 1.4rem;transition:border-color .2s}}
  .news-card:hover{{border-color:var(--accent)}}
  .news-top-row{{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-bottom:.2rem}}
  .news-tag{{display:inline-block;padding:.12rem .55rem;border-radius:4px;
    font-size:.65rem;font-weight:700;letter-spacing:.05em}}
  .news-title{{font-size:1.05rem;font-weight:700;color:var(--white);margin:.3rem 0 .4rem;line-height:1.45}}
  .news-summary{{font-size:.86rem;color:var(--text);margin-bottom:.5rem;line-height:1.65}}
  .news-en{{
    font-size:.78rem;color:var(--muted);font-style:italic;
    border-right:3px solid var(--border);
    padding:.35rem .7rem;margin:.4rem 0;
    line-height:1.55;font-family:'Inter',sans-serif;
    text-align:left;
  }}
  .news-meta{{font-size:.72rem;color:var(--muted);display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}}
  .read-more{{color:var(--accent);text-decoration:none;font-weight:600;font-size:.72rem}}
  .read-more:hover{{text-decoration:underline}}
  .wa-btn{{color:#25d366;display:inline-flex;align-items:center;opacity:.8;transition:opacity .2s}}
  .wa-btn:hover{{opacity:1}}
  .news-img{{width:100%;height:175px;object-fit:cover;border-radius:8px;
    margin-bottom:.7rem;display:block;background:var(--border)}}
  .ticker-badge{{
    display:inline-block;
    background:rgba(14,165,233,.18);
    color:#38bdf8;
    border:1px solid rgba(56,189,248,.45);
    border-radius:6px;
    padding:.25rem .75rem;
    font-size:.95rem;
    font-weight:800;
    font-family:monospace;
    letter-spacing:.04em;
    margin-bottom:.4rem;
    margin-right:.3rem;
  }}
  /* ── Ticker highlight inside text ── */
  .ticker-hl{{
    color:#38bdf8;
    font-weight:800;
    font-family:monospace;
    font-style:normal;
    letter-spacing:.02em;
  }}

  /* ── News card header / footer ── */
  .news-header{{
    display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;
    padding-bottom:.7rem;
    border-bottom:1px solid var(--border);
    margin-bottom:.65rem;
  }}
  .news-num-badge{{
    font-size:.7rem;font-weight:800;color:var(--muted);
    background:rgba(100,116,139,.12);border-radius:4px;
    padding:.1rem .45rem;font-variant-numeric:tabular-nums;
    margin-right:auto;
  }}
  .news-footer{{
    display:flex;align-items:center;gap:.5rem;
    padding-top:.65rem;
    border-top:1px solid var(--border);
    margin-top:.65rem;
    font-size:.72rem;
  }}

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

  /* ── Footer ── */
  footer{{text-align:center;padding:2rem;font-size:.72rem;color:var(--muted);border-top:1px solid var(--border)}}
  footer a{{color:var(--accent);text-decoration:none}}
  footer a:hover{{text-decoration:underline}}

  @media(max-width:600px){{
    .hero h1{{font-size:1.65rem}}
    .mkt-card{{min-width:105px}}
    .news-card{{grid-template-columns:1fr}}
    .news-num{{display:none}}
    .heatmap-grid{{grid-template-columns:repeat(3,1fr)}}
    .fg-il-row{{flex-direction:column}}
  }}
</style>
</head>
<body>

<!-- Sticky Nav -->
<nav class="nav">
  <button class="theme-btn" onclick="toggleTheme()" id="themeBtn" title="החלף מצב">🌙</button>
  <a href="#us">🇺🇸 שוק ההון</a>
  <span class="nav-sep">|</span>
  <a href="#heatmap">🌡 מגזרים</a>
  <span class="nav-sep">|</span>
  <a href="#israel">🇮🇱 ישראל</a>
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

  <!-- Calendar Strip -->
  {f'<div class="section-label">📅 אירועים כלכליים קרובים</div><div class="cal-strip">{cal_strip}</div>' if cal_strip else ""}

  <!-- US Markets -->
  <section class="section" id="us">
    <div class="section-label">📈 מדדים אמריקאיים</div>
    <div class="mkt-strip">{us_market_cards}</div>
    <div class="mkt-strip" style="margin-top:.75rem">{comm_cards}</div>
  </section>

  <!-- Fear & Greed + IL Markets -->
  <div class="fg-il-row">
    {fg_card}
    <div style="flex:1">
      <div class="section-label" style="margin-bottom:.7rem">🏦 שוק ישראלי</div>
      <div class="mkt-strip il">{il_market_cards}</div>
    </div>
  </div>

  <!-- Sector Heatmap -->
  {f'<section class="section" id="heatmap"><div class="section-label">🌡 מפת מגזרים — S&P 500</div><div class="heatmap-grid">{heatmap_cells}</div></section>' if heatmap_cells else ""}

  <!-- US News -->
  <section class="section" id="us-news">
    <div class="section-label">📰 חדשות וול סטריט</div>
    <div class="news-list">{us_news_cards}</div>
  </section>

  <!-- Israel News -->
  <section class="section" id="israel">
    <div class="section-label">🇮🇱 חדשות ישראל</div>
    <div class="news-list">{il_news_cards}</div>
  </section>


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
</script>
</body>
</html>"""



# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  לוח חדשות יומי v4 — {TODAY_HE}")
    print(f"{'='*55}\n")

    # 1. RSS — Israel only (US news comes from StockTwits)
    print("[ 1 ] שולף כותרות RSS ישראל...")
    il_raw_all = fetch_rss(ISRAEL_FEEDS, max_per_feed=12)
    il_raw = filter_headlines(il_raw_all, is_il_relevant, 10)
    print(f"\n  נבחרו: {len(il_raw)} ישראל")

    # 2. Market data + Sparklines + Fear&Greed + Twitter + StockTwits trending — in parallel
    print("\n[ 2 ] שולף נתוני שוק, sparklines, Fear & Greed, Twitter ו-StockTwits במקביל...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        fut_mkt     = ex.submit(fetch_market_data)
        fut_spark   = ex.submit(fetch_sparklines)
        fut_fg      = ex.submit(fetch_fear_greed)
        fut_tw      = ex.submit(fetch_twitter_feeds, TWITTER_HANDLES)
        fut_st_tick = ex.submit(fetch_stocktwits_trending)
        mkt             = fut_mkt.result()
        sparks          = fut_spark.result()
        fg              = fut_fg.result()
        tw_feed         = fut_tw.result()
        trending_tickers = fut_st_tick.result()

    # 2b. Fetch ticker-specific news for trending stocks
    print(f"\n[ 2b ] שולף חדשות מניות ספציפיות עבור: {', '.join(trending_tickers[:8])}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_tk_news = ex.submit(fetch_ticker_news, trending_tickers[:8])
        tk_news     = fut_tk_news.result()

    # 3. Claude Enrichment — translate Twitter + ticker news → Hebrew news
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    news_data = None
    if api_key:
        try:
            print("\n[ 3 ] מתרגם ציוצים וחדשות מניות לעברית עם Claude...")
            client = anthropic.Anthropic(api_key=api_key)
            news_data = run_enrichment_agent(client, tw_feed, il_raw, ticker_news_raw=tk_news)
            for i, n in enumerate(news_data.get("israel_news", [])):
                if i < len(il_raw) and not n.get("link"): n["link"] = il_raw[i]["link"]
            print("✓ תרגום הושלם")
        except Exception as e:
            print(f"✗ Claude נכשל: {e} — עובר ל-fallback")
    else:
        print("⚠  ANTHROPIC_API_KEY לא מוגדר — fallback")

    if news_data is None:
        news_data = fallback_data(tw_feed, il_raw)

    # 4. Assemble full data dict
    data = {
        **news_data,
        "market_us":   mkt["market_us"],
        "commodities": mkt["commodities"],
        "market_il":   mkt["market_il"],
        "sectors":     mkt["sectors"],
        "sparklines": sparks,
        "fear_greed": fg,
        "events":     get_upcoming_events(5),
    }

    # 5. Fetch images
    print("\n[ 4 ] שולף תמונות לכתבות...")
    print("  US:");   us_imgs = fetch_all_images(data.get("us_news", []))
    print("  ישראל:"); il_imgs = fetch_all_images(data.get("israel_news", []))
    for i, img in enumerate(us_imgs):
        if i < len(data["us_news"]): data["us_news"][i]["image"] = img
    for i, img in enumerate(il_imgs):
        if i < len(data["israel_news"]): data["israel_news"][i]["image"] = img

    # 6. Build & Save HTML
    print("\n[ 5 ] בונה HTML...")
    html = build_html(data)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"✓ HTML נשמר → {OUTPUT_PATH}")

    print(f"\n✓ הושלם — {TODAY} {TIME}\n")


if __name__ == "__main__":
    main()
