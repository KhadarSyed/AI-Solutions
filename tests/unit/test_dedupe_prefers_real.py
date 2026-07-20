from app.services.ingestion_service import dedupe
from app.tools.connectors.base import RawArticle


def test_dedupe_prefers_real_domain_over_gnews():
    gnews = RawArticle(title="Same Story Here Today", url="https://news.google.com/rss/articles/CBMiabc",
                       content="body", publisher_domain="news.google.com")
    real = RawArticle(title="Same Story Here Today", url="https://reuters.com/x",
                      content="body", publisher_domain="reuters.com")
    # gnews seen first — the real-URL copy must still win
    unique, _ = dedupe([gnews, real])
    assert len(unique) == 1
    assert unique[0].publisher_domain == "reuters.com"


def test_dedupe_keeps_real_when_seen_first():
    real = RawArticle(title="Another Shared Headline", url="https://apnews.com/y",
                      content="body", publisher_domain="apnews.com")
    gnews = RawArticle(title="Another Shared Headline", url="https://news.google.com/rss/articles/CBMizzz",
                       content="longer body wins normally", publisher_domain="news.google.com")
    unique, _ = dedupe([real, gnews])
    assert len(unique) == 1
    assert unique[0].publisher_domain == "apnews.com"
