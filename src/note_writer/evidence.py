"""Orchestrates all evidence sources: Google Fact Check Tools API,
PolitiFact direct search, and (when both miss) Anthropic web_search.

Returns a deduplicated list of FactCheckEvidence — the only thing the
note-writer touches. New sources slot in here without changes upstream.
"""

from __future__ import annotations

import logging
from typing import List

from data_models import FactCheckEvidence
from note_writer import (
    fact_check_api,
    politifact_search,
    self_fact_check,
    web_search_evidence,
    x_post_evidence,
)

logger = logging.getLogger(__name__)


def _dedupe_by_url(evidence: List[FactCheckEvidence]) -> List[FactCheckEvidence]:
    seen = set()
    out = []
    for e in evidence:
        url = e.review_url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        out.append(e)
    return out


def gather_for_post(post_text: str, max_total: int = 12) -> List[FactCheckEvidence]:
    """Run all evidence sources for a post and return a deduped, merged list.

    Tiered by safety: IFCN verdicts first, then constrained web_search to
    IFCN domains, then broad self-fact-check across primary/news sources.
    Each `FactCheckEvidence` records which tier produced it.
    """
    all_evidence: List[FactCheckEvidence] = []

    # 0. X-as-source — linked tweets the post references. Runs first because
    # these are most directly relevant when the post quotes/screenshots another
    # post. Closes the gap Alexios flagged: humans cite X 2.5x more often
    # than AI bots, because they can reference the original post being misrepresented.
    try:
        xp_results = x_post_evidence.gather_for_post(post_text, max_results=3)
        all_evidence.extend(xp_results)
    except Exception as e:
        logger.warning("x_post search errored: %s", e)

    # 1. PolitiFact direct (ifcn_verified)
    try:
        pf_results = politifact_search.search_for_post(post_text, max_results=6)
        for r in pf_results:
            r.evidence_tier = "ifcn_verified"
        all_evidence.extend(pf_results)
    except Exception as e:
        logger.warning("PolitiFact search errored: %s", e)

    # 2. Google Fact Check Tools API (ifcn_verified)
    try:
        fct_results = fact_check_api.search_for_post(post_text, max_results=6)
        for r in fct_results:
            r.evidence_tier = "ifcn_verified"
        all_evidence.extend(fct_results)
    except Exception as e:
        logger.warning("Fact Check Tools search errored: %s", e)

    # 3. Self-fact-check — broad search across gov data, major news, IFCN sites.
    # Runs in PARALLEL with the IFCN-specific sources above (not as fallback).
    # Real CRH notes often cite primary sources (gov reports, coroner, congressional
    # transcripts) without any fact-check verdict. We need this tier to contribute
    # candidates the picker can choose from, not just to backstop empty results.
    try:
        sfc_results = self_fact_check.search_for_post(post_text, max_results=5)
        all_evidence.extend(sfc_results)
    except Exception as e:
        logger.warning("self-fact-check errored: %s", e)

    # 4. Constrained web_search to IFCN domains — narrower fallback when other
    # sources are thin. Still runs even if we already have evidence, since it
    # may surface IFCN coverage Google FCT's index missed.
    if len(all_evidence) < 3:
        try:
            ws_results = web_search_evidence.search_for_post(post_text, max_results=5)
            for r in ws_results:
                r.evidence_tier = "ifcn_verified"
            all_evidence.extend(ws_results)
        except Exception as e:
            logger.warning("web_search errored: %s", e)

    deduped = _dedupe_by_url(all_evidence)[:max_total]
    tier_counts = {}
    for e in deduped:
        tier_counts[e.evidence_tier] = tier_counts.get(e.evidence_tier, 0) + 1
    logger.info("Total evidence for post: %d unique by tier: %s", len(deduped), tier_counts)
    return deduped
