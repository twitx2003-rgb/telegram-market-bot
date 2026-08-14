#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Learning Agent — לומד מהשוק
Daily learning site that studies (1) Micha Stocks' YouTube videos and
(2) the significant tweets rising on X, and synthesizes them in Hebrew.

Free by default (extractive summaries). With ANTHROPIC_API_KEY: Claude
analyst-voice Hebrew summaries. Runs headless on GitHub Actions.
"""
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import feedparser

# ── Reuse the proven ingestion + text helpers (news_dashboard is kept as a library) ──
from news_dashboard import (
    TWITTER_HANDLES, fetch_twitter_feeds, is_finance_tweet, _clean_tweet_text,
    classify_sentiment, classify_tag, _extract_ticker, _all_tickers,
    _importance_score, _handle_authority, _relative_time, _TRADE_SIGNAL_KW,
)

ROOT       = Path(__file__).parent
OUT_HTML   = ROOT / "docs" / "index.html"
LEARN_JSON = ROOT / "docs" / "learn.json"
CONFIG     = ROOT / "learn_config.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}

HE_MONTHS = ["ינואר","פברואר","מרץ","אפריל","מאי","יוני","יולי","אוגוסט","ספטמבר","אוקטובר","נובמבר","דצמבר"]


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {"youtube": {"handle": "@Micha.Stocks", "fallback_channel_id": "UCQpDtNipLcAr13nU9MtXAIg",
                            "max_videos": 3, "transcript_languages": ["iw", "he", "en"]}}


def load_prev_learn() -> dict:
    try:
        return json.loads(LEARN_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── YouTube ingestion ───────────────────────────────────────────────────────────

def resolve_channel_id(handle: str, fallback: str = "") -> str:
    """Resolve @handle → channelId by scraping the channel page. Falls back to pinned id."""
    h = handle.lstrip("@")
    for url in (f"https://www.youtube.com/@{h}", f"https://www.youtube.com/c/{h}"):
        try:
            req = urllib.request.Request(url, headers=UA)
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', html)
            if m:
                print(f"  ✓ פתרתי {handle} → {m.group(1)}")
                return m.group(1)
        except Exception as e:
            print(f"  ⚠ resolve {url}: {e}")
    if fallback:
        print(f"  ↩ נופל למזהה ברירת מחדל: {fallback}")
    return fallback


def fetch_channel_videos(channel_id: str, n: int = 3) -> list:
    """Latest uploads via the channel RSS feed (no API key)."""
    if not channel_id:
        return []
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=20).read()
        feed = feedparser.parse(raw)
    except Exception as e:
        print(f"  ✗ RSS ערוץ נכשל: {e}")
        return []
    vids = []
    for e in feed.entries[:n]:
        vid = e.get("yt_videoid") or (e.get("id", "").split(":")[-1])
        # Full description (media:description) — richer than the truncated summary;
        # this is the best signal when the transcript is IP-blocked.
        desc = getattr(e, "media_description", "") or getattr(e, "summary", "") or ""
        vids.append({
            "video_id":  vid,
            "title":     e.get("title", ""),
            "link":      e.get("link", f"https://www.youtube.com/watch?v={vid}"),
            "published": e.get("published", ""),
            "description": desc[:2500],
        })
    print(f"  ✓ נמצאו {len(vids)} סרטונים")
    return vids


def _proxy_config():
    """Optional residential proxy so transcripts work from cloud IPs (YouTube blocks
    datacenter IPs). Configured via secrets — no-op when unset."""
    try:
        wu, wp = os.environ.get("WEBSHARE_PROXY_USERNAME"), os.environ.get("WEBSHARE_PROXY_PASSWORD")
        if wu and wp:
            from youtube_transcript_api.proxies import WebshareProxyConfig
            return WebshareProxyConfig(proxy_username=wu, proxy_password=wp)
        http_url = os.environ.get("YT_PROXY_URL")
        if http_url:
            from youtube_transcript_api.proxies import GenericProxyConfig
            return GenericProxyConfig(http_url=http_url, https_url=http_url)
    except Exception as e:
        print(f"    ⚠ proxy config: {e}")
    return None


def fetch_transcript(video_id: str, languages: list) -> str:
    """Transcript via youtube-transcript-api (proxy-aware). '' on failure/IP-block."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return ""
    try:
        proxy = _proxy_config()
        # Newer API (instance .fetch); fall back to legacy classmethod.
        try:
            api = YouTubeTranscriptApi(proxy_config=proxy) if proxy else YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=languages)
            segments = [{"text": s.text} for s in fetched]
        except TypeError:
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        text = " ".join(seg["text"] for seg in segments if seg.get("text"))
        return re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        print(f"    ⚠ תמלול {video_id} נכשל: {msg}")
        return ""


