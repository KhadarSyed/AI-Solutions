"""Auto chart selection — data shape → chart type, biased by learned preferences.

Shape rules (playbook of the Dashboard Agent spec):
  date + metric → line · category + metric → bar · share/proportion → donut ·
  numeric correlation → scatter · country counts → D3 choropleth ·
  pillar scores → radar · section×sentiment → heatmap
Preference/feedback memories can override (e.g. "bar instead of donut"), and hot
competitor edges from the graph promote competitive charts."""

import re
from dataclasses import dataclass, field

PALETTE = ["#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#f87171", "#38bdf8"]
SENT_COLORS = {"POS": "#34d399", "NEU": "#94a3b8", "NEG": "#f87171"}


@dataclass
class SelectionPrefs:
    donut_to_bar: bool = False
    hidden_kinds: set = field(default_factory=set)
    emphasize_competitive: bool = False
    preferred_types: dict = field(default_factory=dict)   # chart_id → echarts type

    @classmethod
    def from_memories(cls, memory_texts: list[str], hot_competitors: list[str]) -> "SelectionPrefs":
        prefs = cls(emphasize_competitive=bool(hot_competitors))
        for text in memory_texts:
            t = text.lower()
            if re.search(r"bar (chart )?(instead of|over|not) (a )?(donut|pie)", t) or \
               re.search(r"no (donut|pie)", t):
                prefs.donut_to_bar = True
            if "no heatmap" in t or "hide heatmap" in t:
                prefs.hidden_kinds.add("heatmap")
            if "no map" in t or "hide map" in t or "no geo" in t:
                prefs.hidden_kinds.add("geo")
            if re.search(r"(focus|emphasi[sz]e).{0,25}competitor", t):
                prefs.emphasize_competitive = True
        return prefs


def _donut_or_bar(prefs: SelectionPrefs, chart_id: str, title: str, pairs: list[tuple],
                  colors: list[str] | None = None) -> dict:
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    if prefs.donut_to_bar or prefs.preferred_types.get(chart_id) == "bar":
        option = {
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": values,
                        "itemStyle": {"borderRadius": [6, 6, 0, 0]}}],
            "color": colors or PALETTE,
            "tooltip": {"trigger": "axis"},
        }
    else:
        option = {
            "series": [{"type": "pie", "radius": ["45%", "72%"],
                        "label": {"formatter": "{b}: {d}%"},
                        "data": [{"name": n, "value": v} for n, v in pairs]}],
            "color": colors or PALETTE,
            "tooltip": {"trigger": "item"},
        }
    return {"id": chart_id, "engine": "echarts", "title": title, "option": option}


