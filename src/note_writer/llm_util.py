"""Anthropic SDK wrappers for the Community Notes bot.

Uses Opus 4.7 for note writing (highest quality, adaptive thinking)
and Haiku 4.5 for cheap classification tasks (relevance filter,
image description, misleading-tag tagging).
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Type, TypeVar

import anthropic
import dotenv
from pydantic import BaseModel

dotenv.load_dotenv()

OPUS_MODEL = "claude-opus-4-7"
SONNET_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL = "claude-haiku-4-5"

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


def complete(
    *,
    user_prompt: str,
    system: Optional[str | List[dict]] = None,
    model: str = OPUS_MODEL,
    max_tokens: int = 4096,
    effort: str = "high",
    adaptive_thinking: bool = True,
) -> str:
    """Generic text completion. Returns concatenated text content."""
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system is not None:
        kwargs["system"] = system
    if adaptive_thinking and model == OPUS_MODEL:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort}

    response = client().messages.create(**kwargs)
    return "".join(_text_blocks(response.content)).strip()


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
) -> T:
    """Structured output parsed into the given Pydantic model."""
    json_schema = _strictify_schema(schema.model_json_schema())

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": json_schema}},
    }
    if system is not None:
        kwargs["system"] = system

    response = client().messages.create(**kwargs)
    text = "".join(_text_blocks(response.content)).strip()
    return schema.model_validate_json(text)


def describe_image(image_url: str) -> str:
    """Brief description of an image. Uses Haiku for cost — vision is cheap on Haiku 4.5."""
    response = client().messages.create(
        model=HAIKU_MODEL,
        max_tokens=400,
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
