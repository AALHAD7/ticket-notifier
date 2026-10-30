import os
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

PORTAL_BASE = os.environ.get("PORTAL_BASE_URL", "https://emp.iimtrichy.ac.in").rstrip("/")
LOGIN_URL = f"{PORTAL_BASE}/iimt-terminal"
PROFILE_DIR = Path(__file__).parent / "browser_profile"

print("")
print("==========================================================")
print("              🔑 GOOGLE LOGIN SETUP")
print("=========================================================="
)
print("A browser window will open.")
print("1. Log in with your Google account.")
print("2. Once logged in and on the portal, return to this window.")
print("==========================================================")
print("")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
    )
    page = context.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    input("👉 After logging in successfully, press [Enter] here to continue...")
    context.close()

print("✅ Login session saved.")
