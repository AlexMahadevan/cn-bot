"""Pre-submission URL liveness gate.

X's AI Note Writer earn-in requires that at least 95% of a writer's 50 most
recently submitted notes cite a URL that resolves to HTTP 200 after redirects
(Miller et al. 2026, arXiv:2609.40067, "earn-in"). The same UrlValidity
evaluator keeps scoring notes after admission.

Nothing upstream guaranteed that. Evidence URLs come from the Fact Check Tools
API and from web-search results, and the evidence-text fetch is best-effort: a
403 from a bot-blocking site (Bloomberg, WSJ, most paywalls) was only a
warning. On 2026-10-19 a dry run drafted a note citing a bloomberg.com article
that 403'd. One such note in the 50-note window is 2% of a 5% budget.

Policy: GET the URL with redirects and a browser-like UA, without downloading
the body. Pass only on a final 200. Fail closed on any other status or on a
network error after one retry; a lost note is cheaper than a UrlValidity miss.
x.com status URLs pass through: X's own checker is authoritative for its own
domain, and x.com serves a JS shell to non-browser clients anyway.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 15
PASSTHROUGH_HOSTS = {
    "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com",
}


@dataclass
class LivenessResult:
    ok: bool
    status: Optional[int]
    final_url: Optional[str]
    detail: str


def check_url_live(url: str, *, retries: int = 1) -> LivenessResult:
    """Return whether `url` resolves to HTTP 200 (after redirects)."""
    host = (urlparse(url).hostname or "").lower()
    if host in PASSTHROUGH_HOSTS:
        return LivenessResult(True, None, url, "x.com passthrough")

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=TIMEOUT,
                allow_redirects=True,
                stream=True,  # headers only; don't pull the body
            )
            try:
                status, final_url = resp.status_code, resp.url
            finally:
                resp.close()
            if status == 200:
                return LivenessResult(True, status, final_url, "HTTP 200")
            return LivenessResult(False, status, final_url, f"HTTP {status}")
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(2)
    return LivenessResult(False, None, None, f"{type(last_exc).__name__}: {last_exc}")
