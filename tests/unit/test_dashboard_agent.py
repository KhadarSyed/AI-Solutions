from app.analytics.chart_selector import PALETTE, SelectionPrefs, select_charts
from app.llm_gateway.registry import provider_for
from app.services.html_renderer import render
from app.services.template_store import layout_of

BOARDS = {
    "media_measurement": {
        "kpis": {"total": 3, "positive_pct": 33.3, "negative_pct": 33.3,
                 "neutral_pct": 33.3, "total_reach": 42_315_000},
        "sentiment_split": {"POS": 1, "NEU": 1, "NEG": 1},
        "datewise_coverage": [{"date": "2026-07-14", "count": 1, "reach": 42_000_000},
                              {"date": "2026-07-15", "count": 2, "reach": 315_000}],
        "top_themes": [{"theme": "Product launch", "count": 2}],
        "top_publications": [{"publisher": "Reuters", "count": 1, "reach": 42_000_000}],
        "top_authors": [],
    },
    "pr_impact": {
        "gauge": {"daily_average": 13.5, "rating": "Poor", "total_impact": 27, "days": 2},
        "share_of_voice": {"brand": "Trane", "brand_mentions": 2, "brand_share": 0.5,
                           "competitors": [{"name": "Carrier", "mentions": 2, "share": 0.5}]},
        "competitive_matrix": [{"competitor": "Carrier", "POS": 0, "NEU": 1, "NEG": 1}],
        "outlet_tiers": {"tier1": 1, "w2": 1, "w1": 1},
        "top_impact_articles": [{"id": "A0", "title": "t", "impact": 30.0, "sentiment": "POS"}],
    },
    "reputation_index": {
        "composite": 55.2,
        "radar": {"trust": 60, "advocacy": 50, "value_perception": 30,
                  "social_responsibility": 10, "brand_strength": 50, "risk": 66},
        "waterfall": [], "heatmap": {"Brand News": {"POS": 1, "NEU": 0, "NEG": 0}},
    },
    "narrative_intelligence": {"threads": [{"theme": "Product launch", "count": 2}]},
    "_geo_counts": {"US": 2, "GB": 1},
}


def test_selector_produces_expected_chart_set():
    charts = select_charts(BOARDS, SelectionPrefs())
    ids = {c["id"] for c in charts}
    assert {"coverage_trend", "sentiment_split", "share_of_voice",
            "competitive_matrix", "reputation_radar", "coverage_map"} <= ids
    geo = next(c for c in charts if c["id"] == "coverage_map")
    assert geo["engine"] == "d3geo" and geo["geo"]["counts"]["US"] == 2


def test_feedback_flips_donut_to_bar_and_hides_geo():
    prefs = SelectionPrefs.from_memories(
        ["Dashboard feedback: use a bar chart instead of donut for share of voice",
         "no map please"], [],
    )
    assert prefs.donut_to_bar and "geo" in prefs.hidden_kinds
    charts = select_charts(BOARDS, prefs)
    sov = next(c for c in charts if c["id"] == "share_of_voice")
    assert sov["option"]["series"][0]["type"] == "bar"
    assert not any(c["id"] == "coverage_map" for c in charts)


def test_competitive_emphasis_reorders():
    prefs = SelectionPrefs.from_memories(["please focus on competitor movement"], [])
    charts = select_charts(BOARDS, prefs)
    assert charts[0]["tab"] == "competitive"


def _schema():
    charts = select_charts(BOARDS, SelectionPrefs())
    return {
        "title": "Trane — Media Intelligence", "theme": "dark",
        "generated_at": "2026-07-18T12:00:00+00:00", "template_reused": False,
        "tabs": [{"id": "overview", "label": "Overview"},
                 {"id": "competitive", "label": "Competitive"},
                 {"id": "insights", "label": "AI Insights"}],
        "kpis": [{"label": "Articles", "value": 3}],
        "charts": charts,
        "summaries": {"executive": ["Coverage rose."], "recommendations": ["Expand east."]},
        "logos": {"brand": {"name": "Trane", "kind": "monogram", "initials": "T",
                            "color": "#2563eb"}, "competitors": []},
        "banner": {"video_url": "https://videos.pexels.com/video-files/1/1-hd.mp4"},
    }


def test_renderer_emits_self_contained_html():
    html = render(_schema())
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Trane — Media Intelligence</title>" in html
    assert 'class="tab on" data-t="overview"' in html
    assert "echarts.init" in html and "var " not in html[:200]
    assert "<video autoplay muted loop" in html
    assert 'class="logo mono"' in html
    assert "kpi-card" in html and "min-height:400px" in html
    # embedded mode: the actual library source is inline, not a CDN tag
    assert "cdn.jsdelivr.net/npm/echarts" not in html
    assert len(html) > 500_000            # ~1MB echarts inlined
    assert "renderGeo" in html and "__WORLD__" in html


def test_light_theme_variant():
    schema = _schema() | {"theme": "light"}
    html = render(schema)
    assert "--bg:#F4F5F7" in html


def test_template_layout_extraction():
    layout = layout_of(_schema())
    assert layout["theme"] == "dark"
    assert layout["tab_order"] == ["overview", "competitive", "insights"]
    assert layout["chart_types"]["sentiment_split"] == "pie"
    assert layout["include_logos"] is True


def test_provider_override_per_stage(monkeypatch):
    from app.config import settings as settings_mod

    s = settings_mod.get_settings()
    monkeypatch.setattr(s, "llm_provider", "claude")
    monkeypatch.setattr(s, "llm_provider_overrides", {"tagging": "gpt"})
    assert provider_for("tagging") == "gpt"
    assert provider_for("dashboards") == "claude"
    assert provider_for(None) == "claude"


def test_palette_is_accessible_on_dark():
    assert all(c.startswith("#") and len(c) == 7 for c in PALETTE)


def test_renderer_escapes_untrusted_content():
    schema = _schema()
    schema["title"] = '<script>alert(1)</script>'
    schema["summaries"]["executive"] = ['<img src=x onerror=alert(2)>']
    schema["kpis"] = [{"label": "<b>bad</b>", "value": '"><svg onload=alert(3)>'}]
    schema["logos"]["brand"]["name"] = '"><script>alert(4)</script>'
    schema["banner"]["video_url"] = "javascript:alert(5)"
    html = render(schema)
    assert "<script>alert(1)" not in html             # raw tag never survives
    assert "<img src=x onerror" not in html           # summary bullet neutralized
    assert "<svg onload" not in html
    assert "javascript:alert(5)" not in html          # non-http scheme dropped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_renderer_rejects_non_image_logo_datauri():
    schema = _schema()
    schema["logos"]["brand"] = {"name": "Evil", "kind": "image",
                                "data_uri": "data:text/html,<script>alert(6)</script>"}
    html = render(schema)
    assert "data:text/html" not in html               # falls back to monogram
    assert 'class="logo mono"' in html