# ── Summarization (free extractive; Claude premium) ─────────────────────────────

_HEBREW_RE = re.compile("[֐-׿]")

# Hebrew sentiment lexicon (Micha speaks Hebrew — the English classifier misses it)
_HE_POS = ["זינוק", "זינק", "מזנק", "לזנק", "עלייה", "עולה", "עלתה", "פריצה", "פורץ", "פרץ",
           "חזק", "חזקה", "שורי", "קנייה", "לקנות", "שיא", "מומנטום", "ראלי", "קפיצה", "קופץ",
           "טס", "מרקיע", "אופטימי", "התאוששות", "ירוק", "רווח", "מנצח", "פוטנציאל", "בולט"]
_HE_NEG = ["ירידה", "יורד", "יורדת", "ירדה", "נפילה", "נופל", "נפל", "חלש", "חלשה", "צונח", "צניחה",
           "מתרסק", "התרסקות", "דובי", "מכירה", "למכור", "שבירה", "נשבר", "הפסד", "אזהרה", "קורס",
           "אדום", "שלילי", "פסימי", "סיכון", "צלילה", "מדשדש"]


def sentiment_he(text: str) -> str:
    """Hebrew-aware sentiment; falls back to the English classifier."""
    pos = sum(1 for w in _HE_POS if w in text)
    neg = sum(1 for w in _HE_NEG if w in text)
    if pos != neg:
        return "bullish" if pos > neg else "bearish"
    return classify_sentiment(text)


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+|[\n·]", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def summarize_video_free(video: dict, transcript: str) -> dict:
    """Extractive Hebrew summary: key sentences + tickers with sentiment. No LLM."""
    source = transcript or f'{video.get("title","")}. {video.get("description","")}'
    tickers = Counter()
    ticker_sent = {}
    for tk in _all_tickers(source):
        tickers[tk] += 1
    sentences = _split_sentences(source)

    # Score sentences by tickers + trade-signal language + sentiment strength
    scored = []
    for s in sentences:
        low = s.lower()
        score = len(_all_tickers(s)) * 2
        score += sum(1 for kw in _TRADE_SIGNAL_KW if kw in low)
        if sentiment_he(s) != "neutral":
            score += 1
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    bullets = [s for _, s in scored[:6]] or sentences[:3]

    # Per-ticker sentiment from sentences mentioning it
    tk_out = []
    for tk, _cnt in tickers.most_common(10):
        sents = [sentiment_he(s) for s in sentences if tk in s]
        bull = sents.count("bullish"); bear = sents.count("bearish")
        sentiment = "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")
        tk_out.append({"ticker": tk, "sentiment": sentiment})

    return {
        "bullets":  bullets,
        "tickers":  tk_out,
        "has_transcript": bool(transcript),
        "engine":   "free",
    }


