"""Polite HTTP client: one shared session, timeouts, limited retries, honest user agent."""
from __future__ import annotations

import logging
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("radar.http")

UA = "Mozilla/5.0 (compatible; signal-radar/1.0; personal job digest)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def session(browser_like: bool = False) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=2, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": BROWSER_UA if browser_like else UA,
                      "Accept-Language": "en-US,en;q=0.9"})
    return s


def polite_pause(min_s: float, max_s: float) -> None:
    """Human-scale jitter between requests to the same host."""
    if max_s <= 0:
        return
    time.sleep(random.uniform(min_s, max_s))


class SourceError(Exception):
    """Raised by a source when it cannot run; the pipeline records it and carries on."""
