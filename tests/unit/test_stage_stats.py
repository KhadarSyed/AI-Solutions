"""Deterministic stage aggregations feeding the staged updates."""
from app.analytics import stage_stats

ARTS = [
    {"query_group": "Brand News", "subject_brand": "BeOne", "published_date": "2026-07-10",
     "published_time": "09:00", "author": "Jane Roe", "country": "US",
     "publisher_name": "Reuters", "xai_sentiment": "POS", "theme_primary": "Launch"},
    {"query_group": "Brand News", "subject_brand": "BeOne", "published_date": "2026-07-10",
     "author": "", "country": "all", "publisher_name": "Reuters",
     "xai_sentiment": "NEG", "theme_primary": "Recall"},
    {"query_group": "Competitors News", "subject_brand": "Amway",
     "published_date": "2026-07-11", "author": "Bo Li", "country": "IN",
     "publisher_name": "Bloomberg", "xai_sentiment": "NEU", "theme_primary": "Market"},
    {"query_group": "Industry News", "subject_brand": "", "published_date": "2026-07-11",
     "publisher_name": "PharmaTimes", "xai_sentiment": "NEU", "theme_primary": "Regulation"},
]


def test_collection_counts_by_group_and_subject():
    s = stage_stats.collection_stats(ARTS, brand="BeOne", competitors=["Amway", "Herbalife"])
    assert s["total"] == 4
    assert s["group_series"] == [("Brand", 2), ("Competitors", 1), ("Industry", 1)]
    assert ("BeOne", 2) in s["subject_series"]
    assert ("Amway", 1) in s["subject_series"]
    assert "Amway, Herbalife" in s["summary"]


def test_tagging_stats_enriched_themes_peaks():
    s = stage_stats.tagging_stats(ARTS)
    assert s["tagged"] == 4
    assert s["enriched"] == 2          # only 2 have an author or a resolved country
    assert s["volume_peaks"][0] == ("2026-07-10", 2)
    assert s["sentiment"]["POS"] == 1 and s["sentiment"]["NEG"] == 1
    themes = dict(s["top_themes"])
    assert themes.get("Launch") == 1 and themes.get("Market") == 1


def test_final_stats_dropped():
    s = stage_stats.final_stats(approved_count=10, monitoring_count=7)
    assert s["in_dashboard"] == 7 and s["dropped"] == 3 and s["approved"] == 10
