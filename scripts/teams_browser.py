"""Launch a visible Chromium (with CDP) on Teams web for the user to sign into.
A separate step attaches over CDP to post the @mention in the compose box."""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE = Path("data/local/teams_browser_profile")
PROFILE.mkdir(parents=True, exist_ok=True)


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            args=["--remote-debugging-port=9222", "--start-maximized"],
            no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://teams.microsoft.com/", wait_until="domcontentloaded")
        print("BROWSER_READY — sign in, open PRSolution > BeOne, click the message box.")
        # keep alive until the attach step drives it (up to 30 min)
        for _ in range(1800):
            time.sleep(1)
        ctx.close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
