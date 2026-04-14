#!/usr/bin/env python3
"""
AI Improvement Agent — Daily Dashboard Enhancer
================================================
Reads docs/index.html every day, analyzes it with a professional eye,
and optionally injects ONE high-value improvement section.

Each injected section includes a dismiss button (stored in localStorage)
so the user can remove an improvement from view without rerunning the script.

Usage:
  python improvement_agent.py          # analyze + inject if worthwhile
  python improvement_agent.py --dry    # print suggestion only, don't write
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
else:
    IDOP_DIR   = Path("C:/Users/idoph/OneDrive/IDOP")
    INDEX_PATH = IDOP_DIR / "reports/docs/index.html"

TODAY = date.today().isoformat()
MODEL = os.environ.get("DASHBOARD_MODEL", "claude-opus-4-6")

# ── Previous-improvement cleanup ───────────────────────────────────────────────

_IMP_BLOCK_RE = re.compile(
    r'\n?<!-- AI-IMPROVEMENT:[\w-]+ -->.*?<!-- /AI-IMPROVEMENT:[\w-]+ -->',
    re.DOTALL,
)

def strip_old_improvements(html: str) -> str:
    return _IMP_BLOCK_RE.sub("", html)

# ── Dashboard context extractor ────────────────────────────────────────────────

def extract_context(html: str) -> str:
    """
    Produce a compact, text-only summary of what's currently on the dashboard.
    Sent to Claude so it knows what already exists.
    """
    def find_all(cls: str) -> list[str]:
        return [s.strip() for s in re.findall(
            rf'class="{re.escape(cls)}"[^>]*>([^<]+)', html
        ) if s.strip()]

    sections  = find_all("section-label")
    mkt_names = list(dict.fromkeys(find_all("mkt-name")))  # preserve order, dedupe
    headlines = find_all("news-title")[:8]
    tickers   = find_all("tick-name")[:14]

    widgets = []
    if "fg-card"       in html: widgets.append("Fear & Greed gauge")
    if "heatmap-grid"  in html: widgets.append("Sector heatmap (S&P 500 ETFs)")
    if "cal-strip"     in html: widgets.append("Economic calendar strip")
    if "ticker-wrap"   in html: widgets.append("Live ticker tape")
    if "spark"         in html: widgets.append("5-day sparklines")
    if "tw-card"       in html: widgets.append("Twitter/X insight cards")
    if "StockTwits"    in html: widgets.append("StockTwits posts")
    if "ai-improvement" in html: widgets.append("AI improvement block (prev day)")

    news_count = len(re.findall(r'class="news-title"', html))
    il_count   = len(re.findall(r'class="il-card"', html))

    lines = [
        f"DATE: {TODAY}",
        f"SECTIONS:  {' | '.join(sections)}",
        f"MARKET DATA: {', '.join(mkt_names)}",
        f"WIDGETS PRESENT: {', '.join(widgets) or 'none'}",
        f"TICKER TAPE ITEMS: {', '.join(tickers)}",
        f"US NEWS CARDS: {news_count}   IL NEWS CARDS: {il_count}",
        f"SAMPLE HEADLINES: {' / '.join(headlines)}",
    ]
    return "\n".join(lines)

# ── Claude Agent ───────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a senior financial dashboard designer with deep expertise in:
- Bloomberg Terminal / Refinitiv Eikon UX patterns
- Real-time market-data visualization
- Professional trader/investor workflows
- Hebrew RTL web layouts

Your job: review a daily Hebrew financial news dashboard and decide whether to add
ONE high-value improvement today.

Return a single JSON object — no markdown, no extra text:

If you have a good improvement:
{
  "improvement_id": "descriptive-slug-YYYY-MM-DD",
  "title_he": "קצר בעברית",
  "reason_he": "1-2 משפטים בעברית למה זה מוסיף ערך למשתמש מקצועי",
  "inject_after": "#heatmap",
  "html": "<complete self-contained HTML>"
}

If the dashboard is already excellent and nothing meaningful to add:
{ "improvement_id": null }

═══════════════════════════════════════════════════
HTML RULES (strictly enforced):
• Use ONLY these existing CSS variables:
    var(--bg)  var(--surface)  var(--card)  var(--border)
    var(--accent)  var(--green)  var(--red)  var(--gold)
    var(--purple)  var(--text)  var(--muted)  var(--white)
• RTL layout — dir="rtl" on any container div
• Hebrew labels for all UI text
• Vanilla JS/CSS only — zero external libraries
• Compact and data-dense — Bloomberg style, not decorative
• If you need extra styles, embed a <style> block inside the html field
• inject_after = CSS id of an existing section, e.g. "#heatmap", "#us-news", "#israel"
═══════════════════════════════════════════════════
HIGH-VALUE IDEAS (pick whichever fits best, avoid what's already there):
- Top movers table: biggest % gainers / losers from the ticker tape today
- Weekly earnings preview: companies reporting this week (date + ticker + consensus EPS)
- VIX regime card: current VIX level vs 30-day avg + simple regime label
- Bond yield curve mini-strip: 2Y / 5Y / 10Y / 30Y with change arrows
- Currency strength meter: DXY + EUR/USD + GBP/USD + USD/JPY in a small row
- Crypto dominance bar: BTC vs ETH vs others as a thin percentage bar
- News sentiment summary: count of positive / negative / neutral from today's headlines
- Analyst activity summary: upgrades / downgrades / initiations (if visible in headlines)
- Options flow note: unusual options activity signals from StockTwits headlines
═══════════════════════════════════════════════════
Be honest: if nothing adds clear value, return null. Quality > quantity.
"""


