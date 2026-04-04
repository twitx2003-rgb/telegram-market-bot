#!/usr/bin/env python3
"""
לוח חדשות יומי — שוק ההון האמריקאי + חדשות ישראל
Personal News Dashboard: Wall Street + Israel News
"""

import anthropic
import concurrent.futures
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
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

# Auto-detect environment: GitHub Actions writes to docs/index.html (relative),
# local Windows writes to the full OneDrive path.
if os.environ.get("GITHUB_ACTIONS"):
    OUTPUT_PATH = Path("docs/index.html")
    MODEL       = os.environ.get("DASHBOARD_MODEL", "claude-haiku-4-5-20251001")
else:
    IDOP_DIR    = Path("C:/Users/idoph/OneDrive/IDOP")
    OUTPUT_PATH = IDOP_DIR / "reports/docs/index.html"
    MODEL       = os.environ.get("DASHBOARD_MODEL", "claude-opus-4-6")

MAX_TOKENS  = 12000

_now   = datetime.now()
TODAY  = _now.strftime("%d.%m.%Y")
TIME   = _now.strftime("%H:%M")

HEBREW_MONTHS = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]
TODAY_HE = f"{_now.day} ב{HEBREW_MONTHS[_now.month]} {_now.year}"

# ── RSS Feed Definitions ───────────────────────────────────────────────────────

US_FEEDS = [
    {"name": "Reuters Business",  "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Markets",      "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch",       "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance",     "url": "https://finance.yahoo.com/news/rssindex"},
]

ISRAEL_FEEDS = [
    {"name": "Jerusalem Post",    "url": "https://www.jpost.com/Rss/RssFeedsHeadlines.aspx"},
    {"name": "Times of Israel",   "url": "https://www.timesofisrael.com/feed/"},
    {"name": "Haaretz",           "url": "https://www.haaretz.com/cmlink/1.628765"},
    {"name": "Walla News",        "url": "https://rss.walla.co.il/feed/1"},
]

# ── Relevance Filters (from market_news.py) ────────────────────────────────────

US_HIGH_IMPACT = [
    "federal reserve", "rate hike", "rate cut", "interest rate", "fomc",
    "merger", "acquisition", "buyout", "takeover",
    "ipo", "bankruptcy", "bankrupt", "default",
    "earnings beat", "earnings miss", "beats estimates", "misses estimates",
    "layoffs", "crash", "plunge", "surge", "soar",
    "trade war", "tariff", "sanctions", "jobs report", "gdp",
]
US_MEDIUM_IMPACT = [
    "earnings", "revenue", "profit", "loss", "guidance",
    "fed ", "inflation", "recession",
    "s&p 500", "nasdaq", "dow jones",
    "apple", "microsoft", "google", "alphabet", "amazon", "tesla",
    "meta", "nvidia", "openai", "jpmorgan", "goldman sachs",
    "oil", "gold", "bitcoin", "rally", "drop",
    "netflix", "amd", "intel", "visa", "mastercard", "paypal", "salesforce",
    "pfizer", "moderna", "disney", "boeing", "bank of america", "wells fargo",
]
US_NOISE = [
    "should i", "what do you think", "advice", "help me", "my portfolio",
    "is it worth", "eli5", "how do i", "first time", "noob", "beginner",
    "what should", "anyone else", "opinion",
]

IL_KEYWORDS_EN = [
    "israel", "gaza", "hamas", "netanyahu", "knesset", "idf", "hezbollah",
    "tel aviv", "jerusalem", "hostage", "ceasefire", "war", "operation",
    "shekel", "bank of israel", "economy", "government", "coalition",
    "west bank", "iran", "biden", "trump", "minister", "protest", "reform",
    "judicial", "democracy", "election", "security",
]
IL_KEYWORDS_HE = [
    "ממשלה", "ביטחון", "כלכלה", "מלחמה", "בורסה", "שקל", "נתניהו",
    "כנסת", "צבא", "חרב", "עזה", "חמאס", "חטופים", "הפגנה", "רפורמה",
    "משפטית", "איראן", "בנק", "ריבית", "אינפלציה", "תקציב",
]


