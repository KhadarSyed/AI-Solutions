"""Tagged CSV columns + the monitoring opt-out parse."""
from app.channels.csv_export import TAGGED_COLUMNS, articles_to_csv
from app.services.review_service import monitoring_drop_ids

_ARTICLE = {
    "id": "A0", "publisher_name": "Reuters", "title": "BeOne launch",
    "content": "x", "published_date": "2026-07-10", "published_time": "09:15",
    "xai_sentiment": "POS", "sentiment_confidence": 0.9,
    "theme_primary": "Launch", "theme_secondary": "Europe", "theme_tertiary": "",
    "emotions": ["joy", "trust"], "signals": ["product_launch"],
    "entities": {"brand_of_interest": ["BeOne"], "competitors": ["Amway"],
                 "other_competitors": ["Usana"], "organizations": ["FDA"],
                 "peoples": ["Jane Roe"], "products": ["InsulinX"]},
    "is_approved": True, "is_approved_for_monitoring": True,
}


def test_tagged_csv_has_new_columns():
    for col in ("published_time", "theme_primary", "theme_secondary", "theme_tertiary",
                "emotions", "signals", "entity_brand", "entity_competitors",
                "entity_products", "Monitoring"):
        assert col in TAGGED_COLUMNS


def test_csv_serializes_lists_and_entities():
    text = articles_to_csv([_ARTICLE], TAGGED_COLUMNS).decode("utf-8-sig")
    header, row = text.splitlines()[0], text.splitlines()[1]
    assert "Monitoring" in header
    assert "joy; trust" in row               # emotions joined
    assert "Amway; Usana" in row             # competitors + other_competitors
    assert "InsulinX" in row                 # product entity
    assert "09:15" in row                    # published_time column
    assert row.rstrip().endswith("TRUE")     # Monitoring TRUE last


def test_monitoring_drop_ids_parses_false_rows():
    csv_text = ("id,Monitoring\r\nA0,TRUE\r\nA1,FALSE\r\nA2,false\r\nA3,0\r\n"
                "A4,no\r\nA5,\r\nA6,keep\r\n")
    drop = monitoring_drop_ids(csv_text.encode("utf-8-sig"))
    assert drop == {"A1", "A2", "A3", "A4", "A5"}   # A0/A6 kept


def test_monitoring_drop_ids_no_column():
    assert monitoring_drop_ids(b"id,title\r\nA0,hi\r\n") == set()
