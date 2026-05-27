"""X-as-source evidence tier.

When a post quotes, screenshots, or reacts to another X post (or links to
one via t.co), the cleanest correction often cites that linked post
directly — what the politician actually said, the original photo
before it was edited, the reply that clarifies the claim.

Alexios's data showed humans cite X 2.5x more often than AI bots. Most
existing bots cluster around Grok because Grok can search X freely.
We don't have that, but we DO have the syndication endpoint, and most
of these citations don't require search — just resolving the t.co URLs
the post already contains.

This tier adds linked X posts to the evidence pool. The note writer
can then cite x.com/{user}/status/{id} as the correction URL.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import List

from data_models import FactCheckEvidence

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; PoynterCNBot/1.0; +https://www.poynter.org)"
SYND = "https://cdn.syndication.twimg.com/tweet-result"

# Match t.co short links (X's URL shortener) and direct x.com / twitter.com status URLs.
TCO_RE = re.compile(r"https?://t\.co/[A-Za-z0-9]+", re.IGNORECASE)
STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/(?:i/web/status|[A-Za-z0-9_]+/status)/(\d+)",
    re.IGNORECASE,
)


def _resolve_tco(short_url: str) -> str | None:
    """Follow a t.co short URL to its destination. Returns the final URL or None."""
    try:
        req = urllib.request.Request(short_url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.url
    except Exception as e:
        logger.debug("Failed to resolve %s: %s", short_url, e)
        return None


def _extract_x_post_ids(post_text: str) -> List[str]:
    """Find every X status ID linked from the post text.

    Handles direct x.com/twitter.com status URLs and t.co short links.
    """
    ids: list[str] = []
    seen: set[str] = set()

    # Direct status URLs in the text
    for m in STATUS_URL_RE.finditer(post_text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))

    # t.co links — follow each to see if it points to a status URL
    for m in TCO_RE.finditer(post_text):
        resolved = _resolve_tco(m.group(0))
        if not resolved:
            continue
        sm = STATUS_URL_RE.match(resolved)
        if sm and sm.group(1) not in seen:
            seen.add(sm.group(1))
            ids.append(sm.group(1))

    return ids


def _fetch_tweet(tweet_id: str) -> dict | None:
    """Fetch a single tweet via X's syndication endpoint."""
    url = f"{SYND}?id={tweet_id}&token=4"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("Failed to fetch tweet %s: %s", tweet_id, e)
        return None


def gather_for_post(post_text: str, *, max_results: int = 3) -> List[FactCheckEvidence]:
    """Return any tweets linked from the post text as evidence candidates."""
    ids = _extract_x_post_ids(post_text)
    if not ids:
        return []

    results: List[FactCheckEvidence] = []
    for tid in ids[:max_results]:
        tw = _fetch_tweet(tid)
        if not tw or not tw.get("text"):
            continue
        author = tw.get("user", {}) or {}
        screen_name = author.get("screen_name")
        canonical_url = f"https://x.com/{screen_name}/status/{tid}" if screen_name else f"https://x.com/i/web/status/{tid}"

        results.append(
            FactCheckEvidence(
                claim_text=tw["text"][:500],
                claimant=author.get("name"),
                claim_date=tw.get("created_at"),
                publisher_name=f"X (@{screen_name})" if screen_name else "X",
                publisher_site="x.com",
                review_url=canonical_url,
                review_title=f"Linked X post by @{screen_name}" if screen_name else "Linked X post",
                review_date=tw.get("created_at"),
                rating=None,
                snippet=tw["text"][:500],
                evidence_tier="x_post",
            )
        )

    logger.info("x_post evidence: %d linked X posts found from %d candidate IDs", len(results), len(ids))
    return results
