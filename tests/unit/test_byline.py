from app.tools.scraping.extractor import extract_byline


def test_meta_author():
    assert extract_byline('<meta name="author" content="Jane Reporter">') == "Jane Reporter"


def test_meta_article_author_content_first():
    html = '<meta content="John Byline" property="article:author">'
    assert extract_byline(html) == "John Byline"


def test_json_ld_author():
    html = ('<script type="application/ld+json">'
            '{"@type":"NewsArticle","author":{"@type":"Person","name":"Sara Writer"}}'
            '</script>')
    assert extract_byline(html) == "Sara Writer"


def test_rel_author_link():
    assert extract_byline('<a rel="author" href="/x">Kwame Author</a>') == "Kwame Author"


def test_by_prefix_stripped():
    assert extract_byline('<span class="byline">By Maria Santos</span>') == "Maria Santos"


def test_rejects_junk():
    assert extract_byline('<meta name="author" content="editor@news.com">') == ""
    assert extract_byline("<p>no author here</p>") == ""
