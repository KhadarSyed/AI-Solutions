"""Extended tagging schema — controlled vocabularies (anti-hallucination), theme
tiers, and product entities."""
from app.tools.schemas.tagging import ArticleEntities, TaggedArticle


def _base(**over):
    data = dict(index=0, xai_sentiment="POS", xai_section="Brand News",
                sentiment_confidence=0.9, theme_confidence=0.8, section_confidence=0.7,
                relevancy_confidence=0.95, xai_sentiment_reason="praises brand",
                xai_theme_reason="about launch", xai_relevancy_reason="brand is subject")
    data.update(over)
    return TaggedArticle(**data)


def test_invented_emotion_is_dropped():
    a = _base(theme_primary="Launch", emotions=["joy", "euphoria", "TRUST", "made_up"])
    # only the valid Plutchik labels survive, normalized and de-duped
    assert a.emotions == ["joy", "trust"]


def test_invented_signal_is_dropped():
    a = _base(theme_primary="Launch", signals=["Product Launch", "world_domination", "recall"])
    assert a.signals == ["product_launch", "recall"]


def test_theme_alias_mirrors_primary():
    a = _base(theme_primary="Pricing")
    assert a.xai_theme == "Pricing"
    b = _base(xai_theme="Legacy Theme", theme_primary="")
    assert b.theme_primary == "Legacy Theme"


def test_theme_tiers_default_empty():
    a = _base(theme_primary="A")
    assert a.theme_secondary == "" and a.theme_tertiary == ""


def test_products_entity_present():
    e = ArticleEntities(products=["InsulinX", "PumpPro"])
    assert e.products == ["InsulinX", "PumpPro"]
    a = _base(theme_primary="A")
    assert a.entities.products == []


def test_confidences_bounded():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _base(theme_primary="A", emotion_confidence=1.5)
