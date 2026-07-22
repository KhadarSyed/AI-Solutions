"""Automate posting the @mention in PRSolution › General via the signed-in
Teams profile. Navigates by deep link (correct org/team/channel), waits out the
slow loader, selects the agent from the mention popup, and sends. Screenshots at
each step land in data/local/ for verification."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contextlib

from playwright.sync_api import sync_playwright  # noqa: E402

PROFILE = Path("data/local/teams_browser_profile")
SHOTS = Path("data/local")
TEAM_ID = "6ed2c0a3-aaee-467b-93a5-40b81c67c699"
CHANNEL_ID = "19:8ugvh-8OzwF59GDiqz1Dh0t2iLNRqMubt0PTJWntOzc1@thread.tacv2"
DEEPLINK = (f"https://teams.microsoft.com/l/channel/"
            f"{CHANNEL_ID.replace(':', '%3A').replace('@', '%40')}/General"
            f"?groupId={TEAM_ID}")

COMPOSE = ['div[data-tid="ckeditor"]', '[contenteditable="true"][role="textbox"]',
           'div[role="textbox"][aria-label*="message" i]']


def shot(page, name):
    try:
        page.screenshot(path=str(SHOTS / f"auto_{name}.png"))
        print(f"  shot: auto_{name}.png")
    except Exception as e:
        print(f"  shot {name} failed: {str(e)[:60]}")


def wait_loaded(page, secs=180):
    for _ in range(secs):
        try:
            if page.locator("#loading-screen").count() == 0:
                return True
        except Exception:
            return True
        time.sleep(1)
    return False


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        with contextlib.suppress(Exception):
            page.goto("https://teams.microsoft.com/", wait_until="domcontentloaded", timeout=90000)

        # wait for the user to sign in as khadar (up to 12 min)
        print("SIGN IN as khadar.syed@infovision.com in this window, then wait...")
        signed = False
        for i in range(360):
            page = ctx.pages[-1]
            u = page.url
            if "teams.microsoft.com" in u and "login" not in u and "auth" not in u:
                # app shell present?
                try:
                    if page.locator('div[role="main"], [data-tid="app-layout-area--main"]').count():
                        signed = True
                        print(f"signed in after {i*2}s — url {u[:70]}")
                        break
                except Exception:
                    pass
            if i % 15 == 0:
                print(f"  ...waiting for sign-in ({i*2}s) url={u[:60]}")
            time.sleep(2)
        if not signed:
            shot(page, "0_signin")
            print("NOT_SIGNED_IN")
            time.sleep(3)
            return

        # No deep link (it triggers cross-tenant re-auth). The user navigates to
        # PRSolution > General in THIS window; we wait for the compose box.
        print(">>> In THIS browser: switch org to AlphaMetricx, open PRSolution > "
              "General, and CLICK the message box. Waiting up to 8 min...")
        compose = None
        for i in range(240):
            page = ctx.pages[-1]
            try:
                if page.locator("#loading-screen").count():
                    time.sleep(2)
                    continue
            except Exception:
                pass
            for sel in COMPOSE:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    compose = loc.first
                    break
            if compose:
                print(f"compose box visible after {i*2}s")
                break
            if i % 15 == 0:
                print(f"  ...waiting for the General compose box ({i*2}s)")
            time.sleep(2)
        if not compose:
            shot(page, "2_nocompose")
            print("NO_COMPOSE")
            time.sleep(3)
            return

        try:
            compose.click(force=True, timeout=8000)
        except Exception:
            compose.evaluate("el => el.focus()")
        time.sleep(1)
        page.keyboard.type("@InfoVision Agent", delay=140)
        time.sleep(5)                       # popup loads (slow net)
        shot(page, "3_popup")

        # click the suggestion whose text mentions Agent1317
        picked = False
        for sel in ['[role="option"]', '[data-tid*="mention"]', 'li', '[role="listitem"]']:
            try:
                opts = page.locator(sel)
                for i in range(min(opts.count(), 12)):
                    txt = (opts.nth(i).inner_text() or "")
                    if "Agent1317" in txt or "InfoVision Agent" in txt:
                        opts.nth(i).click(timeout=4000)
                        picked = True
                        break
            except Exception:
                pass
            if picked:
                break
        if not picked:                      # fallback: keyboard select first
            page.keyboard.press("ArrowDown")
            time.sleep(0.4)
            page.keyboard.press("Enter")
        time.sleep(1)
        shot(page, "4_afterpick")
        page.keyboard.type(" start BeOne monitoring", delay=45)
        time.sleep(0.6)
        shot(page, "5_composed")
        page.keyboard.press("Enter")
        time.sleep(4)
        shot(page, "6_sent")
        print("SENT (verify screenshots + agent logs)")
        time.sleep(6)


if __name__ == "__main__":
    main()