def is_us_relevant(title: str) -> bool:
    t = title.lower()
    if any(n in t for n in US_NOISE):
        return False
    if any(kw in t for kw in US_HIGH_IMPACT):
        return True
    return sum(1 for kw in US_MEDIUM_IMPACT if kw in t) >= 2


def is_il_relevant(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in IL_KEYWORDS_EN) or any(kw in title for kw in IL_KEYWORDS_HE)


# ── RSS Fetching ────────────────────────────────────────────────────────────────

def fetch_rss(feeds: list, max_per_feed: int = 10) -> list:
    """Fetch headlines from RSS feeds (last 36 hours)."""
    if not HAS_FEEDPARSER:
        print("⚠  feedparser not installed — skipping RSS fetch")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
    results = []

    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"])
            count = 0
            for entry in parsed.entries:
                if count >= max_per_feed:
                    break
                title = (entry.get("title") or "").strip()
                link  = entry.get("link", "")
                if not title:
                    continue
                # date filter (best-effort)
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                results.append({"title": title, "link": link, "source": feed["name"]})
                count += 1
            print(f"  ✓ {feed['name']}: {count} articles")
        except Exception as e:
            print(f"  ✗ {feed['name']}: {e}")

    return results


def filter_headlines(items: list, filter_fn, max_out: int) -> list:
    """Apply relevance filter and return up to max_out items."""
    relevant = [i for i in items if filter_fn(i["title"])]
    if len(relevant) < 4:
        relevant = items  # fallback: skip filtering if too few
    return relevant[:max_out]


# ── Yahoo Finance Market Data ──────────────────────────────────────────────────

# Symbol definitions: (symbol, display_name, group, format_type)
_YF_SYMBOLS = [
    ("^GSPC",   "S&P 500",           "market_us",   "index"),
    ("^IXIC",   "Nasdaq",            "market_us",   "index"),
    ("^DJI",    "Dow Jones",         "market_us",   "index"),
    ("^RUT",    "Russell 2000",      "market_us",   "index"),
    ("^VIX",    "VIX",               "market_us",   "vix"),
    ("GC=F",    "זהב",               "commodities", "commodity"),
    ("BZ=F",    "נפט ברנט",          "commodities", "commodity"),
    ("BTC-USD", "ביטקוין",           "commodities", "btc"),
    ("^TNX",    'אג"ח ארה"ב 10Y',   "commodities", "tnx"),
    ("TA35.TA", 'ת"א 35',            "market_il",   "index"),
    ("ILS=X",   "דולר/שקל",          "market_il",   "ils"),
]
_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _format_yf(price: float, pct: float, fmt: str) -> tuple:
    """Format price + pct into (value_str, change_str, direction)."""
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    change = f"{pct:+.2f}%"
    if fmt == "index":
        value = f"{price:,.0f}"
    elif fmt == "vix":
        value = f"{price:.2f}"
    elif fmt == "commodity":
        value = f"${price:,.1f}"
    elif fmt == "btc":
        value = f"${price:,.0f}"
    elif fmt == "tnx":
        value = f"{price:.2f}%"
    elif fmt == "ils":
        value = f"{price:.3f}"
    else:
        value = f"{price:,.2f}"
    return value, change, direction


def _fetch_yf_one(args: tuple) -> dict:
    """Fetch a single Yahoo Finance symbol. Returns market card dict."""
    sym, name, group, fmt = args
    placeholder = {"name": name, "value": "—", "change": "—", "direction": "flat", "group": group}
    if not HAS_REQUESTS:
        return placeholder
    try:
        url  = _YF_BASE.format(sym=urllib.parse.quote(sym))
        resp = _requests.get(url, headers=_YF_HEADERS, timeout=8)
        resp.raise_for_status()
        meta  = resp.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        pct   = float(meta.get("regularMarketChangePercent", 0.0))
        value, change, direction = _format_yf(price, pct, fmt)
        return {"name": name, "value": value, "change": change, "direction": direction, "group": group}
    except Exception as e:
        print(f"  ✗ {sym}: {e}")
        return placeholder


