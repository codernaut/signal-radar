"""Public conversations worth joining: places where a thoughtful comment or a merged
contribution is visible to hiring managers and peers.

- Hacker News (Algolia API)
- Reddit subreddit search (public JSON; often blocked from cloud IPs, fails quietly)
- GitHub issues/discussions in the projects you know deeply (Quarkus, Keycloak, Teiid...)
- OSDU issues on the Open Group community GitLab

LinkedIn posts are not fetched. linkedin_search_links() builds one-click searches
for the last 24h of posts on your topics, which you open in your own browser.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from ..http import session
from ..models import Post
from .ats import strip_html


def hackernews(queries: list[str], days: int = 3, min_points: int = 5, http=None) -> list[Post]:
    http = http or session()
    since = int(time.time()) - days * 86400
    out: list[Post] = []
    for q in queries:
        r = http.get("https://hn.algolia.com/api/v1/search_by_date", timeout=20, params={
            "query": q, "tags": "story",
            "numericFilters": f"created_at_i>{since},points>{min_points}"})
        r.raise_for_status()
        out += parse_hn(r.json())
    return out


def parse_hn(data: dict) -> list[Post]:
    out = []
    for h in data.get("hits", []):
        oid = h.get("objectID")
        out.append(Post(title=h.get("title") or "", source="hackernews",
                        url=f"https://news.ycombinator.com/item?id={oid}",
                        summary=(h.get("url") or "")[:300], author=h.get("author", ""),
                        created_at=h.get("created_at"),
                        engagement=(h.get("points") or 0) + (h.get("num_comments") or 0)))
    return out


def reddit(subreddits: list[str], queries: list[str], http=None) -> list[Post]:
    http = http or session()
    subs = "+".join(subreddits)
    out: list[Post] = []
    for q in queries:
        r = http.get(f"https://www.reddit.com/r/{subs}/search.json", timeout=20, params={
            "q": q, "restrict_sr": 1, "sort": "new", "t": "week", "limit": 25})
        r.raise_for_status()
        out += parse_reddit(r.json())
    return out


def parse_reddit(data: dict) -> list[Post]:
    out = []
    for c in (data.get("data") or {}).get("children", []):
        d = c.get("data") or {}
        created = d.get("created_utc")
        out.append(Post(title=d.get("title", ""), source=f"reddit/r/{d.get('subreddit', '')}",
                        url="https://www.reddit.com" + d.get("permalink", ""),
                        summary=(d.get("selftext") or "")[:600], author=d.get("author", ""),
                        created_at=(datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
                                    if created else None),
                        engagement=(d.get("score") or 0) + (d.get("num_comments") or 0)))
    return out


def github_issues(queries: list[str], http=None) -> list[Post]:
    """queries are GitHub search syntax, e.g. 'repo:keycloak/keycloak is:open label:help-wanted'."""
    http = http or session()
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out: list[Post] = []
    for q in queries:
        r = http.get("https://api.github.com/search/issues", headers=headers, timeout=20,
                     params={"q": q, "sort": "updated", "order": "desc", "per_page": 10})
        r.raise_for_status()
        out += parse_github(r.json())
    return out


def parse_github(data: dict) -> list[Post]:
    out = []
    for i in data.get("items", []):
        repo = "/".join((i.get("repository_url") or "").split("/")[-2:])
        out.append(Post(title=i.get("title", ""), source=f"github/{repo}", url=i.get("html_url", ""),
                        summary=strip_html(i.get("body") or "")[:600],
                        author=(i.get("user") or {}).get("login", ""),
                        created_at=i.get("updated_at"), engagement=i.get("comments") or 0,
                        kind="issue"))
    return out


def osdu_gitlab(searches: list[str], group: str = "osdu/platform",
                host: str = "https://community.opengroup.org", http=None) -> list[Post]:
    http = http or session()
    out: list[Post] = []
    for s in searches:
        r = http.get(f"{host}/api/v4/groups/{quote(group, safe='')}/issues", timeout=25, params={
            "state": "opened", "order_by": "updated_at", "per_page": 15, "search": s})
        r.raise_for_status()
        out += parse_gitlab(r.json())
    return out


def parse_gitlab(data: list) -> list[Post]:
    return [Post(title=i.get("title", ""), source="osdu-gitlab", url=i.get("web_url", ""),
                 summary=(i.get("description") or "")[:600],
                 author=(i.get("author") or {}).get("username", ""),
                 created_at=i.get("updated_at"),
                 engagement=(i.get("user_notes_count") or 0) + (i.get("upvotes") or 0),
                 kind="issue")
            for i in data]


def linkedin_search_links(topics: list[str]) -> list[dict]:
    """One-click LinkedIn searches for fresh posts. Opened by you, in your browser."""
    links = []
    for t in topics:
        qs = urlencode({"keywords": t, "datePosted": '"past-24h"', "sortBy": '"date_posted"'})
        links.append({"topic": t,
                      "url": f"https://www.linkedin.com/search/results/content/?{qs}"})
    return links


def fetch_all(cfg: dict, http=None) -> tuple[list[Post], list[str]]:
    posts: list[Post] = []
    errors: list[str] = []
    runs = [
        ("hackernews", lambda: hackernews(cfg.get("hn_queries", []), http=http)),
        ("reddit", lambda: reddit(cfg.get("subreddits", []), cfg.get("reddit_queries", []), http=http)),
        ("github", lambda: github_issues(cfg.get("github_queries", []), http=http)),
        ("osdu", lambda: osdu_gitlab(cfg.get("osdu_searches", []), http=http)),
    ]
    for name, fn in runs:
        try:
            posts += fn()
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    return posts, errors
