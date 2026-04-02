import feedparser
import requests
import json
import os
import sys
from datetime import datetime
from deep_translator import GoogleTranslator

# תמיכה בעברית/אמוג'י ב-Windows terminal
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ===================== הגדרות =====================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")
# ==================================================

FEEDS = [
    {"name": "Reuters Business",   "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Markets",       "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch",        "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance",      "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Investing.com",      "url": "https://www.investing.com/rss/news.rss"},
    # Reddit - דיוני משקיעים וסוחרים
    {"name": "🔴 Reddit WallStreetBets", "url": "https://www.reddit.com/r/wallstreetbets/new.rss"},
    {"name": "🔴 Reddit Investing",      "url": "https://www.reddit.com/r/investing/new.rss"},
    {"name": "🔴 Reddit Stocks",         "url": "https://www.reddit.com/r/stocks/new.rss"},
]

# מילות מפתח שמספיקה אחת מהן — אירועים משמעותיים בהחלט
HIGH_IMPACT = [
    "federal reserve", "rate hike", "rate cut", "interest rate decision",
    "merger", "acquisition", "buyout", "takeover",
    "ipo", "bankruptcy", "bankrupt", "default",
    "earnings beat", "earnings miss", "beats estimates", "misses estimates",
    "layoffs", "mass layoff",
    "crash", "plunge", "surge", "soar",
    "trade war", "tariff", "sanctions",
    "jobs report", "gdp",
]

# מילות מפתח שדורשות לפחות 2 כדי לשלוח
MEDIUM_IMPACT = [
    "earnings", "revenue", "profit", "loss", "guidance",
    "fed ", "inflation", "recession",
    "s&p 500", "nasdaq", "dow jones",
    "apple", "microsoft", "google", "alphabet", "amazon", "tesla",
    "meta", "nvidia", "openai", "jpmorgan", "goldman sachs", "berkshire",
    "oil", "gold", "bitcoin", "dollar",
    "rally", "drop", "cut",
]

# ביטויים שמסמנים רעש — פוסטים אישיים מ-Reddit
NOISE_PHRASES = [
    "should i", "what do you think", "advice", "help me", "my portfolio",
    "am i", "is it worth", "question", "eli5", "how do i", "thoughts on",
    "first time", "noob", "beginner", "loss porn", "gain porn",
    "what should", "anyone else", "opinion", "dd:", "[dd]",
]

# ניתוח השפעה לפי מילות מפתח
POSITIVE_SIGNALS = ["beat", "surge", "soar", "rally", "profit", "raise", "acquisition", "deal", "revenue"]
NEGATIVE_SIGNALS = ["miss", "plunge", "drop", "crash", "loss", "layoffs", "fired", "bankrupt", "default", "recession", "cut"]

def translate(text):
    try:
        return GoogleTranslator(source='en', target='iw').translate(text)
    except Exception:
        return text

def market_opinion(title):
    title_lower = title.lower()
    pos = sum(1 for w in POSITIVE_SIGNALS if w in title_lower)
    neg = sum(1 for w in NEGATIVE_SIGNALS if w in title_lower)
    if pos > neg:
        return "💹 נראה חיובי לשוק — עשוי לתמוך בעליות."
    elif neg > pos:
        return "📉 נראה שלילי — עלול ללחוץ על המניות."
    else:
        return "🔍 השפעה מעורבת — כדאי לעקוב."

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    seen_list = list(seen)[-500:]
    with open(SEEN_FILE, "w") as f:
        json.dump(seen_list, f)

def is_market_relevant(title):
    t = title.lower()
    # סנן רעש (פוסטים אישיים מ-Reddit וכו')
    if any(noise in t for noise in NOISE_PHRASES):
        return False
    # מספיק מילת מפתח אחת בעלת השפעה גבוהה
    if any(kw in t for kw in HIGH_IMPACT):
        return True
    # דרוש לפחות 2 מילות מפתח בינוניות
    matches = sum(1 for kw in MEDIUM_IMPACT if kw in t)
    return matches >= 2

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })
    if r.status_code != 200:
        print(f"ERROR sending message: {r.text}")

def check_alerts():
    seen = load_seen()
    new_alerts = []

    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:10]:
                link = entry.get("link", "")
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
        opinion = market_opinion(title)
        message = (
            f"🚨 <b>התראת שוק</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{title_he}</b>\n"
            f"🔗 <a href='{link}'>קרא עוד</a> | <i>{source}</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{opinion}\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
        send_telegram(message)
        print(f"שלחתי: {title_he}")

    if not new_alerts:
        print("אין חדשות רלוונטיות חדשות.")

def daily_summary():
    seen = load_seen()
    sections = []

    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            items = []
            for entry in feed.entries[:4]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue
                title_he = translate(title)
                seen.add(link)
                items.append(f"• <a href='{link}'>{title_he}</a>")
            if items:
                sections.append(f"\n<b>{feed_info['name']}</b>\n" + "\n".join(items))
        except Exception:
            continue

    save_seen(seen)

    now = datetime.now().strftime("%d/%m/%Y")
    message = (
        f"📊 <b>סיכום שוק יומי — {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
        + "\n".join(sections) +
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 <i>הסיכום הבא מחר ב-12:00</i>"
    )
    send_telegram(message)
    print("סיכום יומי נשלח!")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "alert"
    if mode == "summary":
        daily_summary()
    else:
        check_alerts()
