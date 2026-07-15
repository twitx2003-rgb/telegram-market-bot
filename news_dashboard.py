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
    TRACK_PATH       = Path("docs/track_record.json")
    MODEL            = os.environ.get("DASHBOARD_MODEL",    "claude-haiku-4-5-20251001")
    ENRICHMENT_MODEL = os.environ.get("ENRICHMENT_MODEL",   "claude-sonnet-4-6")
else:
    IDOP_DIR         = Path("C:/Users/idoph/OneDrive/IDOP")
    OUTPUT_PATH      = IDOP_DIR / "reports/docs/index.html"
    DATA_PATH        = IDOP_DIR / "reports/docs/data.json"
    TRACK_PATH       = IDOP_DIR / "reports/docs/track_record.json"
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


# ── Technical Analysis Engine ──────────────────────────────────────────────────

_YF_1Y_BASE  = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1y"
_TICKER_OK_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,6}$')


def build_ta_universe(watchlist_stocks: list, trending: list, cap: int = 15) -> list:
    """Watchlist tickers first, then StockTwits trending, deduped, junk filtered, capped."""
    universe, seen = [], set()
    for s in watchlist_stocks:
        t = s.get("ticker", "").upper()
        if t and t not in seen and _TICKER_OK_RE.match(t):
            universe.append(t); seen.add(t)
    for t in trending:
        t = (t or "").upper()
        # Skip crypto pairs / OTC junk StockTwits sometimes returns
        if t and t not in seen and _TICKER_OK_RE.match(t) and "." not in t and len(t) <= 5:
            universe.append(t); seen.add(t)
        if len(universe) >= cap:
            break
    return universe[:cap]


def _fetch_history_one(ticker: str) -> tuple:
    """Yahoo v8 chart, 1y daily. Returns (ticker, {closes, highs, lows, volumes}) or (ticker, None)."""
    if not HAS_REQUESTS:
        return ticker, None
    try:
        url  = _YF_1Y_BASE.format(sym=urllib.parse.quote(ticker))
        resp = _requests.get(url, headers=_YF_HEADERS, timeout=10)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        quote  = result.get("indicators", {}).get("quote", [{}])[0]
        rows = [
            (c, h, l, v) for c, h, l, v in zip(
                quote.get("close", []), quote.get("high", []),
                quote.get("low", []),   quote.get("volume", []))
            if c is not None
        ]
        if len(rows) < 60:
            return ticker, None
        return ticker, {
            "closes":  [r[0] for r in rows],
            "highs":   [r[1] if r[1] is not None else r[0] for r in rows],
            "lows":    [r[2] if r[2] is not None else r[0] for r in rows],
            "volumes": [r[3] or 0 for r in rows],
        }
    except Exception as e:
        print(f"  ✗ היסטוריה {ticker}: {e}")
        return ticker, None


def fetch_ta_history(tickers: list) -> dict:
    """Parallel 1y history fetch. Failures silently dropped."""
    if not tickers:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_history_one, tickers))
    hist = {t: h for t, h in results if h}
    print(f"  ✓ היסטוריה: {len(hist)}/{len(tickers)} טיקרים")
    return hist


def _sma(values: list, n: int):
    return round(sum(values[-n:]) / n, 2) if len(values) >= n else None


def _rsi14(closes: list):
    """Wilder-smoothed RSI(14)."""
    if len(closes) < 15:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g, avg_l = sum(gains[:14]) / 14, sum(losses[:14]) / 14
    for i in range(14, len(deltas)):
        avg_g = (avg_g * 13 + gains[i]) / 14
        avg_l = (avg_l * 13 + losses[i]) / 14
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def _swing_levels(highs: list, lows: list, price: float, window: int = 5, lookback: int = 126):
    """Nearest swing-low support below price and swing-high resistance above price
    from local extrema over the last ~6 months."""
    highs, lows = highs[-lookback:], lows[-lookback:]
    swing_highs, swing_lows = [], []
    for i in range(window, len(highs) - window):
        seg_h = highs[i-window:i+window+1]
        seg_l = lows[i-window:i+window+1]
        if highs[i] == max(seg_h):
            swing_highs.append(highs[i])
        if lows[i] == min(seg_l):
            swing_lows.append(lows[i])
    supports    = [s for s in swing_lows  if s < price]
    resistances = [r for r in swing_highs if r > price]
    support     = round(max(supports), 2)    if supports    else round(min(lows), 2)
    resistance  = round(min(resistances), 2) if resistances else round(max(highs), 2)
    return support, resistance


def compute_indicators(ticker: str, hist: dict, quote_price: float = None) -> dict:
    """Pure computation — every number Claude sees comes from here."""
    closes, highs, lows, vols = hist["closes"], hist["highs"], hist["lows"], hist["volumes"]
    price = quote_price or closes[-1]
    support, resistance = _swing_levels(highs, lows, price)
    high_52w, low_52w = max(highs), min(lows)
    vol_10d = sum(vols[-10:]) / 10 if len(vols) >= 10 else 0
    vol_3m  = sum(vols[-63:]) / min(63, len(vols)) if vols else 0
    spark_src = closes[-63:]
    spark_3m  = [round(c, 2) for c in spark_src[::3]][-21:]
    return {
        "ticker":            ticker,
        "price":             round(price, 2),
        "sma20":             _sma(closes, 20),
        "sma50":             _sma(closes, 50),
        "sma200":            _sma(closes, 200),
        "rsi14":             _rsi14(closes),
        "support":           support,
        "resistance":        resistance,
        "high_52w":          round(high_52w, 2),
        "low_52w":           round(low_52w, 2),
        "pct_from_52w_high": round((price - high_52w) / high_52w * 100, 1) if high_52w else 0,
        "vol_ratio":         round(vol_10d / vol_3m, 2) if vol_3m else 1.0,
        "change_1d_pct":     round((closes[-1] - closes[-2])  / closes[-2]  * 100, 2) if len(closes) > 1  else 0,
        "change_1w_pct":     round((closes[-1] - closes[-6])  / closes[-6]  * 100, 1) if len(closes) > 6  else 0,
        "change_1m_pct":     round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) > 22 else 0,
        "spark_3m":          spark_3m,
    }


TA_MODEL = os.environ.get("TA_MODEL", "")  # empty → use ENRICHMENT_MODEL


