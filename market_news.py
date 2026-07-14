import feedparser
import requests
import json
import os
import sys
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from deep_translator import GoogleTranslator

# תמיכה בעברית ב-Windows terminal
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ===================== הגדרות =====================
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "")
EMAIL_TO       = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
DOCS_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")
REPORT_URL = "https://twitx2003-rgb.github.io/telegram-market-bot/report.html"
# ==================================================

# מקורות — Wall Street קודם, Reddit אחרון
FEEDS = [
    {"name": "Reuters Business",        "url": "https://feeds.reuters.com/reuters/businessNews",            "category": "חדשות עולמיות"},
    {"name": "CNBC Markets",            "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",    "category": "שוק ההון"},
    {"name": "MarketWatch",             "url": "https://feeds.marketwatch.com/marketwatch/topstories/",     "category": "שוק ההון"},
    {"name": "Yahoo Finance",           "url": "https://finance.yahoo.com/news/rssindex",                   "category": "פיננסים"},
    {"name": "Investing.com",           "url": "https://www.investing.com/rss/news.rss",                    "category": "השקעות"},
    {"name": "Reddit WallStreetBets",   "url": "https://www.reddit.com/r/wallstreetbets/new.rss",           "category": "Reddit"},
    {"name": "Reddit Investing",        "url": "https://www.reddit.com/r/investing/new.rss",                "category": "Reddit"},
    {"name": "Reddit Stocks",           "url": "https://www.reddit.com/r/stocks/new.rss",                   "category": "Reddit"},
]

HIGH_IMPACT = [
    "federal reserve", "rate hike", "rate cut", "interest rate decision",
    "merger", "acquisition", "buyout", "takeover",
    "ipo", "bankruptcy", "bankrupt", "default",
    "earnings beat", "earnings miss", "beats estimates", "misses estimates",
    "layoffs", "mass layoff", "crash", "plunge", "surge", "soar",
    "trade war", "tariff", "sanctions", "jobs report", "gdp",
]

MEDIUM_IMPACT = [
    "earnings", "revenue", "profit", "loss", "guidance",
    "fed ", "inflation", "recession",
    "s&p 500", "nasdaq", "dow jones",
    "apple", "microsoft", "google", "alphabet", "amazon", "tesla",
    "meta", "nvidia", "openai", "jpmorgan", "goldman sachs", "berkshire",
    "oil", "gold", "bitcoin", "dollar", "rally", "drop", "cut",
    # חברות US נוספות
    "netflix", "amd", "intel", "qualcomm", "broadcom", "visa", "mastercard",
    "paypal", "salesforce", "adobe", "palantir", "snowflake", "coinbase",
    "exxon", "chevron", "pfizer", "moderna", "disney", "boeing",
    "bank of america", "wells fargo", "citigroup", "morgan stanley",
    "qqq", "spy", "russell", "vix", "uber", "lyft", "airbnb",
    "shopify", "arm", "asml", "tsmc", "micron", "applied materials",
]

# תבניות טכניות — פריצות תבנית לטווח שבועות
TECHNICAL_PATTERNS = [
    "breakout", "breakdown", "breaks out", "breaks above", "breaks below",
    "support", "resistance", "moving average", "52-week high", "52-week low",
    "all-time high", "golden cross", "death cross",
    "rsi", "macd", "oversold", "overbought",
    "cup and handle", "head and shoulders", "double top", "double bottom",
    "bullish flag", "bearish flag", "triangle", "wedge", "channel",
    "trend reversal", "swing high", "swing low", "momentum",
    "technical", "chart pattern", "key level", "price target",
]

NOISE_PHRASES = [
    "should i", "what do you think", "advice", "help me", "my portfolio",
    "am i", "is it worth", "question", "eli5", "how do i", "thoughts on",
    "first time", "noob", "beginner", "loss porn", "gain porn",
    "what should", "anyone else", "opinion", "dd:", "[dd]",
]

POSITIVE_SIGNALS = ["beat", "surge", "soar", "rally", "profit", "raise", "acquisition", "deal", "revenue"]
NEGATIVE_SIGNALS = ["miss", "plunge", "drop", "crash", "loss", "layoffs", "fired", "bankrupt", "default", "recession", "cut"]

# ─────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────

def translate(text):
    try:
        return GoogleTranslator(source='en', target='iw').translate(text)
    except Exception:
        return text

