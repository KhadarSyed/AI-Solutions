from app.tools.connectors.base import RawArticle


def test_raw_article_has_subject_and_medium_defaults():
    a = RawArticle(title="t", url="https://x.com/a")
    assert a.subject_brand == ""
    assert a.medium == "news"


def test_raw_article_accepts_social_medium():
    a = RawArticle(title="t", url="https://reddit.com/p", medium="social", subject_brand="BeOne")
    assert a.medium == "social"
    assert a.subject_brand == "BeOne"
