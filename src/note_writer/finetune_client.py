"""Client for the fine-tuned CN-bot model running on Modal.

If CN_BOT_FINETUNED_URL is set in .env, the note writer routes its first
draft attempt through this endpoint instead of (or in addition to) Opus.
Failure falls back to Opus — fine-tune is an optimization, not a hard
dependency.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

FINETUNED_URL = os.getenv("CN_BOT_FINETUNED_URL", "").strip() or None
TIMEOUT = 30


def is_available() -> bool:
    return FINETUNED_URL is not None


def generate_note(post_text: str, *, max_new_tokens: int = 220, temperature: float = 0.4) -> Optional[str]:
    """Call the Modal-hosted fine-tuned model. Returns the note text, or None on failure."""
    if not FINETUNED_URL:
        return None

    try:
        resp = requests.post(
            FINETUNED_URL,
            json={
                "post_text": post_text,
                "max_new_tokens": max_new_tokens,
                "temperature": temperature,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("note_text") or "").strip() or None
    except requests.RequestException as e:
        logger.warning("Fine-tuned endpoint call failed (%s); falling back.", e)
        return None
