"""HtmlRenderer — DashboardSchema → ONE self-contained dashboard.html.

Embedded CSS (dark-exec default / light Meridian variant), tab JS, inlined
ECharts + (when a geo chart exists) D3 + topojson + world atlas. Video banner is
remote-streamed with a gradient fallback; everything else is embedded."""

import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from app.config.settings import get_settings


def _e(value) -> str:
    """Escape untrusted text for HTML context — titles, summaries, names, KPI
    values all originate from LLM output or the web."""
    return escape(str(value), quote=True)


def _safe_http_url(url: str) -> str:
    """Only http(s) URLs survive into src attributes."""
    try:
        scheme = urlparse(url).scheme
    except Exception:
        return ""
    return escape(url, quote=True) if scheme in ("http", "https") else ""


def _safe_data_image(uri: str) -> str:
    return escape(uri, quote=True) if str(uri).startswith("data:image/") else ""


def _fmt_date(value) -> str:
    """ISO date/datetime → 'Tue, Jul 14, 2026'. Empty/unparseable → '' (caller shows n/a)."""
    from datetime import date

    s = str(value or "").strip()
    if not s:
        return ""
    try:
        d = date.fromisoformat(s[:10])
    except Exception:
        return _e(s)                      # already human text — keep as-is (escaped)
    return f"{d:%a, %b} {d.day}, {d.year}"

VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor"

CDN = {
    "echarts.min.js": "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js",
    "d3.min.js": "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js",
    "topojson-client.min.js":
        "https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js",
}

THEMES = {
    "dark": """
:root{--bg:#0a0a0c;--card:#161719;--card2:#1f2124;--ink:#f5f5f7;--ink2:#a1a1a6;
--muted:#8a8a90;--accent:#3b82f6;--line:#2a2c30;--good:#30d158;--bad:#ff453a;
--shadow:0 1px 2px rgba(0,0,0,.5),0 14px 34px -10px rgba(0,0,0,.6);
--shadow-lift:0 1px 2px rgba(0,0,0,.5),0 22px 46px -12px rgba(0,0,0,.7)}
body{background:var(--bg);color:var(--ink)}
.banner{background:linear-gradient(120deg,#0b1224,#0a0a0c)}
""",
    "light": """
:root{--bg:#fbfbfd;--card:#ffffff;--card2:#f4f4f7;--ink:#1d1d1f;--ink2:#6e6e73;
--muted:#86868b;--accent:#0b6bcb;--line:#e7e7ec;--good:#127a50;--bad:#c0362c;
--shadow:0 1px 2px rgba(0,0,0,.04),0 14px 32px -12px rgba(20,30,60,.14);
--shadow-lift:0 1px 2px rgba(0,0,0,.05),0 24px 46px -14px rgba(20,30,60,.22)}
body{background:var(--bg);color:var(--ink)}
.banner{background:linear-gradient(120deg,#12386e,#0b6bcb)}
""",
}