def run_ta_agent(client: anthropic.Anthropic, indicator_rows: list) -> list:
    """Second Claude call: technical-analyst interpretation of the computed indicators.
    Claude only interprets — every level it cites comes from the input table."""
    if not indicator_rows:
        return []
    system = f"""אתה אנליסט טכני בכיר — 15 שנה בדסק המסחר של Morgan Stanley, מתמחה ב-swing trading.
היום: {TODAY_HE} ({TODAY}).
תקבל טבלת אינדיקטורים מחושבים. המספרים סופיים — אל תמציא ואל תשנה אותם.
החזר אובייקט JSON בלבד — ללא markdown, ללא טקסט נוסף.

── כללי ניתוח ─────────────────────────────────────────
setup_type — בחר אחד:
  פריצה   — מחיר קרוב להתנגדות (עד 3%) עם מומנטום (change_1w חיובי, vol_ratio>1)
  תמיכה   — מחיר קרוב לתמיכה (עד 3%) עם RSI מתחת ל-45
  מומנטום — מחיר מעל SMA20>SMA50, RSI בין 50-70
  תיקון   — ירידה מ-52w high מעל 10%, RSI מתקרר
  טווח    — שום דבר מהנ"ל, המחיר באמצע הטווח

recommendation:
  קנייה  — רק כשלפחות 3 אינדיקטורים תומכים באותו כיוון
  מעקב   — תמונה מעורבת אבל יש סטאפ מתפתח
  המתנה  — אין יתרון סטטיסטי כרגע
  לעולם אל תמליץ קנייה כש-RSI>75 או כשהמחיר מתחת לכל שלושת הממוצעים.

confidence: 1-5 — כמה אינדיקטורים מיישרים קו. 5 נדיר מאוד.

entry: מחיר כניסה הגיוני ביחס לתמיכה/פריצה מהטבלה (קרוב לתמיכה או מעל התנגדות).
stop:  מתחת לתמיכה הקרובה (1-3% מתחתיה).

rationale_he: 2-3 משפטים בעברית, קול אנליסט, מצטט את המספרים במפורש:
  "RSI ב-38 עם מחיר 2% מעל תמיכה ב-$182 — הסיכון-סיכוי כאן מוטה לונג"

── סכמה ───────────────────────────────────────────────
{{"opportunities":[
  {{"ticker":"AAPL","setup_type":"תמיכה","levels":{{"support":182.5,"resistance":198.0,"entry":184.0,"stop":178.0}},"recommendation":"קנייה","confidence":3,"rationale_he":"..."}}
]}}
נתח את כל הטיקרים שבטבלה. JSON בלבד."""

    table = "\n".join(json.dumps(r, ensure_ascii=False) for r in indicator_rows)
    messages = [{"role": "user", "content": f"טבלת אינדיקטורים ({len(indicator_rows)} טיקרים):\n{table}\n\nנתח את כולם. החזר JSON."}]

    model = TA_MODEL or ENRICHMENT_MODEL
    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל Claude TA ({model})...")
    response = client.messages.create(model=model, max_tokens=8000, system=system, messages=messages)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] TA stop_reason={response.stop_reason}")

    text_blocks = [b for b in response.content if b.type == "text"]
    if not text_blocks:
        raise ValueError("TA: לא התקבלה תגובה")
    raw = text_blocks[-1].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    parsed = json.loads(raw.strip())
    return parsed.get("opportunities", [])


def merge_ta_results(claude_ops: list, indicators: dict) -> list:
    """Join Claude's interpretation back to Python-computed numbers.
    Python's support/resistance/price always win; entry/stop clamped to ±25% of price."""
    ops = []
    claude_by_ticker = {op.get("ticker", "").upper().lstrip("$"): op for op in claude_ops}
    for ticker, ind in indicators.items():
        cop = claude_by_ticker.get(ticker)
        price = ind["price"]

        def _clamp(v):
            try:
                v = float(v)
                return round(v, 2) if 0.75 * price <= v <= 1.25 * price else None
            except (TypeError, ValueError):
                return None

        entry = stop = None
        setup, rec, conf, rationale = "טווח", "המתנה", 0, ""
        if cop:
            levels    = cop.get("levels", {}) or {}
            entry     = _clamp(levels.get("entry"))
            stop      = _clamp(levels.get("stop"))
            setup     = cop.get("setup_type", "טווח")
            rec       = cop.get("recommendation", "המתנה")
            conf      = min(max(int(cop.get("confidence", 0) or 0), 0), 5)
            rationale = (cop.get("rationale_he", "") or "")[:400]
        ops.append({
            "ticker":         ticker,
            "price":          price,
            "setup_type":     setup,
            "levels": {
                "support":    ind["support"],
                "resistance": ind["resistance"],
                "entry":      entry,
                "stop":       stop,
            },
            "recommendation": rec,
            "confidence":     conf,
            "rationale_he":   rationale,
            "indicators": {
                "rsi14":             ind["rsi14"],
                "sma20":             ind["sma20"],
                "sma50":             ind["sma50"],
                "sma200":            ind["sma200"],
                "pct_from_52w_high": ind["pct_from_52w_high"],
                "vol_ratio":         ind["vol_ratio"],
                "change_1d_pct":     ind.get("change_1d_pct", 0),
                "change_1w_pct":     ind["change_1w_pct"],
                "change_1m_pct":     ind["change_1m_pct"],
                "spark_3m":          ind.get("spark_3m", []),
            },
            "analyzed":       cop is not None,
            "stale":          False,
        })
    # Buy recommendations first, then by confidence
    rec_order = {"קנייה": 0, "מעקב": 1, "המתנה": 2}
    ops.sort(key=lambda o: (rec_order.get(o["recommendation"], 3), -o["confidence"]))
    return ops


def load_previous_opportunities() -> list:
    """Stale fallback: carry forward the last committed data.json's opportunities."""
    try:
        prev = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        ops = prev.get("opportunities", [])
        for op in ops:
            op["stale"] = True
        return ops
    except Exception:
        return []


# ── Rule-Based TA (free mode — no LLM) ─────────────────────────────────────────