def fetch_market_data() -> dict:
    """
    Fetch live market data from Yahoo Finance (free, no API key needed).
    Returns dict with keys: market_us, commodities, market_il
    """
    if not HAS_REQUESTS:
        print("  ⚠ requests לא זמין — מדדים לא זמינים")
        na = lambda n: {"name": n, "value": "N/A", "change": "—", "direction": "flat"}
        return {
            "market_us":   [na(n) for _, n, g, _ in _YF_SYMBOLS if g == "market_us"],
            "commodities": [na(n) for _, n, g, _ in _YF_SYMBOLS if g == "commodities"],
            "market_il":   [na(n) for _, n, g, _ in _YF_SYMBOLS if g == "market_il"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(_fetch_yf_one, _YF_SYMBOLS))

    market_us   = [r for r in results if r["group"] == "market_us"]
    commodities = [r for r in results if r["group"] == "commodities"]
    market_il   = [r for r in results if r["group"] == "market_il"]

    ok = sum(1 for r in results if r["value"] != "—")
    print(f"  ✓ שוק: {ok}/{len(_YF_SYMBOLS)} סמלים נטענו")
    return {"market_us": market_us, "commodities": commodities, "market_il": market_il}


# ── Image Fetching ─────────────────────────────────────────────────────────────

def _picsum_url(seed_text: str) -> str:
    """Return a deterministic placeholder image URL based on seed text."""
    h = abs(hash(seed_text)) % 1000
    return f"https://picsum.photos/seed/{h}/400/200"


def fetch_og_image(url: str) -> str:
    """
    Try to scrape og:image from the article URL.
    Reads only the first 50 KB to avoid large downloads.
    Returns image URL or a picsum placeholder.
    """
    if not HAS_REQUESTS or not url or url == "#":
        return _picsum_url(url or "default")
    try:
        resp = _requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsDashbot/1.0)"},
            timeout=5,
            stream=True,
        )
        # Read only first 50 KB
        html = b""
        for chunk in resp.iter_content(chunk_size=4096):
            html += chunk
            if len(html) >= 51200:
                break
        html_str = html.decode("utf-8", errors="replace")

        # Try both attribute orders (property before content, OR content before property)
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content="([^"]+)"',
        ]
        for pat in patterns:
            m = re.search(pat, html_str, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith("http"):
                    return img
    except Exception:
        pass
    return _picsum_url(url)


def fetch_all_images(news_items: list) -> list:
    """Fetch og:image for all items in parallel. Returns list of image URLs."""
    if not news_items:
        return []
    links = [item.get("link", "") for item in news_items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(fetch_og_image, links))
    original = sum(1 for r in results if "picsum" not in r)
    print(f"  ✓ תמונות: {original}/{len(results)} מקוריות (שאר — picsum)")
    return results


# ── Claude Enrichment Agent ────────────────────────────────────────────────────

def run_enrichment_agent(client: anthropic.Anthropic, us_raw: list, il_raw: list) -> dict:
    """
    Single Claude API call (NO web search — market data handled by Yahoo Finance).
    Tasks: translate + summarize US headlines in Hebrew + category tagging for Israel news.
    Returns dict with keys: us_news, israel_news (no market data keys).
    """
    us_titles = "\n".join(f"{i+1}. [{item['source']}] {item['title']}" for i, item in enumerate(us_raw))
    il_titles = "\n".join(f"{i+1}. [{item['source']}] {item['title']}" for i, item in enumerate(il_raw))

    system = f"""אתה עיתונאי פיננסי בכיר ומומחה לחדשות ישראל.
היום: {TODAY_HE} ({TODAY}).
החזר אובייקט JSON בלבד — ללא markdown, ללא טקסט נוסף.

עליך לבצע:
1. לכל כותרת US: כתוב כותרת בעברית (תרגום חופשי ותמציתי) + סיכום 2-3 משפטים בעברית + תגית מהרשימה: EARNINGS/MACRO/FED/TECH/M&A/ENERGY/CRYPTO/BANKS
2. לכל כותרת ישראלית: כתוב כותרת בעברית + סיכום 2-3 משפטים בעברית + תגית מהרשימה: ביטחון/פוליטיקה/כלכלה/חברה/דיפלומטיה

החזר JSON בדיוק במבנה הבא:
{{
  "us_news": [
    {{
      "title_he": "כותרת בעברית",
      "summary_he": "סיכום בעברית 2-3 משפטים.",
      "source": "Reuters",
      "link": "https://...",
      "tag": "EARNINGS"
    }}
  ],
  "israel_news": [
    {{
      "title_he": "כותרת בעברית",
      "summary_he": "סיכום בעברית 2-3 משפטים.",
      "source": "Jerusalem Post",
      "link": "https://...",
      "tag": "ביטחון"
    }}
  ]
}}

כתוב את כל הכותרות והסיכומים בעברית בלבד.
אל תכלול הערות, אל תוסיף markdown. JSON בלבד."""

    user_msg = f"""עבד את כותרות החדשות הבאות ל-{TODAY_HE}.

כותרות US לתרגום וסיכום ({len(us_raw)} כותרות):
{us_titles}

כותרות ישראל לתרגום וסיכום ({len(il_raw)} כותרות):
{il_titles}

צרף כותרת בעברית + סיכום קצר + תגית לכל כותרת. החזר JSON מלא."""

    messages = [{"role": "user", "content": user_msg}]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] מפעיל agent העשרה (ללא web search)...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )

    print(f"[{datetime.now().strftime('%H:%M:%S')}] stop_reason={response.stop_reason}")

    text_blocks = [b for b in response.content if b.type == "text"]
    if text_blocks:
        raw = text_blocks[-1].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    raise ValueError("לא התקבלה תגובת טקסט מה-agent")


