import feedparser
import requests
import json
import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from deep_translator import GoogleTranslator

# תמיכה בעברית ב-Windows terminal
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ===================== הגדרות =====================
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "")
EMAIL_TO       = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")
# ==================================================

FEEDS = [
    {"name": "Reuters Business",        "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Markets",            "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch",             "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance",           "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Investing.com",           "url": "https://www.investing.com/rss/news.rss"},
    {"name": "Reddit WallStreetBets",   "url": "https://www.reddit.com/r/wallstreetbets/new.rss"},
    {"name": "Reddit Investing",        "url": "https://www.reddit.com/r/investing/new.rss"},
    {"name": "Reddit Stocks",           "url": "https://www.reddit.com/r/stocks/new.rss"},
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
]

NOISE_PHRASES = [
    "should i", "what do you think", "advice", "help me", "my portfolio",
    "am i", "is it worth", "question", "eli5", "how do i", "thoughts on",
    "first time", "noob", "beginner", "loss porn", "gain porn",
    "what should", "anyone else", "opinion", "dd:", "[dd]",
]

POSITIVE_SIGNALS = ["beat", "surge", "soar", "rally", "profit", "raise", "acquisition", "deal", "revenue"]
NEGATIVE_SIGNALS = ["miss", "plunge", "drop", "crash", "loss", "layoffs", "fired", "bankrupt", "default", "recession", "cut"]

def translate(text):
    try:
        return GoogleTranslator(source='en', target='iw').translate(text)
    except Exception:
        return text

def market_opinion(title):
    t = title.lower()
    pos = sum(1 for w in POSITIVE_SIGNALS if w in t)
    neg = sum(1 for w in NEGATIVE_SIGNALS if w in t)
    if pos > neg:
        return ("🟢", "נראה חיובי לשוק — עשוי לתמוך בעליות")
    elif neg > pos:
        return ("🔴", "נראה שלילי — עלול ללחוץ על המניות")
    else:
        return ("🟡", "השפעה מעורבת — כדאי לעקוב")

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)

def is_market_relevant(title):
    t = title.lower()
    if any(noise in t for noise in NOISE_PHRASES):
        return False
    if any(kw in t for kw in HIGH_IMPACT):
        return True
    return sum(1 for kw in MEDIUM_IMPACT if kw in t) >= 2

def send_email(subject, html_body):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("ERROR: EMAIL_FROM or EMAIL_PASSWORD is missing!")
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

def alert_html(title_he, title_en, link, source, opinion_icon, opinion_text):
    return f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:600px;margin:10px auto;
         border:1px solid #ddd;border-radius:10px;overflow:hidden;">
      <div style="background:#1a1a2e;color:white;padding:16px 20px;">
        <span style="font-size:20px;">🚨</span>
        <strong style="font-size:16px;margin-right:8px;">התראת שוק</strong>
        <span style="float:left;font-size:12px;color:#aaa;">{datetime.now().strftime('%H:%M')}</span>
      </div>
      <div style="padding:20px;background:#f9f9f9;">
        <h2 style="margin:0 0 8px;font-size:17px;color:#1a1a2e;">
          <a href="{link}" style="color:#1a1a2e;text-decoration:none;">{title_he}</a>
        </h2>
        <p style="margin:0 0 12px;font-size:13px;color:#666;">{title_en}</p>
        <div style="background:white;border-right:4px solid #e0c060;padding:10px 14px;
             border-radius:6px;font-size:14px;color:#333;">
          {opinion_icon} {opinion_text}
        </div>
      </div>
      <div style="padding:10px 20px;background:#eee;font-size:12px;color:#888;text-align:right;">
        מקור: {source} &nbsp;|&nbsp;
        <a href="{link}" style="color:#555;">קרא עוד</a>
      </div>
    </div>
    """

def summary_html(sections):
    now = datetime.now().strftime("%d/%m/%Y")
    rows = ""
    for source, items in sections:
        rows += f"""
        <tr>
          <td colspan="2" style="background:#1a1a2e;color:white;padding:10px 16px;
              font-size:14px;font-weight:bold;">{source}</td>
        </tr>"""
        for title_he, title_en, link in items:
            rows += f"""
        <tr>
          <td style="padding:10px 16px;border-bottom:1px solid #eee;vertical-align:top;
              font-size:14px;direction:rtl;">
            <a href="{link}" style="color:#1a1a2e;text-decoration:none;font-weight:bold;">
              {title_he}</a><br>
            <span style="color:#888;font-size:12px;">{title_en}</span>
          </td>
        </tr>"""

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:650px;margin:0 auto;">
      <div style="background:#1a1a2e;color:white;padding:20px;text-align:center;">
        <h1 style="margin:0;font-size:22px;">📊 סיכום שוק יומי</h1>
        <p style="margin:6px 0 0;color:#aaa;font-size:14px;">{now}</p>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid #ddd;border-top:none;">
        {rows}
      </table>
      <div style="background:#eee;padding:12px;text-align:center;font-size:12px;color:#888;">
        🔔 הסיכום הבא מחר ב-12:00
      </div>
    </div>
    """

def check_alerts():
    seen = load_seen()
    new_alerts = []

    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:10]:
                link  = entry.get("link", "")
                title = entry.get("title", "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                if is_market_relevant(title):
                    new_alerts.append((title, link, feed_info["name"]))
        except Exception:
            continue

    save_seen(seen)

    for title, link, source in new_alerts:
        title_he = translate(title)
        icon, opinion = market_opinion(title)
        html = alert_html(title_he, title, link, source, icon, opinion)
        send_email(f"🚨 התראת שוק: {title_he}", html)

    if not new_alerts:
        print("אין חדשות רלוונטיות חדשות.")

def daily_summary():
    seen  = load_seen()
    sections = []

    for feed_info in FEEDS:
        try:
            feed  = feedparser.parse(feed_info["url"])
            items = []
            for entry in feed.entries[:4]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "")
                if not title or not link:
                    continue
                title_he = translate(title)
                seen.add(link)
                items.append((title_he, title, link))
            if items:
                sections.append((feed_info["name"], items))
        except Exception:
            continue

    save_seen(seen)
    html = summary_html(sections)
    send_email(f"📊 סיכום שוק יומי — {datetime.now().strftime('%d/%m/%Y')}", html)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "alert"
    if mode == "summary":
        daily_summary()
    else:
        check_alerts()
