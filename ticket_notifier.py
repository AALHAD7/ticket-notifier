#!/usr/bin/env python3
"""
IIMT Ticket Portal -> Vertical Email Notifier
------------------------------------------------
Logs into the ticket portal, pulls Open tickets created on/after CUTOFF_DATE,
simplifies each subject line, groups by vertical, and emails each vertical's
student contact via Gmail SMTP. Designed to be run twice a day via cron/Task
Scheduler. Already-notified ticket IDs are tracked in a local JSON file so
nothing is sent twice.

The portal logs in via Google SSO, so there's no username/password form to
automate. Instead, run `Google Login.py` once (or whenever the session
expires) to open a real browser, log in manually, and persist the browser
profile to browser_profile/. This script reuses that same profile directory
on every run (not just a cookie snapshot), which keeps the browser identity
consistent across runs and is much less likely to get flagged by Google's
login anomaly detection than replaying cookies in a fresh context each time.

Every run also syncs all handled-category tickets into a Google Sheet named
"Daily Tickets" (one row per ticket; status/assignee/last-reply get updated
in place on later runs). This is done via a small Google Apps Script bound
to the sheet, deployed as a private web endpoint -- no Google Cloud project,
service account, or billing needed, just your own free Google account.

SETUP (see bottom of file for full instructions):
  pip install playwright python-dotenv
  playwright install chromium
  # optional: set NVIDIA_BASE_URL + NVIDIA_API_KEY in .env for LLM-based
  # subject simplification via NVIDIA Nemotron. No extra pip install needed.

Create a .env file next to this script (chmod 600) with:
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD
  GOOGLE_SHEETS_WEBHOOK_URL, GOOGLE_SHEETS_WEBHOOK_SECRET
  ANTHROPIC_API_KEY   (optional, for LLM subject simplification)
Never hardcode secrets in this file.

Google Sheet setup (one-time, see apps_script_daily_tickets.gs next to this
file for the exact script to paste in):
  1. Create a Google Sheet named exactly "Daily Tickets" in your own Drive.
  2. Extensions -> Apps Script. Paste in apps_script_daily_tickets.gs.
  3. Project Settings (gear icon) -> Script Properties -> add SHARED_SECRET
     with a random value you make up. Put that same value in .env as
     GOOGLE_SHEETS_WEBHOOK_SECRET.
  4. Deploy -> New deployment -> type "Web app". Execute as "Me", who has
     access "Anyone". Deploy, authorize when prompted, and copy the Web
     app URL into .env as GOOGLE_SHEETS_WEBHOOK_URL.
"""

import os
import re
import json
import smtplib
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ----------------------------------------------------------------------------
# CONFIG - edit this section
# ----------------------------------------------------------------------------

PORTAL_BASE = os.environ.get("PORTAL_BASE_URL")  # Set in .env
PORTAL_TICKETS_URL = f"{PORTAL_BASE}/employee/ticket-list"

# Persistent Google SSO browser profile, created/refreshed by "Google Login.py".
PROFILE_DIR = Path(__file__).parent / "browser_profile"

# Google Sheets sync, via an Apps Script web endpoint (see setup notes above).
GOOGLE_SHEET_NAME = "Daily Tickets"
GOOGLE_SHEETS_WEBHOOK_URL = os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL")
GOOGLE_SHEETS_WEBHOOK_SECRET = os.environ.get("GOOGLE_SHEETS_WEBHOOK_SECRET")

# Ticket categories your committee handles, exactly as they appear in the
# "Category" column on the portal.
INCLUDED_CATEGORIES = {
    "Estate Office - Electrical",
    "Estate Office - AC",
    "Estate Office - Civil",
    "Estate Office - Carpentry related",
    "Estate Office - Chairs",
    "Estate Office - Plumbing",
    "Estate Office - Housekeeping and Gardening",
    "Estate Office - Washing Machine",
    "Estate Office - Water Cooler",
    "IT Office - Network & Wifi",
    "IT Office - Audio & Video Issue",
}

