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


def _daily_view(rows: list[dict]) -> str:
    from collections import defaultdict

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[str(r.get("date", ""))[:10] or "Undated"].append(r)
    if not by_day:
        return '<div class="card"><h3>Daily Monitoring</h3><p>No dated coverage yet.</p></div>'
    out = []
    for day in sorted(by_day, reverse=True):
        items = by_day[day]
        rows_html = ""
        for r in items:
            time = _e(r.get("time", ""))
            theme = _e(r.get("theme", ""))
            emotions = _e(r.get("emotions", ""))
            signals = _e(r.get("signals", ""))
            tags = " · ".join(x for x in [theme, emotions, signals] if x)
            search = _e(" ".join(str(r.get(k, "")) for k in
                                 ("title", "publisher", "theme", "emotions", "signals",
                                  "section", "time")).lower())
            time_html = (f'<span style="margin-left:8px;font-size:11px;color:var(--muted);'
                         f'font-weight:600;">{time}</span>' if time else "")
            rows_html += (
                f'<div class="daily-row" data-date="{_e(day)}" data-time="{time}" '
                f'data-text="{search}">'
                f'<span class="sent sent-{_e((r.get("sentiment") or "NEU").lower()[:3])}"></span>'
                f'<div class="dr-main"><div class="dr-title">{_e(r.get("title", ""))}'
                f'{time_html}</div>'
                f'<div class="dr-meta">{_e(r.get("publisher", "")) or "—"} · '
                f'{_e(r.get("section", "")) or "—"}'
                f'{(" · " + tags) if tags else ""}</div></div></div>')
        out.append(f'<div class="daily-day" data-date="{_e(day)}">'
                   f'<div class="daily-date">{_e(day)}'
                   f'<span class="daily-count">{len(items)} article'
                   f'{"s" if len(items) != 1 else ""}</span></div>{rows_html}</div>')
    return f'<div class="daily">{_DAILY_FILTER}{"".join(out)}</div>{_DAILY_FILTER_JS}'


_DAILY_FILTER = (
    '<div class="card" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">'
    '<label style="font-size:12px;">From date<br><input type="date" id="dm-from" '
    'style="padding:6px;border:1px solid var(--line);border-radius:8px;"></label>'
    '<label style="font-size:12px;">To date<br><input type="date" id="dm-to" '
    'style="padding:6px;border:1px solid var(--line);border-radius:8px;"></label>'
    '<label style="font-size:12px;flex:1;min-width:180px;">Search (title, publisher, theme, '
    'time…)<br><input type="text" id="dm-q" placeholder="type to filter…" '
    'style="width:100%;padding:6px;border:1px solid var(--line);border-radius:8px;"></label>'
    '</div>'
)

_DAILY_FILTER_JS = """<script>
(function(){
  var f=document.getElementById('dm-from'),t=document.getElementById('dm-to'),
      q=document.getElementById('dm-q');
  if(!f||!t||!q)return;
  function apply(){
    var from=f.value,to=t.value,s=(q.value||'').toLowerCase();
    document.querySelectorAll('.daily-day').forEach(function(day){
      var vis=0;
      day.querySelectorAll('.daily-row').forEach(function(row){
        var d=row.getAttribute('data-date')||'',txt=row.getAttribute('data-text')||'',ok=true;
        if(from&&(d<from||d==='Undated'))ok=false;
        if(to&&(d>to||d==='Undated'))ok=false;
        if(s&&txt.indexOf(s)<0)ok=false;
        row.style.display=ok?'':'none'; if(ok)vis++;
      });
      day.style.display=vis?'':'none';
    });
  }
  [f,t,q].forEach(function(el){el.addEventListener('input',apply);});
})();
</script>"""


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

    # top-level views: Home landing + Daily Monitoring in front of the analytics tabs
    nav_items = ([{"id": "home", "label": "Home"},
                  {"id": "daily", "label": "Daily Monitoring"}] + tabs)
    default_tab = "home"
    nav = "".join(
        f'<button class="tab{" on" if t["id"] == default_tab else ""}" data-t="{_e(t["id"])}">'
        f'{_e(t["label"])}</button>' for t in nav_items
    )
    pages = []
    for t in nav_items:
        if t["id"] == "home":
            body = _home_cards(tabs)
        elif t["id"] == "daily":
            body = _daily_view(schema.get("daily", []))
        elif t["id"] == "insights":
            s = schema.get("summaries", {})
            body = (
                '<div class="summary-container">'
                '<div class="card"><h3>Executive Summary</h3><ul>'
                + "".join(f"<li>{_e(b)}</li>" for b in s.get("executive", [])
                          or ["No summary generated."])
                + '</ul></div><div class="card"><h3>Recommendations</h3><ul>'
                + "".join(f"<li>{_e(b)}</li>" for b in s.get("recommendations", []) or ["—"])
                + "</ul></div></div>"
            )
        else:
            cards = "".join(
                f'<div class="card"><h3>{_e(c["title"])}</h3>'
                + (f'<p class="chart-insight">{_e(c["insight"])}</p>'
                   if c.get("insight") else "")
                + f'<div class="chart" id="chart-{_e(c["id"])}"></div></div>'
                for c in charts if c["tab"] == t["id"]
            )
            body = f'<div class="chart-grid">{cards}</div>'
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
        chat_widget = f"""
<button class="chat-fab" onclick="chatToggle()">💬 Ask the data agent</button>
<div class="chat-panel" id="chatPanel">
  <div class="chat-head">Ask the data agent<button class="chat-close" onclick="chatToggle()" aria-label="close">×</button></div>
  <div class="chat-log" id="chatLog"><div class="chat-msg bot">Ask anything about this coverage — I answer from the analyzed articles.</div></div>
  <div class="chat-input"><input id="chatIn" placeholder="e.g. top negative story this week?" autocomplete="off"><button onclick="chatSend()">Send</button></div>
  <div class="chat-note">Grounded in this report · available for {chat.get('window_days', 3)} days</div>
</div>
<script>
const CHAT={chat_json};
// empty api_base → same-origin (dashboard served from the API), else the configured public base
const CHAT_API=(CHAT.api_base||window.location.origin).replace(/\\/$/,'');
function chatToggle(){{document.getElementById('chatPanel').classList.toggle('open');}}
function chatEsc(s){{return (s||'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));}}
function chatSend(){{
  const inp=document.getElementById('chatIn'),log=document.getElementById('chatLog');
  const q=(inp.value||'').trim(); if(!q)return; inp.value='';
  log.insertAdjacentHTML('beforeend','<div class="chat-msg user">'+chatEsc(q)+'</div>');
  const t=document.createElement('div'); t.className='chat-msg bot'; t.textContent='…'; log.appendChild(t); log.scrollTop=log.scrollHeight;
  fetch(CHAT_API+'/chat/'+CHAT.session_id,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:q,token:CHAT.token}})}})
   .then(r=>r.json().then(d=>({{ok:r.ok,d}})))
   .then(o=>{{ if(!o.ok){{t.textContent=(o.d&&o.d.detail)||'That request failed.';}} else {{ t.innerHTML=chatEsc(o.d.answer).replace(/\\n/g,'<br>')+'<span class="ts">'+new Date(o.d.timestamp).toLocaleString()+'</span>'; }} log.scrollTop=log.scrollHeight; }})
   .catch(()=>{{t.textContent='Network error — is the agent reachable?';}});
}}
document.getElementById('chatIn').addEventListener('keydown',e=>{{if(e.key==='Enter')chatSend();}});
</script>
"""

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
