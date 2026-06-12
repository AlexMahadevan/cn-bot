"""Self-fact-check evidence tier — broader web_search across primary sources,
government data, and major news outlets when IFCN signatories don't have
coverage of a specific claim.

This is the riskiest tier — Claude evaluates evidence rather than relaying a
fact-checker's verdict. Two architectural guardrails remain:

1. URLs are extracted from real web_search_tool_result blocks — Claude
   never invents a URL.
2. The downstream prose validator rejects any note that contains a URL,
   domain, or http reference. Code substitutes the verified URL.

See feedback memory `feedback-cn-bot-self-factcheck` for why this tier
exists despite the reputational risk.
"""

from __future__ import annotations

import logging
import re
from typing import List

from data_models import FactCheckEvidence
from note_writer.llm_util import client as anthropic_client
from note_writer.web_search_domains import filter_allowed, learn_inaccessible_from_error

logger = logging.getLogger(__name__)

# Tier-2 trusted publishers: official US government, major newsrooms, primary-source
# repositories, and reputable wonk shops. NOT a verdict-rating list — these are
# sources Claude can cite as "per X" rather than "X rated this false."
BROAD_ALLOWED_DOMAINS = sorted({
    # US government
    "treasury.gov", "bls.gov", "cbo.gov", "congress.gov", "whitehouse.gov",
    "fec.gov", "census.gov", "cdc.gov", "fbi.gov", "doj.gov", "state.gov",
    "defense.gov", "gao.gov", "irs.gov", "uscourts.gov", "supremecourt.gov",
    "house.gov", "senate.gov", "loc.gov", "energy.gov", "ed.gov", "hhs.gov",
    "ssa.gov", "dhs.gov", "epa.gov", "noaa.gov", "nasa.gov", "fda.gov",
    "nih.gov", "usda.gov", "doi.gov", "dot.gov", "hud.gov", "va.gov",
    "uscis.gov", "ice.gov", "cbp.gov", "tsa.gov",
    # Major US newsrooms (high editorial standards, US politics beat)
    "apnews.com", "reuters.com", "washingtonpost.com", "nytimes.com",
    "wsj.com", "bloomberg.com", "npr.org", "pbs.org", "cbsnews.com",
    "abcnews.go.com", "nbcnews.com", "axios.com", "politico.com",
    "thehill.com", "usatoday.com", "latimes.com", "chicagotribune.com",
    "bostonglobe.com", "miamiherald.com", "houstonchronicle.com",
    "denverpost.com", "azcentral.com", "phoenixnewtimes.com",
    "theatlantic.com", "newyorker.com", "propublica.org", "vox.com",
    "businessinsider.com", "cnbc.com", "marketwatch.com",
    "bbc.com", "bbc.co.uk", "theguardian.com", "ft.com", "economist.com",
    # Academic / nonpartisan analysis
    "brookings.edu", "pewresearch.org", "cbpp.org", "taxfoundation.org",
    "crfb.org", "kff.org", "rand.org", "urban.org", "mercatus.org",
    # IFCN signatories (also tier-1 but include here for fallback queries)
    "politifact.com", "factcheck.org", "snopes.com", "leadstories.com",
    "checkyourfact.com", "factcheck.afp.com",
})

_NAME_MAP = {
    "apnews.com": "AP", "reuters.com": "Reuters",
    "washingtonpost.com": "Washington Post", "nytimes.com": "The New York Times",
    "wsj.com": "Wall Street Journal", "bloomberg.com": "Bloomberg",
    "npr.org": "NPR", "axios.com": "Axios", "politico.com": "Politico",
    "thehill.com": "The Hill", "theguardian.com": "The Guardian",
    "ft.com": "Financial Times", "economist.com": "The Economist",
    "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "treasury.gov": "U.S. Treasury", "bls.gov": "Bureau of Labor Statistics",
    "cbo.gov": "Congressional Budget Office", "congress.gov": "Congress.gov",
    "whitehouse.gov": "The White House", "fec.gov": "FEC",
    "census.gov": "U.S. Census Bureau", "gao.gov": "GAO",
    "supremecourt.gov": "U.S. Supreme Court", "uscourts.gov": "U.S. Courts",
    "brookings.edu": "Brookings", "pewresearch.org": "Pew Research",
    "politifact.com": "PolitiFact", "factcheck.org": "FactCheck.org",
    "snopes.com": "Snopes", "leadstories.com": "Lead Stories",
}