BASE_CSS = """
:root{--font-display:'Fraunces',Georgia,'Times New Roman',serif;
--font-body:'Hanken Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
*{box-sizing:border-box;margin:0}
html{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{font-family:var(--font-body);font-size:14px;line-height:1.55;letter-spacing:-.006em}
.banner{position:relative;height:300px;overflow:hidden}
.banner video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.42}
.banner .overlay{position:absolute;inset:0;max-width:1320px;margin:0 auto;
  display:flex;flex-direction:column;justify-content:flex-end;padding:0 46px 42px;
  background:linear-gradient(180deg,rgba(2,6,23,.05),rgba(2,6,23,.74))}
.eyebrow{font-size:11px;font-weight:700;letter-spacing:.24em;text-transform:uppercase;
  color:rgba(255,255,255,.74);margin-bottom:14px;display:flex;align-items:center;gap:10px}
.eyebrow::before{content:"";width:24px;height:1px;background:rgba(255,255,255,.55)}
.banner h1{font-family:var(--font-display);font-size:46px;font-weight:600;line-height:1.03;
  letter-spacing:-.02em;color:#fff;max-width:22ch;font-optical-sizing:auto}
.banner .meta{color:rgba(255,255,255,.72);font-size:12.5px;margin-top:14px;letter-spacing:.01em}
.logos{display:flex;gap:10px;align-items:center;position:absolute;top:26px;right:32px;z-index:2}
.logo{width:46px;height:46px;border-radius:13px;background:rgba(255,255,255,.92);display:grid;
  place-items:center;overflow:hidden;box-shadow:0 6px 18px rgba(0,0,0,.28);transition:transform .25s ease}
.logo:hover{transform:translateY(-2px)}
.logo img{width:78%;height:78%;object-fit:contain}
.logo.mono{color:#12386e;font-weight:800;font-size:16px;font-family:var(--font-display)}
.logo.small{width:36px;height:36px;border-radius:10px;font-size:12px}
.wrap{max-width:1320px;margin:0 auto;padding:34px 46px 90px}
.tabs{display:inline-flex;gap:4px;margin:4px 0 30px;padding:5px;border-radius:14px;
  background:var(--card2);border:1px solid var(--line);flex-wrap:wrap}
.tab{padding:9px 18px;border-radius:10px;background:transparent;color:var(--ink2);border:0;
  cursor:pointer;font-weight:600;font-size:13px;font-family:var(--font-body);
  transition:color .2s ease,background .2s ease,box-shadow .2s ease}
.tab:hover{color:var(--ink)}
.tab.on{background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.14)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.kpi-container{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:16px;margin-bottom:30px}
.kpi-card{position:relative;overflow:hidden;background:var(--card);border:1px solid var(--line);
  border-radius:18px;padding:22px 24px;box-shadow:var(--shadow);
  transition:transform .25s ease,box-shadow .25s ease}
.kpi-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent)}
.kpi-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lift)}
.kpi-card small{display:block;font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);font-weight:700}
.kpi-card b{display:block;margin-top:8px;font-size:32px;font-weight:600;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;color:var(--ink)}
.kpi-card .sub{font-size:11.5px;color:var(--ink2);margin-top:3px}
.page{display:none}.page.on{display:block;animation:fade .4s ease both}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.subtabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 20px;background:var(--card);
  border:1px solid var(--line);border-radius:14px;padding:6px}
.subtab{border:0;background:transparent;color:var(--ink2);font:inherit;font-size:13.5px;
  font-weight:600;padding:8px 14px;border-radius:9px;cursor:pointer;transition:all .15s}
.subtab:hover{color:var(--ink)}
.subtab.on{background:var(--accent);color:#fff}
.subpage{display:none}.subpage.on{display:block;animation:fade .35s ease both}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:20px}
.chart-insight{margin:0 0 14px;font-size:12.5px;line-height:1.5;color:var(--ink2)}
.card{background:var(--card);border-radius:20px;padding:24px;box-shadow:var(--shadow);
  border:1px solid var(--line);transition:transform .25s ease,box-shadow .25s ease}
.card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lift)}
.card h3{font-family:var(--font-display);font-size:18px;font-weight:600;letter-spacing:-.01em;
  margin-bottom:4px;color:var(--ink)}
.chart{min-height:400px;width:100%}
.summary-container .card{margin-bottom:16px}
.summary-container li{margin:10px 0 10px 18px;font-size:14px;line-height:1.55;color:var(--ink)}
.summary-container h3{color:var(--ink);font-family:var(--font-display)}
/* home command-center */
.home-intro{text-align:center;max-width:640px;margin:14px auto 26px}
.eyebrow2{font-size:11px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:var(--accent)}
.home-intro h2{font-family:var(--font-display);font-size:34px;font-weight:600;letter-spacing:-.02em;margin:8px 0}
.home-sub{color:var(--ink2);font-size:14px;line-height:1.6}
.view-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.view-card{text-align:left;cursor:pointer;background:var(--card);border:1px solid var(--line);
  border-radius:20px;padding:24px;box-shadow:var(--shadow);display:flex;flex-direction:column;
  gap:8px;transition:transform .25s ease,box-shadow .25s ease;font-family:var(--font-body)}
.view-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lift)}
.vc-label{font-family:var(--font-display);font-size:20px;font-weight:600;color:var(--ink)}
.vc-desc{font-size:13px;line-height:1.5;color:var(--ink2);flex:1}
.vc-go{font-size:13px;font-weight:600;color:var(--accent);margin-top:6px}
/* daily monitoring */
.daily-day{background:var(--card);border:1px solid var(--line);border-radius:20px;
  padding:18px 22px;margin-bottom:16px;box-shadow:var(--shadow)}
.daily-date{font-family:var(--font-display);font-size:17px;font-weight:600;margin-bottom:10px;
  display:flex;align-items:center;gap:10px}
.daily-count{font-family:var(--font-body);font-size:11px;font-weight:700;color:var(--muted);
  background:var(--card2);border-radius:20px;padding:2px 9px}
.daily-row{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--line)}
.dr-title{font-size:13.5px;font-weight:500;line-height:1.45;color:var(--ink)}
.dr-meta{font-size:11.5px;color:var(--muted);margin-top:2px}
.sent{width:8px;height:8px;border-radius:50%;margin-top:6px;flex-shrink:0;background:var(--muted)}
.sent-pos{background:var(--good)}.sent-neg{background:var(--bad)}.sent-neu{background:var(--muted)}
/* in-report chat */
.chat-fab{position:fixed;right:24px;bottom:24px;z-index:1000;border:0;cursor:pointer;
  background:var(--accent);color:#fff;border-radius:24px;padding:13px 20px;font-weight:600;
  font-family:var(--font-body);font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.28);
  display:flex;align-items:center;gap:8px;transition:transform .2s ease}
.chat-fab:hover{transform:translateY(-2px)}
.chat-panel{position:fixed;right:24px;bottom:84px;z-index:1000;width:min(400px,92vw);
  height:min(560px,72vh);background:var(--card);border:1px solid var(--line);border-radius:20px;
  box-shadow:0 24px 60px rgba(0,0,0,.4);display:none;flex-direction:column;overflow:hidden}
.chat-panel.open{display:flex}
.chat-head{padding:15px 18px;border-bottom:1px solid var(--line);font-family:var(--font-display);
  font-size:16px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
.chat-close{border:0;background:transparent;color:var(--ink2);font-size:22px;cursor:pointer;line-height:1}
.chat-log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.chat-msg{max-width:88%;padding:10px 13px;border-radius:14px;font-size:13.5px;line-height:1.5;
  white-space:pre-wrap;word-wrap:break-word}
.chat-msg.user{align-self:flex-end;background:var(--accent);color:#fff}
.chat-msg.bot{align-self:flex-start;background:var(--card2);color:var(--ink)}
.chat-msg .ts{display:block;font-size:10px;opacity:.55;margin-top:5px}
.chat-msg table{border-collapse:collapse;width:100%;margin:6px 0;font-size:12px}
.chat-msg th,.chat-msg td{border:1px solid var(--line);padding:4px 7px;text-align:left}
.chat-msg th{background:var(--card2);font-weight:600}
.chat-msg h4,.chat-msg h5{margin:8px 0 4px;font-family:var(--font-display)}
.chat-msg ul,.chat-msg ol{margin:4px 0;padding-left:18px}
.chat-msg code{background:var(--card2);padding:1px 5px;border-radius:5px;font-size:12px}
.chat-echarts{width:100%;height:240px;margin:8px 0}
.chat-input{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line)}
.chat-input input{flex:1;border:1px solid var(--line);border-radius:12px;padding:10px 12px;
  background:var(--bg);color:var(--ink);font-family:var(--font-body);font-size:13.5px}
.chat-input button{border:0;background:var(--accent);color:#fff;border-radius:12px;padding:0 16px;
  font-weight:600;cursor:pointer}
.chat-note{font-size:11px;color:var(--muted);text-align:center;padding:0 12px 11px}
footer{margin-top:44px;color:var(--muted);font-size:11.5px;text-align:center;letter-spacing:.02em}
@media(max-width:640px){.chart-grid{grid-template-columns:1fr}.banner{height:250px}
  .banner h1{font-size:30px}.wrap{padding:24px 20px 70px}.banner .overlay{padding:0 22px 28px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

TAB_JS = """
function showTab(id){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.t===id));
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.id==='page-'+id));
  window.dispatchEvent(new Event('resize'));
}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.t)));
"""

GEO_JS = """
function renderGeo(el, counts, dark){
  const world = window.__WORLD__;
  const countries = topojson.feature(world, world.objects.countries);
  const w = el.clientWidth||900, h=420;
  const svg = d3.select(el).append('svg').attr('viewBox',`0 0 ${w} ${h}`);
  const proj = d3.geoNaturalEarth1().fitSize([w,h], countries);
  const path = d3.geoPath(proj);
  const max = Math.max(1,...Object.values(counts));
  const color = d3.scaleSequential(dark?d3.interpolateGnBu:d3.interpolateBlues).domain([0,max]);
  function countFor(f){
    const n=(f.properties.name||'');
    const hit=Object.keys(counts).find(k=>n.toLowerCase().startsWith(countryName(k).toLowerCase()));
    return hit?counts[hit]:0;
  }
  let tip=document.querySelector('.geo-tip');
  if(!tip){ tip=document.createElement('div'); tip.className='geo-tip';
    tip.style.cssText='position:fixed;pointer-events:none;z-index:9999;opacity:0;'
      +'background:rgba(15,23,42,.94);color:#fff;padding:6px 10px;border-radius:8px;'
      +'font:12px/1.3 system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.3);'
      +'transition:opacity .12s';
    document.body.appendChild(tip); }
  svg.selectAll('path').data(countries.features).join('path')
    .attr('d',path)
    .attr('fill',f=>{const c=countFor(f); return c?color(c):(dark?'#1e293b':'#E2E5EA');})
    .attr('stroke',dark?'#334155':'#cbd5e1').attr('stroke-width',.4)
    .style('cursor','pointer')
    .on('mousemove',(ev,f)=>{const c=countFor(f);
        tip.innerHTML='<b>'+(f.properties.name||'')+'</b>: '+c+' article'+(c===1?'':'s');
        tip.style.left=(ev.clientX+14)+'px'; tip.style.top=(ev.clientY+14)+'px';
        tip.style.opacity=1;})
    .on('mouseleave',()=>{tip.style.opacity=0;});
}
function countryName(code){
  const m={US:'United States',GB:'United Kingdom',DE:'Germany',FR:'France',IN:'India',
  AU:'Australia',CA:'Canada',JP:'Japan',CN:'China',SG:'Singapore',AE:'United Arab',
  BR:'Brazil',MX:'Mexico',ES:'Spain',IT:'Italy',NL:'Netherlands',SE:'Sweden',
  CH:'Switzerland',IE:'Ireland',NZ:'New Zealand',ZA:'South Africa',KR:'Korea'};
  return m[code]||code;
}
"""


@lru_cache
def _vendor(name: str) -> str:
    return (VENDOR / name).read_text(encoding="utf-8")


def _script(name: str, embed: bool) -> str:
    if embed:
        return f"<script>{_vendor(name)}</script>"
    return f'<script src="{CDN[name]}"></script>'


_MONO_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _logo_html(logo: dict, small: bool = False) -> str:
    cls = "logo small" if small else "logo"
    name = _e(logo.get("name", ""))
    if logo.get("kind") == "image":
        src = _safe_data_image(logo.get("data_uri", ""))
        if src:
            return (f'<span class="{cls}" title="{name}">'
                    f'<img src="{src}" alt="{name} logo"></span>')
    color = logo.get("color", "#2563eb")
    if not _MONO_COLOR_RE.match(str(color)):
        color = "#2563eb"
    return (f'<span class="{cls} mono" title="{name}" '
            f'style="background:{color}">{_e(logo.get("initials", "?"))}</span>')


_TAB_DESC = {
    "overview": "Coverage volume, reach and sentiment across every tracked source.",
    "coverage": "Where the story landed — geography, outlets and the voices behind it.",
    "competitive": "Share of voice and how the brand stacks up against its rivals.",
    "reputation": "A composite reputation score, tracked over time and by driver.",
    "narratives": "The active narratives shaping perception and how they move.",
    "insights": "The board-ready read: executive summary and recommendations.",
    "daily": "Track coverage day by day — filter by date, browse and re-file by section.",
    "media": "Measure the period: overview, sentiment, themes, media coverage and key stories.",
}


def _home_cards(view_tabs: list[dict]) -> str:
    cards = "".join(
        f'<button class="view-card" onclick="showTab(\'{_e(t["id"])}\')">'
        f'<span class="vc-label">{_e(t["label"])}</span>'
        f'<span class="vc-desc">{_e(_TAB_DESC.get(t["id"], ""))}</span>'
        f'<span class="vc-go">Open →</span></button>'
        for t in view_tabs)
    return ('<div class="home-intro"><span class="eyebrow2">Ways in</span>'
            '<h2>Choose your view</h2>'
            '<p class="home-sub">Track the story as it breaks, step back and measure the '
            'period, or dive into narratives, PR impact and reputation — every signal '
            'stays in sync across all views.</p></div>'
            f'<div class="view-grid">{cards}</div>')


_DAILY_CSS = """<style>
.dm-wrap{display:grid;grid-template-columns:260px 1fr;gap:20px;align-items:start}
@media (max-width:820px){.dm-wrap{grid-template-columns:1fr}}
.dm-side{display:flex;flex-direction:column;gap:16px;position:sticky;top:76px}
.dm-panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}
.dm-panel h4{margin:0 0 10px;font-family:var(--font-display);font-size:14px}
.dm-secs{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.dm-secs li{display:flex;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:9px;
  font-size:13px;cursor:pointer;color:var(--ink);transition:background .15s}
.dm-secs li:hover{background:var(--card2)}
.dm-secs li.on{background:var(--accent);color:#fff}
.dm-secs li .c{color:var(--muted);font-variant-numeric:tabular-nums}
.dm-secs li.on .c{color:#fff;opacity:.85}
.dm-filter label{display:block;font-size:11px;color:var(--muted);margin-bottom:8px}
.dm-filter input{width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:9px;
  padding:8px 10px;background:var(--card2);color:var(--ink);font-size:13px}
.dm-sec{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:0 0 16px;
  overflow:hidden}
.dm-sec-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--line);
  font-family:var(--font-display);font-size:15px}
.dm-sec-head .c{margin-left:auto;font-size:11px;font-weight:700;color:var(--muted);
  background:var(--card2);border-radius:20px;padding:3px 10px}
.dm-drop{padding:8px 10px;min-height:44px}
.dm-drop.over{background:color-mix(in srgb,var(--accent) 8%,transparent);outline:2px dashed var(--accent)}
.dm-art{display:flex;gap:10px;padding:12px 10px;border-bottom:1px solid var(--line);cursor:grab}
.dm-art:last-child{border-bottom:0}
.dm-art.drag{opacity:.4}
.dm-handle{color:var(--muted);cursor:grab;user-select:none;font-size:15px;line-height:1.4;letter-spacing:-2px}
.dm-dot{flex:0 0 9px;height:9px;border-radius:50%;margin-top:5px}
.dm-pos{background:#16a34a}.dm-neu{background:#9aa2ad}.dm-neg{background:#dc2626}
.dm-title-row{display:flex;align-items:baseline;flex-wrap:wrap}
.dm-title{font-weight:600;font-size:14px;color:var(--ink);line-height:1.4;text-decoration:none}
a.dm-title:hover{color:var(--accent);text-decoration:underline}
a.dm-title::after{content:"\2197";font-size:11px;color:var(--muted);margin-left:5px;opacity:.7}
.dm-badge{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.04em;border-radius:6px;padding:2px 7px;margin-left:8px;background:#fde7e1;color:#b5462f}
.dm-meta{font-size:12px;color:var(--muted);margin-top:3px}
.dm-snip{font-size:12.5px;color:var(--ink2,var(--muted));margin-top:5px;line-height:1.5}
.dm-tags{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}
.dm-tag{font-size:11px;color:var(--muted);background:var(--card2);border-radius:20px;padding:2px 9px}
.dm-save{font-size:11px;color:var(--accent);margin-left:8px}
.dm-fav{border-radius:3px;vertical-align:-2px;margin-right:5px}
.dm-sent{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.04em;border-radius:6px;padding:2px 7px;margin-left:8px}
.dm-sent-pos{background:#e5f4ec;color:#16794b}
.dm-sent-neg{background:#fde7e1;color:#b5462f}
.dm-sent-neu{background:#eef0f2;color:#6b7280}
.dm-syn{font-size:11px;font-weight:600;color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent);
  border:1px solid color-mix(in srgb,var(--accent) 25%,transparent);border-radius:20px;padding:2px 10px;
  cursor:pointer;font-family:inherit}
.dm-syn:hover{background:color-mix(in srgb,var(--accent) 15%,transparent)}
</style>"""


def _daily_view(rows: list[dict], chat: dict | None = None) -> str:
    import json as _json
    from collections import Counter, defaultdict

    if not rows:
        return '<div class="card"><h3>Daily Monitoring</h3><p>No coverage yet.</p></div>'

    by_sec: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sec[r.get("section") or "Uncategorized"].append(r)
    counts = Counter({s: len(v) for s, v in by_sec.items()})

    side = (
        '<div class="dm-panel dm-filter">'
        '<h4>Filter by date</h4>'
        '<label>From<input type="date" id="dm-from"></label>'
        '<label>To<input type="date" id="dm-to"></label>'
        '<label>Search<input type="text" id="dm-q" placeholder="title, publisher, theme…">'
        '</label></div>'
        '<div class="dm-panel"><h4>Sections</h4><ul class="dm-secs" id="dm-secs">'
        '<li class="on" data-sec="__all">All sections<span class="c">'
        f'{len(rows)}</span></li>'
        + "".join(
            f'<li data-sec="{_e(s)}">{_e(s)}<span class="c">{n}</span></li>'
            for s, n in counts.most_common())
        + "</ul></div>")

    def _card(r: dict) -> str:
        sent = (r.get("sentiment") or "NEU").upper()
        dot = {"POS": "dm-pos", "NEG": "dm-neg"}.get(sent, "dm-neu")
        sent_label = {"POS": "Positive", "NEG": "Negative"}.get(sent, "Neutral")
        sent_pill = f'<span class="dm-sent dm-sent-{sent.lower()}">{sent_label}</span>'
        badge = '<span class="dm-badge">Priority</span>' if r.get("priority") else ""
        reach = r.get("reach") or 0
        reach_s = (f" · reach {reach/1_000_000:.1f}M" if reach >= 1_000_000
                   else f" · reach {reach/1000:.0f}K" if reach >= 1000 else "")
        tags = "".join(f'<span class="dm-tag">{_e(x)}</span>' for x in
                       [r.get("theme"), r.get("emotions"), r.get("signals")] if x)
        # publisher with favicon icon
        pubdom = r.get("publisher_domain", "")
        icon = (f'<img class="dm-fav" src="https://www.google.com/s2/favicons?domain='
                f'{_e(pubdom)}&sz=32" width="15" height="15" alt="" loading="lazy">'
                if pubdom else "")
        when = _fmt_date(r.get("date", "")) or "date n/a"
        if r.get("time"):
            when += f" · {_e(r.get('time', ''))}"
        country = (r.get("country") or "").strip()
        bits = [f'{icon}{_e(r.get("publisher", "")) or "—"}']
        if r.get("author"):
            bits.append(_e(r.get("author", "")))
        if country and country.lower() != "all":
            bits.append(_e(country.upper()))
        bits.append(when)
        meta = " · ".join(bits) + reach_s
        # syndication count badge (Phase 2 turns this into a popup)
        syn = [u for u in (r.get("syndicated") or []) if u]
        syn_badge = (f'<button class="dm-syn" data-urls="{_e("|".join(syn))}" type="button">'
                     f'&#128279; {len(syn)} syndicated</button>' if syn else "")
        search = _e(" ".join(str(r.get(k, "")) for k in
                    ("title", "publisher", "author", "country", "summary",
                     "theme", "emotions", "signals")).lower())
        title = _e(r.get("title", "")) or "(untitled)"
        href = _safe_http_url(r.get("url", ""))
        title_html = (f'<a class="dm-title" href="{href}" target="_blank" '
                      f'rel="noopener noreferrer">{title}</a>' if href
                      else f'<span class="dm-title">{title}</span>')
        return (
            f'<article class="dm-art" draggable="true" data-id="{_e(r.get("id",""))}" '
            f'data-date="{_e(r.get("date",""))}" data-text="{search}">'
            f'<span class="dm-handle" title="drag to move section">&#8942;&#8942;</span>'
            f'<span class="dm-dot {dot}"></span>'
            f'<div style="flex:1"><div class="dm-title-row">{title_html}{badge}{sent_pill}</div>'
            f'<div class="dm-meta">{meta}</div>'
            f'<div class="dm-snip">{_e(r.get("summary", ""))}</div>'
            f'<div class="dm-tags">{tags}{syn_badge}</div></div></article>')

    sections = ""
    for sec, n in counts.most_common():
        cards = "".join(_card(r) for r in by_sec[sec])
        sections += (
            f'<section class="dm-sec" data-sec="{_e(sec)}">'
            f'<div class="dm-sec-head"><span class="dm-handle">&#8942;&#8942;</span>{_e(sec)}'
            f'<span class="c" data-count>{n}</span></div>'
            f'<div class="dm-drop" data-sec="{_e(sec)}">{cards}</div></section>')

    cfg = _json.dumps({"session_id": (chat or {}).get("session_id", ""),
                       "token": (chat or {}).get("token", ""),
                       "api_base": (chat or {}).get("api_base", "")})
    return (
        _DAILY_CSS
        + f'<div class="dm-wrap"><aside class="dm-side">{side}</aside>'
        + f'<main class="dm-main" id="dm-main">{sections}</main></div>'
        + f"<script>{_DAILY_JS}\n__dailyInit({cfg});</script>")


_DAILY_JS = r"""
function __dailyInit(CFG){
  var API=(CFG.api_base||window.location.origin).replace(/\/$/,'');
  var main=document.getElementById('dm-main'), secs=document.getElementById('dm-secs');
  var dragEl=null;
  // section sidebar filter
  secs.addEventListener('click',function(e){
    var li=e.target.closest('li'); if(!li)return;
    secs.querySelectorAll('li').forEach(function(x){x.classList.remove('on');});
    li.classList.add('on'); var sec=li.getAttribute('data-sec');
    main.querySelectorAll('.dm-sec').forEach(function(s){
      s.style.display=(sec==='__all'||s.getAttribute('data-sec')===sec)?'':'none';});
  });
  // date + text filter
  function flt(){
    var f=(document.getElementById('dm-from')||{}).value||'';
    var t=(document.getElementById('dm-to')||{}).value||'';
    var q=((document.getElementById('dm-q')||{}).value||'').toLowerCase();
    main.querySelectorAll('.dm-sec').forEach(function(s){
      var vis=0;
      s.querySelectorAll('.dm-art').forEach(function(a){
        var d=a.getAttribute('data-date')||'',x=a.getAttribute('data-text')||'',ok=true;
        if(f&&d<f)ok=false; if(t&&d>t)ok=false; if(q&&x.indexOf(q)<0)ok=false;
        a.style.display=ok?'':'none'; if(ok)vis++;});
    });
  }
  ['dm-from','dm-to','dm-q'].forEach(function(id){var el=document.getElementById(id);
    if(el)el.addEventListener('input',flt);});
  // drag to move between sections (persists via the review API)
  main.addEventListener('dragstart',function(e){
    var a=e.target.closest('.dm-art'); if(!a)return; dragEl=a; a.classList.add('drag');
    e.dataTransfer.effectAllowed='move';});
  main.addEventListener('dragend',function(){ if(dragEl)dragEl.classList.remove('drag'); dragEl=null;
    main.querySelectorAll('.dm-drop.over').forEach(function(d){d.classList.remove('over');});});
  main.addEventListener('dragover',function(e){var d=e.target.closest('.dm-drop'); if(d){e.preventDefault();
    d.classList.add('over');}});
  main.addEventListener('dragleave',function(e){var d=e.target.closest('.dm-drop');
    if(d)d.classList.remove('over');});
  main.addEventListener('drop',function(e){
    var drop=e.target.closest('.dm-drop'); if(!drop||!dragEl)return; e.preventDefault();
    drop.classList.remove('over');
    var from=dragEl.closest('.dm-sec'), toSec=drop.getAttribute('data-sec');
    if(from&&from.getAttribute('data-sec')===toSec)return;
    drop.appendChild(dragEl);
    recount();
    var id=dragEl.getAttribute('data-id'), note=document.createElement('span');
    note.className='dm-save'; note.textContent='saving…';
    (dragEl.querySelector('.dm-title-row')||dragEl.querySelector('.dm-title')).appendChild(note);
    fetch(API+'/chat/'+CFG.session_id+'/section',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({article_id:id,section:toSec,token:CFG.token})})
     .then(function(r){note.textContent=r.ok?'moved ✓':'save failed';
        setTimeout(function(){note.remove();},1800);})
     .catch(function(){note.textContent='offline';setTimeout(function(){note.remove();},1800);});
  });
  function recount(){
    main.querySelectorAll('.dm-sec').forEach(function(s){
      var n=s.querySelectorAll('.dm-art').length; var c=s.querySelector('[data-count]');
      if(c)c.textContent=n;});
  }
}
"""


_CHAT_JS = r"""
const CHAT=__CHAT_JSON__;
const CHAT_API=(CHAT.api_base||window.location.origin).replace(/\/$/,'');
function chatToggle(){document.getElementById('chatPanel').classList.toggle('open');}
function chatEsc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function chatTime(ts){
  var d=new Date(ts), diff=(Date.now()-d.getTime())/1000; if(diff<0)diff=0;
  if(diff<86400){
    if(diff<60)return Math.floor(diff)+'s ago';
    if(diff<3600)return Math.floor(diff/60)+'m ago';
    return Math.floor(diff/3600)+'h ago';
  }
  return d.toLocaleString();
}
function chatMd(src){
  src=(src||''); var charts=[];
  src=src.replace(/```echarts\s*([\s\S]*?)```/g,function(_,j){var i=charts.length;charts.push(j.trim());return 'C'+i+'';});
  var s=chatEsc(src);
  // markdown tables
  s=s.replace(/(?:^|\n)((?:[ \t]*\|.*\|[ \t]*(?:\n|$))+)/g,function(m,block){
    var rows=block.trim().split('\n').filter(function(r){return r.indexOf('|')>=0 && !/^[\s|:-]+$/.test(r);});
    if(rows.length<1)return m;
    var html='<table>';
    rows.forEach(function(r,idx){
      var cells=r.replace(/^[ \t]*\|/,'').replace(/\|[ \t]*$/,'').split('|').map(function(c){return c.trim();});
      var tag=idx===0?'th':'td';
      html+='<tr>'+cells.map(function(c){return '<'+tag+'>'+c+'</'+tag+'>';}).join('')+'</tr>';
    });
    return '\n'+html+'</table>\n';
  });
  s=s.replace(/^\s*###\s+(.*)$/gm,'<h5>$1</h5>').replace(/^\s*##\s+(.*)$/gm,'<h4>$1</h4>').replace(/^\s*#\s+(.*)$/gm,'<h4>$1</h4>');
  s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');
  s=s.replace(/(?:^|\n)((?:[ \t]*[-*]\s+.*(?:\n|$))+)/g,function(m,b){
    var items=b.trim().split('\n').map(function(l){return l.replace(/^[ \t]*[-*]\s+/,'');});
    return '\n<ul>'+items.map(function(i){return '<li>'+i+'</li>';}).join('')+'</ul>\n';});
  s=s.replace(/(?:^|\n)((?:[ \t]*\d+\.\s+.*(?:\n|$))+)/g,function(m,b){
    var items=b.trim().split('\n').map(function(l){return l.replace(/^[ \t]*\d+\.\s+/,'');});
    return '\n<ol>'+items.map(function(i){return '<li>'+i+'</li>';}).join('')+'</ol>\n';});
  s=s.replace(/\n/g,'<br>').replace(/(<br>\s*){2,}/g,'<br>');
  charts.forEach(function(j,i){s=s.replace('C'+i+'','<div class="chat-echarts" data-opt="'+chatEsc(j).replace(/"/g,'&quot;')+'"></div>');});
  return s;
}
// Neutralize any HTML/JS-capable fields in LLM-produced ECharts JSON (defense-in-depth):
// drop every formatter/rich, and force richText tooltips so no field is rendered as HTML.
function chatSafeOpt(o){
  if(Array.isArray(o)){o.forEach(chatSafeOpt);return o;}
  if(o&&typeof o==='object'){
    if(o.tooltip&&typeof o.tooltip==='object'){o.tooltip.renderMode='richText';}
    for(var k in o){
      if(k==='formatter'||k==='rich'){delete o[k];continue;}
      if(k==='renderMode'){o[k]='richText';continue;}
      chatSafeOpt(o[k]);
    }
  }
  return o;
}
function chatInitCharts(el){
  if(!window.echarts)return;
  el.querySelectorAll('.chat-echarts').forEach(function(d){
    try{var raw=d.getAttribute('data-opt').replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
      var opt=chatSafeOpt(JSON.parse(raw)); d.style.height='240px'; var ch=echarts.init(d);
      opt.backgroundColor='transparent'; ch.setOption(opt);
      window.addEventListener('resize',function(){ch.resize();});}catch(e){}
  });
}
function chatSend(){
  var inp=document.getElementById('chatIn'),log=document.getElementById('chatLog');
  var q=(inp.value||'').trim(); if(!q)return; inp.value='';
  log.insertAdjacentHTML('beforeend','<div class="chat-msg user">'+chatEsc(q)+'</div>');
  var t=document.createElement('div'); t.className='chat-msg bot'; t.textContent='…'; log.appendChild(t); log.scrollTop=log.scrollHeight;
  fetch(CHAT_API+'/chat/'+CHAT.session_id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,token:CHAT.token})})
   .then(r=>r.json().then(d=>({ok:r.ok,d})))
   .then(o=>{ if(!o.ok){t.textContent=(o.d&&o.d.detail)||'That request failed.';}
     else { t.innerHTML=chatMd(o.d.answer)+'<span class="ts">'+chatTime(o.d.timestamp)+'</span>'; chatInitCharts(t); }
     log.scrollTop=log.scrollHeight; })
   .catch(()=>{t.textContent='Network error — is the agent reachable?';});
}
document.addEventListener('DOMContentLoaded',function(){var i=document.getElementById('chatIn'); if(i)i.addEventListener('keydown',function(e){if(e.key==='Enter')chatSend();});});
"""


def render(schema: dict) -> str:
    settings = get_settings()
    embed = settings.dashboard_asset_mode == "embed"
    theme = schema.get("theme", "dark")
    dark = theme != "light"
    charts = schema.get("charts", [])
    has_geo = any(c["engine"] == "d3geo" for c in charts)
    tabs = schema.get("tabs", [])
    default_tab = tabs[0]["id"] if tabs else "overview"

    # banner — only http(s) video URLs pass
    banner = schema.get("banner") or {}
    video_src = _safe_http_url(banner.get("video_url", "")) if banner.get("video_url") else ""
    video = (f'<video autoplay muted loop playsinline src="{video_src}"></video>'
             if video_src else "")
    # editorial display + clean body fonts; skipped in true-offline embed mode
    fonts_link = ("" if embed else
                  '<link rel="preconnect" href="https://fonts.googleapis.com">'
                  '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                  '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
                  'family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&'
                  'family=Hanken+Grotesk:wght@400;500;600;700&display=swap">')
    logos = schema.get("logos") or {}
    logo_strip = ""
    if logos:
        parts = []
        if logos.get("brand"):
            parts.append(_logo_html(logos["brand"]))
        parts += [_logo_html(c, small=True) for c in logos.get("competitors", [])]
        logo_strip = f'<div class="logos">{"".join(parts)}</div>'

    # top-level navigation (the agreed design): Home · Daily Monitoring · Media Measurement.
    # All analytics live as sub-tabs INSIDE Media Measurement.
    mm_tabs = tabs
    nav_items = [{"id": "home", "label": "Home"},
                 {"id": "daily", "label": "Daily Monitoring"},
                 {"id": "media", "label": "Media Measurement"}]
    default_tab = "home"
    nav = "".join(
        f'<button class="tab{" on" if t["id"] == default_tab else ""}" data-t="{_e(t["id"])}">'
        f'{_e(t["label"])}</button>' for t in nav_items
    )

    def _grid_for(tab_id: str, prepend: str = "") -> str:
        cards = prepend + "".join(
            f'<div class="card"><h3>{_e(c["title"])}</h3>'
            + (f'<p class="chart-insight">{_e(c["insight"])}</p>' if c.get("insight") else "")
            + f'<div class="chart" id="chart-{_e(c["id"])}"></div></div>'
            for c in charts if c["tab"] == tab_id)
        return f'<div class="chart-grid">{cards or "<p>No charts for this view yet.</p>"}</div>'

    # executive summary + recommendations fold into the Overview sub-tab (no separate tab)
    s = schema.get("summaries", {})
    summary_card = ""
    if s.get("executive") or s.get("recommendations"):
        summary_card = (
            '<div class="card"><h3>Executive Summary</h3><ul>'
            + "".join(f"<li>{_e(b)}</li>" for b in s.get("executive", []) or ["—"])
            + '</ul><h3 style="margin-top:14px">Recommendations</h3><ul>'
            + "".join(f"<li>{_e(b)}</li>" for b in s.get("recommendations", []) or ["—"])
            + "</ul></div>")

    sub_default = mm_tabs[0]["id"] if mm_tabs else ""
    subnav = "".join(
        f'<button class="subtab{" on" if t["id"] == sub_default else ""}" '
        f'data-sub="{_e(t["id"])}">{_e(t["label"])}</button>' for t in mm_tabs)
    subpages = "".join(
        f'<section class="subpage{" on" if t["id"] == sub_default else ""}" '
        f'id="sub-{_e(t["id"])}">'
        f'{_grid_for(t["id"], summary_card if t["id"] == "mm_overview" else "")}</section>'
        for t in mm_tabs)
    media_body = (
        f'<nav class="subtabs">{subnav}</nav>{subpages}'
        '<script>document.querySelectorAll(".subtab").forEach(function(b){'
        'b.addEventListener("click",function(){var s=b.getAttribute("data-sub");'
        'document.querySelectorAll(".subtab").forEach(function(x){x.classList.toggle("on",x===b);});'
        'document.querySelectorAll(".subpage").forEach(function(p){'
        'p.classList.toggle("on",p.id==="sub-"+s);});'
        'window.dispatchEvent(new Event("resize"));});});</script>')

    pages = []
    for t in nav_items:
        if t["id"] == "home":
            body = _home_cards([{"id": "daily", "label": "Daily Monitoring"},
                                {"id": "media", "label": "Media Measurement"}])
        elif t["id"] == "daily":
            body = _daily_view(schema.get("daily", []), schema.get("chat", {}))
        else:
            body = media_body
        on = " on" if t["id"] == default_tab else ""
        pages.append(f'<section class="page{on}" id="page-{_e(t["id"])}">{body}</section>')

    kpis = "".join(
        f'<div class="kpi-card"><small>{_e(k["label"])}</small><b>{_e(k["value"])}</b>'
        f'<div class="sub">{_e(k.get("sub", ""))}</div></div>'
        for k in schema.get("kpis", [])
    )

    echarts_specs = {c["id"]: c["option"] for c in charts if c["engine"] == "echarts"}
    geo_specs = {c["id"]: c["geo"] for c in charts if c["engine"] == "d3geo"}
    # '<' escaped so untrusted strings inside chart data can't close the script tag
    chart_data = json.dumps({"echarts": echarts_specs, "geo": geo_specs},
                            ensure_ascii=False).replace("<", "\\u003c")

    init_js = f"""
const DATA = {chart_data};
const DARK = {str(dark).lower()};
for (const [id, option] of Object.entries(DATA.echarts)) {{
  const el = document.getElementById('chart-'+id);
  if (!el) continue;
  const chart = echarts.init(el, DARK ? 'dark' : null);
  option.backgroundColor = 'transparent';
  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}}
for (const [id, spec] of Object.entries(DATA.geo)) {{
  const el = document.getElementById('chart-'+id);
  if (el && window.d3 && window.__WORLD__) renderGeo(el, spec.counts, DARK);
}}
"""

    geo_scripts = ""
    if has_geo:
        geo_scripts = (
            _script("d3.min.js", embed) + _script("topojson-client.min.js", embed)
            + f"<script>window.__WORLD__ = {_vendor('world-110m.json')};</script>"
            + f"<script>{GEO_JS}</script>"
        )

    chat = schema.get("chat") or {}
    chat_widget = ""
    if chat.get("session_id") and chat.get("token"):
        chat_json = json.dumps(chat).replace("<", "\\u003c")
        window_days = chat.get("window_days", 3)
        chat_widget = (
            '<button class="chat-fab" onclick="chatToggle()">💬 Ask the data agent</button>'
            '<div class="chat-panel" id="chatPanel">'
            '<div class="chat-head">Ask the data agent<button class="chat-close" '
            'onclick="chatToggle()" aria-label="close">×</button></div>'
            '<div class="chat-log" id="chatLog"><div class="chat-msg bot">Ask anything about '
            'this coverage — I answer from the analyzed articles.</div></div>'
            '<div class="chat-input"><input id="chatIn" placeholder="e.g. top negative story '
            'this week?" autocomplete="off"><button onclick="chatSend()">Send</button></div>'
            f'<div class="chat-note">Grounded in this report · available for {window_days} '
            'days</div></div>'
            "<script>" + _CHAT_JS.replace("__CHAT_JSON__", chat_json) + "</script>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(schema.get("title", "Dashboard"))}</title>
{fonts_link}
<style>{THEMES.get(theme, THEMES["dark"])}{BASE_CSS}</style>
</head>
<body>
<header class="banner">
  {video}
  {logo_strip}
  <div class="overlay">
    <div class="eyebrow">PR Intelligence</div>
    <h1>{_e(schema.get("title", "Executive Dashboard"))}</h1>
    <div class="meta">Generated {_e(schema.get("generated_at", "")[:16].replace("T", " "))}
      · PR Intelligence Agent{" · template reused" if schema.get("template_reused") else ""}</div>
  </div>
</header>
<div class="wrap">
  <nav class="tabs">{nav}</nav>
  <div class="kpi-container">{kpis}</div>
  {"".join(pages)}
  <footer>Self-contained dashboard · charts by Apache ECharts{" + D3" if has_geo else ""} ·
    video: Pexels</footer>
</div>
{chat_widget}
{_script("echarts.min.js", embed)}
{geo_scripts}
<script>{TAB_JS}</script>
<script>{init_js}</script>
</body>
</html>"""