# ── Fallback: RSS-only mode ────────────────────────────────────────────────────

def fallback_data(us_raw: list, il_raw: list) -> dict:
    """Build minimal data dict without Claude API (RSS + Google Translate fallback)."""
    def tr(text):
        if HAS_TRANSLATOR:
            try:
                return GoogleTranslator(source="en", target="iw").translate(text)
            except Exception:
                pass
        return text

    us_news = []
    for item in us_raw:
        us_news.append({
            "title_he": tr(item["title"]),
            "summary_he": "",
            "source": item["source"],
            "link": item["link"],
            "tag": "NEWS",
        })

    il_news = []
    for item in il_raw:
        il_news.append({
            "title_he": tr(item["title"]),
            "summary_he": "",
            "source": item["source"],
            "link": item["link"],
            "tag": "כללי",
        })

    mkt = fetch_market_data()

    return {
        "market_us":   mkt["market_us"],
        "commodities": mkt["commodities"],
        "market_il":   mkt["market_il"],
        "us_news":     us_news,
        "israel_news": il_news,
    }


# ── HTML Builder ───────────────────────────────────────────────────────────────

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
    "ביטחון":     ("var(--red)",    "rgba(239,68,68,0.12)"),
    "פוליטיקה":   ("var(--accent)", "rgba(14,165,233,0.12)"),
    "כלכלה":      ("var(--green)",  "rgba(34,197,94,0.12)"),
    "חברה":       ("var(--purple)", "rgba(168,85,247,0.12)"),
    "דיפלומטיה":  ("var(--gold)",   "rgba(245,158,11,0.12)"),
    "כללי":       ("var(--muted)",  "rgba(100,116,139,0.12)"),
}


def _arrow(direction: str) -> str:
    return "▲" if direction == "up" else "▼" if direction == "down" else "–"


def _css(direction: str) -> str:
    return "up" if direction == "up" else "down" if direction == "down" else "flat"


