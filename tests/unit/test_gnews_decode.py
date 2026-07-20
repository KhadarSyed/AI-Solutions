import base64

from app.tools.scraping import gnews


def test_is_gnews_url():
    assert gnews.is_gnews_url("https://news.google.com/rss/articles/CBMiAbc")
    assert not gnews.is_gnews_url("https://economictimes.com/x")


def test_decode_non_gnews_returns_none():
    assert gnews.decode("https://economictimes.com/x") is None


def test_decode_extracts_domain_from_base64_url():
    real = "https://www.reuters.com/world/story-123"
    payload = b"\x08\x13\x12" + bytes([len(real)]) + real.encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/CBMi{token}"
    out = gnews.decode(url)
    assert out is not None
    real_url, domain = out
    assert "reuters.com" in domain
