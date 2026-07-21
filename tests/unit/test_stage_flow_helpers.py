"""Plan/collect gate helpers — tagging-addition parsing, brand color, duration label."""
from app.orchestration.graphs.pipeline_graph import (
    _brand_color,
    _duration,
    _parse_tagging_additions,
)


def test_parse_tagging_additions_extracts_data_points():
    assert _parse_tagging_additions("approve, also add pricing mentions") == ["pricing mentions"]
    assert _parse_tagging_additions("include ESG and layoffs") == ["ESG", "layoffs"]
    assert _parse_tagging_additions("also track product recalls") == ["product recalls"]


def test_parse_ignores_competitor_adds():
    # competitor changes are handled by the plan gate, not tagging additions
    assert _parse_tagging_additions("add competitor Carrier") == []


def test_parse_empty():
    assert _parse_tagging_additions("approve") == []
    assert _parse_tagging_additions("") == []


def test_brand_color_deterministic():
    assert _brand_color("BeOne") == _brand_color("BeOne")
    assert _brand_color("").startswith("#")


def test_duration_label():
    label, days = _duration(7)
    assert "last 7 days" in label and days == "7"
    label1, days1 = _duration(1)
    assert "last 1 day" in label1 and days1 == "1"
