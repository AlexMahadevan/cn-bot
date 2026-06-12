"""Shared helper for Anthropic web_search allowed_domains handling.

The web_search API rejects the ENTIRE request with a 400 if ANY domain in
allowed_domains blocks Anthropic's crawler ("domains are not accessible to
our user agent"). With ~25 major publishers (Reuters, AP, NYT, WSJ...)
blocking the crawler, a static allowlist that includes them kills the whole
evidence tier — every call 400s and returns zero results.

Publishers change their robots policies over time, so instead of hand-pruning
the list we learn the blocked set at runtime: parse the 400 error body, drop
those domains for the rest of the process, and let the caller retry.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Set

logger = logging.getLogger(__name__)

# Matches quoted domains in the error body, e.g. 'reuters.com'. The dot
# requirement keeps it from matching plain quoted words like 'error'.
_QUOTED_DOMAIN_RE = re.compile(r"'([a-z0-9-]+(?:\.[a-z0-9-]+)+)'")

# Learned at runtime; shared by every web_search tier for the process lifetime.
KNOWN_INACCESSIBLE: Set[str] = set()


def filter_allowed(domains: Iterable[str]) -> List[str]:
    """Return the allowlist minus domains we've learned the crawler can't reach."""
    return sorted(d for d in domains if d not in KNOWN_INACCESSIBLE)


def learn_inaccessible_from_error(err_message: str) -> bool:
    """If err_message is the 'domains not accessible' 400, record the domains
    it names and return True (caller should retry with filter_allowed)."""
    if "not accessible to our user agent" not in err_message:
        return False
    found = set(_QUOTED_DOMAIN_RE.findall(err_message))
    if not found:
        return False
    KNOWN_INACCESSIBLE.update(found)
    logger.warning(
        "web_search: %d allowed_domains block Anthropic's crawler; dropping "
        "them for this process: %s",
        len(found), sorted(found),
    )
    return True
