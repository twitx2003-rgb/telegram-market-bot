#!/usr/bin/env python3
"""
AI Improvement Agent — Daily Dashboard Brief & Enhancement
==========================================================
Two-phase pipeline:

  Phase 1 — BRIEF
    Claude reads the full dashboard context and writes a structured
    professional assessment: what works, what's weak, what's missing.
    The brief is printed to the console (GitHub Actions log) and saved
    to docs/brief.json for reference.

  Phase 2 — IMPLEMENT
    Based on the brief, Claude generates up to 3 concrete HTML
    improvements. Each is injected into docs/index.html with:
      • A labeled section header ("AI ✦ <title>")
      • A "✕ הסר" dismiss button (persisted in localStorage)
    Previous day's improvements are stripped before analysis.

Usage:
  python improvement_agent.py           # full run
  python improvement_agent.py --dry     # brief only, no file writes
"""

import anthropic
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

if os.environ.get("GITHUB_ACTIONS"):
    INDEX_PATH = Path("docs/index.html")
    BRIEF_PATH = Path("docs/brief.json")
else:
    _BASE       = Path("C:/Users/idoph/OneDrive/IDOP/reports/docs")
    INDEX_PATH  = _BASE / "index.html"
    BRIEF_PATH  = _BASE / "brief.json"

TODAY = date.today().isoformat()
MODEL = os.environ.get("DASHBOARD_MODEL", "claude-opus-4-6")

# ── Strip previous improvements ────────────────────────────────────────────────

_IMP_RE = re.compile(
    r'\n?<!-- AI-IMPROVEMENT:[\w-]+ -->.*?<!-- /AI-IMPROVEMENT:[\w-]+ -->',
    re.DOTALL,
)

def strip_old_improvements(html: str) -> str:
    return _IMP_RE.sub("", html)

# ── Context extractor ──────────────────────────────────────────────────────────

def extract_context(html: str) -> str:
    """
    Produce a rich text summary of the current dashboard state.
    Includes sections, widgets, market symbols, news, and any data gaps.
    """

    def find(cls: str) -> list[str]:
        return [s.strip() for s in
                re.findall(rf'class="{re.escape(cls)}"[^>]*>([^<]+)', html)
                if s.strip()]

    sections  = find("section-label")
    mkt_names = list(dict.fromkeys(find("mkt-name")))
    headlines = find("news-title")[:10]
    tickers   = find("tick-name")[:16]
    summaries = find("news-summary")[:5]
    tags      = list(dict.fromkeys(find("news-tag")))

    widgets = []
    if "fg-card"        in html: widgets.append("Fear & Greed gauge")
    if "heatmap-grid"   in html: widgets.append("Sector heatmap (11 S&P ETFs)")
    if "cal-strip"      in html: widgets.append("Economic calendar")
    if "ticker-wrap"    in html: widgets.append("Animated ticker tape")
    if "sparkline"      in html: widgets.append("5-day sparklines")
    if "tw-card"        in html: widgets.append("Twitter/X cards")
    if "StockTwits"     in html: widgets.append("StockTwits posts")
    if "news-en"        in html: widgets.append("Original English tweet quotes")
    if "ticker-badge"   in html: widgets.append("Ticker badges in news cards")
    if "ticker-hl"      in html: widgets.append("Inline $TICKER bold highlights")
    if "ai-improvement" in html: widgets.append("AI improvement block (prev day)")

    us_news_count = len(re.findall(r'id="us-news"', html))
    il_news_count = len(re.findall(r'class="il-card"', html))
    card_count    = len(re.findall(r'class="news-card', html))

    # Check for notable absences
    absent = []
    if "top-movers"    not in html: absent.append("no top-movers table")
    if "yield"         not in html: absent.append("no yield curve data")
    if "put.*call"     not in html: absent.append("no put/call ratio")
    if "dominance"     not in html: absent.append("no crypto dominance")
    if "sentiment"     not in html: absent.append("no sentiment summary")
    if "earnings-week" not in html: absent.append("no weekly earnings preview")

    return "\n".join([
        f"DATE: {TODAY}",
        f"SECTIONS:       {' | '.join(sections)}",
        f"WIDGETS:        {', '.join(widgets) or 'none listed'}",
        f"MARKET SYMBOLS: {', '.join(mkt_names)}",
        f"TICKER TAPE:    {', '.join(tickers)}",
        f"NEWS TAGS SEEN: {', '.join(tags)}",
        f"CARD COUNT:     {card_count} total  (US section: {us_news_count}, IL: {il_news_count})",
        f"SAMPLE HEADLINES:",
        *[f"  • {h}" for h in headlines],
        f"SAMPLE SUMMARIES:",
        *[f"  – {s[:120]}" for s in summaries],
        f"NOTABLE ABSENCES: {', '.join(absent) or 'none detected'}",
    ])

