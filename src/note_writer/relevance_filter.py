"""Beat filter: drop posts that aren't about US politicians or political
misinformation. Cheap (Haiku) so we don't waste Opus tokens on off-topic posts.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from note_writer.llm_util import HAIKU_MODEL, parse_json

logger = logging.getLogger(__name__)


class RelevanceVerdict(BaseModel):
    on_beat: bool = Field(description="True if the post is about US politicians, the 2026 midterms, federal/state policy, election integrity, or political misinformation.")
    reason: str = Field(description="One short sentence explaining why.")


_SYSTEM = """You are a TOPIC-FILTERING triage assistant for a Community Notes bot focused on US political misinformation around the 2026 midterms.

YOUR JOB IS ONLY TO IDENTIFY TOPIC, NOT TO JUDGE REALITY.
- Do NOT decide whether named officials, agencies, or events are "real."
- Do NOT mark posts off-beat as "parody" or "satire" unless the post is itself humorously framed (jokes, memes, clearly absurd).
- If a post names a US government agency, official, or political figure you do not recognize, ASSUME IT IS REAL. Your training data may be older than the post. Downstream stages will verify facts; you only triage topic.

Notable recent context you may not know (do not let unfamiliarity make you mark these off-beat):
- The "Department of War" is the real renamed Department of Defense (executive order, 2025).
- "Sean Parnell" is the current Pentagon press secretary / DoW spokesperson.
- Cabinet, agency leaders, and naming may have changed recently. Assume name changes are real.

ON-BEAT (mark on_beat=true) — anything in any of these categories:
- US politicians (current + candidates, any party) and their public claims
- Members of Congress, governors, mayors, judges, federal agency officials
- US federal departments / agencies (DoD/DoW, DOJ, DHS, ICE, State, Treasury, FBI, etc.)
- 2026 midterm campaigns, election integrity, voter fraud claims
- Federal policy claims (immigration, economy, taxes, healthcare, foreign policy attributed to US officials)
- Court rulings affecting US politics
- Misattributed quotes or doctored screenshots of US political figures
- AI-generated images or deepfakes of US politicians
- Statements made by official US government accounts or spokespeople

OFF-BEAT (mark on_beat=false) — only these:
- Sports (any league, any country)
- Entertainment, music, celebrities, video games
- Foreign politics with NO US angle
- Pure local crime, weather, or lifestyle with no political content
- Personal anecdotes about non-political topics
- Pure opinion or future predictions with no underlying fact to check

When in doubt between on-beat and off-beat, MARK ON-BEAT. Downstream filtering will handle false positives. The cost of dropping a real political post here is much higher than the cost of letting one through.

Return JSON."""


def is_on_beat(post_text: str) -> tuple[bool, str]:
    """Return (on_beat, reason). Uses Haiku for speed and cost."""
    if not post_text or len(post_text.strip()) < 10:
        return False, "Post too short to evaluate."

    try:
        verdict = parse_json(
            user_prompt=f"Post:\n{post_text}",
            schema=RelevanceVerdict,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=256,
        )
        return verdict.on_beat, verdict.reason
    except Exception as e:
        logger.warning("Relevance filter failed (%s); defaulting to off-beat.", e)
        return False, f"Filter error: {e}"