def rule_based_analysis(ind: dict) -> dict:
    """Deterministic Hebrew analysis from computed indicators — same rulebook as the
    Claude TA prompt. Free mode: runs when no ANTHROPIC_API_KEY is available."""
    price      = ind["price"]
    support    = ind["support"]
    resistance = ind["resistance"]
    rsi        = ind.get("rsi14")
    sma20      = ind.get("sma20")
    sma50      = ind.get("sma50")
    sma200     = ind.get("sma200")
    w1         = ind.get("change_1w_pct") or 0
    vol_r      = ind.get("vol_ratio") or 1.0
    p52        = ind.get("pct_from_52w_high") or 0

    pct_above_support    = (price - support) / support * 100 if support else 999
    pct_below_resistance = (resistance - price) / price * 100 if resistance else 999

    # ── setup_type ──
    if pct_below_resistance <= 3 and w1 > 0 and vol_r > 1:
        setup = "פריצה"
    elif pct_above_support <= 3 and rsi is not None and rsi < 45:
        setup = "תמיכה"
    elif sma20 and sma50 and price > sma20 > sma50 and rsi is not None and 50 <= rsi <= 70:
        setup = "מומנטום"
    elif p52 < -10 and rsi is not None and rsi < 50:
        setup = "תיקון"
    else:
        setup = "טווח"

    # ── aligned bullish signals ──
    signals = []
    if sma20 and price > sma20:            signals.append("מחיר מעל SMA20")
    if sma20 and sma50 and sma20 > sma50:  signals.append("SMA20 מעל SMA50")
    if sma200 and price > sma200:          signals.append("מחיר מעל SMA200")
    if rsi is not None and 40 <= rsi <= 70: signals.append(f"RSI בריא ({rsi})")
    if w1 > 0:                             signals.append(f"מומנטום שבועי {w1:+.1f}%")
    if vol_r > 1.1:                        signals.append(f"נפח מוגבר (פי {vol_r})")
    if pct_above_support <= 5:             signals.append("קרוב לתמיכה")
    n = len(signals)

    below_all_smas = all(s and price < s for s in (sma20, sma50, sma200) if s) and any((sma20, sma50, sma200))
    overbought     = rsi is not None and rsi > 75

    if n >= 4 and not overbought and not below_all_smas:
        rec = "קנייה"
    elif n >= 2:
        rec = "מעקב"
    else:
        rec = "המתנה"

    conf = max(0, min(4, n - 1))  # rule engine never claims 5/5

    # ── entry / stop ──
    entry = stop = None
    if setup == "תמיכה":
        entry = round(support * 1.01, 2)
    elif setup == "פריצה":
        entry = round(resistance * 1.005, 2)
    if rec in ("קנייה", "מעקב") and support:
        stop = round(support * 0.98, 2)

    # ── Hebrew rationale (templates per setup) ──
    top_signals = " · ".join(signals[:3]) if signals else "אין אינדיקטורים תומכים"
    rsi_txt = f"RSI ב-{rsi}" if rsi is not None else "RSI לא זמין"
    if setup == "פריצה":
        rationale = (f"המחיר ${price:,.2f} נמצא {pct_below_resistance:.1f}% מתחת להתנגדות ב-${resistance:,.2f} "
                     f"עם מומנטום שבועי של {w1:+.1f}%. {top_signals}. פריצה מעל ההתנגדות תפתח יעד גבוה יותר.")
    elif setup == "תמיכה":
        rationale = (f"{rsi_txt} כשהמחיר ${price:,.2f} רק {pct_above_support:.1f}% מעל תמיכה ב-${support:,.2f} — "
                     f"יחס סיכון-סיכוי נוח ללונג. {top_signals}.")
    elif setup == "מומנטום":
        rationale = (f"מבנה עולה מסודר: מחיר מעל SMA20 (${sma20:,.2f}) שמעל SMA50 (${sma50:,.2f}), {rsi_txt}. "
                     f"{top_signals}. כל עוד המחיר מעל הממוצעים — המגמה בעדך.")
    elif setup == "תיקון":
        rationale = (f"ירידה של {p52:.1f}% מהשיא השנתי עם {rsi_txt} — התיקון בעיצומו. "
                     f"תמיכה קרובה ב-${support:,.2f}. {top_signals}.")
    else:
        rationale = (f"המחיר ${price:,.2f} באמצע הטווח בין ${support:,.2f} ל-${resistance:,.2f}, {rsi_txt}. "
                     f"{n} אינדיקטורים תומכים — אין יתרון סטטיסטי מובהק כרגע.")

    return {
        "ticker":         ind["ticker"],
        "setup_type":     setup,
        "levels":         {"support": support, "resistance": resistance, "entry": entry, "stop": stop},
        "recommendation": rec,
        "confidence":     conf,
        "rationale_he":   rationale,
    }


def run_rule_based_ta(indicators: dict) -> list:
    """Free-mode TA: rule engine over all computed indicators."""
    ops = [rule_based_analysis(ind) for ind in indicators.values()]
    merged = merge_ta_results(ops, indicators)
    for op in merged:
        op["engine"] = "rules"
    return merged


# ── Recommendation Track Record ────────────────────────────────────────────────

TRACK_CLOSE_DAYS = 14   # a buy rec's PnL is frozen after this many days
TRACK_MAX_ENTRIES = 200


def update_track_record(opportunities: list, today_str: str = None) -> dict:
    """Log new buy recommendations, evaluate open ones against current prices,
    freeze PnL after TRACK_CLOSE_DAYS. Returns a summary dict for display/data.json."""
    today = today_str or date.today().isoformat()
    try:
        record = json.loads(TRACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        record = {"entries": []}
    entries = record.get("entries", [])

    price_now = {op["ticker"]: op.get("price") for op in opportunities if op.get("price")}

    # 1. Log new buy recs (one per ticker per day)
    logged_keys = {(e["ticker"], e["rec_date"]) for e in entries}
    open_tickers = {e["ticker"] for e in entries if "closed_pnl" not in e}
    for op in opportunities:
        if op.get("recommendation") != "קנייה" or op.get("stale"):
            continue
        t = op["ticker"]
        if (t, today) in logged_keys or t in open_tickers:
            continue
        entries.append({
            "ticker":       t,
            "rec_date":     today,
            "price_at_rec": op.get("price"),
            "confidence":   op.get("confidence", 0),
            "engine":       op.get("engine", "rules"),
        })

    # 2. Evaluate open entries; freeze after TRACK_CLOSE_DAYS
    for e in entries:
        if "closed_pnl" in e:
            continue
        p_now = price_now.get(e["ticker"])
        if p_now and e.get("price_at_rec"):
            e["last_pnl"] = round((p_now - e["price_at_rec"]) / e["price_at_rec"] * 100, 2)
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(e["rec_date"])).days
        except ValueError:
            age = 0
        if age >= TRACK_CLOSE_DAYS and "last_pnl" in e:
            e["closed_pnl"]  = e["last_pnl"]
            e["closed_date"] = today

    entries = entries[-TRACK_MAX_ENTRIES:]
    record["entries"] = entries
    try:
        TRACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRACK_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"  ✗ track_record: {e}")

    closed = [e for e in entries if "closed_pnl" in e]
    wins   = [e for e in closed if e["closed_pnl"] > 0]
    open_e = [e for e in entries if "closed_pnl" not in e]
    summary = {
        "closed":   len(closed),
        "wins":     len(wins),
        "hit_rate": round(len(wins) / len(closed) * 100) if closed else 0,
        "avg_pnl":  round(sum(e["closed_pnl"] for e in closed) / len(closed), 2) if closed else 0,
        "open":     len(open_e),
    }
    print(f"  ✓ מעקב המלצות: {summary['closed']} סגורות ({summary['hit_rate']}% מוצלחות) · {summary['open']} פתוחות")
    return summary


