# IIMT Ticket Notifier

> **Automated email notifier for the IIM Trichy student Infra & IT Committee.**  
> Watches the campus maintenance ticket portal, groups new tickets by category, and emails the responsible student contact for each vertical — so no ticket goes unnoticed.

---

## What does this system do?

The ticket portal at `emp.iimtrichy.ac.in` receives maintenance and IT complaints raised by students (e.g. "AC not working in Hostel B, Room 203"). The portal is checked manually by staff, but there's no automated way for student committee members to know when a new ticket in their domain has been raised.

This system bridges that gap:

1. **Scrapes** the ticket portal twice a day (9 AM and 5 PM by default).
2. **Filters** tickets belonging to the verticals the student committee handles (Estate Office and IT Office categories).
3. **Simplifies** the ticket subject line into a clean, readable issue description (optionally using Claude AI).
4. **Emails** the responsible student contact(s) for each category with a digest of all new tickets.
5. **Syncs** every handled ticket into a live Google Sheet ("Daily Tickets") so the committee has a running log — with status, assignee, and last-reply kept up to date on every run.
6. **Remembers** which tickets have already been notified so no one gets the same email twice.

---

## How does it work?

### Authentication
The ticket portal uses **Google SSO** (Single Sign-On) — there's no traditional username/password form to automate. To handle this:

- **`Google Login.py`** opens a real visible Chrome browser window pointed at the portal. You log in manually once. Playwright saves the entire browser profile (cookies, session storage, etc.) to the `browser_profile/` directory.
- **`ticket_notifier.py`** reuses that same persistent profile on every automated run, keeping the browser identity consistent. This avoids Google's login anomaly detection, which triggers when cookies are replayed in a fresh headless context.
- If the session ever expires (Google invalidates it), the script detects the redirect to the login page, emails you an alert, and exits cleanly. Just re-run `Google Login.py` to refresh.

### Scraping
`ticket_notifier.py` launches a headless Chromium browser using **Playwright**, navigates to the ticket list, sets the page size to 100, and walks through all pages. For each row it extracts:

| Field | Description |
|---|---|
| Ticket ID | Unique portal ID |
| Subject | Raw complaint text entered by the student |
| Room details | Hostel and room number |
| Category | The vertical (e.g. "Estate Office - Electrical") |
| Created date | When the ticket was raised |
| Status | Open / In Progress / Closed |
| Assigned To | Staff member assigned |
| Last Reply | Latest activity timestamp |
| Detail URL | Link to the individual ticket page |

For tickets about to be emailed, it also visits each ticket's **detail page** to pick up the optional "Availability" field (the student's free hours for access — not shown in the list view).

### Subject Simplification
Raw subject lines from students are often verbose or poorly phrased (e.g. *"Requesting for urgent attention regarding the non-functioning air conditioning unit in my hostel room"*). The system rewrites them into a concise 8-word description:

- **With `NVIDIA_API_KEY` set:** Uses `nvidia/nemotron-3.5-bolt-e4b` via the NVIDIA API for high-quality rewrites. No extra pip install needed — uses Python's built-in `urllib`.
- **Without it:** Falls back to a lightweight rule-based simplifier (strips filler words like "requesting", "please", "kindly"; trims to 80 chars).

### Email Notifications
Uses **Gmail SMTP** (with a Gmail App Password) to send emails. Each category's responsible student contact(s) receive a digest email listing only new tickets in their domain, formatted as:

```
New Estate Office - Electrical tickets as of 30-Oct-2026 09:00:

Ticket ID: 4521
Hostel & Room No: Hostel A, Room 112
Issue: Electrical short circuit in room
Availability (if mentioned): After 6 PM

(Automated notification from the Infra & IT Committee ticket system)
```

### Google Sheets Sync
Every run (regardless of new tickets) pushes all handled-category tickets to a Google Sheet via a **Google Apps Script** webhook deployed as a private web app:

- **New tickets** are appended as new rows.
- **Existing tickets** have their Status, Assigned To, and Last Reply columns updated in place.
- The webhook validates requests using a `SHARED_SECRET` so only this script can write to the sheet.

### Deduplication
A local file (`notified_tickets.json`) tracks every ticket ID that has been emailed. On each run, only tickets whose IDs are not in this file are emailed. The file is updated after a successful send.

---

## File Structure

```
ticket-notifier/
│
├── ticket_notifier.py              # Main script: scrape → filter → email → sync
├── Google Login.py                 # One-time setup: opens browser for manual Google SSO login
├── apps_script_daily_tickets.gs    # Paste into Google Apps Script (bound to "Daily Tickets" sheet)
│
├── .env                            # Secrets and config (never commit real values)
├── notified_tickets.json           # Auto-generated: list of already-emailed ticket IDs
├── ticket_notifier.log             # Auto-generated: combined stdout/stderr log from cron runs
│
├── browser_profile/                # Auto-generated by Google Login.py: persistent Chromium session
│
├── run_ticket_notifier.sh          # Shell wrapper: runs ticket_notifier.py (used by cron/launchd)
├── run_full_flow.sh                # Shell wrapper: runs Google Login.py then ticket_notifier.py
└── com.iimt.ticketnotifier.plist.disabled   # macOS launchd config (disabled; rename to enable)
```

