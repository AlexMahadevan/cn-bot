#!/usr/bin/env python3
"""Cached-pipeline cross-writer evaluation.

Reads data/pipeline_cache.jsonl (produced by an earlier run of
evaluate_pipeline.py with CN_BOT_PIPELINE_CACHE_FILE set), then for each
cached entry runs the writer step with a chosen model and the same
downstream validators the production pipeline runs. Logs shipped vs
refused per writer.

The point: control for retrieval variability across the writer comparison.
All writers see the *same* evidence package — only the writer model differs.

Usage:
    .venv/bin/python scripts/evaluate_pipeline_cached.py \
        --writers opus sonnet haiku gpt-5 gpt-5-mini gemini-pro gemini-flash grok-4 grok-4-fast qwen-v2 \
        --concurrency 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import HAIKU_MODEL, OPUS_MODEL, complete  # noqa: E402
from note_writer.multivendor_clients import (  # noqa: E402
    ALL_MODELS as MULTIVENDOR_MODELS,
    generate_note as multivendor_generate,
)
from note_writer import finetune_client  # noqa: E402
from note_writer.opinion_check import passes_opinion_filter  # noqa: E402
from note_writer.error_check import check_note as check_note_for_hallucination  # noqa: E402

logger = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO / "data" / "pipeline_cache.jsonl"
OUT_PATH = REPO / "data" / "pipeline_cached.jsonl"

NOTE_MAX_CHARS_INCLUDING_URL = 280
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

ANTHROPIC_WRITERS = {
    "opus": OPUS_MODEL,
    "sonnet": "claude-sonnet-4-6",
    "haiku": HAIKU_MODEL,
}

ALL_WRITERS = {
    **{k: ("anthropic", v) for k, v in ANTHROPIC_WRITERS.items()},
    **{k: ("multivendor", k) for k in MULTIVENDOR_MODELS},
    "qwen-v2": ("qwen", None),
}


def _generate_prose(cache_record: dict, writer: str) -> str | None:
    """Run the writer step with the chosen model. Returns prose or None."""
    kind, model_id = ALL_WRITERS[writer]
    if kind == "qwen":
        return finetune_client.generate_note(
            post_text=cache_record["post_text"],
            evidence_text=cache_record["article_text"]
                or cache_record["evidence"]["review_title"]
                or cache_record["evidence"]["claim_text"]
                or "",
            max_chars=cache_record["prose_budget"],
            max_new_tokens=220,
            temperature=0.3,
        )
    if kind == "anthropic":
        return complete(
            user_prompt=cache_record["user_prompt"],
            system=cache_record["system_prompt"],
            model=model_id,
            max_tokens=600,
            effort="high" if writer == "opus" else "medium",
            adaptive_thinking=(writer == "opus"),
        )
    if kind == "multivendor":
        return multivendor_generate(
            model_alias=writer,
            system=cache_record["system_prompt"],
            user=cache_record["user_prompt"],
            max_tokens=2000,
        )
    raise ValueError(f"Unknown writer kind: {kind}")


def _validate_prose(prose: str) -> tuple[bool, str]:
    """Hallucination guard: prose must not contain URLs (we append later)."""
    if not prose or not prose.strip():
        return False, "Empty prose"
    if prose.strip().upper() == "NO_NOTE":
        return False, "Writer returned NO_NOTE"
    if URL_RE.search(prose):
        return False, "Prose contains a URL (hallucination guard)"
    return True, ""


def _render_note(prose: str, url: str) -> str:
    return f"{prose.strip()} {url}".strip()


def _validate_final_note(note_text: str, url: str) -> tuple[bool, str]:
    if len(note_text) > NOTE_MAX_CHARS_INCLUDING_URL:
        return False, f"Note is {len(note_text)} chars (limit {NOTE_MAX_CHARS_INCLUDING_URL})"
    urls = URL_RE.findall(note_text)
    if len(urls) != 1:
        return False, f"Found {len(urls)} URLs, expected 1"
    if url not in note_text:
        return False, "Verified URL missing from note"
    return True, ""


def _already_done(writer: str) -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("writer") == writer:
                    done.add(r["post_id"])
            except Exception:
                continue
    return done


def _process(cache_record: dict, writer: str) -> dict:
    """Run writer + validators for one (cache_record, writer) pair."""
    post_id = cache_record["post_id"]
    evidence = cache_record["evidence"]
    review_url = evidence["review_url"]
    article_text = cache_record["article_text"]

    # 1. Generate prose
    try:
        prose = _generate_prose(cache_record, writer)
    except Exception as e:
        return {
            "post_id": post_id, "writer": writer, "shipped": False,
            "failure_stage": "writer_error", "failure_reason": repr(e)[:300],
            "prose": "", "final_note": "",
        }

    # 2. URL-hallucination guard
    ok, why = _validate_prose(prose or "")
    if not ok:
        return {
            "post_id": post_id, "writer": writer, "shipped": False,
            "failure_stage": "url_guard", "failure_reason": why,
            "prose": prose or "", "final_note": "",
        }

    # 3. Opinion filter
    try:
        ok, why = passes_opinion_filter(prose)
    except Exception as e:
        return {
            "post_id": post_id, "writer": writer, "shipped": False,
            "failure_stage": "opinion_filter_error", "failure_reason": repr(e)[:300],
            "prose": prose, "final_note": "",
        }
    if not ok:
        return {
            "post_id": post_id, "writer": writer, "shipped": False,
            "failure_stage": "opinion_filter", "failure_reason": why,
            "prose": prose, "final_note": "",
        }

    # 4. Render and validate final form
    note_text = _render_note(prose, review_url)
    ok, why = _validate_final_note(note_text, review_url)
    if not ok:
        return {
            "post_id": post_id, "writer": writer, "shipped": False,
            "failure_stage": "final_form", "failure_reason": why,
            "prose": prose, "final_note": note_text,
        }

    # 5. Hallucination check (only when we have article text)
    if article_text:
        try:
            ok, why, _ = check_note_for_hallucination(
                prose, article_text,
                publisher=evidence["publisher_name"],
                rating=evidence.get("rating"),
            )
        except Exception as e:
            return {
                "post_id": post_id, "writer": writer, "shipped": False,
                "failure_stage": "hallucination_check_error", "failure_reason": repr(e)[:300],
                "prose": prose, "final_note": note_text,
            }
        if not ok:
            return {
                "post_id": post_id, "writer": writer, "shipped": False,
                "failure_stage": "hallucination_check", "failure_reason": why,
                "prose": prose, "final_note": note_text,
            }

    return {
        "post_id": post_id, "writer": writer, "shipped": True,
        "failure_stage": None, "failure_reason": None,
        "prose": prose, "final_note": note_text,
        "evidence_url": review_url,
        "evidence_publisher": evidence["publisher_name"],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--writers", nargs="+", required=True,
                        help=f"Writer aliases to evaluate. Available: {sorted(ALL_WRITERS.keys())}")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    for w in args.writers:
        if w not in ALL_WRITERS:
            sys.exit(f"Unknown writer: {w}. Available: {sorted(ALL_WRITERS.keys())}")

    if not CACHE_PATH.exists():
        sys.exit(f"No cache at {CACHE_PATH}. Run evaluate_pipeline.py with CN_BOT_PIPELINE_CACHE_FILE first.")

    with CACHE_PATH.open() as f:
        cache = [json.loads(l) for l in f if l.strip()]
    if args.limit:
        cache = cache[: args.limit]
    logger.info("Loaded %d cached evidence packages", len(cache))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()

    for writer in args.writers:
        done = _already_done(writer)
        todo = [c for c in cache if c["post_id"] not in done]
        logger.info("[%s] %d to process (%d already done)", writer, len(todo), len(done))

        n_done = 0

        def _job(c, writer=writer):
            return _process(c, writer)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(_job, c) for c in todo]
            for f in as_completed(futures):
                r = f.result()
                with write_lock:
                    with OUT_PATH.open("a") as out:
                        out.write(json.dumps(r) + "\n")
                n_done += 1
                if n_done % 10 == 0:
                    logger.info("[%s] %d / %d", writer, n_done, len(todo))

        with OUT_PATH.open() as f:
            shipped = sum(1 for line in f
                          if (rec := json.loads(line))["writer"] == writer and rec["shipped"])
        logger.info("[%s] done. %d shipped of %d", writer, shipped, len(cache))


if __name__ == "__main__":
    main()
