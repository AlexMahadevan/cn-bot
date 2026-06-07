"""Dry-run note scoring via X's evaluate_note endpoint.

Docs: https://docs.x.com/x-api/community-notes/evaluate-a-community-note

Previews X's claim/opinion model score for a draft *before* we submit — free,
and it does NOT submit the note. Unlike POST /2/notes, this endpoint's schema is
strict: it accepts ONLY {post_id, note_text} and rejects any other field
(`additionalProperties: false`). Do NOT send test_mode / info / classification
here — those 400 the request. (That bug silently disabled this gate for weeks.)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from cnapi.client import CNClient

logger = logging.getLogger(__name__)


def evaluate_note(client: CNClient, *, post_id: str, note_text: str) -> Dict[str, Any]:
    """Score a draft note. Returns the raw API response:
    ``{"data": {"claim_opinion_score": <float>}}``.
    """
    payload = {"post_id": post_id, "note_text": note_text}
    return client._request("POST", "https://api.x.com/2/evaluate_note", json=payload)


def claim_opinion_score(resp: Dict[str, Any]) -> Optional[float]:
    """Pull the numeric claim_opinion_score out of an evaluate_note response.

    Higher = more checkable-claim-like; negative = opinion-like. Returns None if
    the field is absent (e.g., an error payload), so callers can fail open.
    """
    try:
        return float(resp["data"]["claim_opinion_score"])
    except (KeyError, TypeError, ValueError):
        return None