def build_track_card(summary: dict) -> str:
    if not summary:
        return ""
    if summary.get("closed", 0) < 3:
        n_open = summary.get("open", 0)
        if n_open == 0:
            return ""
        return (f'<div class="track-card"><span class="track-pending">'
                f'📊 מעקב ביצועים: {n_open} המלצות קנייה פתוחות — סטטיסטיקה תוצג אחרי 3 המלצות שנסגרו (14 יום)'
                f'</span></div>')
    avg = summary["avg_pnl"]
    avg_css = "up" if avg > 0 else ("down" if avg < 0 else "")
    return (
        f'<div class="track-card">'
        f'<span class="track-stat">📊 ביצועי המלצות:</span>'
        f'<span class="track-stat"><b>{summary["wins"]}/{summary["closed"]}</b> רווחיות (<b>{summary["hit_rate"]}%</b>)</span>'
        f'<span class="track-stat">תשואה ממוצעת <b class="{avg_css}" dir="ltr">{avg:+.1f}%</b></span>'
        f'<span class="track-stat"><b>{summary["open"]}</b> פתוחות</span>'
        f'</div>'
    )


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

# ── Fallback: RSS-only mode (free — no LLM) ────────────────────────────────────

_TAG_KEYWORDS = [
    ("EARNINGS", ["earnings", "eps", "revenue", "guidance", "beats", "misses", "quarterly", "q1 ", "q2 ", "q3 ", "q4 "]),
    ("FED",      ["fed ", "fomc", "powell", "rate cut", "rate hike", "interest rate", "federal reserve"]),
    ("CRYPTO",   ["bitcoin", "btc", "ethereum", "crypto", "coinbase", "blockchain"]),
    ("M&A",      ["merger", "acquisition", "acquire", "buyout", "takeover", "deal"]),
    ("ENERGY",   ["oil", "crude", "energy", "opec", "gas prices", "brent"]),
    ("BANKS",    ["jpmorgan", "goldman", "bank of america", "wells fargo", "citigroup", "morgan stanley"]),
    ("MACRO",    ["inflation", "cpi", "gdp", "jobs report", "unemployment", "recession", "treasury", "yield"]),
    ("TECH",     ["nvidia", "apple", "microsoft", "google", "meta", "amazon", "tesla", "amd", "intel", "ai ", "chip"]),
]
_POS_SIGNALS = ["beat", "beats", "surge", "soar", "rally", "record", "raise", "upgrade", "jumps", "gains", "profit"]
_NEG_SIGNALS = ["miss", "misses", "plunge", "drop", "crash", "layoffs", "cuts", "downgrade", "falls", "loss", "bankrupt"]
_HEBREW_RE   = re.compile('[\\u0590-\\u05FF]')


def classify_tag(text: str) -> str:
    t = text.lower()
    for tag, kws in _TAG_KEYWORDS:
        if any(kw in t for kw in kws):
            return tag
    return "NEWS"


def classify_sentiment(text: str) -> str:
    t = text.lower()
    pos = sum(1 for w in _POS_SIGNALS if w in t)
    neg = sum(1 for w in _NEG_SIGNALS if w in t)
    return "bullish" if pos > neg else ("bearish" if neg > pos else "neutral")


def _extract_ticker(text: str) -> str:
    m = _TICKER_RE.search(text)
    return m.group(1) if m else ""


