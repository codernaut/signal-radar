# Signal Radar

A daily, LLM-ranked digest of the senior roles worth applying for and the public conversations
worth joining. It runs on GitHub Actions and arrives by email at 08:17 Pakistan time.

```
 sources (public APIs)          stage 1: rules              stage 2: Claude            delivery
 ─────────────────────          ───────────────             ───────────────            ────────
 Company ATS (Greenhouse,  ┐    seniority gate         ┐    fit score 0-100        ┐
   Lever, Ashby, Workday)  │    function excludes      │    track (lead/technical) │   HTML email
 Remotive, RemoteOK        ├──► location / visa rules  ├──► "lead with" angle      ├─► + run notes
 LinkedIn public jobs (≤3) │    domain + stack score   │    risk flags             │   (state in
 LinkedIn alert emails     ┘    dedupe, hide seen      ┘    comment drafts         ┘    Actions cache)
 HN, Reddit, GitHub, OSDU GitLab ─────────────────────────► weekly post draft (Mon)
```

## LinkedIn: how it stays account-safe

| Need | How | Account risk |
|---|---|---|
| LinkedIn jobs | Your own **job-alert emails**, read over IMAP | None: no LinkedIn traffic |
| More LinkedIn jobs | **Logged-out** public job search, max **3 requests/day** (persisted cap), 25–70 s random pauses, last-24h only, stops on first throttle | None to your account (never signed in) |
| LinkedIn posts | One-click "last 24h" topic searches in the digest; you open them yourself | None |

It never signs in as you or reads your feed. LinkedIn restricts accounts based on behavioural
signals, not only volume, so even a few automated logged-in requests a day is a real risk to the
account you need most right now.

GitHub Actions IPs are sometimes throttled by LinkedIn. The digest reports it and carries on;
the alert emails remain the dependable LinkedIn source.

## Setup (about 15 minutes)

1. **Create a private repo** and push this folder. Keep it private while you're employed: a
   public job-search repo is visible to your current employer. A public demo version can come later.
2. **LinkedIn job alerts.** In LinkedIn, create 3–5 daily alerts, for example "Director of
   Engineering" in UAE, "Head of Engineering" in Saudi Arabia, and "Engineering Manager" remote
   from Pakistan.
3. **Gmail App Password** (Google Account → Security → 2-Step Verification → App passwords).
   One password works for both SMTP (sending) and IMAP (reading alerts).
4. **Anthropic API key** from console.anthropic.com. Expect roughly a few US cents a day.
5. **Repo → Settings → Secrets and variables → Actions → New repository secret:**

   | Secret | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | your key |
   | `RADAR_PROFILE` | paste `profile.example.md`, edited to taste |
   | `SMTP_USER` / `IMAP_USER` | your Gmail address |
   | `SMTP_PASSWORD` / `IMAP_PASSWORD` | the App Password |
   | `DIGEST_TO` | where to send the digest (optional; defaults to `SMTP_USER`) |

6. **Actions tab → signal-radar → Run workflow** for the first digest. After that it runs daily.

Nothing is required except the workflow itself. Without the API key it ranks by keywords. Without
SMTP it writes `digest.html`, which is kept as a run artifact in private repos.

## Tuning

Everything lives in `config.yaml`:
- `strategy`: the positioning Claude scores against. This is the most important setting.
- `ats.companies`: add target employers' career sites (instructions inline).
- `linkedin.public_jobs.queries`: a rotating pool; 3 run per day, so each runs every ~5 days.
- `filters`: title, location and visa rules, plus domain and stack terms.
- `engage`: HN, Reddit, GitHub and OSDU queries, and LinkedIn topic links.

## Run locally

```bash
pip install -r requirements.txt
python -m unittest discover -s tests                  # 15 offline tests
python -m radar --fixtures tests/fixtures --no-email  # sample digest from saved payloads
python -m radar --no-email                            # live run, writes digest.html
```

## Design notes

- **Fail-soft sources.** Each company and feed is isolated; a broken one becomes a run note, not a failed run.
- **Cross-source dedupe.** The same role on LinkedIn and the company ATS collapses to one entry (company + title + city fingerprint).
- **No personal data in git.** The profile, credentials and seen-state live in Secrets and the Actions cache.
- **Cost control.** The rules stage cuts hundreds of listings to about 30 before any LLM call, and Claude reads them in batches of 12.
