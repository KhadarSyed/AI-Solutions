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
:root{--bg:#0f172a;--card:#1e293b;--card2:#273449;--ink:#f1f5f9;--ink2:#94a3b8;
--accent:#2563eb;--line:#334155;--good:#34d399;--bad:#f87171}
body{background:var(--bg);color:var(--ink)}
.kpi-card{background:linear-gradient(135deg,var(--accent),#1d4ed8);color:#fff}
""",
    "light": """
:root{--bg:#F4F5F7;--card:#FFFFFF;--card2:#F0F2F5;--ink:#161B22;--ink2:#57606C;
--accent:#12386E;--line:#E2E5EA;--good:#127A50;--bad:#B3261E}
body{background:var(--bg);color:var(--ink)}
.kpi-card{background:linear-gradient(135deg,var(--accent),#1E4E96);color:#fff}
""",
}

BASE_CSS = """
*{box-sizing:border-box;margin:0}
body{font-family:Inter,'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.5}
.banner{position:relative;height:210px;overflow:hidden;
  background:linear-gradient(120deg,var(--accent),#0e7490)}
.banner video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.5}
.banner .overlay{position:absolute;inset:0;display:flex;flex-direction:column;
  justify-content:flex-end;padding:26px 34px;
  background:linear-gradient(180deg,transparent,rgba(2,6,23,.72))}
.banner h1{font-size:30px;font-weight:800;letter-spacing:-.02em;color:#fff}
.banner .meta{color:#cbd5e1;font-size:12.5px;margin-top:4px}
.logos{display:flex;gap:10px;align-items:center;position:absolute;top:18px;right:26px}
.logo{width:44px;height:44px;border-radius:12px;background:#ffffffde;display:grid;
  place-items:center;overflow:hidden;box-shadow:0 4px 14px rgba(0,0,0,.35)}
.logo img{width:80%;height:80%;object-fit:contain}
.logo.mono{color:#fff;font-weight:800;font-size:15px}
.logo.small{width:34px;height:34px;border-radius:9px;font-size:12px}
.wrap{max-width:1280px;margin:0 auto;padding:22px 26px 70px}
.tabs{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}
.tab{padding:12px 24px;border-radius:8px;background:var(--card);color:var(--ink2);
  border:1px solid var(--line);cursor:pointer;font-weight:600;font-size:13.5px}
.tab.on{background:var(--accent);color:#fff;border-color:transparent}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.kpi-container{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:14px;margin-bottom:20px}
.kpi-card{border-radius:16px;padding:20px;box-shadow:0 4px 20px rgba(0,0,0,.3)}
.kpi-card small{display:block;font-size:11px;letter-spacing:.12em;text-transform:uppercase;
  opacity:.75;font-weight:700}
.kpi-card b{font-size:26px;font-variant-numeric:tabular-nums}
.kpi-card .sub{font-size:11.5px;opacity:.8}
.page{display:none}.page.on{display:block}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:16px}
.chart-insight{margin:2px 0 12px;font-size:12.5px;line-height:1.45;color:var(--muted,#64748b)}
.card{background:var(--card);border-radius:16px;padding:20px;
  box-shadow:0 4px 20px rgba(0,0,0,.3);border:1px solid var(--line)}
.card h3{font-size:14.5px;margin-bottom:10px}
.chart{min-height:400px;width:100%}
.summary-container .card{margin-bottom:14px}
.summary-container li{margin:8px 0 8px 18px;font-size:14px}
.summary-container h3{color:var(--accent)}
footer{margin-top:34px;color:var(--ink2);font-size:11.5px;text-align:center}
@media(max-width:640px){.chart-grid{grid-template-columns:1fr}.banner h1{font-size:22px}}
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
    logos = schema.get("logos") or {}
    logo_strip = ""
    if logos:
        parts = []
        if logos.get("brand"):
            parts.append(_logo_html(logos["brand"]))
        parts += [_logo_html(c, small=True) for c in logos.get("competitors", [])]
        logo_strip = f'<div class="logos">{"".join(parts)}</div>'

    # tab nav + pages (ids come from our fixed selector vocabulary; escape anyway)
    nav = "".join(
        f'<button class="tab{" on" if t["id"] == default_tab else ""}" data-t="{_e(t["id"])}">'
        f'{_e(t["label"])}</button>' for t in tabs
    )
    pages = []
    for t in tabs:
        if t["id"] == "insights":
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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(schema.get("title", "Dashboard"))}</title>
<style>{THEMES.get(theme, THEMES["dark"])}{BASE_CSS}</style>
</head>
<body>
<header class="banner">
  {video}
  {logo_strip}
  <div class="overlay">
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
{_script("echarts.min.js", embed)}
{geo_scripts}
<script>{TAB_JS}</script>
<script>{init_js}</script>
</body>
</html>"""
