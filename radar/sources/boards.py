"""Remote job boards with public APIs (Remotive, RemoteOK)."""
from __future__ import annotations

from ..http import session
from ..models import Job
from .ats import strip_html


def remotive(searches: list[str], http=None) -> list[Job]:
    http = http or session()
    out: list[Job] = []
    for q in searches:
        r = http.get("https://remotive.com/api/remote-jobs",
                     params={"search": q, "limit": 50}, timeout=25)
        r.raise_for_status()
        out += parse_remotive(r.json())
    return out


def parse_remotive(data: dict) -> list[Job]:
    return [Job(title=j.get("title", ""), company=j.get("company_name", ""),
                location=j.get("candidate_required_location", "") or "Remote",
                url=j.get("url", ""), source="remotive",
                description=strip_html(j.get("description", ""))[:4000],
                posted_at=j.get("publication_date"), remote=True)
            for j in data.get("jobs", [])]


def remoteok(http=None) -> list[Job]:
    """One request returns the whole current board; filtering happens downstream."""
    http = http or session()
    r = http.get("https://remoteok.com/api", timeout=25)
    r.raise_for_status()
    return parse_remoteok(r.json())


def parse_remoteok(data: list) -> list[Job]:
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:   # first element is a legal notice
            continue
        out.append(Job(title=j.get("position", ""), company=j.get("company", ""),
                       location=j.get("location", "") or "Remote",
                       url=j.get("url", ""), source="remoteok",
                       description=strip_html(j.get("description", ""))[:4000],
                       posted_at=j.get("date"), remote=True))
    return out