def sentiment(title):
    t = title.lower()
    pos = sum(1 for w in POSITIVE_SIGNALS if w in t)
    neg = sum(1 for w in NEGATIVE_SIGNALS if w in t)
    if pos > neg:   return "positive", "#22c55e", "🟢", "חיובי לשוק"
    elif neg > pos: return "negative", "#ef4444", "🔴", "שלילי לשוק"
    else:           return "neutral",  "#f59e0b", "🟡", "מעורב"

def is_market_relevant(title):
    t = title.lower()
    if any(n in t for n in NOISE_PHRASES): return False
    if any(kw in t for kw in HIGH_IMPACT): return True
    return sum(1 for kw in MEDIUM_IMPACT if kw in t) >= 2

def is_summary_relevant(title):
    """סינון לסיכום היומי: רק פריצות טכניות על חברות US משמעותיות"""
    t = title.lower()
    if any(n in t for n in NOISE_PHRASES): return False
    has_us      = any(c in t for c in MEDIUM_IMPACT)
    has_tech    = any(p in t for p in TECHNICAL_PATTERNS)
    has_high    = any(kw in t for kw in HIGH_IMPACT)
    return has_us and (has_tech or has_high)

def is_today(entry):
    """בדוק אם הכתבה פורסמה ב-24 שעות האחרונות"""
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - pub) < timedelta(hours=24)
    except Exception:
        pass
    return True  # אם אין תאריך — כלול

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)

def send_email(subject, html_body):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("ERROR: EMAIL credentials missing!")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO or EMAIL_FROM
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, msg["To"], msg.as_string())
        print(f"מייל נשלח: {subject}")
    except Exception as e:
        print(f"ERROR שליחת מייל: {e}")

# ─────────────────────────────────────────────────
# Alert mode
# ─────────────────────────────────────────────────

def alert_html(title_he, title_en, link, source, tone, color, icon, tone_text):
    now = datetime.now().strftime('%H:%M')
    return f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:16px;background:#f1f5f9;font-family:Arial,sans-serif;">
<div style="max-width:580px;margin:0 auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.12);">
  <div style="background:#0f172a;padding:18px 22px;display:flex;align-items:center;justify-content:space-between;">
    <div>
      <span style="font-size:22px;">🚨</span>
      <span style="color:#fff;font-size:17px;font-weight:700;margin-right:8px;">התראת שוק</span>
    </div>
    <span style="color:#94a3b8;font-size:13px;">{now}</span>
  </div>
  <div style="background:#fff;padding:22px;">
    <a href="{link}" style="text-decoration:none;">
      <h2 style="margin:0 0 6px;font-size:18px;color:#0f172a;line-height:1.4;">{title_he}</h2>
    </a>
    <p style="margin:0 0 16px;font-size:12px;color:#94a3b8;">{title_en}</p>
    <div style="border-right:4px solid {color};background:{color}15;padding:12px 16px;border-radius:0 8px 8px 0;">
      <span style="font-size:14px;color:#1e293b;font-weight:600;">{icon} {tone_text}</span>
    </div>
  </div>
  <div style="background:#f8fafc;padding:12px 22px;display:flex;justify-content:space-between;align-items:center;">
    <span style="font-size:12px;color:#94a3b8;">📡 {source}</span>
    <a href="{link}" style="font-size:12px;color:#3b82f6;text-decoration:none;font-weight:600;">קרא עוד ←</a>
  </div>
</div>
</body></html>"""

def watchlist_alert_html(ticker, name, price, change_pct, change_str, reason):
    now = datetime.now().strftime('%H:%M')
    color = "#ef4444" if change_pct < 0 else "#22c55e"
    arrow = "▼" if change_pct < 0 else "▲"
    return f"""
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:16px;background:#f1f5f9;font-family:Arial,sans-serif;">
<div style="max-width:480px;margin:0 auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.12);">
  <div style="background:#0f172a;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;">
    <span style="color:#fff;font-size:16px;font-weight:700;">📊 התראת תיק מעקב</span>
    <span style="color:#94a3b8;font-size:12px;">{now}</span>
  </div>
  <div style="background:#fff;padding:20px;text-align:center;">
    <div style="font-size:28px;font-weight:900;color:#0f172a;letter-spacing:-.02em;">{ticker}</div>
    <div style="font-size:14px;color:#64748b;margin-bottom:14px;">{name}</div>
    <div style="font-size:32px;font-weight:800;color:{color};">{arrow} {change_str}</div>
    <div style="font-size:18px;color:#0f172a;margin-top:4px;">${price:,.2f}</div>
    <div style="margin-top:14px;padding:10px 14px;background:{color}15;border-radius:8px;
         font-size:13px;color:{color};font-weight:600;">{reason}</div>
  </div>
  <div style="background:#f8fafc;padding:10px 20px;text-align:center;">
    <a href="https://twitx2003-rgb.github.io/telegram-market-bot/" style="font-size:12px;color:#3b82f6;text-decoration:none;font-weight:600;">פתח לוח הבקרה ←</a>
  </div>