def select_charts(boards: dict, prefs: SelectionPrefs) -> list[dict]:
    """boards = the five-dashboard payload from dashboard_service. Returns chart
    specs [{id, engine: echarts|d3geo, title, option|geo, tab}]."""
    charts: list[dict] = []
    m = boards.get("media_measurement", {})
    p = boards.get("pr_impact", {})
    r = boards.get("reputation_index", {})
    n = boards.get("narrative_intelligence", {})

    # date + count/reach → line (Overview)
    dw = m.get("datewise_coverage", [])
    if dw:
        charts.append({
            "id": "coverage_trend", "engine": "echarts", "tab": "overview",
            "title": "Coverage Trend",
            "option": {
                "xAxis": {"type": "category", "data": [d["date"] for d in dw]},
                "yAxis": [{"type": "value", "name": "articles"},
                          {"type": "value", "name": "reach"}],
                "series": [
                    {"type": "line", "smooth": True, "name": "Articles",
                     "areaStyle": {"opacity": 0.15}, "data": [d["count"] for d in dw]},
                    {"type": "line", "smooth": True, "name": "Reach", "yAxisIndex": 1,
                     "data": [d["reach"] for d in dw]},
                ],
                "color": PALETTE, "tooltip": {"trigger": "axis"},
                "legend": {"textStyle": {"color": "inherit"}},
            },
        })

    split = m.get("sentiment_split", {})
    if split:
        c = _donut_or_bar(prefs, "sentiment_split", "Sentiment Split",
                          [(k, v) for k, v in split.items()],
                          [SENT_COLORS.get(k, "#94a3b8") for k in split])
        c["tab"] = "overview"
        charts.append(c)

    themes = m.get("top_themes", [])
    if themes:
        charts.append({
            "id": "top_themes", "engine": "echarts", "tab": "coverage",
            "title": "Top Themes",
            "option": {
                "yAxis": {"type": "category",
                          "data": [t["theme"] for t in reversed(themes)]},
                "xAxis": {"type": "value"},
                "series": [{"type": "bar",
                            "data": [t["count"] for t in reversed(themes)],
                            "itemStyle": {"borderRadius": [0, 6, 6, 0]}}],
                "color": [PALETTE[4]], "tooltip": {"trigger": "axis"},
                "grid": {"containLabel": True},
            },
        })

    pubs = m.get("top_publications", [])
    if pubs:
        charts.append({
            "id": "top_publications", "engine": "echarts", "tab": "coverage",
            "title": "Top Publications",
            "option": {
                "xAxis": {"type": "category", "data": [x["publisher"] for x in pubs[:8]],
                          "axisLabel": {"rotate": 30}},
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [x["count"] for x in pubs[:8]],
                            "itemStyle": {"borderRadius": [6, 6, 0, 0]}}],
                "color": [PALETTE[0]], "tooltip": {"trigger": "axis"},
                "grid": {"containLabel": True},
            },
        })

    sov = p.get("share_of_voice", {})
    if sov and (sov.get("brand_mentions") or sov.get("competitors")):
        pairs = [(sov.get("brand", "Brand"), sov.get("brand_mentions", 0))] + [
            (c["name"], c["mentions"]) for c in sov.get("competitors", [])
        ]
        c = _donut_or_bar(prefs, "share_of_voice", "Share of Voice", pairs)
        c["tab"] = "competitive"
        charts.append(c)

    matrix = p.get("competitive_matrix", [])
    if matrix:
        charts.append({
            "id": "competitive_matrix", "engine": "echarts", "tab": "competitive",
            "title": "Competitive Sentiment Matrix",
            "option": {
                "xAxis": {"type": "category", "data": [m_["competitor"] for m_ in matrix]},
                "yAxis": {"type": "value"},
                "legend": {"textStyle": {"color": "inherit"}},
                "series": [
                    {"type": "bar", "stack": "s", "name": s,
                     "data": [m_.get(s, 0) for m_ in matrix],
                     "itemStyle": {"color": SENT_COLORS[s]}}
                    for s in ("POS", "NEU", "NEG")
                ],
                "tooltip": {"trigger": "axis"},
            },
        })

    top_imp = p.get("top_impact_articles", [])
    if top_imp:
        charts.append({
            "id": "impact_ranking", "engine": "echarts", "tab": "competitive",
            "title": "PR Impact — Top Articles",
            "option": {
                "yAxis": {"type": "category",
                          "data": [(f'{a.get("publisher_name", "")} — {a.get("title", "")[:36]}'
                                    .strip(" —") or a.get("id", ""))
                                   for a in reversed(top_imp)]},
                "xAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [
                    {"value": a["impact"],
                     "itemStyle": {"color": SENT_COLORS.get(a.get("sentiment", "NEU"))}}
                    for a in reversed(top_imp)]}],
                "tooltip": {"trigger": "axis"}, "grid": {"containLabel": True},
            },
        })

    radar = r.get("radar", {})
    if radar:
        charts.append({
            "id": "reputation_radar", "engine": "echarts", "tab": "reputation",
            "title": f"Reputation Index — {r.get('composite', 0)}",
            "option": {
                "radar": {"indicator": [{"name": k.replace("_", " ").title(), "max": 100}
                                        for k in radar]},
                "series": [{"type": "radar",
                            "data": [{"value": list(radar.values()),
                                      "areaStyle": {"opacity": 0.25}}]}],
                "color": [PALETTE[0]],
            },
        })

    heat = r.get("heatmap", {})
    if heat and "heatmap" not in prefs.hidden_kinds:
        sections = list(heat.keys())
        sentiments = ["POS", "NEU", "NEG"]
        data = [[si, yi, heat[sec].get(s, 0)]
                for yi, sec in enumerate(sections)
                for si, s in enumerate(sentiments)]
        max_v = max((d[2] for d in data), default=1) or 1
        charts.append({
            "id": "section_heatmap", "engine": "echarts", "tab": "reputation",
            "title": "Sentiment × Section Heatmap",
            "option": {
                "xAxis": {"type": "category", "data": sentiments},
                "yAxis": {"type": "category", "data": sections},
                "visualMap": {"min": 0, "max": max_v, "show": False,
                              "inRange": {"color": ["#1e293b", "#2563eb", "#60a5fa"]}},
                "series": [{"type": "heatmap", "data": data,
                            "label": {"show": True}}],
                "tooltip": {},
                "grid": {"containLabel": True},
            },
        })

    threads = n.get("threads", [])
    if threads:
        charts.append({
            "id": "narrative_threads", "engine": "echarts", "tab": "narratives",
            "title": "Narrative Threads",
            "option": {
                "yAxis": {"type": "category",
                          "data": [t["theme"] for t in reversed(threads[:8])]},
                "xAxis": {"type": "value"},
                "series": [{"type": "bar",
                            "data": [t["count"] for t in reversed(threads[:8])],
                            "itemStyle": {"borderRadius": [0, 6, 6, 0]}}],
                "color": [PALETTE[3]], "tooltip": {"trigger": "axis"},
                "grid": {"containLabel": True},
            },
        })

    # correlation: reach vs impact → scatter
    if top_imp and pubs:
        pass  # reach isn't on top_imp rows; correlation scatter needs article-level join

    # country counts → D3 choropleth
    geo = boards.get("_geo_counts", {})
    if geo and "geo" not in prefs.hidden_kinds:
        charts.append({
            "id": "coverage_map", "engine": "d3geo", "tab": "overview",
            "title": "Coverage by Country", "geo": {"counts": geo},
        })

    if prefs.emphasize_competitive:
        charts.sort(key=lambda c: 0 if c["tab"] == "competitive" else 1)
    return [c for c in charts if c["id"] not in prefs.hidden_kinds]