def summarize_video_claude(client, video: dict, transcript: str) -> dict:
    """Claude analyst-voice Hebrew summary. Falls back to free on any error."""
    try:
        import anthropic  # noqa
        MODEL = os.environ.get("ENRICHMENT_MODEL", "claude-sonnet-5")
        text = transcript[:14000] if transcript else f'{video.get("title","")}\n{video.get("description","")}'
        prompt = (
            "אתה אנליסט שוק ההון. לפניך תמלול (או תיאור) של סרטון יוטיוב יומי של מיכה סטוקס בעברית. "
            "סכם בקצרה בעברית: 4-6 תובנות/המלצות עיקריות כנקודות, ולאילו מניות (טיקרים) הוא התייחס ובאיזה כיוון. "
            "החזר JSON בלבד: {\"bullets\":[...], \"tickers\":[{\"ticker\":\"$XXX\",\"sentiment\":\"bullish|bearish|neutral\"}]}.\n\n"
            f"תוכן:\n{text}"
        )
        msg = client.messages.create(model=MODEL, max_tokens=1200,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
        data = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))
        data["has_transcript"] = bool(transcript)
        data["engine"] = "claude"
        data.setdefault("bullets", []); data.setdefault("tickers", [])
        return data
    except Exception as e:
        print(f"    ✗ Claude summary נכשל: {e} — עובר לחינמי")
        return summarize_video_free(video, transcript)


