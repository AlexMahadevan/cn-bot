"""Anthropic SDK wrappers for the Community Notes bot.

Model roles (swapped 2026-10-19, Alex's call):
  - Opus 5.5  writes the note prose (adaptive thinking, effort from config).
  - Haiku 5.5 runs the cheap stages: relevance, specificity, opinion check,
    evidence picking, tagging, image description, web_search evidence.

All three IDs are env-overridable so a revert is one variable, no code:
  CN_BOT_OPUS_MODEL / CN_BOT_SONNET_MODEL / CN_BOT_HAIKU_MODEL

Current-generation models (Opus 5.5, Sonnet 5.5, Haiku 5.5) think by default,
take `output_config.effort` as the only depth control, reject sampling params
and prefill, and can return `stop_reason == "refusal"` from safety classifiers.
`complete()` and `parse_json()` handle all of that; callers just pick a model
and an effort. Opus 5.5's thinking is always on (its default effort is
`medium`); Haiku 5.5 routes run at `low` so classification stays cheap.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, List, Optional, Type, TypeVar

import anthropic
import dotenv
from pydantic import BaseModel

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

OPUS_MODEL = os.getenv("CN_BOT_OPUS_MODEL", "claude-opus-5-5").strip()
SONNET_MODEL = os.getenv("CN_BOT_SONNET_MODEL", "claude-sonnet-5-5").strip()
HAIKU_MODEL = os.getenv("CN_BOT_HAIKU_MODEL", "claude-haiku-5-5").strip()

# Models that predate adaptive thinking / effort: never send those fields.
LEGACY_NO_ADAPTIVE = {"claude-haiku-4-5"}
_LEGACY_NO_ADAPTIVE = LEGACY_NO_ADAPTIVE  # internal alias

# Server-side refusal fallback (beta). On a safety-classifier decline the API
# re-runs the request on the model Anthropic recommends for that category.
# Only Opus/Sonnet 5.x accept it; Haiku 5.5 has NO fallback (a list 400s), so
# a Haiku refusal is handled client-side as "no answer".
_FALLBACK_HEADER = {"anthropic-beta": "server-side-fallback-2026-07-01"}


def _supports_fallback(model: str) -> bool:
    return model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-fable"))


def _is_refusal(response) -> bool:
    return getattr(response, "stop_reason", None) == "refusal"


class LLMRefusal(RuntimeError):
    """The model (and any fallback) declined the request on safety grounds."""

_client: Optional[anthropic.Anthropic] = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY missing from environment.")
        _client = anthropic.Anthropic()
    return _client


def _text_blocks(content: Iterable) -> List[str]:
    return [b.text for b in content if getattr(b, "type", None) == "text"]


def _cacheable_system(system: str | List[dict]) -> List[dict]:
    """Mark a static system prompt cacheable. The Opus note-writer prompt +
    exemplars is ~4.5K tokens and identical across every note in a run; cache
    reads bill at 0.05-0.1x input. The 5.x models' minimum cacheable prefix is
    512 tokens, so the Haiku-stage prompts now cache too. Below the minimum the
    marker is a silent no-op."""
    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return system


def complete(
    *,
    user_prompt: str,
    system: Optional[str | List[dict]] = None,
    model: str = HAIKU_MODEL,  # cheap default; callers pass an explicit model
    max_tokens: int = 4096,
    effort: str = "low",
    adaptive_thinking: bool = True,
) -> str:
    """Generic text completion. Returns concatenated text content.

    Raises LLMRefusal if the model (and its fallback, where enabled) declined.
    Thinking blocks are never returned; only text blocks are concatenated, so
    size `max_tokens` with headroom for thinking on current-gen models.
    """
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system is not None:
        kwargs["system"] = _cacheable_system(system)
    if model not in _LEGACY_NO_ADAPTIVE:
        # Current-gen models think on every call; `adaptive_thinking=False`
        # can only mean "as little as possible" (effort low), not "off".
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort if adaptive_thinking else "low"}
    if _supports_fallback(model):
        # SDK 0.104.1 predates the typed `fallbacks` kwarg; pass it raw.
        kwargs["extra_body"] = {"fallbacks": "default"}
        kwargs["extra_headers"] = _FALLBACK_HEADER

    response = client().messages.create(**kwargs)
    if _is_refusal(response):
        details = getattr(response, "stop_details", None)
        raise LLMRefusal(f"{model} declined (stop_details={details})")
    text = "".join(_text_blocks(response.content)).strip()
    if not text and getattr(response, "stop_reason", None) == "max_tokens":
        # Thinking counts toward max_tokens and is not returned as text. A cap
        # sized for a no-thinking model can be eaten entirely by a thinking
        # block (2026-10-19: a 60-token keyword call on Haiku 5.5 returned ""
        # and the Fact Check Tools tier got an empty query). Say so loudly.
        logger.warning(
            "%s hit max_tokens=%d with no text (thinking consumed the cap); raise max_tokens",
            model, max_tokens,
        )
    return text


T = TypeVar("T", bound=BaseModel)


_UNSUPPORTED_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                     "minLength", "maxLength", "multipleOf", "minItems", "maxItems"}


def _strictify_schema(node):
    """Sanitize a Pydantic-generated JSON schema for Anthropic structured outputs:
    - Add additionalProperties: false to every object node (required).
    - Strip unsupported numeric/string/array constraints (Anthropic rejects them).
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for key in list(node.keys()):
            if key in _UNSUPPORTED_KEYS:
                del node[key]
        for v in node.values():
            _strictify_schema(v)
    elif isinstance(node, list):
        for item in node:
            _strictify_schema(item)
    return node


def parse_json(
    *,
    user_prompt: str,
    schema: Type[T],
    system: Optional[str] = None,
    model: str = HAIKU_MODEL,
    max_tokens: int = 1024,
    effort: str = "low",
) -> T:
    """Structured output parsed into the given Pydantic model.

    Classification routes run at `low` effort: the model thinks briefly or not
    at all, and the old 256-400 token caps still fit (verified 2026-10-19:
    Haiku 5.5 answered a 256-cap JSON call in 73 output tokens).
    Raises LLMRefusal on a safety decline; callers already default-on-error.
    """
    json_schema = _strictify_schema(schema.model_json_schema())

    output_config: dict = {"format": {"type": "json_schema", "schema": json_schema}}
    if model not in _LEGACY_NO_ADAPTIVE:
        output_config["effort"] = effort
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
        "output_config": output_config,
    }
    if system is not None:
        kwargs["system"] = _cacheable_system(system)

    response = client().messages.create(**kwargs)
    if _is_refusal(response):
        raise LLMRefusal(f"{model} declined (stop_details={getattr(response, 'stop_details', None)})")
    text = "".join(_text_blocks(response.content)).strip()
    return schema.model_validate_json(text)


def describe_image(image_url: str) -> str:
    """Brief description of an image. Uses Haiku for cost."""
    kwargs: dict = {}
    if HAIKU_MODEL not in _LEGACY_NO_ADAPTIVE:
        kwargs["output_config"] = {"effort": "low"}
    response = client().messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,  # thinking counts toward the cap on current-gen models
        **kwargs,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": image_url}},
                    {"type": "text", "text": "Describe this image in 1-2 sentences. Note any text visible, public figures, or claims being made."},
                ],
            }
        ],
    )
    return "".join(_text_blocks(response.content)).strip()
