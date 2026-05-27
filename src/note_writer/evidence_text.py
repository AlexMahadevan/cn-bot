"""Fetch and extract a fact-check article's actual ruling text.

This eliminates the "Claude embellishes from training data" risk: instead of
showing Claude only a URL slug + rating word, we show the publisher's own
words from the article itself. Claude can quote/paraphrase the article
directly without inventing detail.

Strategy: og:description meta tag is universal and well-curated by editors.
For PolitiFact specifically we also try the "If Your Time is Short" section,
which is even tighter than og:description.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; PoynterCNBot/1.0; +https://www.poynter.org)"
FETCH_TIMEOUT = 12

_OG_DESC_RE = re.compile(
    r"""<meta[^>]+property=["']og:description["'][^>]+content=["']([^"'>]+)["']""",
    re.IGNORECASE,
)
_META_DESC_RE = re.compile(
    r"""<meta[^>]+name=["']description["'][^>]+content=["']([^"'>]+)["']""",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_TAG_STRIP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip(text: str) -> str:
    text = _TAG_STRIP.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _politifact_short(html: str) -> Optional[str]:
    """PolitiFact's 'If Your Time is Short' bullet section, when present."""
    m = re.search(
        r"If Your Time is short.*?(?=See the sources|Read More|Our Sources)",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    text = _strip(m.group(0))
    # The section sometimes leads with the heading itself — trim that
    text = re.sub(r"^If Your Time is short:?\s*", "", text, flags=re.IGNORECASE)
    return text[:1200] if text else None


def fetch_evidence_text(url: str, *, max_chars: int = 1200) -> Optional[str]:
    """Return the most-grounding text we can pull from a fact-check page.

    Order: site-specific summary (PolitiFact's 'If Your Time is Short')
    → og:description → meta description → <title>.

    Returns None on fetch failure — callers should fall back gracefully.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("evidence-text fetch failed for %s: %s", url, e)
        return None

    html = resp.text

    if "politifact.com" in url.lower():
        short = _politifact_short(html)
        if short:
            return short[:max_chars]

    for pattern in (_OG_DESC_RE, _META_DESC_RE, _TITLE_RE):
        m = pattern.search(html)
        if m:
            text = _strip(m.group(1))
            if text and len(text) >= 40:  # skip stubby titles
                return text[:max_chars]

    return None
