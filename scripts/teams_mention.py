"""Reuse the signed-in Teams profile, wait for the user to open the BeOne channel
and focus the compose box, then post a real @mention of the agent."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

PROFILE = Path("data/local/teams_browser_profile")
SHOT = Path("data/local/teams_shot.png")

COMPOSE_SELECTORS = [
    'div[data-tid="ckeditor"]',
    '[contenteditable="true"][role="textbox"]',
    'div[role="textbox"][aria-label*="message" i]',
    'div[aria-label*="Type a" i]',
]


def find_compose(page):
    for sel in COMPOSE_SELECTORS:
        loc = page.locator(sel)
        if loc.count():
            return loc.first
    return None


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False,
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://teams.microsoft.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        print("WAITING — open PRSolution > BeOne and click the message box.")

        compose = None
        for i in range(180):  # up to 6 min
            page = ctx.pages[-1]
            # wait out the Teams loading overlay before touching anything
            try:
                if page.locator("#loading-screen").count():
                    time.sleep(2)
                    continue
            except Exception:
                pass
            c = find_compose(page)
            if c and c.is_visible():
                compose = c
                print(f"compose box visible after {i*2}s")
                break
            time.sleep(2)
        if not compose:
            page.screenshot(path=str(SHOT))
            print("NO_COMPOSE — could not find a visible message box; screenshot saved.")
            time.sleep(4)
            return

        # focus via JS + force click to bypass any residual overlay
        try:
            compose.click(force=True, timeout=8000)
        except Exception:
            compose.evaluate("el => el.focus()")
        time.sleep(1)
        page.keyboard.type("@Agent1317", delay=120)
        time.sleep(4)                       # let the mention suggestion popup load
        page.screenshot(path=str(SHOT))
        print("typed @Agent1317; selecting first suggestion")
        page.keyboard.press("ArrowDown")
        time.sleep(0.5)
        page.keyboard.press("Enter")        # pick the highlighted mention -> blue chip
        time.sleep(1)
        page.keyboard.type(" start BeOne monitoring", delay=40)
        time.sleep(0.5)
        page.screenshot(path=str(SHOT))
        print("composed message; sending")
        page.keyboard.press("Enter")        # send
        time.sleep(3)
        page.screenshot(path=str(SHOT))
        print("SENT — mention posted (check screenshot + agent logs)")
        time.sleep(5)


if __name__ == "__main__":
    main()
