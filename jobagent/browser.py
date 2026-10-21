"""The saved browser profile in browser_profile/.

Logins live in that profile. The agent never types a password: you log in yourself with
`python3 run.py --login`, and later runs reuse the saved session.
"""
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright

from .settings import CACHE_DIR, ROOT

PROFILE_DIR = ROOT / "browser_profile"
LOGIN_DONE = CACHE_DIR / "login_done"
REED_SIGN_IN = "https://www.reed.co.uk/account/signin"


def launch(playwright, headless: bool = False) -> BrowserContext:
    PROFILE_DIR.mkdir(exist_ok=True)
    chrome = Path("/Applications/Google Chrome.app").exists()
    return playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome" if chrome else None,
        headless=headless,
        viewport={"width": 1280, "height": 900},
        accept_downloads=False,
    )


def login() -> None:
    """Open Reed's sign in page and wait until you close the window or the done file appears."""
    LOGIN_DONE.unlink(missing_ok=True)
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(REED_SIGN_IN)
        print("Log in to Reed in the browser window. Close the window (or create cache/login_done) when finished.",
              flush=True)
        while ctx.pages and not LOGIN_DONE.exists():
            time.sleep(1)
        ctx.close()
    LOGIN_DONE.unlink(missing_ok=True)
    print("Browser closed; the session is saved in browser_profile/.", flush=True)