def run_agent(client: anthropic.Anthropic, context: str) -> dict | None:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content":
            f"Analyze this dashboard and suggest one improvement (or none).\n\n{context}"}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw.strip())
    return result if result.get("improvement_id") else None

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
  if(el){el.style.transition='opacity .35s';el.style.opacity='0';
    setTimeout(function(){el.style.display='none'},380);}
}
</script>"""

_IMP_CSS = """
  /* ── AI Improvement ── */
  .imp-badge{font-size:.6rem;font-weight:700;letter-spacing:.08em;
    color:var(--accent);background:rgba(14,165,233,.13);
    border:1px solid rgba(14,165,233,.3);border-radius:999px;
    padding:.1rem .55rem;margin-left:.4rem}
  .imp-reason{font-size:.78rem;color:var(--muted);font-style:italic;
    line-height:1.55;margin-bottom:.9rem}
  .imp-dismiss{
    margin-right:auto;background:none;
    border:1px solid var(--border);border-radius:6px;
    color:var(--muted);cursor:pointer;font-size:.68rem;
    padding:.22rem .6rem;transition:all .2s;white-space:nowrap;
  }
  body[dir=rtl] .imp-dismiss{margin-right:0;margin-left:auto}
  .imp-dismiss:hover{border-color:var(--red);color:var(--red)}
"""


def _inject_block(html: str, after_id: str, block: str) -> str:
    """Insert block after the closing </section> of the element with id=after_id."""
    needle = f'id="{after_id}"'
    idx = html.find(needle)
    if idx != -1:
        close = html.find("</section>", idx)
        if close != -1:
            pos = close + len("</section>")
            return html[:pos] + block + html[pos:]
    # Fallback: before </div> that wraps the main container
    return html.replace("<footer>", block + "\n<footer>", 1)


def inject_improvement(html: str, imp: dict) -> str:
    imp_id   = imp["improvement_id"]
    title    = imp["title_he"]
    reason   = imp.get("reason_he", "")
    content  = imp["html"]
    after_id = imp.get("inject_after", "#us-news").lstrip("#")

    dismiss = (
        f'<button class="imp-dismiss" '
        f'onclick="dismissImprovement(\'{imp_id}\')" '
        f'title="הסר תוסף זה מהתצוגה">✕ הסר</button>'
    )
    badge = '<span class="imp-badge">AI ✦</span>'

    block = (
        f'\n<!-- AI-IMPROVEMENT:{imp_id} -->\n'
        f'<section class="section ai-improvement" '
        f'data-improvement-id="{imp_id}" id="ai-{imp_id}">\n'
        f'  <div class="section-label">{badge} {title} {dismiss}</div>\n'
        + (f'  <p class="imp-reason">{reason}</p>\n' if reason else "")
        + f'  {content}\n'
        f'</section>\n'
        f'<!-- /AI-IMPROVEMENT:{imp_id} -->\n'
    )

    html = _inject_block(html, after_id, block)

    # CSS — inject once
    if ".imp-dismiss" not in html:
        html = html.replace("</style>", _IMP_CSS + "</style>", 1)

    # JS dismiss script — replace or append
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
        print("  (dry-run mode — no files written)")
    print(f"{'='*52}\n")

    # Validate env
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("✗  ANTHROPIC_API_KEY not set — aborting")
        sys.exit(1)

    if not INDEX_PATH.exists():
        print(f"✗  Dashboard not found: {INDEX_PATH}")
        sys.exit(1)

    # Read current dashboard
    html = INDEX_PATH.read_text(encoding="utf-8")
    print(f"  Read {len(html):,} chars from {INDEX_PATH}")

    # Remove yesterday's improvement
    clean_html = strip_old_improvements(html)
    removed_chars = len(html) - len(clean_html)
    if removed_chars > 0:
        print(f"  ✓ Stripped previous improvement ({removed_chars} chars)")

    # Extract what's on the page now
    context = extract_context(clean_html)
    print(f"\n  Dashboard context:\n  " + context.replace("\n", "\n  "))

    # Ask Claude
    print("\n[ Claude ] Analyzing dashboard with professional eye...")
    client = anthropic.Anthropic(api_key=api_key)
    try:
        improvement = run_agent(client, context)
    except Exception as e:
        print(f"✗  Agent error: {e}")
        sys.exit(1)

    if not improvement:
        print("\n  ✓ Dashboard looks complete — no improvement needed today.")
        if not dry_run:
            INDEX_PATH.write_text(clean_html, encoding="utf-8")
            print(f"  (saved cleaned html → {INDEX_PATH})")
        return

    # Print suggestion
    print(f"\n  ✦ Improvement suggested:")
    print(f"    ID     : {improvement['improvement_id']}")
    print(f"    Title  : {improvement['title_he']}")
    print(f"    Reason : {improvement.get('reason_he', '')}")
    print(f"    After  : {improvement.get('inject_after', '?')}")

    if dry_run:
        print("\n  [dry-run] HTML preview:")
        print("  " + "-"*48)
        print(improvement["html"][:800])
        print("  " + "-"*48)
        return

    # Inject and save
    final_html = inject_improvement(clean_html, improvement)
    INDEX_PATH.write_text(final_html, encoding="utf-8")
    print(f"\n  ✓ Dashboard updated → {INDEX_PATH}")
    print(f"  ✓ Dismiss button injected (uses localStorage)")


if __name__ == "__main__":
    main()
