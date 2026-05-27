"""Dry-run note scoring via X's evaluate_note endpoint.

Docs: https://docs.x.com/x-api/community-notes/evaluate-a-community-note

Lets us preview how raters might score a draft before actually submitting.
Free safety net — if X's scorer rejects the draft, we skip submission and log.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from cnapi.client import CNClient

logger = logging.getLogger(__name__)


def _normalize_tags(tags: List[Any]) -> List[str]:
    return [t.value if hasattr(t, "value") else str(t) for t in tags]


def evaluate_note(
    client: CNClient,
    *,
    post_id: str,
    note_text: str,
    classification: str,
    misleading_tags: list,
    trustworthy_sources: bool = True,
    test_mode: bool = True,
) -> Dict[str, Any]:
    payload = {
        "test_mode": test_mode,
        "post_id": post_id,
        "info": {
            "text": note_text,
            "classification": classification,
            "misleading_tags": _normalize_tags(misleading_tags),
            "trustworthy_sources": trustworthy_sources,
        },
    }
    return client._request("POST", "https://api.x.com/2/evaluate_note", json=payload)
