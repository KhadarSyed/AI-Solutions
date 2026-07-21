from app.analytics.chart_builders import impact, measurement, monitoring, narrative, reputation
from app.analytics.scoring import gauge_rating, pr_impact

ARTICLES = [
    {"id": "A0", "title": "Trane wins big", "publisher_name": "Reuters",
     "publisher_domain": "reuters.com", "published_date": "2026-07-14", "url": "u0",
     "xai_sentiment": "POS", "xai_theme": "Product launch", "xai_section": "Brand News",
     "priority_watch": False, "monthly_reach": 42_000_000, "reach_weight": 5, "tier1": True,
     "spokesperson_level": "c_suite", "author": "J. Doe",
     "entities": {"brand_of_interest": ["Trane"], "competitors": ["Carrier"]}},
    {"id": "A1", "title": "Carrier counters", "publisher_name": "ACHR News",
     "publisher_domain": "achrnews.com", "published_date": "2026-07-15", "url": "u1",
     "xai_sentiment": "NEG", "xai_theme": "Competitive pressure", "xai_section": "Competitors News",
     "priority_watch": True, "monthly_reach": 220_000, "reach_weight": 2, "tier1": False,
     "spokesperson_level": "none",
     "entities": {"brand_of_interest": [], "competitors": ["Carrier"]}},
    {"id": "A2", "title": "HVAC market grows", "publisher_name": "Cooling Post",
     "publisher_domain": "coolingpost.com", "published_date": "2026-07-15", "url": "u2",
     "xai_sentiment": "NEU", "xai_theme": "Market growth", "xai_section": "Industry News",
     "priority_watch": False, "monthly_reach": 95_000, "reach_weight": 1, "tier1": False,
     "spokesperson_level": "mid_level",
     "entities": {"brand_of_interest": ["Trane"], "competitors": []}},
]
SECTIONS = ["Brand News", "Competitors News", "Industry News"]


def test_pr_impact_formula():
    # reach 5 × c_suite 5 × POS 1.2 = 30
    assert pr_impact(ARTICLES[0]) == 30.0
    # reach 2 × none 1 × NEG -1.5 = -3
    assert pr_impact(ARTICLES[1]) == -3.0


def test_gauge_bands():
    assert gauge_rating(-1) == "Very Poor"
    assert gauge_rating(10) == "Poor"
    assert gauge_rating(20) == "Good"
    assert gauge_rating(40) == "Very Good"
    assert gauge_rating(50) == "Excellent"


def test_monitoring_orders_priority_then_reach():
    board = monitoring.build(ARTICLES, SECTIONS)
    assert board["kpis"] == {"total": 3, "priority_watch": 1}
    comp = next(s for s in board["sections"] if s["section"] == "Competitors News")
    assert comp["articles"][0]["priority_watch"] is True


def test_measurement_kpis():
    board = measurement.build(ARTICLES)
    assert board["kpis"]["total"] == 3
    assert board["kpis"]["positive_pct"] == 33.3
    assert board["datewise_coverage"][0]["date"] == "2026-07-14"
    assert board["top_publications"][0]["count"] >= 1


def test_impact_share_of_voice_and_matrix():
    board = impact.build(ARTICLES, "Trane", ["Carrier", "Daikin"])
    sov = board["share_of_voice"]
    # single primary attribution via the tagger's entities: A0 + A2 → Trane, A1 → Carrier
    assert sov["brand_mentions"] == 2
    carrier = next(c for c in sov["competitors"] if c["name"] == "Carrier")
    assert carrier["mentions"] == 1
    matrix = {m["competitor"]: m for m in board["competitive_matrix"]}
    assert matrix["Carrier"]["NEG"] == 1
    assert "Daikin" not in matrix


def test_narrative_threads_and_reputation_pillars():
    threads = narrative.build(ARTICLES)["threads"]
    assert threads[0]["count"] >= 1
    rep = reputation.build(ARTICLES)
    assert 0 <= rep["composite"] <= 100
    assert set(rep["radar"]) == {
        "trust", "advocacy", "value_perception", "social_responsibility",
        "brand_strength", "risk",
    }
    assert rep["heatmap"]["Brand News"]["POS"] == 1
