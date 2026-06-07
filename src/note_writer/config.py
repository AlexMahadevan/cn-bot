"""Earn-in phase configuration: beat scope + which model writes notes.

Single source of truth so the relevance filter, the note writer, and the audit
log all agree on — and record — the phase each note was produced under. That's
what makes the earn-in experiment analyzable later (broad-beat + Opus vs.
narrow-beat + Haiku): every draft row in notes.db carries the beat_mode and
writer_model it was generated with. See the Global Fact writeup.

Both knobs are env-overridable so we can A/B without code changes:
    CN_BOT_BEAT_MODE   = broad | us_politics      (default: broad)
    CN_BOT_NOTE_WRITER = opus  | sonnet | haiku   (default: opus)

This module imports only llm_util (a leaf), so it can be imported anywhere
without risking a cycle.
"""

from __future__ import annotations

import os

from note_writer.llm_util import HAIKU_MODEL, OPUS_MODEL, SONNET_MODEL

# Beat scope.
#   "broad"       — EARN-IN: note any post with a specific, checkable claim on
#                   ANY topic, to fill the rolling-50 that Community Notes'
#                   automated admission evaluator scores. Maximizes note volume.
#   "us_politics" — POST-ADMISSION: the project's actual beat (US politicians,
#                   2026 midterms, election integrity, political misinformation).
# Flip to "us_politics" once scoring_status.has_access turns true.
BEAT_MODE = os.getenv("CN_BOT_BEAT_MODE", "broad").strip().lower()

# Model that writes the note prose. Opus during earn-in: the automated evaluator
# scores note quality (esp. the ClaimOpinion bucket, our one soft spot), and
# Opus leads quality in the cross-writer benchmark. The cheap reject + evidence
# stages stay on Haiku regardless — this only sets the final note writer.
_WRITER_CHOICE = os.getenv("CN_BOT_NOTE_WRITER", "opus").strip().lower()
NOTE_WRITER_MODEL = {
    "opus": OPUS_MODEL,
    "sonnet": SONNET_MODEL,
    "haiku": HAIKU_MODEL,
}.get(_WRITER_CHOICE, OPUS_MODEL)

# Pre-submission quality gate. evaluate_note returns a numeric claim_opinion_score
# (higher = more checkable-claim-like; negative = opinion-like). During earn-in we
# skip submitting notes below this floor so opinion-ish notes don't drag down the
# rolling-50 the automated admission evaluator scores — ClaimOpinion is our one
# soft bucket. Conservative default (0.0) drops only clearly opinion-like notes;
# raise it as we calibrate against the High/Medium/Low buckets. A note that can't
# be scored (API error) fails open and still submits.
CLAIM_OPINION_MIN = float(os.getenv("CN_BOT_CLAIM_OPINION_MIN", "0.0"))