def summarize_video_gemini(video: dict) -> dict:
    """Gemini watches the YouTube video directly (bypasses transcript IP-block).
    Free tier supports YouTube URLs. Returns None if unavailable/failed."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        import requests
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        prompt = (
            "צפה בסרטון של מיכה סטוקס (עברית, שוק ההון) וסכם בעברית. "
            "החזר JSON בלבד: {\"bullets\":[4-6 תובנות/המלצות עיקריות כמחרוזות], "
            "\"tickers\":[{\"ticker\":\"$XXX\",\"sentiment\":\"bullish|bearish|neutral\"}]}. "
            "טיקרים בפורמט $XXX. בלי טקסט מחוץ ל-JSON."
        )
        body = {"contents": [{"parts": [
            {"file_data": {"file_uri": video.get("link", "")}},
            {"text": prompt},
        ]}]}
        r = requests.post(url, json=body, timeout=120)
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))
        data["has_transcript"] = True          # Gemini watched the actual video
        data["engine"] = "gemini"
        data.setdefault("bullets", []); data.setdefault("tickers", [])
        print(f"    ✓ Gemini סיכם את הסרטון ({len(data['bullets'])} נקודות)")
        return data
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else e.__class__.__name__
        print(f"    ✗ Gemini נכשל: {msg}")
        return None


# ── Tweet synthesis ─────────────────────────────────────────────────────────────

def synthesize_tweets(tweets: list) -> dict:
    """Aggregate ALL finance tweets: sentiment split, top tickers, top significant tweets."""
    fin = [t for t in tweets if is_finance_tweet(t.get("body", ""))]
    total = len(fin)
    sent = {"bullish": 0, "bearish": 0, "neutral": 0}
    tickers = Counter()
    ranked = []
    for t in fin:
        body = _clean_tweet_text(t.get("body", ""))
        s = classify_sentiment(body)
        sent[s] += 1
        for tk in set(_all_tickers(body)):
            tickers[tk] += 1
        score = _importance_score(body, False, bool(_extract_ticker(body)), _handle_authority(t.get("handle", "")))
        ranked.append((score, {
            "text": body[:220], "handle": t.get("handle", ""), "url": t.get("url", "#"),
            "sentiment": s, "tag": classify_tag(body),
            "time": t.get("relative_time", "") or (_relative_time(t["published_at"]) if t.get("published_at") else ""),
        }))
    ranked.sort(key=lambda x: x[0], reverse=True)
    pct = lambda n: round(n / total * 100) if total else 0
    return {
        "total": total,
        "sentiment": {k: {"count": v, "pct": pct(v)} for k, v in sent.items()},
        "top_tickers": tickers.most_common(8),
        "top_tweets": [t for _, t in ranked[:8]],
    }


# ── HTML (broadsheet — serif headlines, hybrid dark/FT-salmon, amber accent) ─────

CSS = """
:root{--bg:#1c1f22;--card:#14171a;--surface:#0d0f11;--border:rgba(255,255,255,.08);
--accent:#e0a35a;--white:#f4f1ea;--text:#a8a49c;--muted:#6c6860;
--green:#34d399;--red:#f87171;--gold:#fbbf24;
--e1:0 8px 24px rgba(0,0,0,.35);--e2:0 16px 48px rgba(0,0,0,.45);}
body.light{--bg:#fff1e5;--card:#fffaf3;--surface:#f6e7d7;--border:rgba(51,48,46,.12);
--accent:#b06a34;--white:#33302e;--text:#4a453f;--muted:#8a8178;
--green:#0a7a4f;--red:#c0392b;--gold:#9a6a15;
--e1:0 8px 24px rgba(0,0,0,.10);--e2:0 16px 48px rgba(0,0,0,.14);}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Assistant',sans-serif;
line-height:1.65;-webkit-font-smoothing:antialiased;transition:background .3s,color .3s}
.masthead{text-align:center;padding:1.8rem 1.4rem 1.1rem;border-bottom:2px solid var(--white)}
.mast-kicker{font-size:.62rem;font-weight:700;letter-spacing:.4em;color:var(--accent);
text-transform:uppercase;margin-bottom:.5rem}
.masthead h1{font-family:'Frank Ruhl Libre',Georgia,serif;font-weight:900;color:var(--white);
font-size:clamp(2rem,6vw,3.4rem);line-height:1.05}
.masthead h1 span{color:var(--accent)}
.mast-date{margin-top:.7rem;font-size:.72rem;font-weight:700;letter-spacing:.06em;
color:var(--muted);text-transform:uppercase;display:flex;gap:1rem;justify-content:center;
flex-wrap:wrap;align-items:center;padding-top:.7rem;border-top:1px solid var(--border)}
.mast-date b{color:var(--white);font-family:'Inter',sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:900px;margin:0 auto;padding:1.8rem 1.4rem 5rem}
.section{margin-bottom:2.8rem}
.section-label{font-size:.66rem;font-weight:700;letter-spacing:.28em;text-transform:uppercase;
color:var(--accent);margin-bottom:1.1rem;padding-bottom:.55rem;border-bottom:1px solid var(--border)}
.theme-toggle{position:fixed;top:1rem;left:1rem;z-index:50;background:var(--card);
border:1px solid var(--border);color:var(--text);border-radius:999px;width:38px;height:38px;
cursor:pointer;font-size:1.1rem;box-shadow:var(--e1)}
.vid{padding-bottom:1.6rem;margin-bottom:1.6rem;border-bottom:1px solid var(--border)}
.vid:last-child{border-bottom:none;margin-bottom:0}
.vid-kicker{font-size:.66rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
color:var(--accent);margin-bottom:.5rem;display:flex;gap:.5rem;align-items:center}
.vid-title{font-family:'Frank Ruhl Libre',Georgia,serif;font-weight:900;color:var(--white);
font-size:clamp(1.35rem,3.4vw,1.9rem);line-height:1.25;margin-bottom:.5rem}
.vid-title a{color:inherit;text-decoration:none}
.vid-title a:hover{color:var(--accent)}
.byline{font-size:.72rem;color:var(--muted);margin-bottom:.9rem;font-weight:600}
.byline a{color:var(--accent);text-decoration:none;font-weight:700}
.bullets{list-style:none;display:flex;flex-direction:column;gap:.55rem;margin-bottom:1rem}
.bullets li{position:relative;padding-right:1.1rem;color:var(--text);font-size:.95rem;line-height:1.6}
.bullets li::before{content:'';position:absolute;right:0;top:.6em;width:6px;height:6px;
border-radius:50%;background:var(--accent)}
.tk-chips{display:flex;flex-wrap:wrap;gap:.4rem}
.tk-chip{display:inline-flex;align-items:center;gap:.35rem;font-family:'Inter',sans-serif;
font-weight:700;font-size:.76rem;color:var(--white);background:var(--surface);
border:1px solid var(--border);border-radius:999px;padding:.18rem .7rem}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.dot.bullish{background:var(--green)}.dot.bearish{background:var(--red)}.dot.neutral{background:var(--muted)}
.note{font-size:.76rem;color:var(--muted);font-style:italic;margin-top:.5rem}
.pulse-bar{display:flex;height:28px;border-radius:6px;overflow:hidden;background:var(--surface);margin-bottom:.6rem}
.pulse-seg{display:flex;align-items:center;justify-content:center;font-family:'Inter',sans-serif;
font-size:.72rem;font-weight:700;color:#14171a}
.pulse-seg.up{background:var(--green)}.pulse-seg.down{background:var(--red)}.pulse-seg.flat{background:var(--muted)}
.pulse-legend{display:flex;gap:1rem;flex-wrap:wrap;font-size:.76rem;font-weight:700;margin-bottom:1.2rem}
.pulse-legend .up{color:var(--green)}.pulse-legend .down{color:var(--red)}.pulse-legend .flat{color:var(--muted)}
.freq-row{display:flex;align-items:center;gap:.7rem;padding:.3rem 0}
.freq-tk{min-width:82px;font-family:'Inter',sans-serif;font-weight:700;font-size:.8rem;color:var(--white)}
.freq-track{flex:1;height:9px;background:var(--surface);border-radius:999px;overflow:hidden}
.freq-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,rgba(224,163,90,.5),var(--white))}
.freq-n{min-width:24px;text-align:left;font-family:'Inter',sans-serif;font-size:.74rem;color:var(--muted);direction:ltr}
.story-row{padding:.9rem 0;border-bottom:1px solid var(--border)}
.story-row:last-child{border-bottom:none}
.row-kicker{font-size:.64rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
color:var(--accent);margin-bottom:.35rem;display:flex;gap:.45rem;align-items:center}
.row-text{font-size:.92rem;color:var(--white);line-height:1.5;margin-bottom:.3rem}
.row-meta{font-size:.7rem;color:var(--muted);font-weight:600}
.row-meta a{color:var(--accent);text-decoration:none}
.consensus{background:var(--card);border-radius:8px;box-shadow:var(--e1);padding:1.2rem 1.4rem}
.consensus .tk-chip{border-color:var(--accent);color:var(--accent)}
.empty{color:var(--muted);text-align:center;padding:2rem;background:var(--card);
border:1px dashed var(--border);border-radius:8px;font-size:.9rem}
footer{text-align:center;padding:2rem;font-size:.72rem;color:var(--muted);border-top:1px solid var(--border)}
"""


def _dot(sent): return f'<span class="dot {sent}"></span>'


def build_video_block(v: dict, is_lead: bool) -> str:
    chips = "".join(
        f'<span class="tk-chip" dir="ltr">{_dot(t["sentiment"])}{t["ticker"]}</span>'
        for t in v.get("tickers", [])[:10]
    )
    bullets = "".join(f'<li>{b}</li>' for b in v.get("summary", {}).get("bullets", []))
    note = "" if v.get("summary", {}).get("has_transcript") else \
        '<div class="note">* לא נמצא תמלול לסרטון — הסיכום מבוסס על הכותרת והתיאור</div>'
    kicker = "הסרטון האחרון" if is_lead else "סרטון קודם"
    pub = v.get("published", "")[:10]
    return (
        f'<article class="vid">'
        f'<div class="vid-kicker">📺 {kicker}{" · " + pub if pub else ""}</div>'
        f'<h3 class="vid-title"><a href="{v.get("link","#")}" target="_blank">{v.get("title","")}</a></h3>'
        + (f'<ul class="bullets">{bullets}</ul>' if bullets else '<p class="note">אין סיכום זמין</p>')
        + (f'<div class="tk-chips">{chips}</div>' if chips else "")
        + note
        + f'</article>'
    )


def build_micha_section(micha: dict) -> str:
    vids = micha.get("videos", [])
    if not vids:
        return ('<section class="section"><div class="section-label">📺 מיכה סטוקס — היום</div>'
                '<div class="empty">אין סרטונים זמינים כרגע — נסה שוב בריצה הבאה</div></section>')
    blocks = "".join(build_video_block(v, i == 0) for i, v in enumerate(vids))
    return f'<section class="section"><div class="section-label">📺 מיכה סטוקס — היום</div>{blocks}</section>'


def build_x_section(x: dict) -> str:
    total = x.get("total", 0)
    if not total:
        return ('<section class="section"><div class="section-label">📈 מה ה-X אומר היום</div>'
                '<div class="empty">אין מספיק ציוצים לניתוח כרגע</div></section>')
    s = x["sentiment"]
    seg = lambda k, c: (f'<div class="pulse-seg {c}" style="width:{s[k]["pct"]}%">{s[k]["pct"]}%</div>'
                        if s[k]["pct"] >= 8 else f'<div class="pulse-seg {c}" style="width:{s[k]["pct"]}%"></div>')
    bar = (f'<div class="pulse-bar">{seg("bullish","up")}{seg("neutral","flat")}{seg("bearish","down")}</div>'
           f'<div class="pulse-legend"><span class="up">▲ שורי {s["bullish"]["pct"]}%</span>'
           f'<span class="flat">● ניטרלי {s["neutral"]["pct"]}%</span>'
           f'<span class="down">▼ דובי {s["bearish"]["pct"]}%</span></div>')
    top = x.get("top_tickers", [])
    maxf = top[0][1] if top else 1
    freq = "".join(
        f'<div class="freq-row"><span class="freq-tk" dir="ltr">{tk}</span>'
        f'<div class="freq-track"><div class="freq-fill" style="width:{round(f/maxf*100)}%"></div></div>'
        f'<span class="freq-n">{f}</span></div>' for tk, f in top)
    tweets = "".join(
        f'<div class="story-row"><div class="row-kicker">{_dot(t["sentiment"])}@{t["handle"]}'
        f'{" · " + t["time"] if t.get("time") else ""}</div>'
        f'<div class="row-text" dir="{"rtl" if _HEBREW_RE.search(t["text"]) else "ltr"}">{t["text"]}</div>'
        f'<div class="row-meta"><a href="{t["url"]}" target="_blank">X ←</a></div></div>'
        for t in x.get("top_tweets", []))
    return (
        f'<section class="section"><div class="section-label">📈 מה ה-X אומר היום · {total} ציוצים</div>'
        f'{bar}{freq}'
        f'<div style="margin-top:1.4rem">{tweets}</div>'
        f'</section>'
    )


def build_consensus_section(tickers: list) -> str:
    if not tickers:
        return ""
    chips = "".join(f'<span class="tk-chip" dir="ltr">{tk}</span>' for tk in tickers)
    return (
        f'<section class="section"><div class="section-label">🎯 קונצנזוס — מיכה וגם ה-X</div>'
        f'<div class="consensus"><div class="tk-chips">{chips}</div>'
        f'<div class="note">מניות שהופיעו גם אצל מיכה וגם בציוצים המשמעותיים היום — שווה תשומת לב</div>'
        f'</div></section>'
    )


def build_html(data: dict) -> str:
    now = datetime.now(timezone.utc)
    today_he = f"{now.day} ב{HE_MONTHS[now.month-1]} {now.year}"
    time_str = now.strftime("%H:%M UTC")
    micha = build_micha_section(data.get("micha", {}))
    xsec  = build_x_section(data.get("x_synthesis", {}))
    cons  = build_consensus_section(data.get("consensus", []))
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>לומד מהשוק — {today_he}</title>
<meta name="theme-color" content="#1c1f22"/>
<link rel="manifest" href="manifest.json"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Frank+Ruhl+Libre:wght@400;500;700;900&family=Assistant:wght@300;400;600;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<style>{CSS}</style>
</head>
<body>
<button class="theme-toggle" onclick="document.body.classList.toggle('light');try{{localStorage.setItem('lt',document.body.classList.contains('light')?'1':'0')}}catch(e){{}}">◐</button>
<div class="masthead">
  <div class="mast-kicker">סוכן לימוד שוק ההון</div>
  <h1>לומד <span>מהשוק</span></h1>
  <div class="mast-date">{today_he} <span>·</span> עודכן <b>{time_str}</b></div>
</div>
<div class="wrap">
  {micha}
  {cons}
  {xsec}
</div>
<footer>נוצר אוטומטית · לומד יומית ממיכה סטוקס ומהציוצים המשמעותיים ב-X · אינו מהווה ייעוץ השקעות</footer>
<script>
  try{{if(localStorage.getItem('lt')==='1')document.body.classList.add('light');}}catch(e){{}}
  if('serviceWorker' in navigator){{navigator.serviceWorker.register('sw.js').catch(function(){{}});
    var r=false;navigator.serviceWorker.addEventListener('controllerchange',function(){{if(r)return;r=true;location.reload();}});}}
</script>
</body>
</html>"""


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*52}\n  🎓 לומד מהשוק — {datetime.now(timezone.utc):%Y-%m-%d %H:%M}\n{'='*52}\n")
    cfg = load_config()
    yt = cfg.get("youtube", {})
    prev = load_prev_learn()
    prev_vids = {v["video_id"]: v for v in prev.get("micha", {}).get("videos", [])}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = None
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except Exception:
            client = None

    # 1. Micha Stocks videos
    print("[ 1 ] מיכה סטוקס — יוטיוב")
    ch = resolve_channel_id(yt.get("handle", "@Micha.Stocks"), yt.get("fallback_channel_id", ""))
    videos = fetch_channel_videos(ch, yt.get("max_videos", 3))
    langs = yt.get("transcript_languages", ["iw", "he", "en"])
    out_videos = []
    for v in videos:
        vid = v["video_id"]
        if vid in prev_vids and prev_vids[vid].get("summary", {}).get("bullets"):
            print(f"  ↩ {vid} כבר סוכם — טוען מהמטמון")
            out_videos.append(prev_vids[vid]); continue
        print(f"  ⇣ מתמלל ומסכם {vid}...")
        transcript = fetch_transcript(vid, langs)
        summary = None
        if transcript:
            # Best when captions are reachable: Claude (or free) over the transcript
            summary = summarize_video_claude(client, v, transcript) if client \
                else summarize_video_free(v, transcript)
        else:
            # Transcript IP-blocked → Gemini watches the video directly (free, reliable)
            summary = summarize_video_gemini(v)
            if summary is None:
                summary = summarize_video_free(v, "")  # last resort: description-based
        v["summary"] = summary
        out_videos.append(v)
    micha = {"channel_id": ch, "handle": yt.get("handle", ""), "videos": out_videos}

    # 2. Tweet synthesis
    print("\n[ 2 ] ציוצים משמעותיים ב-X")
    tweets = fetch_twitter_feeds(TWITTER_HANDLES)
    x_syn = synthesize_tweets(tweets)
    print(f"  ✓ {x_syn['total']} ציוצים · סנטימנט {x_syn['sentiment']['bullish']['pct']}%/"
          f"{x_syn['sentiment']['bearish']['pct']}% שורי/דובי")

    # 3. Consensus — tickers in BOTH the video and the day's tweets
    vid_tk = {t["ticker"] for v in out_videos for t in v.get("summary", {}).get("tickers", [])}
    x_tk = {tk for tk, _ in x_syn.get("top_tickers", [])}
    consensus = sorted(vid_tk & x_tk)
    print(f"  ✓ קונצנזוס: {len(consensus)} מניות משותפות")

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "micha": micha, "x_synthesis": x_syn, "consensus": consensus,
    }
    LEARN_JSON.parent.mkdir(parents=True, exist_ok=True)
    LEARN_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(build_html(data), encoding="utf-8")
    print(f"\n✓ נכתב {OUT_HTML} + {LEARN_JSON}\n")


if __name__ == "__main__":
    main()
