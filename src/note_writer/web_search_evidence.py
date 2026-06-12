"""Anthropic web_search fallback for when FCT + PolitiFact return nothing.

Uses Claude's server-side web_search_20260209 tool with allowed_domains
restricted to IFCN signatories. URLs are pulled directly from the tool
result blocks (no Claude-generated URLs) — same anti-hallucination
guarantee as our other sources.

Cost note: web_search is ~$10/1000 searches. Only invoked when cheaper
sources return zero results.
"""

from __future__ import annotations

import logging
import re
from typing import List

from data_models import FactCheckEvidence
from note_writer.fact_check_api import (
    FALSE_RATINGS as FCT_FALSE_RATINGS,
    TRUSTED_PUBLISHERS,
    _is_false_rating,
    _is_trusted_publisher,
)
from note_writer.llm_util import client as anthropic_client
from note_writer.web_search_domains import filter_allowed, learn_inaccessible_from_error

logger = logging.getLogger(__name__)

# Domains we let Claude search — same allowlist as the rest of the pipeline.
ALLOWED_DOMAINS = sorted(TRUSTED_PUBLISHERS)


def _extract_publisher_from_url(url: str) -> tuple[str, str]:
    """Return (publisher_name, publisher_site) from a URL."""
    m = re.match(r"https?://(?:www\.)?([^/]+)/", url)
    site = m.group(1).lower() if m else ""
    # Friendly name for the most common publishers
    name_map = {
        "politifact.com": "PolitiFact",
        "factcheck.org": "FactCheck.org",
        "apnews.com": "AP",
        "reuters.com": "Reuters",
        "washingtonpost.com": "Washington Post",
        "leadstories.com": "Lead Stories",
        "usatoday.com": "USA Today",
        "snopes.com": "Snopes",
        "checkyourfact.com": "Check Your Fact",
        "factcheck.afp.com": "AFP Fact Check",
    }
    return name_map.get(site, site), site


_RATING_FROM_TITLE = [
    (re.compile(r"\bpants on fire\b", re.IGNORECASE), "Pants on Fire"),
    (re.compile(r"\bfact[- ]check:?\s+(?:false|fake|fabricated)\b", re.IGNORECASE), "False"),
    (re.compile(r"\bfalse(?:ly)?\b", re.IGNORECASE), "False"),
    (re.compile(r"\bmisleading\b", re.IGNORECASE), "Misleading"),
    (re.compile(r"\bdebunked\b", re.IGNORECASE), "Debunked"),
    (re.compile(r"\bno evidence\b", re.IGNORECASE), "No Evidence"),
    (re.compile(r"\bnot true\b", re.IGNORECASE), "Not True"),
    (re.compile(r"\bdidn'?t happen\b", re.IGNORECASE), "Didn't Happen"),
    (re.compile(r"\bexagger(at(ed|ing|es))?\b", re.IGNORECASE), "Exaggerated"),
    (re.compile(r"\bwrong(ly)?\b", re.IGNORECASE), "Wrong"),
    (re.compile(r"\bunsupported\b", re.IGNORECASE), "Unsupported"),
    (re.compile(r"\bdoctored\b", re.IGNORECASE), "Doctored"),
    (re.compile(r"\baltered\b", re.IGNORECASE), "Altered"),
    (re.compile(r"\bfabricated\b", re.IGNORECASE), "Fabricated"),
]


def _infer_rating_from_title(title: str) -> str | None:
    """Best-effort rating extraction from a fact-check headline.

    Search-result titles for IFCN sites usually contain the verdict
    (e.g. 'PolitiFact: Trump's claim about X is False').
    """
    if not title:
        return None
    for pattern, rating in _RATING_FROM_TITLE:
        if pattern.search(title):
            return rating
    return None


def search_for_post(post_text: str, *, max_results: int = 5) -> List[FactCheckEvidence]:
    """Use Claude's server-side web_search to find fact-checks across IFCN sites."""
    response = None
    for attempt in range(2):
        try:
            response = anthropic_client().messages.create(
                # Haiku 4.5 (swapped from Sonnet 4.6 on 2026-06-02 to cut cost). This
                # call only triggers web_search and returns raw results — no prose
                # synthesis — so the cheaper model is fine here.
                model="claude-haiku-4-5",
                max_tokens=2048,
                tools=[
                    {
                        "type": "web_search_20260209",
                        "name": "web_search",
                        # Haiku 4.5 doesn't support programmatic tool calling, which
                        # web_search_20260209 requires by default — without this the
                        # API 400s every call and this tier silently returns [].
                        "allowed_callers": ["direct"],
                        # The API 400s the WHOLE request if any allowed domain
                        # blocks Anthropic's crawler — filter out known blockers
                        # (learned from prior 400s this process).
                        "allowed_domains": filter_allowed(ALLOWED_DOMAINS),
                        "max_uses": 2,
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Search the web for IFCN-accredited fact-checks of the main factual "
                            "claim in this X post. Use one or two search queries — focus on the "
                            "most distinctive keywords. After your searches return, do NOT write "
                            "a summary or analysis — your job is only to trigger the searches so "
                            "I can read the raw results.\n\n"
                            f"Post:\n{post_text}"
                        ),
                    }
                ],
            )
            break
        except Exception as e:
            if attempt == 0 and learn_inaccessible_from_error(str(e)):
                continue  # retry once with the blocked domains removed
            logger.warning("web_search call failed: %s", e)
            return []
    if response is None:
        return []

    results: List[FactCheckEvidence] = []
    for block in response.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        # block.content is a list of web_search_result blocks
        for hit in getattr(block, "content", []) or []:
            url = getattr(hit, "url", None) or (hit.get("url") if isinstance(hit, dict) else None)
            title = getattr(hit, "title", None) or (hit.get("title") if isinstance(hit, dict) else None)
            if not url:
                continue
            publisher_name, site = _extract_publisher_from_url(url)
            if not _is_trusted_publisher(site):
                continue  # belt-and-suspenders even though allowed_domains gates it
            rating = _infer_rating_from_title(title or "")
            if not rating or not _is_false_rating(rating):
                continue
            results.append(
                FactCheckEvidence(
                    claim_text=title or "",
                    claimant=None,
                    claim_date=None,
                    publisher_name=publisher_name,
                    publisher_site=site,
                    review_url=url,
                    review_title=title,
                    review_date=None,
                    rating=rating,
                )
            )
            if len(results) >= max_results:
                break

    logger.info("web_search: %d false-rated results", len(results))
    return results