def build_market_card(m: dict) -> str:
    css = _css(m.get("direction", "flat"))
    arrow = _arrow(m.get("direction", "flat"))
    return (
        f'<div class="mkt-card">'
        f'<div class="mkt-name">{m["name"]}</div>'
        f'<div class="mkt-value">{m["value"]}</div>'
        f'<div class="mkt-change {css}">{arrow} {m["change"]}</div>'
        f'</div>'
    )


def build_us_news_card(n: dict, idx: int) -> str:
    tag = n.get("tag", "NEWS")
    color, bg = TAG_COLORS_US.get(tag, TAG_COLORS_US["NEWS"])
    link = n.get("link", "#")
    image = n.get("image", "")
    img_html = (
        f'<img class="news-img" src="{image}" alt="" '
        f'onerror="this.style.display=\'none\'" loading="lazy"/>'
    ) if image else ""
    read_more = f'<a href="{link}" target="_blank" class="read-more">קרא עוד ←</a>' if link and link != "#" else ""
    return (
        f'<div class="news-card">'
        f'<div class="news-num">{idx:02d}</div>'
        f'<div class="news-body">'
        + img_html
        + f'<span class="news-tag" style="color:{color};background:{bg}">{tag}</span>'
        f'<div class="news-title">{n["title_he"]}</div>'
        + (f'<div class="news-summary">{n["summary_he"]}</div>' if n.get("summary_he") else "")
        + f'<div class="news-meta">{n.get("source","")} {read_more}</div>'
        f'</div>'
        f'</div>'
    )


def build_il_news_card(n: dict) -> str:
    tag = n.get("tag", "כללי")
    color, bg = TAG_COLORS_IL.get(tag, TAG_COLORS_IL["כללי"])
    link = n.get("link", "#")
    image = n.get("image", "")
    img_html = (
        f'<img class="news-img" src="{image}" alt="" '
        f'onerror="this.style.display=\'none\'" loading="lazy"/>'
    ) if image else ""
    read_more = f'<a href="{link}" target="_blank" class="read-more">קרא עוד ←</a>' if link and link != "#" else ""
    return (
        f'<div class="news-card il-card">'
        f'<div class="news-body">'
        + img_html
        + f'<span class="news-tag" style="color:{color};background:{bg}">{tag}</span>'
        f'<div class="news-title">{n["title_he"]}</div>'
        + (f'<div class="news-summary">{n["summary_he"]}</div>' if n.get("summary_he") else "")
        + f'<div class="news-meta">{n.get("source","")} {read_more}</div>'
        f'</div>'
        f'</div>'
    )