</div>
</body></html>"""


ALERTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_alerts.json")
DATA_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")


def load_alerts():
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_alerts(seen):
    with open(ALERTS_FILE, "w") as f:
        json.dump(list(seen)[-200:], f)


def check_watchlist_alerts():
    if not os.path.exists(DATA_FILE):
        print("data.json לא נמצא — דלג על התראות watchlist")
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"שגיאה בקריאת data.json: {e}")
        return

    watchlist    = data.get("watchlist", [])
    alert_config = data.get("alert_config", {})
    threshold    = float(alert_config.get("price_move_pct", 5))
    on_earnings  = alert_config.get("on_earnings_day", True)

    if not watchlist:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    seen = load_alerts()
    sent = 0

    for stock in watchlist:
        ticker     = stock.get("ticker", "")
        name       = stock.get("name", ticker)
        price      = stock.get("price", 0)
        change_pct = stock.get("change_pct", 0)
        change_str = stock.get("change_str", "N/A")
        is_earn    = stock.get("is_earnings_today", False)

        alert_key = f"{ticker}:{today_str}"
        if alert_key in seen:
            continue

        reason = None
        if abs(change_pct) >= threshold:
            direction = "ירידה" if change_pct < 0 else "עלייה"
            reason = f"{direction} של {change_str} ביום אחד (סף: {threshold:.0f}%)"
        elif on_earnings and is_earn:
            reason = "יום פרסום דוח רווחים"

        if reason:
            html = watchlist_alert_html(ticker, name, price, change_pct, change_str, reason)
            send_email(f"📊 התראה: {ticker} — {change_str}", html)
            seen.add(alert_key)
            sent += 1
            print(f"  ✓ התראה נשלחה: {ticker} ({reason})")

    save_alerts(seen)
    if sent == 0:
        print("  אין התראות watchlist חדשות.")


def check_alerts():
    # First: check personal watchlist price alerts
    print("בודק התראות watchlist...")
    check_watchlist_alerts()

    # Then: check RSS feeds for market news alerts
    seen = load_seen()
    new_alerts = []
    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:10]:
                link  = entry.get("link", "")
                title = entry.get("title", "").strip()
                if not link or link in seen: continue
                seen.add(link)
                if is_market_relevant(title):
                    new_alerts.append((title, link, feed_info["name"]))
        except Exception:
            continue
    save_seen(seen)
    for title, link, source in new_alerts:
        title_he = translate(title)
        tone, color, icon, tone_text = sentiment(title)
        html = alert_html(title_he, title, link, source, tone, color, icon, tone_text)
        send_email(f"🚨 התראת שוק: {title_he}", html)
        print(f"שלחתי: {title_he}")
    if not new_alerts:
        print("אין חדשות רלוונטיות חדשות.")

# ─────────────────────────────────────────────────
# Daily summary mode
# ─────────────────────────────────────────────────

def build_report_html(all_items, date_str):
    """HTML מלא ומעוצב לדף GitHub Pages"""
    cards = ""
    for title_he, title_en, link, source, tone, color, icon in all_items:
        cards += f"""
    <div class="card {tone}">
      <div class="card-top">
        <span class="source">{source}</span>
        <span class="badge" style="background:{color}20;color:{color};">{icon} {tone}</span>
      </div>
      <a href="{link}" class="card-title">{title_he}</a>
      <p class="card-sub">{title_en[:120]}{'...' if len(title_en)>120 else ''}</p>
      <a href="{link}" class="read-link">קרא עוד ←</a>
    </div>"""

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>סיכום Wall Street — {date_str}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',Arial,sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; }}
  header {{ background:linear-gradient(135deg,#1e3a5f,#0f172a); padding:32px 24px; text-align:right; border-bottom:1px solid #1e293b; }}
  header h1 {{ font-size:28px; color:#fff; margin-bottom:6px; text-align:right; }}
  header p  {{ color:#94a3b8; font-size:15px; text-align:right; }}
  .container {{ max-width:900px; margin:0 auto; padding:24px 16px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }}
  .card {{ background:#1e293b; border-radius:12px; padding:18px; border:1px solid #334155; transition:transform .15s; }}
  .card:hover {{ transform:translateY(-2px); }}
  .card-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
  .source {{ font-size:11px; color:#64748b; }}
  .badge {{ font-size:11px; font-weight:600; padding:2px 8px; border-radius:999px; }}
  .card-title {{ display:block; font-size:15px; font-weight:700; color:#f1f5f9; text-decoration:none; line-height:1.4; margin-bottom:8px; }}
  .card-title:hover {{ color:#60a5fa; }}
  .card-sub {{ font-size:12px; color:#64748b; line-height:1.5; margin-bottom:12px; text-align:right; }}
  .read-link {{ font-size:12px; color:#3b82f6; text-decoration:none; font-weight:600; }}
  footer {{ text-align:right; padding:24px; color:#475569; font-size:13px; border-top:1px solid #1e293b; margin-top:24px; }}
  @media(max-width:600px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>📊 סיכום Wall Street</h1>
  <p>{date_str} • עודכן אוטומטית ב-12:00</p>
</header>
<div class="container">
  <div class="grid">{cards}
  </div>
</div>
<footer>🤖 נוצר אוטומטית ע"י Market News Bot</footer>
</body>
</html>"""