# Fill in the real email address responsible for each vertical.
# Group several categories under the same email if one person covers them.
VERTICAL_EMAIL_MAP = {
    "Estate Office - Electrical": ["contact1@yourdomain.ac.in", "contact2@yourdomain.ac.in"],
    "Estate Office - AC": ["contact3@yourdomain.ac.in"],
    "Estate Office - Civil": ["contact4@yourdomain.ac.in"],
    "Estate Office - Carpentry related": ["contact4@yourdomain.ac.in"],
    "Estate Office - Chairs": ["contact4@yourdomain.ac.in"],
    "Estate Office - Plumbing": ["contact5@yourdomain.ac.in"],
    "Estate Office - Housekeeping and Gardening": ["contact6@yourdomain.ac.in"],
    "Estate Office - Washing Machine": ["contact5@yourdomain.ac.in"],
    "Estate Office - Water Cooler": ["contact1@yourdomain.ac.in", "contact2@yourdomain.ac.in"],
    "IT Office - Network & Wifi": ["contact7@yourdomain.ac.in"],
    "IT Office - Audio & Video Issue": ["contact7@yourdomain.ac.in"],
}

# Only tickets created on/after this date will ever be considered (initial floor).
CUTOFF_DATE = datetime(2026, 7, 15)

# Local state file that remembers which ticket IDs were already emailed.
STATE_FILE = Path(__file__).parent / "notified_tickets.json"

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = "nvidia/nemotron-3.5-bolt-e4b"
USE_LLM_SIMPLIFICATION = bool(NVIDIA_API_KEY)

# ----------------------------------------------------------------------------
# SCRAPING
# ----------------------------------------------------------------------------

class SessionExpiredError(RuntimeError):
    """Raised when the saved Google SSO session no longer works."""


def goto_with_retry(page, url, attempts=3, timeout_ms=20000, wait_until="domcontentloaded"):
    """The portal is intermittently slow to fully respond (not just us --
    even curl sees this). Retrying a couple of times clears it up more often
    than not, without making a single flaky moment fail the whole run."""
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as e:
            last_err = e
            if attempt < attempts:
                time.sleep(5)
    raise last_err


def check_session():
    """Checks whether the saved browser session is still valid without scraping."""
    if not PROFILE_DIR.exists():
        return False
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=True,
            )
            page = context.new_page()
            try:
                page.goto(PORTAL_TICKETS_URL, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            url = page.url
            context.close()
            if "accounts.google.com" in url or "iimt-terminal" in url:
                return False
            return "ticket-list" in url
    except Exception:
        return False


def scrape_open_tickets():
    """Uses the persistent Google SSO browser profile to fetch tickets."""
    if not PROFILE_DIR.exists():
        raise SessionExpiredError(
            f"No browser profile at {PROFILE_DIR}. Run 'Google Login.py' first."
        )

    tickets = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
        )
        page = context.new_page()

        try:
            goto_with_retry(page, PORTAL_TICKETS_URL)
        except Exception:
            context.close()
            raise
        page.wait_for_load_state("networkidle")

        if "accounts.google.com" in page.url or "iimt-terminal" in page.url:
            context.close()
            raise SessionExpiredError(
                "Saved session no longer works (redirected to login). "
                "Re-run 'Google Login.py' to refresh the browser profile."
            )

        # Set page size to 100 so we page through fewer times.
        page.select_option('select[name="ticket_table_length"]', "100")
        page.wait_for_load_state("networkidle")

        while True:
            rows = page.query_selector_all("table#ticket_table tbody tr")
            for row in rows:
                cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
                if len(cells) < 14:
                    continue
                ticket_id = cells[1]
                subject = cells[4]
                room_details = cells[5]
                category = cells[6]
                assigned_to = cells[9]
                created_str = cells[10]  # e.g. "15-07-2026 09:13 PM"
                status = cells[11]
                last_reply = cells[13]
                link = row.query_selector("a")
                detail_url = link.get_attribute("href") if link else None

                try:
                    created_dt = datetime.strptime(created_str, "%d-%m-%Y %I:%M %p")
                except ValueError:
                    continue

                tickets.append({
                    "id": ticket_id,
                    "subject": subject,
                    "room": room_details,
                    "category": category,
                    "created": created_dt,
                    "status": status,
                    "assigned_to": assigned_to,
                    "last_reply": last_reply,
                    "detail_url": detail_url,
                })

            next_btn = page.query_selector("a.paginate_button.next:not(.disabled)")
            if next_btn:
                next_btn.click()
                page.wait_for_load_state("networkidle")
            else:
                break

        context.close()

    return tickets