def build_html(data: dict) -> str:
    us_market_cards  = "".join(build_market_card(m) for m in data.get("market_us", []))
    comm_cards       = "".join(build_market_card(m) for m in data.get("commodities", []))
    il_market_cards  = "".join(build_market_card(m) for m in data.get("market_il", []))
    us_news_cards    = "".join(build_us_news_card(n, i+1) for i, n in enumerate(data.get("us_news", [])))
    il_news_cards    = "".join(build_il_news_card(n) for n in data.get("israel_news", []))

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
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter','Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.65;min-height:100vh}}

  /* ── Sticky Nav ── */
  .nav{{
    position:sticky;top:0;z-index:100;
    background:rgba(7,11,15,0.92);
    backdrop-filter:blur(12px);
    border-bottom:1px solid var(--border);
    padding:.6rem 1.5rem;
    display:flex;gap:.8rem;align-items:center;justify-content:center;
  }}
  .nav a{{
    color:var(--muted);text-decoration:none;font-size:.8rem;font-weight:600;
    padding:.35rem .85rem;border-radius:6px;border:1px solid transparent;
    transition:all .2s;
  }}
  .nav a:hover{{color:var(--accent);border-color:var(--accent);background:rgba(14,165,233,.08)}}
  .nav-sep{{color:var(--border);font-size:.9rem}}

  /* ── Hero ── */
  .hero{{
    background:linear-gradient(160deg,#070b0f 0%,#0c1929 50%,#070b0f 100%);
    border-bottom:1px solid var(--border);
    padding:3rem 2rem 2.5rem;text-align:center;position:relative;overflow:hidden;
  }}
  .hero::before{{
    content:'';position:absolute;top:-60px;left:50%;transform:translateX(-50%);
    width:600px;height:200px;
    background:radial-gradient(ellipse,rgba(14,165,233,.12) 0%,transparent 70%);
    pointer-events:none;
  }}
  .hero-label{{font-size:.68rem;font-weight:700;letter-spacing:.35em;color:var(--accent);text-transform:uppercase;margin-bottom:.7rem}}
  .hero h1{{font-size:2.4rem;font-weight:900;color:var(--white);letter-spacing:-.03em;line-height:1.1}}
  .hero h1 span{{color:var(--accent)}}
  .hero-sub{{color:var(--muted);font-size:.9rem;margin-top:.6rem}}
  .hero-date{{
    display:inline-flex;align-items:center;gap:.5rem;margin-top:1.2rem;
    background:rgba(14,165,233,.1);border:1px solid rgba(14,165,233,.3);
    color:var(--accent);padding:.4rem 1.2rem;border-radius:999px;font-size:.82rem;font-weight:600;
  }}
  .pulse{{width:7px;height:7px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(1.4)}}}}

  /* ── Layout ── */
  .container{{max-width:1140px;margin:0 auto;padding:2rem 1.5rem 5rem}}
  .section{{margin-bottom:3rem}}
  .section-label{{
    font-size:.68rem;font-weight:700;letter-spacing:.3em;text-transform:uppercase;
    color:var(--accent);margin-bottom:1.2rem;padding-bottom:.6rem;
    border-bottom:1px solid var(--border);display:flex;align-items:center;gap:.5rem;
  }}

  /* ── Market Strips ── */
  .mkt-strip{{display:flex;flex-wrap:wrap;gap:.85rem;margin-bottom:.5rem}}
  .mkt-card{{
    background:var(--card);border:1px solid var(--border);border-radius:10px;
    padding:1rem 1.3rem;min-width:130px;flex:1;transition:border-color .2s;
  }}
  .mkt-card:hover{{border-color:var(--accent)}}
  .mkt-name{{font-size:.7rem;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.3rem}}
  .mkt-value{{font-size:1.5rem;font-weight:700;color:var(--white);font-variant-numeric:tabular-nums}}
  .mkt-change{{font-size:.85rem;font-weight:600;margin-top:.2rem}}
  .up{{color:var(--green)}}.down{{color:var(--red)}}.flat{{color:var(--muted)}}

  /* ── News Cards ── */
  .news-list{{display:flex;flex-direction:column;gap:.9rem}}
  .news-card{{
    background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:1.3rem 1.5rem;display:grid;grid-template-columns:48px 1fr;gap:1rem;align-items:start;
    transition:border-color .2s;
  }}
  .news-card:hover{{border-color:var(--accent)}}
  .il-card{{grid-template-columns:1fr}}
  .news-num{{
    font-size:1.6rem;font-weight:800;color:var(--border);line-height:1;
    font-variant-numeric:tabular-nums;text-align:center;padding-top:.1rem;
  }}
  .news-tag{{
    display:inline-block;padding:.15rem .6rem;border-radius:4px;
    font-size:.68rem;font-weight:700;letter-spacing:.05em;margin-left:.5rem;
  }}
  .news-title{{font-size:1.02rem;font-weight:600;color:var(--white);margin:.4rem 0}}
  .news-summary{{font-size:.87rem;color:var(--text);margin-bottom:.5rem;line-height:1.6}}
  .news-meta{{font-size:.74rem;color:var(--muted);display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}}
  .read-more{{color:var(--accent);text-decoration:none;font-weight:600;font-size:.74rem}}
  .read-more:hover{{text-decoration:underline}}

  /* ── News Images ── */
  .news-img{{
    width:100%;
    height:180px;
    object-fit:cover;
    border-radius:8px;
    margin-bottom:.75rem;
    display:block;
    background:var(--border);
  }}

  /* ── IL strip card (smaller) ── */
  .mkt-strip.il .mkt-card{{min-width:140px;max-width:200px;flex:0 0 auto}}

  /* ── Footer ── */
  footer{{
    text-align:center;padding:2rem;font-size:.74rem;color:var(--muted);
    border-top:1px solid var(--border);
  }}
  footer a{{color:var(--accent);text-decoration:none}}
  footer a:hover{{text-decoration:underline}}

  @media(max-width:600px){{
    .hero h1{{font-size:1.7rem}}
    .mkt-card{{min-width:110px}}
    .news-card{{grid-template-columns:1fr}}
    .news-num{{display:none}}
  }}
