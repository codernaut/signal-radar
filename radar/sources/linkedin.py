"""LinkedIn, two account-safe ways.

1. public_jobs(): LinkedIn's logged-out public job search (the same listings anyone
   sees without signing in). No cookies, no login, so your account is never involved.
   Hard-capped by a daily request budget that persists across runs, with long
   human-scale pauses. Default: 3 requests/day = up to ~75 fresh listings.

2. alert_emails(): parses the "Job alert" emails LinkedIn already sends you, read over
   IMAP. Zero LinkedIn traffic at all, and it reflects LinkedIn's own matching on your
   full profile. This is the highest-value LinkedIn source; set up 3-5 alerts in the app.

What this deliberately does NOT do: sign in as you, read your feed or scrape posts.
That is what gets accounts restricted, and request volume is not the only signal
LinkedIn uses. Posts are handled via search links in the digest instead.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header

from bs4 import BeautifulSoup

from ..http import SourceError, polite_pause, session
from ..models import Job

log = logging.getLogger("radar.linkedin")

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_ID = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d{8,})")


class DailyBudget:
    """Request allowance per calendar day, persisted in the run state."""

    def __init__(self, state: dict, limit: int, key: str = "linkedin_requests"):
        self.state, self.limit, self.key = state, limit, key
        today = date.today().isoformat()
        rec = state.get(key) or {}
        if rec.get("date") != today:
            rec = {"date": today, "count": 0}
        state[key] = rec

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.state[self.key]["count"])

    def spend(self) -> bool:
        if self.remaining <= 0:
            return False
        self.state[self.key]["count"] += 1
        return True


def rotate(queries: list, per_day: int, day: date | None = None) -> list:
    """Spread a big query pool over the week: today's slice first, the rest after
    (only reached if the budget allows)."""
    if not queries:
        return []
    day = day or date.today()
    start = (day.toordinal() * max(1, per_day)) % len(queries)
    return queries[start:] + queries[:start]


def public_jobs(queries: list[dict], budget: DailyBudget, http=None,
                pause=(25.0, 70.0)) -> tuple[list[Job], list[str]]:
    """queries: [{"keywords": "...", "location": "...", "remote": bool}], run in order
    of priority until the daily budget runs out."""
    http = http or session(browser_like=True)
    jobs: list[Job] = []
    notes: list[str] = []
    for i, q in enumerate(queries):
        if not budget.spend():
            notes.append(f"LinkedIn daily cap reached; {len(queries) - i} queries deferred")
            break
        if i:
            polite_pause(*pause)
        params = {"keywords": q["keywords"], "location": q.get("location", ""),
                  "f_TPR": "r86400",         # posted in the last 24h: only fresh roles
                  "start": 0}
        if q.get("remote"):
            params["f_WT"] = "2"
        try:
            r = http.get(GUEST_URL, params=params, timeout=25)
        except Exception as e:
            notes.append(f"LinkedIn '{q['keywords']}': {type(e).__name__}")
            continue
        if r.status_code in (429, 999):
            # Throttled. Stop for today rather than push; this protects the IP's standing.
            notes.append(f"LinkedIn throttled (HTTP {r.status_code}); stopped for today")
            break
        if r.status_code != 200:
            notes.append(f"LinkedIn '{q['keywords']}': HTTP {r.status_code}")
            continue
        jobs += parse_guest_html(r.text)
    return jobs, notes


def parse_guest_html(text: str) -> list[Job]:
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for card in soup.select("li"):
        title = card.select_one(".base-search-card__title")
        if not title:
            continue
        company = card.select_one(".base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        link = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
        when = card.select_one("time")
        href = (link.get("href") if link else "") or ""
        m = JOB_ID.search(href)
        url = f"https://www.linkedin.com/jobs/view/{m.group(1)}/" if m else href.split("?")[0]
        loc_s = loc.get_text(strip=True) if loc else ""
        out.append(Job(title=title.get_text(strip=True),
                       company=company.get_text(strip=True) if company else "",
                       location=loc_s, url=url, source="linkedin",
                       posted_at=when.get("datetime") if when else None,
                       remote="remote" in loc_s.lower()))
    return out


# ---------------- job-alert emails ----------------
def alert_emails(host: str, user: str, password: str, days: int = 2,
                 sender: str = "jobalerts-noreply@linkedin.com",
                 mailbox: str = "INBOX") -> list[Job]:
    """Read-only IMAP fetch. With Gmail, use an App Password, not your real password."""
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, password)
    except Exception as e:
        raise SourceError(f"IMAP login failed: {type(e).__name__}") from e
    try:
        M.select(mailbox, readonly=True)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f'(FROM "{sender}" SINCE {since})')
        jobs: list[Job] = []
        for num in (data[0].split() if data and data[0] else []):
            typ, msg_data = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            jobs += parse_alert_email(msg)
        return jobs
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _email_html(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


_SKIP = {"view job", "apply", "see all jobs", "easy apply", "view", "jobs"}


def parse_alert_email(msg: email.message.Message) -> list[Job]:
    """Pull job cards out of a LinkedIn alert email. Layout-tolerant: anchors on the
    /jobs/view/<id> links and reads the text block around each one."""
    html_body = _email_html(msg)
    if not html_body:
        return []
    soup = BeautifulSoup(html_body, "html.parser")
    seen: dict[str, Job] = {}
    for a in soup.find_all("a", href=True):
        m = JOB_ID.search(a["href"])
        if not m:
            continue
        jid = m.group(1)
        text = a.get_text(" ", strip=True)
        if jid in seen or not text or text.lower() in _SKIP:
            continue
        # Walk up to the card container and read its lines: title, company · location
        box = a
        for _ in range(4):
            if box.parent is None:
                break
            box = box.parent
            lines = [s for s in box.stripped_strings]
            if len(lines) >= 3:
                break
        lines = [s for s in box.stripped_strings if s.lower() not in _SKIP]
        title = text
        company, location = "", ""
        try:
            idx = lines.index(text)
            rest = lines[idx + 1: idx + 4]
        except ValueError:
            rest = lines[1:4]
        for line in rest:
            if "·" in line:
                company, _, location = (p.strip() for p in line.partition("·"))
                break
        if not company and rest:
            company = rest[0]
            location = rest[1] if len(rest) > 1 else ""
        seen[jid] = Job(title=title, company=company, location=location,
                        url=f"https://www.linkedin.com/jobs/view/{jid}/",
                        source="linkedin-alert",
                        remote="remote" in location.lower())
    return list(seen.values())


def subject(msg) -> str:
    return str(make_header(decode_header(msg.get("Subject", ""))))
