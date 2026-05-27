"""Final pre-submission sanity check for hallucinated specifics.

Defense in depth: even with article-text grounding and tight prompts,
Opus occasionally embellishes a note with a number, date, or name that
isn't in the cited evidence. The Craigslist false-note was this class
of failure. This check catches that before submission.

Alexios's piece flagged that AI bots make "outrageous errors" that the
bridging algorithm has to filter out. This check is our attempt to
catch those at write time instead of letting raters do it.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from note_writer.llm_util import HAIKU_MODEL, parse_json

logger = logging.getLogger(__name__)


class HallucinationCheck(BaseModel):
    has_hallucination: bool = Field(
        description=(
            "True if the note text contains a specific claim (number, date, name, "
            "quote, or factual assertion) that is NOT supported by the evidence text."
        )
    )
    unsupported_claims: list[str] = Field(
        description="Specific phrases in the note that go beyond what the evidence supports."
    )
    reason: str = Field(description="One short sentence explaining the verdict.")


_SYSTEM = """You compare a Community Note draft against the source article it cites, looking ONLY for FABRICATED HARD SPECIFICS — verifiable, ground-truth claims the note states but the article does not support.

HARD SPECIFICS = numbers, dollar amounts, percentages, dates, named events, named places, named people (other than the post subject), or direct quoted statements.

You will be given:
- The note draft.
- The article text (e.g., the publisher's article description / ruling).
- Metadata we already know is true: the publisher name and the verdict the publisher issued.

The publisher name and the verdict are KNOWN-TRUE and need not appear in the article body. A note saying "PolitiFact rated this False" is fine even if the article body doesn't include those exact words — that information comes from our metadata, not from the article.

FLAG (has_hallucination=true) ONLY when the note states a hard specific fact that:
- Is not stated in the article, AND
- Is not the known publisher name or known verdict.

DO NOT FLAG:
- Characterizations or interpretive framing (e.g., "misrepresented", "misleading", "false claim") — a separate filter handles these.
- Reasonable paraphrases of the article's content (e.g., article says "studies do no such thing"; note says "studies do not show that"; same meaning, different words — fine).
- Generic references to the topic of the article (e.g., article is about Trump's tariff claim; note can reference "Trump's tariff claim").
- The publisher name, the publisher's verdict word, or the bot's framing of who said what to whom (those come from metadata).

Examples:

FLAG (has_hallucination=true):
Note: "PolitiFact rated this False. Treasury data shows tariff revenue averaged $200M–$250M per day in 2025."
Article body: "PolitiFact rated Trump's claim False. The Treasury Department shows a much lower figure."
Metadata: publisher=PolitiFact, rating=False
→ The dollar range "$200M–$250M per day" is fabricated; the article only says "a much lower figure."

DO NOT FLAG (has_hallucination=false):
Note: "PolitiFact rated this False. Treasury data shows tariff collections well below the $2 billion a day claim."
Article body: "PolitiFact rated Trump's claim False. The Treasury Department shows a much lower figure."
Metadata: publisher=PolitiFact, rating=False
→ The "Treasury" reference appears in the article. "Well below the $2 billion a day claim" paraphrases "much lower figure." Publisher + verdict come from metadata.

DO NOT FLAG (has_hallucination=false):
Note: "FactCheck.org rated this False. The studies cited do not show Tylenol causes autism."
Article body: "Kennedy's claim does not match what the studies say about acetaminophen and autism."
Metadata: publisher=FactCheck.org, rating=False
→ Publisher and verdict are from metadata. "The studies cited" / "do not show Tylenol causes autism" is supported by the article (acetaminophen IS Tylenol; the article says studies don't support the claim).

Be strict on specifics, permissive on framing and on metadata claims. Return JSON."""


def check_note(
    note_text: str,
    evidence_text: str,
    *,
    publisher: str | None = None,
    rating: str | None = None,
) -> tuple[bool, str, list[str]]:
    """Returns (is_clean, reason, unsupported_claims)."""
    if not evidence_text:
        # Can't compare — be permissive but log
        logger.info("No evidence text to check note against — skipping hallucination check.")
        return True, "No evidence text available to verify against.", []

    meta_lines = []
    if publisher:
        meta_lines.append(f"- publisher: {publisher}")
    if rating:
        meta_lines.append(f"- verdict: {rating}")
    meta_block = "\n".join(meta_lines) if meta_lines else "- (none)"

    try:
        verdict = parse_json(
            user_prompt=(
                f"NOTE DRAFT:\n{note_text.strip()}\n\n"
                f"ARTICLE BODY:\n{evidence_text.strip()[:3000]}\n\n"
                f"KNOWN-TRUE METADATA (does not need to appear in article body):\n{meta_block}"
            ),
            schema=HallucinationCheck,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=500,
        )
        is_clean = not verdict.has_hallucination
        return is_clean, verdict.reason, verdict.unsupported_claims
    except Exception as e:
        logger.warning("Hallucination check failed (%s); defaulting to permissive.", e)
        return True, f"Hallucination check error: {e}", []