</style>
</head>
<body>

<!-- Sticky Nav -->
<nav class="nav">
  <a href="#us">🇺🇸 שוק ההון</a>
  <span class="nav-sep">|</span>
  <a href="#israel">🇮🇱 ישראל</a>
  <span class="nav-sep">|</span>
  <a href="report.html">לדוח המלא</a>
</nav>

<!-- Hero -->
<div class="hero">
  <div class="hero-label">לוח חדשות אישי</div>
  <h1>שוק ההון <span>&</span> ישראל</h1>
  <div class="hero-sub">חדשות וול סטריט · חברות אמריקאיות · חדשות ישראל</div>
  <div class="hero-date">
    <span class="pulse"></span>
    {TODAY_HE} &nbsp;|&nbsp; עודכן {TIME}
  </div>
</div>

<div class="container">

  <!-- US Markets -->
  <section class="section" id="us">
    <div class="section-label">📈 מדדים אמריקאיים</div>
    <div class="mkt-strip">
      {us_market_cards}
    </div>
    <div class="mkt-strip" style="margin-top:.85rem">
      {comm_cards}
    </div>
  </section>

  <!-- Israel Markets -->
  <div class="mkt-strip il" style="margin-bottom:2rem">
    <div class="section-label" style="width:100%;border-bottom:none;margin-bottom:.5rem">🏦 שוק ישראלי</div>
    {il_market_cards}
  </div>

  <!-- US News -->
  <section class="section" id="us-news">
    <div class="section-label">📰 חדשות וול סטריט</div>
    <div class="news-list">
      {us_news_cards}
    </div>
  </section>

  <!-- Israel News -->
  <section class="section" id="israel">
    <div class="section-label">🇮🇱 חדשות ישראל</div>
    <div class="news-list">
      {il_news_cards}
    </div>
  </section>

</div>

<footer>
  נוצר ב-{TODAY} בשעה {TIME} &nbsp;·&nbsp;
  <a href="report.html">דוח שוק ההון המלא</a> &nbsp;·&nbsp;
  אינו מהווה ייעוץ השקעות
</footer>

