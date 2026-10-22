"""Company career sites via their public applicant-tracking-system APIs.

These are the endpoints the companies' own careers pages call. They are public,
unauthenticated and intended for syndication, so they carry no account risk.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..http import SourceError, session
from ..models import Job

log = logging.getLogger("radar.ats")

_TAGS = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", html.unescape(s or ""))).strip()


# ---------- Greenhouse ----------
def greenhouse(board: str, company: str | None = None, http=None, region: str = "") -> list[Job]:
    http = http or session()
    host = "boards-api.eu.greenhouse.io" if region == "eu" else "boards-api.greenhouse.io"
    r = http.get(f"https://{host}/v1/boards/{board}/jobs",
                 params={"content": "true"}, timeout=20)
    if r.status_code == 404:
        raise SourceError(f"greenhouse board '{board}' not found")
    r.raise_for_status()
    return parse_greenhouse(r.json(), company or board)


def parse_greenhouse(data: dict, company: str) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(Job(
            title=j.get("title", ""), company=company, location=loc,
            url=j.get("absolute_url", ""), source="greenhouse",
            description=strip_html(j.get("content", ""))[:4000],
            posted_at=j.get("updated_at"), remote="remote" in loc.lower()))
    return out


# ---------- Lever ----------
def lever(site: str, company: str | None = None, http=None) -> list[Job]:
    http = http or session()
    r = http.get(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"}, timeout=20)
    if r.status_code == 404:
        raise SourceError(f"lever site '{site}' not found")
    r.raise_for_status()
    return parse_lever(r.json(), company or site)


def parse_lever(data: list, company: str) -> list[Job]:
    out = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ", ".join(cats.get("allLocations", []) or [])
        created = j.get("createdAt")
        posted = (datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
                  if isinstance(created, (int, float)) else None)
        out.append(Job(
            title=j.get("text", ""), company=company, location=loc,
            url=j.get("hostedUrl", ""), source="lever",
            description=(j.get("descriptionPlain") or "")[:4000], posted_at=posted,
            remote=(j.get("workplaceType") == "remote") or "remote" in loc.lower()))
    return out


# ---------- Ashby ----------
def ashby(board: str, company: str | None = None, http=None) -> list[Job]:
    http = http or session()
    r = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=20)
    if r.status_code == 404:
        raise SourceError(f"ashby board '{board}' not found")
    r.raise_for_status()
    return parse_ashby(r.json(), company or board)


def parse_ashby(data: dict, company: str) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        out.append(Job(
            title=j.get("title", ""), company=company, location=loc,
            url=j.get("jobUrl", ""), source="ashby",
            description=(j.get("descriptionPlain") or "")[:4000],
            posted_at=j.get("publishedAt"), remote=bool(j.get("isRemote"))))
    return out


# ---------- Workday ----------
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def workday_endpoint(careers_url: str) -> tuple[str, str]:
    """Turn a Workday careers URL into its JSON search endpoint.

    https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers -> (endpoint, public base)
    """
    u = urlparse(careers_url)
    if "myworkdayjobs.com" not in u.netloc:
        raise SourceError(f"not a Workday URL: {careers_url}")
    tenant = u.netloc.split(".")[0]
    segs = [s for s in u.path.split("/") if s and not _LOCALE.match(s)]
    if not segs:
        raise SourceError(f"no Workday site name in {careers_url}")
    site = segs[0]
    return (f"https://{u.netloc}/wday/cxs/{tenant}/{site}/jobs", f"https://{u.netloc}/{site}")


def workday(careers_url: str, company: str, searches: list[str], http=None,
            per_search: int = 20) -> list[Job]:
    http = http or session(browser_like=True)
    endpoint, base = workday_endpoint(careers_url)
    out: list[Job] = []
    for q in searches:
        r = http.post(endpoint, json={"appliedFacets": {}, "limit": per_search,
                                      "offset": 0, "searchText": q}, timeout=25)
        if r.status_code in (400, 404, 422):
            raise SourceError(f"workday endpoint rejected for {company}: HTTP {r.status_code}")
        r.raise_for_status()
        out.extend(parse_workday(r.json(), company, base))
    return out


def parse_workday(data: dict, company: str, base: str) -> list[Job]:
    out = []
    for j in data.get("jobPostings", []):
        loc = j.get("locationsText", "") or ""
        out.append(Job(
            title=j.get("title", ""), company=company, location=loc,
            url=base + (j.get("externalPath") or ""), source="workday",
            posted_at=j.get("postedOn"), remote="remote" in loc.lower()))
    return out


def fetch_all(cfg: dict, http=None) -> tuple[list[Job], list[str]]:
    """Run every configured company. Returns (jobs, errors)."""
    jobs: list[Job] = []
    errors: list[str] = []
    searches = cfg.get("workday_searches", ["engineering manager", "director engineering"])
    for c in cfg.get("companies", []):
        name = c.get("name") or c.get("board") or c.get("site")
        try:
            kind = c["ats"]
            if kind == "greenhouse":
                jobs += greenhouse(c["board"], name, http, c.get("region", ""))
            elif kind == "lever":
                jobs += lever(c["site"], name, http)
            elif kind == "ashby":
                jobs += ashby(c["board"], name, http)
            elif kind == "workday":
                jobs += workday(c["url"], name, c.get("searches", searches), http)
            else:
                errors.append(f"{name}: unknown ats '{kind}'")
        except SourceError as e:
            errors.append(f"{name}: {e}")
        except Exception as e:  # network, JSON, etc. One bad company never sinks the run.
            errors.append(f"{name}: {type(e).__name__}: {e}")
    return jobs, errors
