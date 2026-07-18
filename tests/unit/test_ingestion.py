from app.services.ingestion_service import dedupe
from app.tools.connectors.base import RawArticle
from app.tools.connectors.file_upload import parse_upload
from app.tools.connectors.google_news_rss import parse_feed

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"trane" - Google News</title>
<item>
  <title>Trane expands heat pump rebates - ACHR News</title>
  <link>https://www.achrnews.com/articles/trane-rebates</link>
  <pubDate>Wed, 16 Jul 2026 09:00:00 GMT</pubDate>
  <description>Trane announced expanded rebates...</description>
  <source url="https://www.achrnews.com">ACHR News</source>
</item>
<item>
  <title>HVAC market shifts - Cooling Post</title>
  <link>https://www.coolingpost.com/hvac-market</link>
  <pubDate>Tue, 15 Jul 2026 12:30:00 GMT</pubDate>
  <description>The market is shifting...</description>
  <source url="https://www.coolingpost.com">Cooling Post</source>
</item>
</channel></rss>"""


def test_rss_parse_normalizes_nine_fields():
    articles = parse_feed(RSS_FIXTURE, query="trane", group="Brand", max_results=50)
    assert len(articles) == 2
    a = articles[0]
    assert a.publisher_name == "ACHR News"
    assert a.title == "Trane expands heat pump rebates"          # " - Publisher" stripped
    assert a.publisher_domain == "achrnews.com"
    assert a.published_date is not None and a.published_date.day == 16
    assert a.country == "all"                                     # default until enriched
    assert a.source == "google_news_rss"
    assert a.original_query == "trane"


def test_upload_csv_alias_headers():
    csv = (
        b"Headline,Link,Outlet,Published,Byline,Body\n"
        b"Trane wins award,https://x.com/a,Reuters,2026-07-01,J. Doe,Some text\n"
        b",https://x.com/missing-title,,,,\n"
    )
    articles = parse_upload("coverage.csv", csv)
    assert len(articles) == 1                                     # row without title dropped
    a = articles[0]
    assert a.title == "Trane wins award"
    assert a.publisher_name == "Reuters"
    assert a.author == "J. Doe"
    assert a.published_date is not None and a.published_date.month == 7
    assert a.source == "file_upload"


def test_upload_rejects_unknown_type_and_missing_columns():
    import pytest

    with pytest.raises(ValueError, match="unsupported upload type"):
        parse_upload("notes.txt", b"hello")
    with pytest.raises(ValueError, match="title and url"):
        parse_upload("bad.csv", b"foo,bar\n1,2\n")


def _a(url: str, title: str, content: str = "") -> RawArticle:
    return RawArticle(title=title, url=url, content=content)


def test_dedupe_by_url_and_title_fingerprint():
    articles = [
        _a("https://a.com/story?utm=x", "Trane Expands Rebates!", "short"),
        _a("https://a.com/story", "Trane Expands Rebates!", "short"),      # same url modulo query
        _a("https://b.com/syndicated", "Trane expands rebates", "much longer body kept"),
        _a("https://c.com/other", "Completely different story"),
    ]
    unique, syndication = dedupe(articles)
    titles = {a.title for a in unique}
    assert len(unique) == 2
    assert "Completely different story" in titles
    # richest copy won; the poorer one recorded as syndicated
    keeper = next(a for a in unique if "rebates" in a.title.lower())
    assert keeper.url == "https://b.com/syndicated"
    assert any("a.com/story" in u for urls in syndication.values() for u in urls)
