from pathlib import Path
from playwright.sync_api import sync_playwright

# Persistent browser profile shared with ticket_notifier.py. Using the same
# profile directory (not just a cookie snapshot) keeps the browser identity
# consistent across runs, which is much less likely to get flagged/killed by
# Google's login anomaly detection than replaying cookies in a fresh headless
# context each time.
PROFILE_DIR = Path(__file__).parent / "browser_profile"

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
    )
    page = context.new_page()
    page.goto("https://emp.iimtrichy.ac.in/iimt-terminal", wait_until="domcontentloaded")
    input("Log in manually in the opened browser, then press Enter here...")
    context.close()
