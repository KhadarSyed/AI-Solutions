from app.services.query_plan import build_query_plan


def test_plan_has_brand_and_competitor_groups():
    plan = build_query_plan("BeOne", ["Carrier", "Daikin"], industry="HVAC")
    names = [g["name"] for g in plan]
    assert names == ["Brand News", "Competitors News", "Industry News"]
    brand = plan[0]
    assert brand["queries"] == ["BeOne"] and brand["subject"] == "BeOne"
    comp = plan[1]
    assert comp["queries"] == ["Carrier", "Daikin"] and comp["subject_per_query"] is True


def test_industry_group_skipped_when_empty():
    plan = build_query_plan("BeOne", ["Carrier"], industry=None)
    assert [g["name"] for g in plan] == ["Brand News", "Competitors News"]


def test_no_competitors_still_has_brand():
    plan = build_query_plan("BeOne", [], industry=None)
    assert [g["name"] for g in plan] == ["Brand News"]
