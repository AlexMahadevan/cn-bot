"""Google Fact Check Tools API client.

Replaces the old DuckDuckGo-based PolitiFact search. Returns verified
fact checks from IFCN signatories with URLs the bot is required to
cite verbatim (no hallucination possible if we substitute the URL
programmatically rather than asking Claude to write it).

Docs: https://developers.google.com/fact-check/tools/api/reference/rest/v1alpha1/claims/search
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import List, Optional

import dotenv
import requests

from data_models import FactCheckEvidence

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

# IFCN-verified publishers we trust for political fact-checking.
# Matched against claimReview[].publisher.site.
TRUSTED_PUBLISHERS = {
    "politifact.com",
    "factcheck.org",
    "apnews.com",
    "reuters.com",
    "washingtonpost.com",
    "leadstories.com",
    "usatoday.com",
    "snopes.com",
    "checkyourfact.com",
    "fullfact.org",
    "factcheck.afp.com",  # AFP Fact Check, IFCN signatory
    "afp.com",
}

# Ratings considered "false" enough to warrant a Community Note.
# Substring-matched against rating.lower() — keep keywords short and distinctive.
FALSE_RATINGS = {
    "false",
    "pants on fire",
    "pants-on-fire",
    "mostly false",
    "incorrect",
    "fake",
    "misleading",
    "miscaptioned",
    "fabricated",
    "two pinocchios",
    "three pinocchios",
    "four pinocchios",
    "fiction",
    "exaggerated",
    "no evidence",
    "unsupported",
    "unproven",
    "unsubstantiated",
    "didn't happen",
    "didnt happen",
    "disputed",
    "altered",
    "not true",
    "untrue",
    "debunked",
    "doctored",
}


def _api_key() -> str:
    key = (
        os.getenv("FACT_CHECK_API_KEY")
        or os.getenv("GOOGLE_FACTCHECK_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    if not key:
        raise RuntimeError(
            "No API key. Set FACT_CHECK_API_KEY, GOOGLE_FACTCHECK_API_KEY, "
            "or GEMINI_API_KEY in .env."
        )
    return key


def _is_trusted_publisher(site: str) -> bool:
    if not site:
        return False
    site = site.lower().lstrip(".")
    return any(site == p or site.endswith("." + p) for p in TRUSTED_PUBLISHERS)


def _is_false_rating(rating: str) -> bool:
    if not rating:
        return False
    r = rating.lower().strip()
    return any(false_word in r for false_word in FALSE_RATINGS)


def search_claims(
    query: str,
    *,
    language: str = "en",
    max_age_days: int = 365,
    page_size: int = 10,
) -> List[FactCheckEvidence]:
    """Search the Fact Check Tools API and return only fact checks that are:
    (a) from trusted IFCN publishers, and
    (b) rated false / misleading / similar.

    Empty list if no matching evidence.
    """
    try:
        key = _api_key()
    except RuntimeError as e:
        logger.error("Fact Check API misconfigured: %s", e)
        return []

    params = {
        "query": query,
        "languageCode": language,
        "maxAgeDays": str(max_age_days),
        "pageSize": str(page_size),
        "key": key,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response else ""
        logger.error("Fact Check Tools API error %s: %s", e.response.status_code if e.response else "?", body)
        return []
    except requests.RequestException as e:
        logger.error("Fact Check Tools API request failed: %s", e)
        return []

    data = resp.json()
    results: List[FactCheckEvidence] = []

    for claim in data.get("claims", []):
        for review in claim.get("claimReview", []):
            publisher = review.get("publisher", {}) or {}
            site = publisher.get("site", "")
            rating = review.get("textualRating", "")

            if not _is_trusted_publisher(site):
                continue
            if not _is_false_rating(rating):
                continue
            if not review.get("url"):
                continue

            results.append(
                FactCheckEvidence(
                    claim_text=claim.get("text", ""),
                    claimant=claim.get("claimant"),
                    claim_date=claim.get("claimDate"),
                    publisher_name=publisher.get("name", site),
                    publisher_site=site,
                    review_url=review["url"],
                    review_title=review.get("title"),
                    review_date=review.get("reviewDate"),
                    rating=rating,
                )
            )

    logger.info("Fact Check API: query=%r → %d trusted-false results", query, len(results))
    return results


def search_for_post(post_text: str, max_results: int = 10) -> List[FactCheckEvidence]:
    """Take a post's text, generate keyword queries, dedupe results.

    The Google Fact Check Tools API is keyword-based, not semantic — short
    queries (3-5 words) outperform long ones. We try the LLM-generated keyword
    set and, as a fallback, a query built from the post's most distinctive nouns.
    """
    from note_writer.llm_util import complete, HAIKU_MODEL

    query = complete(
        user_prompt=(
            "Extract the 3-5 most distinctive keywords from this X post for a "
            "Google Fact Check search. Use the proper nouns and key claim terms — "
            "no filler words, no quotes, no 'Search query:' prefix. Just the keywords "
            "separated by spaces.\n\n"
            f"Post:\n{post_text}"
        ),
        model=HAIKU_MODEL,
        max_tokens=60,
        adaptive_thinking=False,
    ).strip().strip('"').strip("'")

    logger.info("Generated fact-check query: %r", query)
    results = search_claims(query, page_size=max_results)

    # If the LLM-generated query returns nothing, fall back to a shorter
    # version (first 3 words) — handles the case where Haiku still gave us
    # too many keywords.
    if not results:
        short_query = " ".join(query.split()[:3])
        if short_query and short_query != query:
            logger.info("Retrying with shorter query: %r", short_query)
            results = search_claims(short_query, page_size=max_results)

    return results
