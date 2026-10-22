"""Tiny JSON state: what you've already been shown, plus the LinkedIn daily budget.
In GitHub Actions it persists via actions/cache, so nothing personal is committed."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


class State:
    def __init__(self, path: str):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        self.data.setdefault("seen_jobs", {})
        self.data.setdefault("seen_posts", {})

    def unseen(self, items, bucket: str):
        seen = self.data[bucket]
        return [i for i in items if i.id not in seen]

    def mark(self, items, bucket: str) -> None:
        today = date.today().isoformat()
        for i in items:
            self.data[bucket][i.id] = today

    def prune(self, days: int = 45) -> None:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        for b in ("seen_jobs", "seen_posts"):
            self.data[b] = {k: v for k, v in self.data[b].items() if v >= cutoff}

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))
