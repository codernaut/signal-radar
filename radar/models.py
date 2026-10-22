"""Core data types shared by every source, the scorer and the digest."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


def _fingerprint(*parts: str) -> str:
    norm = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    description: str = ""
    posted_at: Optional[str] = None
    remote: bool = False
    # filled by scoring
    score: float = 0.0
    track: str = ""            # "leadership" | "technical" | ""
    why: str = ""
    apply_angle: str = ""
    flags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        # Same role cross-posted on LinkedIn and the company ATS dedupes to one entry.
        return _fingerprint(self.company, self.title, self.location.split(",")[0])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


@dataclass
class Post:
    """Something public you can comment on, answer or contribute to."""
    title: str
    url: str
    source: str
    summary: str = ""
    author: str = ""
    created_at: Optional[str] = None
    engagement: int = 0          # points / comments / reactions, source-specific
    kind: str = "discussion"     # discussion | issue | article
    # filled by scoring
    score: float = 0.0
    comment_draft: str = ""
    why: str = ""

    @property
    def id(self) -> str:
        return _fingerprint(self.source, self.url)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d
