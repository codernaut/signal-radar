"""Two-stage ranking.

Stage 1 (deterministic, free): hard filters on seniority, function and location, then a
keyword score for domain and stack fit. Cuts hundreds of listings to a shortlist.

Stage 2 (Claude, optional): reads the shortlist against your profile and positioning,
scores 0-100, explains the fit, suggests the angle to lead with, and flags risks.
Without an API key the pipeline still works on stage-1 scores alone.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from .models import Job, Post

log = logging.getLogger("radar.scoring")


def _rx(words: list[str]) -> re.Pattern | None:
    words = [w for w in words if w]
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def _hits(rx: re.Pattern | None, text: str) -> set[str]:
    return {m.lower() for m in rx.findall(text)} if rx else set()


# ---------------- stage 1: jobs ----------------
class JobFilter:
    def __init__(self, f: dict):
        self.seniority = _rx(f.get("title_include", []))
        self.exclude = _rx(f.get("title_exclude", []))
        self.loc_ok = _rx(f.get("locations_allowed", []))
        self.loc_pref = _rx(f.get("locations_preferred", []))
        self.remote_block = _rx(f.get("remote_excluded_regions", []))
        self.domain = _rx(f.get("domain_terms", []))
        self.stack = _rx(f.get("stack_terms", []))

    def location_ok(self, j: Job) -> bool:
        loc = j.location or ""
        if j.remote or re.search(r"\bremote\b|anywhere|worldwide", loc, re.I):
            # Remote is fine unless it's explicitly limited to a region you can't work from
            return not (self.remote_block and self.remote_block.search(loc))
        return bool(self.loc_ok and self.loc_ok.search(loc))

    def keep(self, j: Job) -> bool:
        if not (self.seniority and self.seniority.search(j.title)):
            return False
        if self.exclude and self.exclude.search(j.title):
            return False
        return self.location_ok(j)

    def score(self, j: Job) -> float:
        text = f"{j.title} {j.company} {j.description}"
        domain = _hits(self.domain, text)
        stack = _hits(self.stack, text)
        s = 30.0                                   # passed seniority gate
        s += min(40.0, 10.0 * len(domain))
        s += min(20.0, 5.0 * len(stack))
        if self.loc_pref and self.loc_pref.search(j.location or ""):
            s += 10.0
        j.flags = sorted(domain | stack)[:8]
        return s


def prefilter_jobs(jobs: list[Job], f: dict, limit: int) -> list[Job]:
    jf = JobFilter(f)
    kept = []
    for j in jobs:
        if jf.keep(j):
            j.score = jf.score(j)
            kept.append(j)
    kept.sort(key=lambda j: j.score, reverse=True)
    return kept[:limit]


# ---------------- stage 1: posts ----------------
def prefilter_posts(posts: list[Post], topic_terms: list[str], limit: int) -> list[Post]:
    rx = _rx(topic_terms)
    kept = []
    for p in posts:
        hits = _hits(rx, f"{p.title} {p.summary}")
        if not hits and p.kind != "issue":     # issues come from targeted queries already
            continue
        p.score = 10.0 * len(hits) + min(30.0, p.engagement / 5.0)
        kept.append(p)
    kept.sort(key=lambda p: p.score, reverse=True)
    return kept[:limit]


# ---------------- stage 2: Claude ----------------
class Claude:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.key = api_key or os.getenv("ANTHROPIC_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def ask(self, system: str, user: str, max_tokens: int = 4000) -> str:
        r = requests.post(self.URL, timeout=180, headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]})
        if r.status_code != 200:
            raise RuntimeError(f"Claude API HTTP {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []))


def extract_json(text: str):
    """Model output -> JSON, tolerating code fences or a sentence around it."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for open_c, close_c in (("[", "]"), ("{", "}")):
        a, b = text.find(open_c), text.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")


JOB_SYSTEM = """You are a blunt executive recruiter for senior engineering leaders in energy tech \
and enterprise data platforms. You score job listings for ONE candidate and you are stingy: \
90+ means "drop everything and apply today", 70-89 strong, 50-69 plausible stretch, below 50 skip.
Weigh: (1) would this hiring manager realistically shortlist this candidate, given their actual \
background, (2) seniority match, (3) location/relocation feasibility for someone relocating WITH \
FAMILY from Pakistan (Gulf and Asia employers that sponsor family visas are a plus; remote roles \
restricted to US/EU work authorization are a hard negative), (4) leverage of the candidate's rare \
assets: upstream E&P data, OSDU, data virtualization, enterprise IAM, engineering leadership + MBA.
Return ONLY a JSON array, one object per listing:
{"id": str, "score": int, "track": "leadership"|"technical", "why": str (<=30 words), \
"apply_angle": str (<=30 words: the single proof point to lead with), "flags": [str] (risks, <=3)}"""


