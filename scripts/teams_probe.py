"""Attach to the running Teams browser (CDP) and report what's on screen so we
know whether we're in the BeOne channel with a compose box ready."""

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        pages = ctx.pages
        print(f"pages open: {len(pages)}")
        for pg in pages:
            try:
                print(f"  url: {pg.url[:90]}")
            except Exception:
                pass
        # pick the Teams page
        page = next((pg for pg in pages if "teams" in pg.url), pages[0])
        print(f"active title: {page.title()[:80]}")
        # signed in? look for the app shell / channel header
        for sel, label in [
            ('div[role="main"]', "main region"),
            ('[data-tid="channel-header-title"]', "channel header"),
            ('[contenteditable="true"][role="textbox"]', "compose box (contenteditable)"),
            ('div[data-tid="ckeditor"]', "compose (ckeditor)"),
            ('div[aria-label*="message"i]', "message aria box"),
        ]:
            try:
                n = page.locator(sel).count()
                print(f"  {label}: {n} match(es)")
            except Exception as e:
                print(f"  {label}: err {str(e)[:40]}")
        # header text (which channel are we on?)
        for sel in ['[data-tid="channel-header-title"]', "h1", '[role="heading"]']:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    print(f"  heading[{sel}]: {loc.inner_text()[:60]!r}")
                    break
            except Exception:
                pass


if __name__ == "__main__":
    main()