def _publisher_from_url(url: str) -> tuple[str, str]:
    m = re.match(r"https?://(?:www\.)?([^/]+)/?", url)
    site = m.group(1).lower() if m else ""
    return _NAME_MAP.get(site, site), site


def _is_allowed(site: str) -> bool:
    return any(site == d or site.endswith("." + d) for d in BROAD_ALLOWED_DOMAINS)


def search_for_post(post_text: str, *, max_results: int = 8) -> List[FactCheckEvidence]:
    """Broad web search; returns ranked evidence without a 'rating' field.

    Downstream prompts treat these as 'per [publisher], [evidence]' rather
    than 'X rated this false.'
    """
    response = None
    for attempt in range(2):
        try:
            response = anthropic_client().messages.create(
                # Haiku 4.5 (swapped from Sonnet 4.6 on 2026-06-02 to cut cost). Broad
                # web_search evidence gathering; raw results, no prose synthesis.
                model="claude-haiku-4-5",
                max_tokens=2048,
                tools=[
                    {
                        "type": "web_search_20260209",
                        "name": "web_search",
                        # Haiku 4.5 doesn't support programmatic tool calling, which
                        # web_search_20260209 requires by default — without this the
                        # API 400s every call and this tier silently returns [] (it
                        # did exactly that from 2026-06-02 to 2026-06-12).
                        "allowed_callers": ["direct"],
                        # The API 400s the WHOLE request if any allowed domain
                        # blocks Anthropic's crawler — filter out known blockers
                        # (learned from prior 400s this process).
                        "allowed_domains": filter_allowed(BROAD_ALLOWED_DOMAINS),
                        "max_uses": 2,
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Search the web for primary sources, official data, or major news "
                            "coverage that bears on the factual claims in this X post. You're "
                            "looking for evidence that could SUPPORT or CONTRADICT the claim — "
                            "do not pre-filter for verdict. Use 1-3 well-targeted queries. "
                            "After the searches return, do NOT write a summary or analysis — "
                            "just trigger the searches and stop.\n\n"
                            f"Post:\n{post_text}"
                        ),
                    }
                ],
            )
            break
        except Exception as e:
            if attempt == 0 and learn_inaccessible_from_error(str(e)):
                continue  # retry once with the blocked domains removed
            logger.warning("self-fact-check web_search failed: %s", e)
            return []
    if response is None:
        return []

    results: List[FactCheckEvidence] = []
    for block in response.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        for hit in getattr(block, "content", []) or []:
            url = getattr(hit, "url", None) or (hit.get("url") if isinstance(hit, dict) else None)
            title = getattr(hit, "title", None) or (hit.get("title") if isinstance(hit, dict) else None)
            # web_search results sometimes carry a 'page_content' / 'text' field
            # depending on SDK version — try a few names. Do NOT fall back to
            # encrypted_content: it's an opaque encrypted blob, not prose, and
            # feeding it into the evidence-picker prompt is pure noise.
            snippet = None
            for key in ("text", "content", "snippet"):
                val = getattr(hit, key, None) or (hit.get(key) if isinstance(hit, dict) else None)
                if isinstance(val, str) and val:
                    snippet = val
                    break

            if not url:
                continue
            publisher_name, site = _publisher_from_url(url)
            if not _is_allowed(site):
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
                    rating=None,
                    snippet=snippet if isinstance(snippet, str) else None,
                    evidence_tier="self_fact_check",
                )
            )
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    logger.info("self-fact-check: %d candidate sources", len(results))
    return results
