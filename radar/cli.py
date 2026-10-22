"""Entry point: python -m radar [--no-email] [--fixtures tests/fixtures]"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import yaml

from . import digest, notify, scoring
from .models import Job, Post
from .sources import ats, boards, engage, linkedin
from .state import State

log = logging.getLogger("radar")


def load_profile(cfg: dict) -> str:
    if os.getenv("RADAR_PROFILE"):
        return os.environ["RADAR_PROFILE"]
    for p in (cfg.get("profile_file", "profile.md"), "profile.example.md"):
        if Path(p).exists():
            return Path(p).read_text()
    return ""


def gather_live(cfg: dict, state: State) -> tuple[list[Job], list[Post], list[str]]:
    notes: list[str] = []
    jobs, errs = ats.fetch_all(cfg.get("ats", {}))
    notes += errs

    b = cfg.get("boards", {})
    for name, fn in (("remotive", lambda: boards.remotive(b.get("remotive_searches", []))),
                     ("remoteok", lambda: boards.remoteok() if b.get("remoteok") else [])):
        try:
            jobs += fn()
        except Exception as e:
            notes.append(f"{name}: {type(e).__name__}")

    li = cfg.get("linkedin", {})
    if li.get("public_jobs", {}).get("enabled"):
        pj = li["public_jobs"]
        per_day = int(pj.get("max_requests_per_day", 3))
        budget = linkedin.DailyBudget(state.data, per_day)
        got, n = linkedin.public_jobs(linkedin.rotate(pj.get("queries", []), per_day), budget,
                                      pause=tuple(pj.get("pause_seconds", [25, 70])))
        jobs += got
        notes += n
    if li.get("alert_emails", {}).get("enabled") and os.getenv("IMAP_PASSWORD"):
        ae = li["alert_emails"]
        try:
            jobs += linkedin.alert_emails(os.getenv("IMAP_HOST", "imap.gmail.com"),
                                          os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"],
                                          days=int(ae.get("days", 2)))
        except Exception as e:
            notes.append(f"linkedin alerts: {e}")

    posts, errs = engage.fetch_all(cfg.get("engage", {}))
    notes += errs
    return jobs, posts, notes


def gather_fixtures(d: Path) -> tuple[list[Job], list[Post], list[str]]:
    """Offline run from saved API payloads: for tests and demos."""
    rd = lambda n: json.loads((d / n).read_text())
    jobs = (ats.parse_greenhouse(rd("greenhouse.json"), "Cognite")
            + ats.parse_lever(rd("lever.json"), "Kongsberg Digital")
            + ats.parse_workday(rd("workday.json"), "Baker Hughes",
                                "https://bakerhughes.wd5.myworkdayjobs.com/BakerHughes")
            + boards.parse_remotive(rd("remotive.json"))
            + linkedin.parse_guest_html((d / "linkedin_guest.html").read_text()))
    posts = (engage.parse_hn(rd("hn.json")) + engage.parse_github(rd("github.json"))
             + engage.parse_gitlab(rd("gitlab.json")))
    return jobs, posts, ["fixture mode: offline sample data"]


def dedupe(items):
    out, seen = [], set()
    for i in items:
        if i.id not in seen:
            seen.add(i.id)
            out.append(i)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default=".radar/state.json")
    ap.add_argument("--out", default="digest.html")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--fixtures", help="run offline from saved payloads in this folder")
    ap.add_argument("--show-seen", action="store_true", help="don't hide previously shown items")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = yaml.safe_load(Path(a.config).read_text())
    state = State(a.state)
    profile = load_profile(cfg)

    jobs, posts, notes = (gather_fixtures(Path(a.fixtures)) if a.fixtures
                          else gather_live(cfg, state))
    stats = {"jobs_scanned": len(jobs), "posts_scanned": len(posts)}
    jobs, posts = dedupe(jobs), dedupe(posts)
    if not a.show_seen:
        jobs, posts = state.unseen(jobs, "seen_jobs"), state.unseen(posts, "seen_posts")

    sc = cfg.get("scoring", {})
    shortlist = scoring.prefilter_jobs(jobs, cfg["filters"], int(sc.get("llm_shortlist", 30)))
    candidates = scoring.prefilter_posts(posts, cfg["engage"].get("topic_terms", []),
                                         int(sc.get("post_shortlist", 20)))

    claude = scoring.Claude(sc.get("model", "claude-sonnet-5-5"))
    weekly = ""
    if claude.enabled and not a.fixtures:
        notes += scoring.rerank_jobs(shortlist, claude, profile, cfg.get("strategy", ""))
        notes += scoring.rerank_posts(candidates, claude, profile)
        if date.today().weekday() == int(cfg.get("weekly_post_day", 0)):
            try:
                weekly = scoring.weekly_post(claude, profile, candidates, cfg.get("themes", []))
            except Exception as e:
                notes.append(f"weekly post: {e}")
    elif not a.fixtures:
        notes.append("ANTHROPIC_API_KEY not set: ranked by keywords only, no comment drafts")

    # Claude's 0-100 is a judgement; the keyword score is a coarse proxy, so it gets its own bar
    llm_ran = claude.enabled and not a.fixtures
    min_score = float(sc.get("min_job_score", 55) if llm_ran else sc.get("min_keyword_score", 40))
    top_jobs = sorted([j for j in shortlist if j.score >= min_score],
                      key=lambda j: j.score, reverse=True)[: int(sc.get("max_jobs", 10))]
    top_posts = sorted([p for p in candidates if p.score > 0],
                       key=lambda p: p.score, reverse=True)[: int(sc.get("max_posts", 6))]
    links = engage.linkedin_search_links(cfg["engage"].get("linkedin_topics", []))

    html = digest.render(top_jobs, top_posts, links, weekly, notes, stats)
    Path(a.out).write_text(html)
    log.info("digest written: %s (%d jobs, %d posts)", a.out, len(top_jobs), len(top_posts))

    if not a.no_email and os.getenv("SMTP_PASSWORD"):
        notify.send(html, f"Signal Radar · {len(top_jobs)} roles · {date.today():%d %b}",
                    os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465")),
                    os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
                    os.getenv("DIGEST_TO", os.environ["SMTP_USER"]))
        log.info("digest emailed")

    if not a.fixtures:
        state.mark(top_jobs, "seen_jobs")
        state.mark(top_posts, "seen_posts")
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