def fallback_data(tw_tweets: list, il_raw: list) -> dict:
    """Free news path: keyword tags + sentiment + Google translation (no LLM)."""
    def tr(text):
        if _HEBREW_RE.search(text):  # already Hebrew — skip translation
            return text
        if HAS_TRANSLATOR:
            try: return GoogleTranslator(source="en", target="iw").translate(text)
            except Exception: pass
        return text

    def clean_cut(text, limit=160):
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "…"

    return {
        "us_news": [
            {
                "title_he":    tr(clean_cut(t["body"])),
                "summary_he":  "",
                "body_en":     t["body"][:200],
                "ticker":      _extract_ticker(t.get("body", "")),
                "source":      f'@{t["handle"]}',
                "link":        t.get("url", "#"),
                "tag":         classify_tag(t.get("body", "")),
                "article_url": t.get("article_url", ""),
                "tweet_image": t.get("tweet_image", ""),
                "relative_time": t.get("relative_time", ""),
                "published_at": t.get("published_at", ""),
                "sentiment":   classify_sentiment(t.get("body", "")),
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

    if not rel_time and n.get("published_at"):
        rel_time = _relative_time(n["published_at"])
    ticker_badge = f'<span class="ticker-badge">{ticker}</span>' if ticker else ""
    en_block     = (f'<blockquote class="news-en" dir="ltr">{body_en[:200]}</blockquote>' if body_en else "")
    read_more    = f'<a href="{link}" target="_blank" class="read-more">מקור ←</a>' if link and link != "#" else ""
    wa           = _wa_link(n.get("title_he",""), link) if link and link != "#" else ""
    time_chip    = f'<span class="feed-time">{rel_time}</span>' if rel_time else ""
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

    ts_epoch = ""
    if n.get("published_at"):
        try:
            dt = datetime.fromisoformat(n["published_at"])
            ts_epoch = str(int(dt.timestamp()))
        except ValueError:
            pass

    return (
        f'<article class="news-card" data-sentiment="{sentiment}" data-tag="{tag}" data-ts="{ts_epoch}">'
        + img_html
        + f'<div class="card-content">'
        + f'<div class="card-top"><span class="news-tag" style="color:{color};background:{bg}">{tag}</span>{ticker_badge}{time_chip}<span class="sentiment-dot {sentiment}"></span></div>'
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


def load_previous_data() -> dict:
    """Read the previous run's data.json (before this run overwrites it)."""
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_morning_brief(data: dict) -> str:
    """Rule-generated TL;DR card — 3-5 Hebrew bullets from data already fetched."""
    bullets = []

    # Indices line
    idx_parts = []
    for m in data.get("market_us", []):
        if m.get("name") in ("S&P 500", "Nasdaq", "Dow Jones") and m.get("change") not in ("—", ""):
            arrow = _arrow(m.get("direction", "flat"))
            idx_parts.append(f'{m["name"]} <b dir="ltr">{arrow}{m["change"]}</b>')
    if idx_parts:
        bullets.append("מדדים: " + " · ".join(idx_parts))

    # Biggest daily mover from the TA universe
    ops = data.get("opportunities", [])
    movers = [(o["ticker"], o.get("indicators", {}).get("change_1d_pct") or 0) for o in ops]
    movers = [m for m in movers if abs(m[1]) >= 0.5]
    if movers:
        t, chg = max(movers, key=lambda m: abs(m[1]))
        direction = "מזנקת" if chg > 0 else "יורדת"
        bullets.append(f'המניה הבולטת: <b dir="ltr">{t}</b> {direction} <b dir="ltr">{chg:+.1f}%</b> היום')

    # Fear & Greed with delta vs previous run
    fg = data.get("fear_greed") or {}
    if fg.get("score") is not None:
        line = f'מדד פחד וחמדנות: <b>{fg["score"]}</b> ({fg.get("rating", "")})'
        prev_fg = (data.get("prev_fear_greed") or {}).get("score")
        if prev_fg is not None and prev_fg != fg["score"]:
            delta = fg["score"] - prev_fg
            line += f' — {"עלייה" if delta > 0 else "ירידה"} של {abs(delta)} מהריצה הקודמת'
        bullets.append(line)

    # Today's economic events
    today_events = [e for e in data.get("events", []) if e.get("days_left") == 0]
    if today_events:
        names = " · ".join(e["name"] for e in today_events[:3])
        bullets.append(f'📌 היום בלוח: {names}')

    # Active buy recommendations
    buys = sum(1 for o in ops if o.get("recommendation") == "קנייה")
    if buys:
        bullets.append(f'{buys} המלצות קנייה פעילות בטאב ההזדמנויות')

    if not bullets:
        return ""
    items = "".join(f'<li>{b}</li>' for b in bullets)
    return (
        f'<div class="brief-card">'
        f'<div class="brief-title">☀️ תמונת מצב — {TODAY_HE}, {TIME}</div>'
        f'<ul class="brief-list">{items}</ul>'
        f'</div>'
    )


def build_movers_strip(opportunities: list) -> str:
    """Top 3 gainers + top 3 losers by daily change across the TA universe."""
    rows = [(o["ticker"], o.get("indicators", {}).get("change_1d_pct") or 0) for o in opportunities]
    rows = [r for r in rows if r[1] != 0]
    if len(rows) < 3:
        return ""
    rows.sort(key=lambda r: r[1], reverse=True)
    gainers, losers = rows[:3], [r for r in rows[-3:] if r[1] < 0]
    pills = []
    for t, chg in gainers + losers[::-1]:
        css = "up" if chg > 0 else "down"
        pills.append(f'<span class="mover-pill {css}" dir="ltr">{t} {chg:+.1f}%</span>')
    return (
        f'<div class="movers-strip">'
        f'<span class="movers-label">מובילי היום</span>'
        + "".join(pills)
        + f'</div>'
    )


def _news_sort_key(n: dict) -> str:
    """Sort key: ISO published_at desc; items without a timestamp sink to the bottom."""
    return n.get("published_at") or "0000"


def build_news_feed_tab(data: dict) -> str:
    """Tab 1: morning brief + movers + merged news feed sorted newest-first + Israel section."""
    us_news = data.get("us_news", [])
    il_news = data.get("israel_news", [])
    brief   = build_morning_brief(data)
    movers  = build_movers_strip(data.get("opportunities", []))

    feed = sorted(us_news, key=_news_sort_key, reverse=True)
    feed_cards = "".join(build_us_news_card(n, i + 1) for i, n in enumerate(feed))

    # Tag filter chips from tags actually present
    tags = sorted({n.get("tag", "NEWS") for n in feed if n.get("tag")})
    chips = ""
    if len(tags) > 1:
        chip_items = '<button class="filter-chip active" onclick="filterFeed(\'*\',this)">הכל</button>'
        chip_items += "".join(
            f'<button class="filter-chip" onclick="filterFeed(\'{t}\',this)">{t}</button>' for t in tags
        )
        chips = f'<div class="filter-chips">{chip_items}</div>'

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
        brief
        + movers
        + f'<section class="section" id="feed">'
        + f'<div class="section-label">📰 פיד חדשות — מהחדש לישן</div>'
        + chips
        + f'<div class="news-list feed-list" id="feedList">{feed_cards or empty}</div>'
        + f'</section>'
        + il_section
    )


_SETUP_COLORS = {
    "פריצה":  ("var(--accent)", "rgba(6,182,212,.12)"),
    "תמיכה":  ("var(--green)",  "rgba(16,185,129,.12)"),
    "מומנטום": ("var(--purple)", "rgba(139,92,246,.12)"),
    "תיקון":  ("var(--gold)",   "rgba(245,158,11,.12)"),
    "טווח":   ("var(--muted)",  "rgba(100,116,139,.12)"),
}
_REC_CLASS = {"קנייה": "rec-buy", "מעקב": "rec-watch", "המתנה": "rec-wait"}


def build_levels_bar(support: float, price: float, resistance: float,
                     entry=None, stop=None) -> str:
    """LTR horizontal track: support → resistance with price/entry/stop markers."""
    if not support or not resistance or resistance <= support:
        return ""
    span = resistance - support
    def pos(v):
        return max(0, min(100, (v - support) / span * 100))
    price_pos = pos(price)
    markers = f'<div class="level-marker mk-price" style="left:{price_pos:.1f}%" title="מחיר נוכחי ${price:,.2f}"></div>'
    if entry:
        markers += f'<div class="level-marker mk-entry" style="left:{pos(entry):.1f}%" title="כניסה ${entry:,.2f}"></div>'
    if stop and stop >= support:
        markers += f'<div class="level-marker mk-stop" style="left:{pos(stop):.1f}%" title="סטופ ${stop:,.2f}"></div>'
    return (
        f'<div class="levels-bar" dir="ltr">'
        f'<div class="levels-track"><div class="levels-fill" style="width:{price_pos:.1f}%"></div>{markers}</div>'
        f'<div class="level-labels">'
        f'<span class="lvl-s">${support:,.2f}</span>'
        f'<span class="lvl-cur">${price:,.2f}</span>'
        f'<span class="lvl-r">${resistance:,.2f}</span>'
        f'</div>'
        f'</div>'
    )


def _ind_chip(label: str, value, state: str = "neutral") -> str:
    return f'<span class="ind-chip {state}" dir="ltr">{label} {value}</span>'


def build_ta_card(op: dict) -> str:
    ticker  = op.get("ticker", "")
    price   = op.get("price", 0)
    setup   = op.get("setup_type", "טווח")
    rec     = op.get("recommendation", "המתנה")
    conf    = op.get("confidence", 0)
    lv      = op.get("levels", {}) or {}
    ind     = op.get("indicators", {}) or {}
    stale   = op.get("stale", False)

    s_color, s_bg = _SETUP_COLORS.get(setup, _SETUP_COLORS["טווח"])
    rec_cls = _REC_CLASS.get(rec, "rec-wait")

    dots = "".join(
        f'<span class="conf-dot{" filled" if i < conf else ""}"></span>' for i in range(5)
    )

    chips = ""
    rsi = ind.get("rsi14")
    if rsi is not None:
        rsi_state = "bad" if rsi > 70 else ("good" if rsi < 35 else "neutral")
        chips += _ind_chip("RSI", rsi, rsi_state)
    sma20, sma50 = ind.get("sma20"), ind.get("sma50")
    if sma20 and sma50:
        trend_up = price > sma20 > sma50
        chips += _ind_chip("SMA20", f"${sma20:,.0f}", "good" if price > sma20 else "bad")
    p52 = ind.get("pct_from_52w_high")
    if p52 is not None:
        chips += _ind_chip("מ-52w", f"{p52:+.1f}%", "neutral")
    w1 = ind.get("change_1w_pct")
    if w1 is not None:
        chips += _ind_chip("שבוע", f"{w1:+.1f}%", "good" if w1 > 0 else ("bad" if w1 < 0 else "neutral"))

    levels_bar = build_levels_bar(lv.get("support"), price, lv.get("resistance"),
                                  lv.get("entry"), lv.get("stop"))

    spark = ind.get("spark_3m", [])
    spark_html = ""
    if len(spark) >= 5:
        m1 = ind.get("change_1m_pct") or 0
        svg = sparkline_svg(spark, "up" if m1 >= 0 else "down")
        spark_html = (f'<div class="ta-spark" dir="ltr">{svg}'
                      f'<span class="ta-spark-label">3 חודשים {m1:+.1f}%</span></div>')

    entry_stop = ""
    if lv.get("entry") or lv.get("stop"):
        parts = []
        if lv.get("entry"): parts.append(f'<span class="es-item" dir="ltr">כניסה <b>${lv["entry"]:,.2f}</b></span>')
        if lv.get("stop"):  parts.append(f'<span class="es-item es-stop" dir="ltr">סטופ <b>${lv["stop"]:,.2f}</b></span>')
        entry_stop = f'<div class="entry-stop">{"".join(parts)}</div>'

    rationale = op.get("rationale_he", "")
    rationale_html = f'<p class="ta-rationale" dir="rtl">{rationale}</p>' if rationale else ""
    stale_badge = '<span class="stale-badge">נכון לריצה קודמת</span>' if stale else ""

    return (
        f'<div class="ta-card" style="border-top:3px solid {s_color}">'
        f'<div class="ta-head">'
        f'<span class="ta-ticker" dir="ltr">{ticker}</span>'
        f'<span class="ta-price" dir="ltr">${price:,.2f}</span>'
        f'<span class="ta-setup-badge" style="color:{s_color};background:{s_bg}">{setup}</span>'
        f'<span class="ta-rec {rec_cls}">{rec}</span>'
        f'</div>'
        f'<div class="ta-conf-row"><span class="conf-label">ביטחון</span><span class="confidence-dots">{dots}</span>{spark_html}{stale_badge}</div>'
        f'{levels_bar}'
        f'{entry_stop}'
        f'<div class="ind-chips">{chips}</div>'
        f'{rationale_html}'
        f'</div>'
    )


def build_ta_grid(opportunities: list) -> str:
    if not opportunities:
        return ""
    cards = "".join(build_ta_card(op) for op in opportunities)
    return f'<div class="ta-grid">{cards}</div>'


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

    track_card = build_track_card(data.get("track_record", {}))
    ta_section = (
        f'<section class="section" id="ta">'
        f'<div class="section-label">🎯 הזדמנויות טכניות</div>'
        f'{track_card}'
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


def build_html(data: dict) -> str:
    ticker_items = build_ticker_items(data)
    news_tab     = build_news_feed_tab(data)
    ta_grid      = build_ta_grid(data.get("opportunities", []))
    opps_tab     = build_opportunities_tab(data, ta_grid)

    # Hero market-mood chip from Fear & Greed
    fg = data.get("fear_greed") or {}
    mood_chip = ""
    if fg.get("score") is not None:
        score = fg["score"]
        mood_color = "var(--red)" if score <= 45 else ("var(--green)" if score >= 55 else "var(--gold)")
        mood_chip = (f'<span class="mood-chip" style="color:{mood_color};border-color:currentColor">'
                     f'מצב שוק: {fg.get("rating", "")} <b>{score}</b></span>')

    # Footer engine badge
    ops = data.get("opportunities", [])
    engine_badge = ""
    if ops and ops[0].get("engine") == "rules":
        engine_badge = ' &nbsp;·&nbsp; מנוע חוקים (מצב חינמי)'
    elif ops and ops[0].get("engine") == "claude":
        engine_badge = ' &nbsp;·&nbsp; ניתוח Claude AI'

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
    padding:1.9rem 2rem 1.5rem;text-align:center;position:relative;overflow:hidden;
  }}
  .hero-chips{{display:flex;justify-content:center;gap:.6rem;flex-wrap:wrap;margin-top:.9rem}}
  .mood-chip{{display:inline-flex;align-items:center;gap:.45rem;padding:.35rem 1rem;
    border-radius:999px;font-size:.78rem;font-weight:700;border:1px solid var(--border);
    background:var(--card);color:var(--text)}}
  .mood-chip b{{font-variant-numeric:tabular-nums;font-family:'Inter',sans-serif}}
  body.light .hero{{background:linear-gradient(160deg,#f8fafc 0%,#e0f2fe 50%,#f8fafc 100%)}}
  .hero::before{{content:'';position:absolute;top:-60px;left:50%;transform:translateX(-50%);
    width:600px;height:200px;background:radial-gradient(ellipse,rgba(14,165,233,.12) 0%,transparent 70%);pointer-events:none}}
  .hero-label{{font-size:.65rem;font-weight:700;letter-spacing:.35em;color:var(--accent);text-transform:uppercase;margin-bottom:.6rem}}
  .hero h1{{font-family:'Heebo',sans-serif;font-size:clamp(1.8rem,5vw,3rem);font-weight:900;color:var(--white);letter-spacing:-.04em;line-height:1.1}}
  .hero h1 span{{color:var(--accent)}}
  .hero-sub{{color:var(--muted);font-size:.88rem;margin-top:.5rem}}
  .hero-date{{display:inline-flex;align-items:center;gap:.5rem;
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
  .feed-time{{font-size:.68rem;font-weight:600;color:var(--muted);background:var(--surface);
    border:1px solid var(--border);border-radius:10px;padding:.12rem .55rem;
    font-variant-numeric:tabular-nums}}

  /* ── Morning brief ── */
  .brief-card{{background:var(--card);border:1px solid var(--border);border-right:3px solid var(--accent);
    border-radius:14px;padding:1rem 1.3rem;margin-bottom:1rem}}
  .brief-title{{font-family:'Heebo',sans-serif;font-weight:800;font-size:.95rem;
    color:var(--white);margin-bottom:.6rem}}
  .brief-list{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.45rem}}
  .brief-list li{{font-size:.86rem;color:var(--text);line-height:1.55;padding-right:1rem;position:relative}}
  .brief-list li::before{{content:'';position:absolute;right:0;top:.55em;width:5px;height:5px;
    border-radius:50%;background:var(--accent)}}
  .brief-list b{{color:var(--white);font-variant-numeric:tabular-nums;font-family:'Inter',sans-serif}}

  /* ── Movers strip ── */
  .movers-strip{{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-bottom:1.6rem}}
  .movers-label{{font-size:.68rem;font-weight:700;letter-spacing:.14em;color:var(--muted)}}
  .mover-pill{{font-size:.74rem;font-weight:800;font-family:'Inter',sans-serif;
    font-variant-numeric:tabular-nums;padding:.2rem .65rem;border-radius:10px}}
  .mover-pill.up{{color:var(--green);background:rgba(16,185,129,.12)}}
  .mover-pill.down{{color:var(--red);background:rgba(244,63,94,.12)}}

  /* ── Feed filter chips ── */
  .filter-chips{{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1rem}}
  .filter-chip{{background:var(--card);border:1px solid var(--border);color:var(--muted);
    font-size:.72rem;font-weight:700;padding:.3rem .85rem;border-radius:999px;cursor:pointer;
    transition:color .2s,border-color .2s;font-family:'Heebo',sans-serif}}
  .filter-chip:hover{{color:var(--white)}}
  .filter-chip.active{{color:var(--accent);border-color:var(--accent);background:rgba(6,182,212,.1)}}

  /* ── New-since-visit divider ── */
  .new-divider{{display:flex;align-items:center;gap:.8rem;color:var(--accent);
    font-size:.72rem;font-weight:700;margin:.4rem 0}}
  .new-divider::before,.new-divider::after{{content:'';flex:1;height:1px;background:var(--accent);opacity:.35}}

  /* ── TA sparkline ── */
  .ta-spark{{display:inline-flex;align-items:center;gap:.4rem;margin-right:auto}}
  .ta-spark-label{{font-size:.64rem;color:var(--muted);font-variant-numeric:tabular-nums}}

  /* ── Track record ── */
  .track-card{{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
    background:var(--card);border:1px solid var(--border);border-radius:14px;
    padding:.8rem 1.3rem;margin-bottom:1.6rem;font-size:.82rem;color:var(--text)}}
  .track-stat{{display:inline-flex;align-items:center;gap:.35rem}}
  .track-stat b{{color:var(--white);font-variant-numeric:tabular-nums;font-family:'Inter',sans-serif}}
  .track-stat b.up{{color:var(--green)}}
  .track-stat b.down{{color:var(--red)}}
  .track-pending{{color:var(--muted);font-size:.78rem}}

  /* ── TA cards ── */
  .ta-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}}
  .ta-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;
    padding:1rem 1.3rem;transition:border-color .2s,transform .2s}}
  .ta-card:hover{{border-color:var(--accent);transform:translateY(-2px)}}
  .ta-head{{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin-bottom:.5rem}}
  .ta-ticker{{font-family:'Inter',sans-serif;font-weight:800;font-size:1.15rem;
    letter-spacing:.04em;color:var(--white)}}
  .ta-price{{font-family:'Inter',sans-serif;font-weight:700;font-size:.92rem;
    color:var(--text);font-variant-numeric:tabular-nums}}
  .ta-setup-badge{{font-size:.68rem;font-weight:700;padding:.14rem .55rem;border-radius:5px}}
  .ta-rec{{font-size:.72rem;font-weight:800;padding:.16rem .7rem;border-radius:999px;margin-right:auto}}
  .rec-buy{{color:var(--green);background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.35)}}
  .rec-watch{{color:var(--gold);background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.35)}}
  .rec-wait{{color:var(--muted);background:rgba(100,116,139,.12);border:1px solid var(--border)}}
  .ta-conf-row{{display:flex;align-items:center;gap:.5rem;margin-bottom:.7rem}}
  .conf-label{{font-size:.65rem;color:var(--muted);letter-spacing:.08em}}
  .confidence-dots{{display:inline-flex;gap:.22rem}}
  .conf-dot{{width:7px;height:7px;border-radius:50%;background:var(--border)}}
  .conf-dot.filled{{background:var(--accent)}}
  .stale-badge{{font-size:.62rem;color:var(--gold);background:rgba(245,158,11,.12);
    padding:.1rem .5rem;border-radius:4px;margin-right:auto}}
  /* Levels bar (LTR) */
  .levels-bar{{margin:.4rem 0 .6rem}}
  .levels-track{{position:relative;height:8px;background:var(--surface);border:1px solid var(--border);
    border-radius:999px;overflow:visible}}
  .levels-fill{{position:absolute;top:0;left:0;bottom:0;border-radius:999px;
    background:linear-gradient(90deg,rgba(16,185,129,.35),rgba(6,182,212,.45))}}
  .level-marker{{position:absolute;top:-3px;width:3px;height:14px;border-radius:2px;transform:translateX(-50%)}}
  .mk-price{{background:var(--white)}}
  .mk-entry{{background:var(--accent)}}
  .mk-stop{{background:var(--red)}}
  .level-labels{{display:flex;justify-content:space-between;font-size:.66rem;color:var(--muted);
    font-variant-numeric:tabular-nums;margin-top:.3rem;font-family:'Inter',sans-serif}}
  .lvl-cur{{color:var(--white);font-weight:700}}
  .entry-stop{{display:flex;gap:.8rem;font-size:.72rem;color:var(--text);margin-bottom:.5rem}}
  .es-item b{{color:var(--accent);font-variant-numeric:tabular-nums}}
  .es-item.es-stop b{{color:var(--red)}}
  .ind-chips{{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.55rem}}
  .ind-chip{{font-size:.66rem;font-weight:600;padding:.14rem .5rem;border-radius:10px;
    font-variant-numeric:tabular-nums;font-family:'Inter',sans-serif;
    color:var(--muted);background:rgba(100,116,139,.12)}}
  .ind-chip.good{{color:var(--green);background:rgba(16,185,129,.12)}}
  .ind-chip.bad{{color:var(--red);background:rgba(244,63,94,.12)}}
  .ta-rationale{{font-size:.8rem;color:var(--text);line-height:1.6;margin:0}}

  /* ── Tab Navigation — pill segmented control ── */
  .tab-nav{{display:flex;gap:.35rem;margin:1.4rem auto 1.6rem;padding:.3rem;
    background:var(--card);border:1px solid var(--border);border-radius:999px;
    width:fit-content;max-width:100%}}
  .tab-btn{{background:none;border:none;padding:.55rem 1.6rem;font-size:.95rem;font-weight:800;
    color:var(--muted);cursor:pointer;border-radius:999px;
    transition:color .2s,background .2s;font-family:'Heebo',sans-serif}}
  .tab-btn.active{{color:var(--white);background:rgba(6,182,212,.16);
    box-shadow:inset 0 0 0 1px rgba(6,182,212,.4)}}
  .tab-btn:hover:not(.active){{color:var(--white)}}
  .tab-pane{{display:none}}
  .tab-pane.active{{display:block;animation:fadein .25s ease}}
  @keyframes fadein{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}
  body.light .tab-btn.active{{background:rgba(2,132,199,.12);box-shadow:inset 0 0 0 1px rgba(2,132,199,.4)}}

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
    .ta-grid{{grid-template-columns:1fr}}
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
  <div class="hero-label">לוח שוק אישי</div>
  <h1>חדשות <span>&</span> הזדמנויות</h1>
  <div class="hero-sub">פיד חדשות חי · ניתוח טכני · וול סטריט וישראל</div>
  <div class="hero-chips">
    <div class="hero-date">
      <span class="pulse"></span>
      {TODAY_HE} &nbsp;|&nbsp; עודכן {TIME}
    </div>
    {mood_chip}
    <span class="mood-chip" id="mktStatus" style="display:none"></span>
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
  נוצר ב-{TODAY} בשעה {TIME}{engine_badge} &nbsp;·&nbsp;
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
    // One-time reload when a new SW takes control (so deploys show up immediately)
    var reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', function() {{
      if (reloaded) return;
      reloaded = true;
      location.reload();
    }});
  }}

  // ── US Market Status (live, Israel-friendly) ──
  function updateMarketStatus() {{
    var el = document.getElementById('mktStatus');
    if (!el) return;
    try {{
      var now = new Date();
      var et = new Date(now.toLocaleString('en-US', {{timeZone: 'America/New_York'}}));
      var day = et.getDay(), mins = et.getHours() * 60 + et.getMinutes();
      var open = 9 * 60 + 30, close = 16 * 60, pre = 4 * 60, after = 20 * 60;
      var label, color;
      if (day === 0 || day === 6) {{
        label = 'וול סטריט: סגור (סופ"ש)'; color = 'var(--muted)';
      }} else if (mins >= open && mins < close) {{
        label = 'וול סטריט: שוק פתוח'; color = 'var(--green)';
      }} else if (mins >= pre && mins < open) {{
        var left = open - mins;
        label = 'פרי-מרקט · נפתח בעוד ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
        color = 'var(--gold)';
      }} else if (mins >= close && mins < after) {{
        label = 'אפטר-מרקט'; color = 'var(--gold)';
      }} else {{
        label = 'וול סטריט: סגור'; color = 'var(--muted)';
      }}
      el.textContent = label;
      el.style.color = color;
      el.style.borderColor = 'currentColor';
      el.style.display = 'inline-flex';
    }} catch (e) {{}}
  }}
  updateMarketStatus();
  setInterval(updateMarketStatus, 60000);

  // ── Feed tag filter ──
  function filterFeed(tag, btn) {{
    document.querySelectorAll('.filter-chip').forEach(function(c) {{ c.classList.remove('active'); }});
    if (btn) btn.classList.add('active');
    document.querySelectorAll('#feedList .news-card').forEach(function(card) {{
      card.style.display = (tag === '*' || card.getAttribute('data-tag') === tag) ? '' : 'none';
    }});
  }}

  // ── New-since-last-visit divider ──
  (function() {{
    try {{
      var last = parseInt(localStorage.getItem('lastVisit') || '0', 10);
      var cards = document.querySelectorAll('#feedList .news-card');
      if (last > 0 && cards.length > 1) {{
        var lastNew = null;
        cards.forEach(function(card) {{
          var ts = parseInt(card.getAttribute('data-ts') || '0', 10);
          if (ts > last) lastNew = card;
        }});
        if (lastNew && lastNew !== cards[cards.length - 1]) {{
          var div = document.createElement('div');
          div.className = 'new-divider';
          div.textContent = '— חדש מאז הביקור הקודם —';
          lastNew.parentNode.insertBefore(div, lastNew.nextSibling);
        }}
      }}
      localStorage.setItem('lastVisit', String(Math.floor(Date.now() / 1000)));
    }} catch (e) {{}}
  }})();
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

    # 2c. Technical analysis — 1y history + indicators for watchlist + trending
    print("\n[ 2c ] מחשב אינדיקטורים טכניים...")
    ta_universe = build_ta_universe(all_wl_stocks, trending)
    ta_hist     = fetch_ta_history(ta_universe)
    quote_by_ticker = {w.get("ticker", "").upper(): w.get("price") for w in watchlist if w.get("price")}
    indicators = {}
    for t in ta_universe:
        if t in ta_hist:
            try:
                indicators[t] = compute_indicators(t, ta_hist[t], quote_by_ticker.get(t))
            except Exception as e:
                print(f"  ✗ אינדיקטורים {t}: {e}")

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

    # 3b. TA — Claude when available, otherwise the free rule engine
    opportunities = []
    if client and indicators:
        try:
            print("\n[ 3b ] מנתח הזדמנויות טכניות עם Claude...")
            claude_ops = run_ta_agent(client, list(indicators.values()))
            opportunities = merge_ta_results(claude_ops, indicators)
            for op in opportunities:
                op["engine"] = "claude"
        except Exception as e:
            print(f"✗ Claude TA נכשל: {e} — עובר למנוע חוקים")
            opportunities = run_rule_based_ta(indicators)
    elif indicators:
        print("\n[ 3b ] מנתח הזדמנויות עם מנוע חוקים (מצב חינמי)...")
        opportunities = run_rule_based_ta(indicators)
    if not opportunities:
        # Yahoo history failed entirely — carry forward the previous run's analysis
        opportunities = load_previous_opportunities()
    if opportunities:
        buys = sum(1 for o in opportunities if o["recommendation"] == "קנייה")
        print(f"✓ ניתוח טכני ({opportunities[0].get('engine','?')}): {len(opportunities)} טיקרים, {buys} המלצות קנייה")

    # 3c. Track record — log buys, evaluate open positions (reads prev data.json first)
    prev_data = load_previous_data()
    track_summary = update_track_record(opportunities)

    # 4. Assemble full data dict
    data = {
        **news_data,
        "watchlist":       watchlist,
        "opportunities":   opportunities,
        "track_record":    track_summary,
        "prev_fear_greed": prev_data.get("fear_greed"),
        "market_us":       mkt["market_us"],
        "commodities":     mkt["commodities"],
        "market_il":       mkt["market_il"],
        "sectors":         mkt["sectors"],
        "sparklines":      sparks,
        "fear_greed":      fg,
        "events":          get_upcoming_events(5),
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
        "generated_at":    _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "watchlist":       watchlist,
        "opportunities":   opportunities,
        "ta_generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat() if opportunities and not any(o.get("stale") for o in opportunities) else "",
        "market_us":       mkt["market_us"],
        "commodities":     mkt["commodities"],
        "market_il":       mkt["market_il"],
        "sectors":         mkt["sectors"],
        "fear_greed":      fg,
        "us_news":         [{k: v for k, v in n.items() if k != "image"} for n in data.get("us_news", [])],
        "il_news":         [{k: v for k, v in n.items() if k != "image"} for n in data.get("israel_news", [])],
        "track_record":    track_summary,
        "alert_config":    wl_config.get("alerts", {}),
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