# ── Phase 1: Brief ─────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """\
You are a senior financial dashboard analyst — Bloomberg/Refinitiv level.
Your task: write a structured professional brief about a Hebrew financial news dashboard.

Return ONLY a JSON object, no markdown:
{
  "overall_score": <1–10>,
  "professional_assessment": "<2-3 sentences in English summarising the dashboard>",
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "weaknesses": ["<weakness 1>", ...],
  "missing_high_priority": ["<item 1>", ...],
  "missing_medium_priority": ["<item 1>", ...],
  "improvement_plan": [
    {
      "priority": "high"|"medium"|"low",
      "title_he": "<short Hebrew section title>",
      "description": "<what to build and why — 1-2 sentences>",
      "inject_after": "#heatmap"|"#us-news"|"#israel"|"#us",
      "tag": "DATA"|"VISUAL"|"UX"|"CONTENT"
    }
  ]
}

Be honest and precise. Score 1-10 based on:
- Data completeness (indices, sectors, macro, crypto, IL)
- Visualization quality (charts, heatmaps, gauges)
- News quality and depth
- Professional usefulness for a trader/investor
- Missing standard Bloomberg/terminal features

Limit improvement_plan to the 3 most impactful items.
"""

def run_brief_phase(client: anthropic.Anthropic, context: str) -> dict:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_BRIEF_SYSTEM,
        messages=[{"role": "user", "content":
            f"Analyze this financial dashboard:\n\n{context}"}],
    )
    raw = _clean_json(resp.content[0].text)
    return json.loads(raw)

# ── Phase 2: Implement ─────────────────────────────────────────────────────────

_IMPLEMENT_SYSTEM = """\
You are a senior financial dashboard developer. You receive a professional brief
about a Hebrew financial dashboard and your job is to BUILD the improvements as
self-contained HTML.

Return ONLY a JSON array of improvements (can be empty []):
[
  {
    "improvement_id": "slug-YYYY-MM-DD",
    "title_he": "<Hebrew section title>",
    "reason_he": "<1-2 Hebrew sentences: why this helps a professional investor>",
    "inject_after": "<CSS id of existing section, e.g. #heatmap>",
    "html": "<complete self-contained HTML with inline styles/script if needed>"
  }
]

STRICT HTML RULES:
• CSS variables available: var(--bg) var(--surface) var(--card) var(--border)
  var(--accent) var(--green) var(--red) var(--gold) var(--purple)
  var(--text) var(--muted) var(--white)
• RTL layout — add dir="rtl" to container divs
• All labels in Hebrew
• Vanilla JS/CSS only — no external libraries, no CDN imports
• Bloomberg/WSJ compact style — data-dense, no decorative padding
• Include <style> block inside html field if you need custom styles
• Simulated/placeholder data is fine for structural components — the next
  dashboard run will populate them with real data via news_dashboard.py

