"""Direct PolitiFact.com search — fills the gap left by Google Fact Check
Tools API, which has narrow ClaimReview coverage.

Scrapes the public /search/ page and pairs each /factchecks/ URL with its
truth-o-meter rating. Returns FactCheckEvidence so callers can merge with
the FCT API results.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import List

import requests

from data_models import FactCheckEvidence

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.politifact.com/search/"
USER_AGENT = "Mozilla/5.0 (compatible; PoynterCNBot/1.0; +https://www.poynter.org)"

# PolitiFact truth-o-meter ratings we treat as "false enough" for a note.
FALSE_RATINGS = {
    "false",
    "mostly-false",
    "pants-on-fire",
    "full-flop",  # politicians reversing positions — rate-worthy in context
}

_URL_RE = re.compile(
    r"/factchecks/(\d{4})/([a-z]+)/(\d+)/([a-z0-9-]+)/([a-z0-9-]+)/"
)
_METER_RE = re.compile(
    r"meter-(false|true|mostly-false|mostly-true|half-true|pants-on-fire|full-flop|half-flop|no-flip)"
)
_WINDOW = 3000  # chars on each side of a URL to look for its rating

_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _slug_to_text(slug: str) -> str:
    """Turn a PolitiFact URL slug into a human-readable claim summary."""
    return slug.replace("-", " ").strip()


def _claimant_to_name(claimant: str) -> str:
    return " ".join(w.capitalize() for w in claimant.split("-"))


def search(query: str, *, max_results: int = 10, false_only: bool = True) -> List[FactCheckEvidence]:
    """Return PolitiFact fact-checks matching `query`."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("PolitiFact search failed: %s", e)
        return []

    html = resp.text

    # Find every (rank-ordered) unique fact-check URL with its position
    seen = set()
    pairs = []
    for m in _URL_RE.finditer(html):
        url_path = m.group(0)
        if url_path in seen:
            continue
        seen.add(url_path)
        year, month, _day, claimant, slug = m.groups()
        # Find the rating closest to this URL in the document
        start = max(0, m.start() - _WINDOW)
        end = m.end() + _WINDOW
        meters = _METER_RE.findall(html[start:end])
        rating = meters[0] if meters else None
        pairs.append((url_path, year, month, _day, claimant, slug, rating))
        if len(pairs) >= max_results * 2:  # collect a buffer for filtering
            break

    results: List[FactCheckEvidence] = []
    for url_path, year, month, day, claimant, slug, rating in pairs:
        if rating is None:
            continue
        if false_only and rating not in FALSE_RATINGS:
            continue
        review_date = f"{year}-{_MONTH_MAP.get(month, '01')}-{int(day):02d}"
        results.append(
            FactCheckEvidence(
                claim_text=_slug_to_text(slug),
                claimant=_claimant_to_name(claimant),
                claim_date=None,
                publisher_name="PolitiFact",
                publisher_site="politifact.com",
                review_url=urllib.parse.urljoin("https://www.politifact.com", url_path),
                review_title=_slug_to_text(slug),
                review_date=review_date,
                rating=rating.replace("-", " ").title(),
            )
        )
        if len(results) >= max_results:
            break

    logger.info("PolitiFact: query=%r → %d false-rated results", query, len(results))
    return results


def search_for_post(post_text: str, max_results: int = 8) -> List[FactCheckEvidence]:
    """Generate a short query via Haiku, then search PolitiFact."""
    from note_writer.llm_util import HAIKU_MODEL, complete

    query = complete(
        user_prompt=(
            "Extract the 3-5 most distinctive keywords from this X post for a "
            "PolitiFact search. Return ONLY the keywords separated by spaces — "
            "no quotes, no commentary, no prefix.\n\n"
            f"Post:\n{post_text}"
        ),
        model=HAIKU_MODEL,
        max_tokens=60,
        adaptive_thinking=False,
    ).strip().strip('"').strip("'")

    logger.info("PolitiFact query: %r", query)
    results = search(query, max_results=max_results, false_only=True)
    if not results:
        short = " ".join(query.split()[:3])
        if short and short != query:
            results = search(short, max_results=max_results, false_only=True)
    return results
