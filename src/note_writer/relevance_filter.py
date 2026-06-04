"""Beat filter: decide whether a post is in scope for note-writing.

Two modes, set by CN_BOT_BEAT_MODE (see note_writer.config):
  - "broad"       EARN-IN: any post with a specific, checkable factual claim,
                  on ANY topic. Maximizes note volume to fill the rolling-50
                  that Community Notes' automated admission evaluator scores.
  - "us_politics" POST-ADMISSION: only US politicians / political misinformation.

Either way this is a cheap (Haiku) TOPIC triage — it never judges whether a
claim is true. Specificity, opinion, and evidence gates downstream do the real
filtering, so this stays permissive on purpose.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from note_writer.config import BEAT_MODE
from note_writer.llm_util import HAIKU_MODEL, parse_json

logger = logging.getLogger(__name__)


class RelevanceVerdict(BaseModel):
    on_beat: bool = Field(description="True if the post is in scope for note-writing per the active beat (see system prompt).")
    reason: str = Field(description="One short sentence explaining why.")


_SYSTEM_US_POLITICS = """You are a TOPIC-FILTERING triage assistant for a Community Notes bot focused on US political misinformation around the 2026 midterms.

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


_SYSTEM_BROAD = """You are a triage assistant for a Community Notes bot in its EARN-IN phase. The bot is temporarily writing notes on ANY topic — not just US politics — to build a track record with Community Notes' automated note evaluator.

YOUR JOB IS ONLY TO DECIDE: does this post contain at least one SPECIFIC, FACTUAL, CHECKABLE claim that a note could correct or add context to? Topic does not matter.

Do NOT judge whether the claim is TRUE, and do NOT decide whether you recognize the people, places, or organizations involved. Your training data may be older than the post. Downstream stages verify facts and require real published evidence — you only decide whether there is something checkable here.

ON-BEAT (mark on_beat=true) — the post makes a concrete factual assertion on ANY subject, e.g.:
- A statistic, number, or measurable claim ("X has doubled since...", "the rate is Y%")
- A specific event that did or didn't happen, or a before/after claim
- A quote or action attributed to a named person, company, or institution
- A science, health, medical, historical, or nature claim
- A viral rumor, miracle cure, doctored image, deepfake, or misattributed screenshot
- A claim about a product, company, sports record, or public figure

OFF-BEAT (mark on_beat=false) — ONLY when the post has nothing to check:
- Pure opinion, prediction, or value judgment with no underlying fact
- A joke, meme, or clearly satirical / absurd post (humorously framed)
- Too vague to contain any checkable claim — a reaction, a gesture, an emoji
- Purely personal with no factual assertion about the world ("had a great day")

When in doubt, MARK ON-BEAT. Downstream specificity, opinion, and evidence gates will drop anything unworkable — the cost of dropping a checkable post here is higher than letting one through.

Return JSON."""


_SYSTEM = _SYSTEM_BROAD if BEAT_MODE == "broad" else _SYSTEM_US_POLITICS


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