BUILD only from the improvement_plan items marked high/medium priority.
Skip low-priority items or if the improvement_plan is empty return [].
Maximum 3 items. Quality > quantity.
"""

def run_implement_phase(client: anthropic.Anthropic,
                        brief: dict, context: str) -> list[dict]:
    plan_text = json.dumps(brief.get("improvement_plan", []), ensure_ascii=False, indent=2)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=_IMPLEMENT_SYSTEM,
        messages=[{"role": "user", "content":
            f"Date: {TODAY}\n\n"
            f"Dashboard context:\n{context}\n\n"
            f"Professional brief assessment:\n"
            f"Score: {brief.get('overall_score')}/10\n"
            f"Assessment: {brief.get('professional_assessment','')}\n"
            f"Weaknesses: {', '.join(brief.get('weaknesses',[]))}\n"
            f"Missing (high): {', '.join(brief.get('missing_high_priority',[]))}\n\n"
            f"Improvement plan:\n{plan_text}\n\n"
            "Build the HTML for the high/medium priority improvements. Return JSON array."}],
    )
    raw = _clean_json(resp.content[0].text)
    result = json.loads(raw)
    return result if isinstance(result, list) else []

# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()

def _print_brief(brief: dict) -> None:
    print("\n" + "─" * 52)
    print("  PROFESSIONAL DASHBOARD BRIEF")
    print("─" * 52)
    print(f"  Score : {brief.get('overall_score')}/10")
    print(f"  Assessment: {brief.get('professional_assessment','')}")
    print(f"\n  Strengths:")
    for s in brief.get("strengths", []):
        print(f"    ✓ {s}")
    print(f"\n  Weaknesses:")
    for w in brief.get("weaknesses", []):
        print(f"    ✗ {w}")
    print(f"\n  Missing (high priority):")
    for m in brief.get("missing_high_priority", []):
        print(f"    ! {m}")
    print(f"\n  Improvement plan:")
    for p in brief.get("improvement_plan", []):
        print(f"    [{p['priority'].upper()}] {p['title_he']} — {p['description']}")
    print("─" * 52 + "\n")

# ── HTML injection ─────────────────────────────────────────────────────────────

_DISMISS_JS = """
<script id="ai-improvement-script">
(function(){
  document.querySelectorAll('.ai-improvement').forEach(function(el){
    if(localStorage.getItem('ai_dismissed_'+el.dataset.improvementId)==='1')
      el.style.display='none';
  });
})();
function dismissImprovement(id){
  localStorage.setItem('ai_dismissed_'+id,'1');
  var el=document.querySelector('[data-improvement-id="'+id+'"]');
  if(el){el.style.transition='opacity .3s';el.style.opacity='0';
    setTimeout(function(){el.style.display='none'},320);}
}
</script>"""

_IMP_CSS = """
  /* ── AI Improvement blocks ── */
  .imp-badge{font-size:.58rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
    color:var(--accent);background:rgba(14,165,233,.13);
    border:1px solid rgba(14,165,233,.3);border-radius:999px;
    padding:.1rem .55rem}
  .imp-reason{font-size:.78rem;color:var(--muted);font-style:italic;
    line-height:1.55;margin:.3rem 0 .9rem}
  .imp-dismiss{
    background:none;border:1px solid var(--border);border-radius:6px;
    color:var(--muted);cursor:pointer;font-size:.68rem;
    padding:.22rem .65rem;transition:all .2s;white-space:nowrap;
    margin-right:auto;
  }
  body[dir=rtl] .imp-dismiss{margin-right:0;margin-left:auto}
  .imp-dismiss:hover{border-color:var(--red);color:var(--red)}