def rerank_jobs(jobs: list[Job], claude: Claude, profile: str, strategy: str,
                batch: int = 12) -> list[str]:
    errors: list[str] = []
    by_id = {j.id: j for j in jobs}
    for i in range(0, len(jobs), batch):
        chunk = jobs[i:i + batch]
        listing = [{"id": j.id, "title": j.title, "company": j.company, "location": j.location,
                    "remote": j.remote, "source": j.source,
                    "description": (j.description or "")[:1500]} for j in chunk]
        user = (f"CANDIDATE PROFILE:\n{profile}\n\nPOSITIONING STRATEGY:\n{strategy}\n\n"
                f"LISTINGS:\n{json.dumps(listing, ensure_ascii=False)}")
        try:
            for row in extract_json(claude.ask(JOB_SYSTEM, user)):
                j = by_id.get(str(row.get("id")))
                if not j:
                    continue
                j.score = float(row.get("score", j.score))
                j.track = row.get("track", "")
                j.why = row.get("why", "")
                j.apply_angle = row.get("apply_angle", "")
                j.flags = row.get("flags", []) or []
        except Exception as e:
            errors.append(f"job rerank batch {i // batch + 1}: {e}")
    return errors


POST_SYSTEM = """You help a senior engineering leader build a visible public reputation by \
leaving genuinely useful comments. His expertise: upstream oil & gas subsurface data (OpenWorks, \
Petrel, SEG-Y, well logs), OSDU, data virtualization/federation (Teiid), Quarkus, Keycloak/IAM, \
Kubernetes, engineering management at scale, and secure enterprise AI/agentic integration.
For each item decide whether he has something specific and credible to add. Prefer items where \
his domain experience gives a non-obvious angle. Skip pure news with nothing to add.
Draft comments must sound like an experienced practitioner, not a marketer: concrete, 2-4 \
sentences, one specific insight or trade-off from real systems, optionally end with a sharp \
question. Never mention job hunting, never self-promote, no hashtags, no emojis, no flattery.
For GitHub/GitLab issues, the draft is a technical comment or an offer to take on the work.
Return ONLY a JSON array: {"id": str, "score": int 0-100, "why": str (<=20 words), \
"comment_draft": str}. Omit items scoring below 40."""


def rerank_posts(posts: list[Post], claude: Claude, profile: str) -> list[str]:
    if not posts:
        return []
    by_id = {p.id: p for p in posts}
    items = [{"id": p.id, "source": p.source, "kind": p.kind, "title": p.title,
              "summary": p.summary[:500]} for p in posts]
    try:
        rows = extract_json(claude.ask(POST_SYSTEM, f"PROFILE:\n{profile}\n\nITEMS:\n"
                                       + json.dumps(items, ensure_ascii=False), max_tokens=6000))
    except Exception as e:
        return [f"post rerank: {e}"]
    returned = set()
    for row in rows:
        p = by_id.get(str(row.get("id")))
        if p:
            returned.add(p.id)
            p.score = float(row.get("score", 0))
            p.why = row.get("why", "")
            p.comment_draft = row.get("comment_draft", "")
    for p in posts:                      # the model omitted it: not worth engaging
        if p.id not in returned:
            p.score = 0
    return []


WEEKLY_SYSTEM = """Write one original LinkedIn post for a senior engineering leader in \
energy-tech data platforms. 120-200 words. Open with a specific, slightly contrarian observation \
from real delivery experience, not a question or a cliche. One concrete example (a data format, \
an integration trade-off, a team decision). End with a practical takeaway. Plain text, short \
paragraphs, no hashtags except up to 3 at the very end, no emojis, no "I'm excited to". \
Then on a new line starting "WHY:" give one sentence on why this topic, this week."""


def weekly_post(claude: Claude, profile: str, top_posts: list[Post], theme_pool: list[str]) -> str:
    context = "\n".join(f"- {p.title} ({p.source})" for p in top_posts[:8])
    user = (f"PROFILE:\n{profile}\n\nTHIS WEEK'S CONVERSATIONS:\n{context or '(none)'}\n\n"
            f"THEMES HE WANTS TO BE KNOWN FOR:\n" + "\n".join(f"- {t}" for t in theme_pool))
    return claude.ask(WEEKLY_SYSTEM, user, max_tokens=1200).strip()
