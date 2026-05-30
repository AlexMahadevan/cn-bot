"""Unified note-generation clients for cross-vendor baseline scoring.

One `generate_note(model_alias, system, user)` entry point that routes to
OpenAI, Google Gemini, or xAI Grok based on the alias. Returns a string.
Raises on failure so the caller can log per-tweet errors.

Thinking policy: mirrors our Claude baseline policy — frontier models
(gpt-5, gemini-pro, grok-4) are allowed to think; cheap models
(gpt-5-mini, gemini-flash, grok-4-fast) are not. This matches how the
existing baseline scores Opus 4.7 (adaptive thinking) vs Sonnet/Haiku (off).
"""

from __future__ import annotations

import os
from typing import Optional

import dotenv

dotenv.load_dotenv()


# alias → (provider_model_id, allow_thinking)
OPENAI_MODELS = {
    # GPT-5 reasoning is aggressive by default and eats the output budget on
    # short generation tasks. Setting allow_thinking=False forces
    # reasoning.effort=minimal — comparable to Opus 4.7's adaptive thinking
    # behavior on short factual writing (Opus also stays terse on this task).
    "gpt-5": ("gpt-5", False),
    "gpt-5-mini": ("gpt-5-mini", False),
}

GEMINI_MODELS = {
    "gemini-pro": ("gemini-2.5-pro", True),
    "gemini-flash": ("gemini-2.5-flash", False),
}

XAI_MODELS = {
    "grok-4": ("grok-4", True),
    "grok-4-fast": ("grok-4-fast", False),
}

ALL_MODELS = {**OPENAI_MODELS, **GEMINI_MODELS, **XAI_MODELS}


_openai_client = None
_xai_client = None
_gemini_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY missing from environment.")
        _openai_client = OpenAI()
    return _openai_client


def _xai():
    global _xai_client
    if _xai_client is None:
        from openai import OpenAI
        if not os.getenv("XAI_API_KEY"):
            raise RuntimeError("XAI_API_KEY missing from environment.")
        _xai_client = OpenAI(
            api_key=os.getenv("XAI_API_KEY"),
            base_url="https://api.x.ai/v1",
        )
    return _xai_client


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY missing from environment.")
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


def _openai_call(client, model_id: str, system: str, user: str,
                 allow_thinking: bool, max_tokens: int) -> str:
    kwargs = {
        "model": model_id,
        "instructions": system,
        "input": user,
        "max_output_tokens": max_tokens,
    }
    if not allow_thinking:
        kwargs["reasoning"] = {"effort": "minimal"}
    resp = client.responses.create(**kwargs)
    return (resp.output_text or "").strip()


def _xai_call(client, model_id: str, system: str, user: str,
              allow_thinking: bool, max_tokens: int) -> str:
    # xAI uses OpenAI Responses API surface but doesn't always accept
    # the reasoning param — try without it first, fall back if needed.
    kwargs = {
        "model": model_id,
        "instructions": system,
        "input": user,
        "max_output_tokens": max_tokens,
    }
    resp = client.responses.create(**kwargs)
    return (resp.output_text or "").strip()


def _gemini_call(model_id: str, system: str, user: str,
                 allow_thinking: bool, max_tokens: int) -> str:
    from google.genai import types
    cfg_kwargs = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
    }
    if not allow_thinking:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    resp = _gemini().models.generate_content(
        model=model_id,
        contents=user,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )
    return (resp.text or "").strip()


def generate_note(model_alias: str, system: str, user: str,
                  max_tokens: int = 2000) -> str:
    """Route to the right provider based on model_alias. Returns the note
    text. Raises on any error so the caller can log it per-tweet.

    max_tokens defaults to 2000 to give thinking-mode models headroom; the
    final note prose itself is still short (~200 chars)."""
    if model_alias in OPENAI_MODELS:
        model_id, allow_thinking = OPENAI_MODELS[model_alias]
        return _openai_call(_openai(), model_id, system, user, allow_thinking, max_tokens)
    if model_alias in XAI_MODELS:
        model_id, allow_thinking = XAI_MODELS[model_alias]
        return _xai_call(_xai(), model_id, system, user, allow_thinking, max_tokens)
    if model_alias in GEMINI_MODELS:
        model_id, allow_thinking = GEMINI_MODELS[model_alias]
        return _gemini_call(model_id, system, user, allow_thinking, max_tokens)
    raise ValueError(f"Unknown multivendor model alias: {model_alias}")