"""

def _inject_after_section(html: str, section_id: str, block: str) -> str:
    needle = f'id="{section_id}"'
    idx = html.find(needle)
    if idx != -1:
        close = html.find("</section>", idx)
        if close != -1:
            pos = close + len("</section>")
            return html[:pos] + block + html[pos:]
    return html.replace("<footer>", block + "\n<footer>", 1)


def inject_all_improvements(html: str, improvements: list[dict]) -> str:
    for imp in improvements:
        imp_id   = imp["improvement_id"]
        title    = imp["title_he"]
        reason   = imp.get("reason_he", "")
        content  = imp["html"]
        after_id = imp.get("inject_after", "#us-news").lstrip("#")

        dismiss = (
            f'<button class="imp-dismiss" '
            f'onclick="dismissImprovement(\'{imp_id}\')" '
            f'title="הסר מהתצוגה">✕ הסר</button>'
        )

        block = (
            f'\n<!-- AI-IMPROVEMENT:{imp_id} -->\n'
            f'<section class="section ai-improvement" '
            f'data-improvement-id="{imp_id}" id="ai-{imp_id}">\n'
            f'  <div class="section-label">'
            f'<span class="imp-badge">AI ✦</span> {title} {dismiss}'
            f'</div>\n'
            + (f'  <p class="imp-reason">{reason}</p>\n' if reason else "")
            + f'  {content}\n'
            f'</section>\n'
            f'<!-- /AI-IMPROVEMENT:{imp_id} -->\n'
        )
        html = _inject_after_section(html, after_id, block)

    # CSS — inject once
    if ".imp-dismiss" not in html:
        html = html.replace("</style>", _IMP_CSS + "</style>", 1)

    # JS — replace or append once
    js_re = re.compile(
        r'<script id="ai-improvement-script">.*?</script>', re.DOTALL
    )
    if js_re.search(html):
        html = js_re.sub(_DISMISS_JS, html)
    else:
        html = html.replace("</body>", _DISMISS_JS + "\n</body>", 1)

    return html

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry" in sys.argv

    print(f"\n{'='*52}")
    print(f"  AI Improvement Agent — {TODAY}")
    if dry_run:
        print("  (dry-run: analysis only, no files written)")
    print(f"{'='*52}\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("✗  ANTHROPIC_API_KEY not set — aborting"); sys.exit(1)

    if not INDEX_PATH.exists():
        print(f"✗  Not found: {INDEX_PATH}"); sys.exit(1)

    # ── Read & clean ──────────────────────────────────────────────────────────
    html = INDEX_PATH.read_text(encoding="utf-8")
    print(f"  Read {len(html):,} chars from {INDEX_PATH}")

    clean_html = strip_old_improvements(html)
    removed = len(html) - len(clean_html)
    if removed > 0:
        print(f"  ✓ Stripped previous improvements ({removed} chars)")

    # ── Extract context ───────────────────────────────────────────────────────
    context = extract_context(clean_html)
    print(f"\n  Dashboard context extracted:")
    for line in context.splitlines()[:6]:
        print(f"    {line}")

    # ── Phase 1: Brief ────────────────────────────────────────────────────────
    client = anthropic.Anthropic(api_key=api_key)

    print("\n[ Phase 1 ] Writing professional brief...")
    try:
        brief = run_brief_phase(client, context)
    except Exception as e:
        print(f"✗  Brief phase failed: {e}"); sys.exit(1)

    _print_brief(brief)

    if not dry_run:
        BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
        BRIEF_PATH.write_text(
            json.dumps({"date": TODAY, **brief}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✓ Brief saved → {BRIEF_PATH}")

    # ── Phase 2: Implement ────────────────────────────────────────────────────
    plan = brief.get("improvement_plan", [])
    actionable = [p for p in plan if p.get("priority") in ("high", "medium")]

    if not actionable:
        print("\n[ Phase 2 ] No high/medium-priority improvements — dashboard looks complete.")
        if not dry_run:
            INDEX_PATH.write_text(clean_html, encoding="utf-8")
        return

    print(f"\n[ Phase 2 ] Building {len(actionable)} improvement(s)...")
    try:
        improvements = run_implement_phase(client, brief, context)
    except Exception as e:
        print(f"✗  Implement phase failed: {e}"); sys.exit(1)

    if not improvements:
        print("  ✓ No HTML improvements generated — dashboard is complete.")
        if not dry_run:
            INDEX_PATH.write_text(clean_html, encoding="utf-8")
        return

    print(f"\n  ✦ {len(improvements)} improvement(s) ready:")
    for imp in improvements:
        print(f"    [{imp.get('improvement_id')}]  {imp.get('title_he')}")
        print(f"     → {imp.get('reason_he','')}")

    if dry_run:
        print("\n  [dry-run] Skipping file write. HTML preview of first block:")
        print("  " + "─" * 48)
        print(improvements[0]["html"][:800])
        return

    # ── Inject & save ─────────────────────────────────────────────────────────
    final_html = inject_all_improvements(clean_html, improvements)
    INDEX_PATH.write_text(final_html, encoding="utf-8")
    print(f"\n  ✓ Dashboard updated → {INDEX_PATH}")
    print(f"  ✓ {len(improvements)} section(s) injected, each with '✕ הסר' dismiss button\n")


if __name__ == "__main__":
    main()