</body>
</html>"""


# ── Telegram ──────────────────────────────────────────────────────────────────

def _tg_arrow(direction: str) -> str:
    return "🟢" if direction == "up" else "🔴" if direction == "down" else "⚪"


def build_telegram_message(data: dict) -> str:
    lines = [f"📊 <b>לוח חדשות — {TODAY}</b>\n"]

    # US markets compact
    for m in data.get("market_us", [])[:4]:
        lines.append(f"{_tg_arrow(m.get('direction',''))} <b>{m['name']}</b> {m['value']} {m['change']}")

    # Commodities
    comms = data.get("commodities", [])
    if comms:
        parts = [f"{_tg_arrow(c.get('direction',''))}{c['name']} {c['change']}" for c in comms]
        lines.append("\n" + "  |  ".join(parts))

    # Israel markets
    il_mkt = data.get("market_il", [])
    if il_mkt:
        lines.append("\n🇮🇱 " + "  |  ".join(f"{m['name']} {m['value']} {m['change']}" for m in il_mkt))

    lines.append("")

    # Top US headlines
    us_news = data.get("us_news", [])[:3]
    if us_news:
        lines.append("🇺🇸 <b>וול סטריט:</b>")
        for n in us_news:
            lines.append(f"• {n['title_he']}")

    lines.append("")

    # Top Israel headlines
    il_news = data.get("israel_news", [])[:4]
    if il_news:
        lines.append("🇮🇱 <b>ישראל:</b>")
        for n in il_news:
            lines.append(f"• {n['title_he']}")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠  Telegram: TELEGRAM_BOT_TOKEN או TELEGRAM_CHAT_ID לא מוגדרים — דילוג.")
        return False

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        req    = urllib.request.Request(url, data=data, method="POST")
        resp   = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        if result.get("ok"):
            print(f"✓ Telegram: הודעה נשלחה ל-chat_id {chat_id}")
            return True
        print(f"✗ Telegram error: {result}")
        return False
    except Exception as e:
        print(f"✗ Telegram connection error: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  לוח חדשות יומי — {TODAY_HE}")
    print(f"{'='*55}\n")

    # 1. Fetch RSS
    print("[ שלב 1 ] שולף כותרות RSS...")
    print("  US:")
    us_raw_all = fetch_rss(US_FEEDS, max_per_feed=12)
    print("  ישראל:")
    il_raw_all = fetch_rss(ISRAEL_FEEDS, max_per_feed=12)

    us_raw = filter_headlines(us_raw_all, is_us_relevant, 8)
    il_raw = filter_headlines(il_raw_all, is_il_relevant, 10)
    print(f"\n  נבחרו: {len(us_raw)} כותרות US, {len(il_raw)} כותרות ישראל")

    # 2. Claude Enrichment
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    data = None

    # 2. Fetch live market data (always — independent of Claude API)
    print("\n[ שלב 2 ] שולף נתוני שוק מ-Yahoo Finance...")
    mkt = fetch_market_data()

    # 3. Claude Enrichment (news summaries + Hebrew translation)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    data = None

    if api_key:
        try:
            print("\n[ שלב 3 ] מעשיר כותרות עם Claude API...")
            client = anthropic.Anthropic(api_key=api_key)
            data = run_enrichment_agent(client, us_raw, il_raw)
            # Attach original links to enriched items (Claude may return empty links)
            for i, n in enumerate(data.get("us_news", [])):
                if i < len(us_raw) and not n.get("link"):
                    n["link"] = us_raw[i]["link"]
            for i, n in enumerate(data.get("israel_news", [])):
                if i < len(il_raw) and not n.get("link"):
                    n["link"] = il_raw[i]["link"]
            print("✓ העשרה הושלמה")
        except Exception as e:
            print(f"✗ Claude API נכשל: {e}")
            print("  עובר למצב RSS-only fallback...")
    else:
        print("⚠  ANTHROPIC_API_KEY לא מוגדר — מצב RSS-only")

    if data is None:
        data = fallback_data(us_raw, il_raw)

    # Always overwrite market data with Yahoo Finance results
    data["market_us"]   = mkt["market_us"]
    data["commodities"] = mkt["commodities"]
    data["market_il"]   = mkt["market_il"]

    # 4. Fetch images for news items (parallel)
    print("\n[ שלב 4 ] שולף תמונות לכתבות...")
    print("  US:")
    us_images = fetch_all_images(data.get("us_news", []))
    for i, img in enumerate(us_images):
        if i < len(data["us_news"]):
            data["us_news"][i]["image"] = img
    print("  ישראל:")
    il_images = fetch_all_images(data.get("israel_news", []))
    for i, img in enumerate(il_images):
        if i < len(data["israel_news"]):
            data["israel_news"][i]["image"] = img

    # 5. Build HTML
    print("\n[ שלב 5 ] בונה HTML...")
    html = build_html(data)

    # 6. Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"✓ HTML נשמר → {OUTPUT_PATH}")
    print(f"  פתח בדפדפן: file:///{OUTPUT_PATH.as_posix()}")

    # 7. Telegram
    print("\n[ שלב 6 ] שולח ל-Telegram...")
    send_telegram(build_telegram_message(data))

    print(f"\n✓ הושלם בהצלחה — {TODAY} {TIME}\n")


if __name__ == "__main__":
    main()
