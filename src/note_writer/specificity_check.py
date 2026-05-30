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

If the post includes a "Linked article" section below the post text, use it ONLY to understand what claim the post is implicitly endorsing — the post is treated as sharing/amplifying that article. The CLAIM is then the article's central factual assertion, and claim_text should restate that claim. A linked article does NOT make a vague reaction noteable on its own — the article must contain a specific, falsifiable factual claim that a fact-checker could verify or refute from independent sources.

When the post quotes someone, makes a numeric claim, names a specific event or action, or asserts a stated fact, mark has_specific_claim=true and restate the checkable claim in claim_text.

Return JSON."""


def has_specific_claim(
    post_text: str,
    linked_content: str | None = None,
) -> tuple[bool, str, str]:
    """Return (has_claim, claim_text, reason).

    If linked_content is supplied, it is appended to the prompt as context
    so Haiku can evaluate posts that share/amplify a linked article. The
    rule about "no claim in image/video means skip" still applies — linked
    article text is only used when we actually fetched it.

    has_claim=False means the post should be skipped — no note possible.
    """
    if not post_text or len(post_text.strip()) < 15:
        return False, "", "Post too short to contain a checkable claim."

    if linked_content:
        user_prompt = (
            f"Post:\n{post_text}\n\n"
            f"Linked article (use to understand what the post is amplifying):\n"
            f"{linked_content.strip()}"
        )
    else:
        user_prompt = f"Post:\n{post_text}"

    try:
        verdict = parse_json(
            user_prompt=user_prompt,
            schema=SpecificityVerdict,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=300,
        )
        return verdict.has_specific_claim, verdict.claim_text, verdict.reason
    except Exception as e:
        logger.warning("Specificity check failed (%s); defaulting to skip.", e)
        return False, "", f"Specificity check error: {e}"
