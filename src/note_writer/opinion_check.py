"""ClaimOpinion-style pre-flight check.

X's internal ClaimOpinion classifier flags notes that read as opinion or
speculation rather than factual correction. Cornell's David Rand found
this classifier systematically downscores political notes — which is why
other AI bots avoid politics altogether (per Alexios Mantzarlis, 2026).

Since @alexcnotes targets US politics specifically, this filter is the
single biggest leverage point for whether our notes ever escape "Needs
More Ratings" purgatory. We approximate X's classifier with a Haiku call
and reject drafts that score too high on the opinion-sounding axis.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from note_writer.llm_util import HAIKU_MODEL, parse_json

logger = logging.getLogger(__name__)


class OpinionScore(BaseModel):
    opinion_score: int = Field(
        description=(
            "Integer 0-10. 0 = pure factual correction citing a specific source. "
            "10 = pure opinion/speculation/editorializing. "
            "X's ClaimOpinion classifier downscores notes scoring high here."
        ),
        ge=0,
        le=10,
    )
    flagged_phrases: list[str] = Field(
        description="Specific words or phrases in the note that read as opinion/speculation."
    )
    reason: str = Field(description="One short sentence explaining the score.")


# Note drafts scoring >= this threshold are rejected as too opinion-shaped.
# Calibrated for the political beat where ClaimOpinion is sensitive.
# Lower number = stricter.
REJECTION_THRESHOLD = 4


_SYSTEM = """You score draft Community Notes for how much they read as OPINION or SPECULATION versus pure FACTUAL CORRECTION.

X uses an internal classifier called ClaimOpinion that downscores notes perceived as expressing opinion. For political content, this classifier is the dominant reason notes fail to be rated helpful. Your job: predict whether a human rater would feel this note is opinion-shaped.

A HIGH opinion score (6-10) means:
- The note uses speculative language ("appears to", "seems", "may have", "could be", "perhaps", "likely")
- The note characterizes intent or motivation ("Trump is trying to...", "this is meant to...", "the goal here is...")
- The note editorializes ("dangerous", "alarming", "troubling", "outrageous", "misleading framing")
- The note draws inferences beyond what the evidence directly establishes
- The note uses "should", "must", or other prescriptive language
- The note compares to standards or values ("not consistent with", "violates", "fails to")

A LOW opinion score (0-3) means:
- The note states what a source rated or reported, with a direct citation
- The note states verifiable facts (dates, numbers, names, primary-source quotes) without elaboration
- The note avoids characterizing the post author's intent or motives
- The note uses declarative, neutral verbs ("rated", "found", "shows", "reports", "states")
- Any qualifier is sourced ("according to X..."), not the writer's hedge

Examples:

HIGH (opinion_score=8): "RFK Jr.'s claim is dangerous misinformation. He appears to be conflating two different studies in a way that suggests he hasn't actually read them. PolitiFact rated this False."
- flagged: "dangerous misinformation", "appears to be", "suggests he hasn't"

LOW (opinion_score=1): "FactCheck.org rated Kennedy's claim False. The circumcision studies he cited do not show that Tylenol causes autism."
- flagged: none

Return JSON."""


def opinion_score(note_text: str) -> tuple[int, str, list[str]]:
    """Return (score 0-10, reason, flagged_phrases)."""
    try:
        verdict = parse_json(
            user_prompt=f"Draft note:\n{note_text.strip()}",
            schema=OpinionScore,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=400,
        )
        return verdict.opinion_score, verdict.reason, verdict.flagged_phrases
    except Exception as e:
        logger.warning("Opinion check failed (%s); defaulting to permissive.", e)
        return 0, f"Opinion check error: {e}", []


def passes_opinion_filter(note_text: str) -> tuple[bool, str]:
    """Returns (passes, reason). Drafts scoring >= REJECTION_THRESHOLD are rejected."""
    score, reason, flagged = opinion_score(note_text)
    if score >= REJECTION_THRESHOLD:
        flag_str = ", ".join(repr(p) for p in flagged[:5])
        return False, (
            f"Opinion score {score}/10 (rejected at >={REJECTION_THRESHOLD}). "
            f"{reason} Flagged: {flag_str}"
        )
    return True, f"Opinion score {score}/10 (under {REJECTION_THRESHOLD}). {reason}"