---

## Setup Guide

### 1. Install dependencies

```bash
pip install playwright python-dotenv
playwright install chromium
```

> No extra library needed for LLM simplification — the NVIDIA API is called using Python's built-in `urllib`.

### 2. Create the `.env` file

Copy the `.env` template and fill in your values:

```env
GMAIL_ADDRESS=your_sending_account@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx      # From Google Account > App Passwords
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_API_KEY=nvapi-...                     # From platform.nvidia.com — optional, leave blank to skip LLM
GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_ID/exec
GOOGLE_SHEETS_WEBHOOK_SECRET=your_random_secret
```

Secure the file: `chmod 600 .env`

### 3. Set up the Google Sheet

1. Create a Google Sheet named **`Daily Tickets`** in your Google Drive.
2. Open **Extensions → Apps Script**.
3. Paste the contents of `apps_script_daily_tickets.gs` into the editor.
4. Go to **Project Settings (gear icon) → Script Properties → Add property:**
   - Key: `SHARED_SECRET` → Value: *(same random string you put in `.env`)*
5. Click **Deploy → New deployment → Web app**.
   - Execute as: **Me**
   - Who has access: **Anyone**
6. Click Deploy, authorize when prompted, and copy the **Web app URL** into `.env` as `GOOGLE_SHEETS_WEBHOOK_URL`.

> If you edit the Apps Script later, create a **new deployment version** (Manage deployments → Edit → New version) for changes to take effect.

### 4. Log in to the portal (one-time)

```bash
python3 "Google Login.py"
```

A visible Chrome window will open. Log into `emp.iimtrichy.ac.in` using your Google account. Once you're on the portal, press **Enter** in the terminal. The session is saved to `browser_profile/`.

> Repeat this step whenever the script reports a session-expired error.

### 5. Test a manual run

```bash
python3 ticket_notifier.py
```

Check the output and your email inbox.

### 6. Schedule automated runs (macOS)

**Option A: launchd (recommended)**

```bash
cp com.iimt.ticketnotifier.plist.disabled ~/Library/LaunchAgents/com.iimt.ticketnotifier.plist
launchctl load ~/Library/LaunchAgents/com.iimt.ticketnotifier.plist
```

This runs `ticket_notifier.py` at **9:00 AM and 5:00 PM** daily. Logs go to `ticket_notifier.log`.

**Option B: cron**

```bash
crontab -e
# Add:
0 9,17 * * * /Users/aalhad/ticket-notifier/run_ticket_notifier.sh
```

---

## Vertical → Contact Email Mapping

| Category | Responsible Contact(s) |
|---|---|
| Estate Office - Electrical | Electrical vertical leads (×4) |
| Estate Office - AC | AC vertical leads (×2) |
| Estate Office - Civil | Civil vertical lead |
| Estate Office - Carpentry related | Civil vertical lead |
| Estate Office - Chairs | Civil vertical lead |
| Estate Office - Plumbing | Plumbing vertical lead |
| Estate Office - Housekeeping & Gardening | Housekeeping vertical leads (×2) |
| Estate Office - Washing Machine | Plumbing vertical lead |
| Estate Office - Water Cooler | Electrical vertical leads (×4) |
| IT Office - Network & Wifi | IT vertical lead |
| IT Office - Audio & Video Issue | IT vertical lead |

> To update contacts or add new categories, edit `VERTICAL_EMAIL_MAP` and `INCLUDED_CATEGORIES` in `ticket_notifier.py`.

---

## Configuration Reference

All config lives in the `# CONFIG` section at the top of `ticket_notifier.py`:

| Variable | Purpose |
|---|---|
| `PORTAL_BASE` | Base URL of the ticket portal |
| `PORTAL_TICKETS_URL` | Full URL of the ticket list page |
| `PROFILE_DIR` | Path to the persistent Chromium browser profile |
| `INCLUDED_CATEGORIES` | Set of category names to monitor |
| `VERTICAL_EMAIL_MAP` | Dict mapping category → list of email addresses |
| `CUTOFF_DATE` | Only tickets created on/after this date are ever considered |
| `STATE_FILE` | Path to the JSON file tracking notified ticket IDs |
| `GOOGLE_SHEET_NAME` | Name of the Google Sheet to sync into |

---

## Error Handling

| Scenario | What happens |
|---|---|
| Google session expired | Script detects login redirect, emails an alert to `GMAIL_ADDRESS`, exits |
| Portal is slow / times out | `goto_with_retry` retries up to 3 times with a 5-second gap |
| Sheets webhook fails | Retried 3 times; on final failure, error is printed but the run still counts as successful (emails were sent) |
| NVIDIA API fails | Falls back silently to the rule-based simplifier |
| Missing `.env` variables | Script exits immediately with a clear error message listing missing keys |

---

## Purpose

This system was built for the **Infra & IT Committee** of IIM Trichy to automate their ticket monitoring workflow. Before this, committee members had to manually check the portal, which led to tickets being missed or picked up late. This tool ensures every new complaint in their domain reaches the right person within hours of being raised.