def build_email_html(top_items, date_str):
    """מייל קצר עם preview + כפתור לדוח המלא"""
    preview_rows = ""
    for title_he, _, link, source, _, color, icon in top_items[:5]:
        preview_rows += f"""
      <tr>
        <td style="padding:12px 0;border-bottom:1px solid #1e293b;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="font-size:11px;color:{color};font-weight:700;">{icon}</span>
            <span style="font-size:11px;color:#94a3b8;">{source}</span>
          </div>
          <a href="{link}" style="color:#f1f5f9;text-decoration:none;font-size:14px;font-weight:600;line-height:1.4;">{title_he}</a>
        </td>
      </tr>"""

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#0f172a;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1e3a5f,#0f172a);padding:32px 24px;text-align:right;">
    <div style="font-size:36px;margin-bottom:8px;">📊</div>
    <h1 style="color:#fff;font-size:22px;margin:0 0 6px;text-align:right;">סיכום Wall Street</h1>
    <p style="color:#94a3b8;font-size:14px;margin:0;text-align:right;">{date_str}</p>
  </div>

  <!-- Preview news -->
  <div style="background:#1e293b;margin:0;padding:24px;">
    <p style="color:#94a3b8;font-size:12px;margin:0 0 16px;text-transform:uppercase;letter-spacing:1px;">חדשות מובילות היום</p>
    <table width="100%" cellpadding="0" cellspacing="0">
      {preview_rows}
    </table>
  </div>

  <!-- CTA Button -->
  <div style="background:#0f172a;padding:28px;text-align:right;">
    <a href="{REPORT_URL}"
       style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#1d4ed8);
              color:#fff;font-size:15px;font-weight:700;text-decoration:none;
              padding:14px 32px;border-radius:10px;letter-spacing:0.5px;">
      📊 פתח דוח מלא
    </a>
    <p style="color:#475569;font-size:12px;margin:16px 0 0;">
      <a href="{REPORT_URL}" style="color:#3b82f6;">{REPORT_URL}</a>
    </p>
  </div>

  <!-- Footer -->
  <div style="background:#0a0f1e;padding:16px;text-align:right;">
    <p style="color:#334155;font-size:11px;margin:0;">🤖 נוצר אוטומטית • הסיכום הבא מחר ב-12:00</p>
  </div>

</div>
</body></html>"""

def daily_summary():
    seen = load_seen()
    all_items = []

    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "")
                if not title or not link or link in seen: continue
                if not is_today(entry): continue
                if not is_summary_relevant(title): continue
                seen.add(link)
                title_he = translate(title)
                time.sleep(0.3)  # מניעת rate limit בתרגום
                tone, color, icon, tone_text = sentiment(title)
                all_items.append((title_he, title, link, feed_info["name"], tone, color, icon))
        except Exception as e:
            print(f"שגיאה ב-{feed_info['name']}: {e}")
            continue

    save_seen(seen)

    if not all_items:
        print("לא נמצאו חדשות להיום.")
        return

    date_str = datetime.now().strftime("%d/%m/%Y")

    # שמור HTML מלא לדוח
    os.makedirs(DOCS_DIR, exist_ok=True)
    report_path = os.path.join(DOCS_DIR, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(all_items, date_str))
    print(f"דוח HTML נשמר: {report_path} ({len(all_items)} פריטים)")

    # שלח מייל עם preview + כפתור
    email_html = build_email_html(all_items, date_str)
    send_email(f"📊 סיכום Wall Street — {date_str}", email_html)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "alert"
    if mode == "summary":
        daily_summary()
    else:
        check_alerts()
