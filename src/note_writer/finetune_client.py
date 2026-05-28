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
# Cold start on Modal A10G + 7B model load is 30-60s. Subsequent warm calls
# are 5-10s. Set timeout generous enough for cold start.
TIMEOUT = 90


def is_available() -> bool:
    return FINETUNED_URL is not None


import re

_STOP_MARKERS = [
    "<|im_start|>",
    "<|im_end|>",
    "\nWrite the Community Note",
    "\n\nWrite the Community Note",
    "\nX post:",
    "\n\nX post:",
    "\nUse this evidence",
]

# Qwen was trained on real CN notes which always end in a URL, so it
# reliably produces one — usually fake. The pipeline already substitutes
# the verified URL at the end, so we just strip whatever Qwen wrote.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _clean(text: str) -> str:
    """Stop-token cleanup + strip Qwen's hallucinated URLs. The pipeline's
    URL-substitution step replaces them with the verified evidence URL."""
    if not text:
        return ""
    if text.lstrip().startswith("Write the Community Note"):
        idx = text.find("\n")
        if idx >= 0:
            text = text[idx + 1:]
    for m in _STOP_MARKERS:
        idx = text.find(m)
        if idx > 0:
            text = text[:idx]
    # Strip any URL Qwen wrote (they're almost always fabricated). The
    # pipeline appends the real URL after the prose validator passes.
    text = _URL_RE.sub("", text)
    # Collapse double-spaces left behind by URL removal
    text = re.sub(r"\s+", " ", text).strip()
    # And strip any trailing punctuation/whitespace artifact
    text = text.rstrip(" .,;:")
    return text + "." if text and not text.endswith((".", "!", "?")) else text


def _truncate_to_budget(text: str, max_chars: int) -> str:
    """Truncate prose at the last sentence boundary that fits."""
    if len(text) <= max_chars:
        return text
    # Find the last `.` `!` or `?` before max_chars
    head = text[:max_chars]
    for term in (". ", "! ", "? ", ".", "!", "?"):
        idx = head.rfind(term)
        if idx > 0:
            return text[:idx + len(term.rstrip())]
    # Fallback: hard truncate at word boundary
    idx = head.rfind(" ")
    return text[:idx if idx > 0 else max_chars]


def generate_note(
    post_text: str,
    *,
    evidence_text: Optional[str] = None,
    max_chars: Optional[int] = None,
    max_new_tokens: int = 220,
    temperature: float = 0.4,
) -> Optional[str]:
    """Call the Modal-hosted fine-tuned model. Returns the note text, or
    None on failure. If evidence_text is supplied, the endpoint grounds
    the generation in it — what the pipeline passes in production."""
    if not FINETUNED_URL:
        return None

    try:
        payload = {
            "post_text": post_text,
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
        }
        if evidence_text:
            payload["evidence_text"] = evidence_text
        if max_chars:
            payload["max_chars"] = max_chars
        resp = requests.post(FINETUNED_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        raw = (resp.json().get("note_text") or "").strip()
        cleaned = _clean(raw)
        # Note: previously had post-hoc length truncation here, but it was
        # cutting at name-abbreviation periods like "Jr. " and producing
        # nonsense fragments. The validator downstream will reject anything
        # too long, and write_note.py falls back to Opus on Qwen failures.
        return cleaned or None
    except requests.RequestException as e:
        logger.warning("Fine-tuned endpoint call failed (%s); falling back.", e)
        return None
