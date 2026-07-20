from app.analytics.chart_builders.impact import build


def _a(id, subj, pub, sent):
    return {"id": id, "title": f"{subj} story", "content": "", "subject_brand": subj,
            "publisher_name": pub, "xai_sentiment": sent, "published_date": "2026-07-01",
            "reach_weight": 1}


def test_sov_uses_subject_brand_and_shows_competitors():
    arts = [_a("A1", "BeOne", "Reuters", "POS"),
            _a("A2", "Carrier", "Bloomberg", "NEU"),
            _a("A3", "Carrier", "WSJ", "NEG"),
            _a("A4", "Daikin", "Nikkei", "POS")]
    out = build(arts, "BeOne", ["Carrier", "Daikin"])
    sov = out["share_of_voice"]
    assert sov["brand_mentions"] == 1
    names = {c["name"]: c["mentions"] for c in sov["competitors"]}
    assert names.get("Carrier") == 2
    assert names.get("Daikin") == 1
    assert any(m["competitor"] == "Carrier" for m in out["competitive_matrix"])


def test_top_impact_articles_carry_publisher():
    arts = [_a("A1", "BeOne", "Reuters", "POS")]
    out = build(arts, "BeOne", ["Carrier"])
    assert out["top_impact_articles"]
    assert all(row.get("publisher_name") for row in out["top_impact_articles"])