def fetch_availability(tickets):
    """Visits each ticket's detail page and fills in t["availability"] from
    the optional "Availability" field there (not present in the list view,
    and not every ticket has one filled in). Only called for tickets about
    to be emailed, to avoid an extra page load per ticket on every run."""
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
        )
        page = context.new_page()

        for t in tickets:
            t["availability"] = None
            if not t.get("detail_url"):
                continue
            try:
                page.goto(t["detail_url"], wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")
                label = page.locator("p.text-bold.text-primary", has_text="Availability")
                if label.count() > 0:
                    value = label.first.locator("xpath=following-sibling::p[1]").inner_text()
                    t["availability"] = value.strip() or None
            except Exception:
                pass  # leave availability as None if the detail page misbehaves

        context.close()


# ----------------------------------------------------------------------------
# SUBJECT SIMPLIFICATION
# ----------------------------------------------------------------------------

def simplify_subject_rule_based(subject: str) -> str:
    """Lightweight fallback: trims filler phrases, no API needed."""
    text = subject.strip()
    text = re.sub(r"(?i)^(request for|requesting|please|kindly|urgent:?)\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > 80:
        text = text[:77].rstrip() + "..."
    return text[0].upper() + text[1:] if text else text


def simplify_subject_llm(subject: str) -> str:
    """Uses NVIDIA Nemotron to rewrite the subject as a short, plain-language line."""
    payload = json.dumps({
        "model": NVIDIA_MODEL,
        "max_tokens": 30,
        "messages": [{
            "role": "user",
            "content": (
                "Rewrite this student maintenance complaint as a short, "
                "plain-language issue description (max 8 words, no fluff, "
                "just the issue). Reply with only the rewritten text.\n\n"
                f"Complaint: {subject}"
            ),
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{NVIDIA_BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"].strip()


def simplify_subject(subject: str) -> str:
    if USE_LLM_SIMPLIFICATION:
        try:
            return simplify_subject_llm(subject)
        except Exception:
            pass  # fall back silently if the API call fails
    return simplify_subject_rule_based(subject)


# ----------------------------------------------------------------------------
# STATE (avoid re-notifying the same ticket)
# ----------------------------------------------------------------------------

def load_notified_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_notified_ids(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


# ----------------------------------------------------------------------------
# GOOGLE SHEET SYNC (via Apps Script web endpoint)
# ----------------------------------------------------------------------------

def sync_tickets_to_sheet(tickets, attempts=3):
    """Sends all handled tickets to the Apps Script endpoint, which appends
    tickets new to the sheet and updates status/assignee/last-reply in place
    for tickets already logged. The endpoint owns the upsert logic; this
    function is just the HTTP call.

    Retries on network-level failures (e.g. connection reset), since the
    webhook is occasionally flaky the same way the portal is -- see
    goto_with_retry."""
    if not GOOGLE_SHEETS_WEBHOOK_URL or not GOOGLE_SHEETS_WEBHOOK_SECRET:
        raise RuntimeError(
            "GOOGLE_SHEETS_WEBHOOK_URL / GOOGLE_SHEETS_WEBHOOK_SECRET not set in .env. "
            "See the Google Sheet setup instructions at the top of this file."
        )

    payload = {
        "secret": GOOGLE_SHEETS_WEBHOOK_SECRET,
        "tickets": [
            {
                "id": t["id"],
                "subject": t.get("simplified_subject") or t["subject"],
                "room": t["room"],
                "category": t["category"],
                "created": t["created"].strftime("%d-%b-%Y %H:%M"),
                "status": t["status"],
                "assigned_to": t["assigned_to"],
                "last_reply": t["last_reply"],
                "availability": t.get("availability") or "",
            }
            for t in tickets
        ],
    }
    data = json.dumps(payload).encode("utf-8")

    last_err = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            GOOGLE_SHEETS_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.URLError as e:
            last_err = e
            if attempt < attempts:
                time.sleep(5)
    else:
        raise RuntimeError(f"Could not reach Sheets webhook: {last_err}") from last_err

    if result.get("error"):
        raise RuntimeError(f"Sheets webhook error: {result['error']}")

    print(f"Sheet sync: {result.get('new', '?')} new, {result.get('updated', '?')} updated.")


# ----------------------------------------------------------------------------
# EMAIL
# ----------------------------------------------------------------------------

def send_email(to_addr, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_addr

    # Try SSL port 465 first (works on restricted/campus networks), fallback to 587 STARTTLS
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.ehlo()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
            return
    except Exception as ssl_err:
        pass

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def build_email_body(vertical, ticket_list):
    lines = [f"New {vertical} tickets as of {datetime.now():%d-%b-%Y %H:%M}:\n"]
    for t in ticket_list:
        lines.append(f"Ticket ID: {t['id']}")
        lines.append(f"Hostel & Room No: {t['room']}")
        lines.append(f"Issue: {t['simplified_subject']}")
        lines.append(f"Availability (if mentioned): {t.get('availability') or 'Not specified'}")
        lines.append("")
    lines.append("(Automated notification from the Infra & IT Committee ticket system)")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    missing = [k for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing required environment variables: {missing}")

    already_notified = load_notified_ids()

    try:
        all_tickets = scrape_open_tickets()
    except SessionExpiredError as e:
        print(f"ERROR: {e}")
        raise SystemExit(2)
    except Exception as e:
        print(f"ERROR: scrape failed: {e}")
        try:
            send_email(GMAIL_ADDRESS, "[Ticket Notifier] Run failed",
                       f"This run couldn't fetch tickets from the portal:\n\n{e}\n\n"
                       "Usually transient portal-side slowness -- the next scheduled run "
                       "will likely succeed on its own. If this keeps happening, it needs "
                       "a look.")
        except Exception as mail_err:
            print(f"Also failed to send alert email: {mail_err}")
        raise SystemExit(1)

    handled_tickets = [
        t for t in all_tickets
        if t["category"] in INCLUDED_CATEGORIES and t["created"] >= CUTOFF_DATE
    ]
    new_tickets = [t for t in handled_tickets if t["id"] not in already_notified]

    if not new_tickets:
        print("No new tickets to notify.")
    else:
        fetch_availability(new_tickets)
        for t in new_tickets:
            t["simplified_subject"] = simplify_subject(t["subject"])

        by_vertical = {}
        for t in new_tickets:
            by_vertical.setdefault(t["category"], []).append(t)

        sent_ids = set()
        for vertical, ticket_list in by_vertical.items():
            addrs = VERTICAL_EMAIL_MAP.get(vertical, [])
            if not addrs:
                print(f"Skipping '{vertical}': no email configured.")
                continue
            to_addr = ", ".join(addrs)
            subject_line = f"[Ticket Update] {len(ticket_list)} new {vertical} ticket(s)"
            body = build_email_body(vertical, ticket_list)
            send_email(to_addr, subject_line, body)
            sent_ids.update(t["id"] for t in ticket_list)
            print(f"Sent {len(ticket_list)} ticket(s) to {to_addr} for {vertical}")

        save_notified_ids(already_notified | sent_ids)

    try:
        sync_tickets_to_sheet(handled_tickets)
    except Exception as e:
        print(f"Sheet sync failed: {e}")


if __name__ == "__main__":
    import sys
    if "--check-session" in sys.argv:
        if check_session():
            print("Session valid.")
            sys.exit(0)
        else:
            print("Session expired or missing.")
            sys.exit(1)
    main()