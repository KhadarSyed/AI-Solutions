from app.tools.enrichment.logos import _extract_logo_url, _valid_image_bytes


def test_prefers_og_image():
    html = ('<head><meta property="og:image" content="https://x.com/logo.png">'
            '<link rel="apple-touch-icon" href="/touch.png">'
            '<link rel="icon" href="/fav.ico"></head>')
    assert _extract_logo_url(html, "https://x.com") == "https://x.com/logo.png"


def test_apple_touch_icon_when_no_og():
    html = '<head><link rel="apple-touch-icon" href="/touch.png"></head>'
    assert _extract_logo_url(html, "https://x.com") == "https://x.com/touch.png"


def test_header_img_logo_fallback():
    html = '<header><img class="site-logo" src="/brand-logo.svg"></header>'
    assert _extract_logo_url(html, "https://x.com") == "https://x.com/brand-logo.svg"


def test_none_when_nothing():
    assert _extract_logo_url("<p>hi</p>", "https://x.com") is None


def test_valid_image_bytes():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 600
    ok, mime = _valid_image_bytes(png, "image/png")
    assert ok and mime == "image/png"
    # too small
    assert _valid_image_bytes(b"\x89PNG", "image/png")[0] is False
    # non-image
    assert _valid_image_bytes(b"<html>not an image</html>" * 50, "text/html")[0] is False
