"""Specificity gate — does the post text contain a specific, falsifiable
factual claim that can be evaluated WITHOUT seeing linked media?

This filter exists because the bot doesn't fetch the contents of linked
images/videos. A post like 'Option for ICE riots. Just saying. [video]'
has no checkable claim in its text — any note we write would be guessing
at what the video says. Skip these.

Failure case this prevents: bot once matched 'ICE riots' to a Craigslist
fact-check and wrote a note treating the video as if it asserted the
Craigslist ad was real — when the text never made any such claim.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from note_writer.llm_util import HAIKU_MODEL, parse_json

logger = logging.getLogger(__name__)


class SpecificityVerdict(BaseModel):
    has_specific_claim: bool = Field(
        description=(
            "True only if the post's TEXT (ignoring any linked image/video) "
            "makes a specific, falsifiable factual claim. False if the post "
            "is vague commentary, opinion, prediction, a reaction to unseen "
            "media, or relies on a link the reader must click to understand."
        )
    )
    claim_text: str = Field(
        description=(
            "If has_specific_claim is true, restate the single most checkable "
            "factual claim from the post in one sentence. If false, return ''."
        )
    )
    reason: str = Field(description="One short sentence explaining the verdict.")


_SYSTEM = """You judge whether an X post contains a specific, falsifiable factual claim that can be fact-checked WITHOUT viewing any linked media.

A SPECIFIC FALSIFIABLE CLAIM has:
- A named subject (person, agency, place, event), AND
- An assertion about that subject (a number, a date, a statement, an action, a status), AND
- Enough context that a fact-checker could verify or refute it from public sources.

NOT a specific falsifiable claim:
- Pure opinion: "This is the worst administration ever."
- Predictions: "He's going to lose the midterms."
- Reactions to unseen media: "Look at this!", "Watch this video", "Option for ICE riots. Just saying."
- Sarcasm or commentary with no underlying assertion.
- Posts whose meaning depends on clicking an unseen link or watching a video.
- Vague gestures: "They're at it again", "This is wild", "Wow."

When the post text is short and gestures at a linked image/video without making the claim in words, mark has_specific_claim=false. The bot cannot see the linked media.

When the post quotes someone, makes a numeric claim, names a specific event or action, or asserts a stated fact, mark has_specific_claim=true and restate the checkable claim in claim_text.

Return JSON."""


def has_specific_claim(post_text: str) -> tuple[bool, str, str]:
    """Return (has_claim, claim_text, reason).

    has_claim=False means the post should be skipped — no note possible.
    """
    if not post_text or len(post_text.strip()) < 15:
        return False, "", "Post too short to contain a checkable claim."

    try:
        verdict = parse_json(
            user_prompt=f"Post:\n{post_text}",
            schema=SpecificityVerdict,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=300,
        )
        return verdict.has_specific_claim, verdict.claim_text, verdict.reason
    except Exception as e:
        logger.warning("Specificity check failed (%s); defaulting to skip.", e)
        return False, "", f"Specificity check error: {e}"
