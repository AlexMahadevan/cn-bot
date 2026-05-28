#!/usr/bin/env python3
"""Run all 332 test tweets through the fine-tuned Qwen 2.5 7B endpoint.

Same input as the baseline eval (just the tweet, no evidence pipeline) so
the comparison is apples-to-apples vs Opus/Sonnet/Haiku zero-shot.

Writes data/finetuned.jsonl with one row per (tweet, generated_note).

Usage:
    .venv/bin/python scripts/evaluate_finetuned.py --concurrency 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
TRAIN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "finetuned.jsonl"
logger = logging.getLogger(__name__)

# Hard-coded since we deploy is the same URL every time.
ENDPOINT = os.getenv(
    "CN_BOT_FINETUNED_URL",
    "https://amahadevan--cn-bot-inference-finetunednotewriter-generate.modal.run",
)

TEST_FRACTION = 0.1

# Patterns that signal the model has rolled past its own turn — happens at
# sampling temperatures. Truncate at the first one we find.
_STOP_MARKERS = [
    "<|im_start|>",
    "<|im_end|>",
    "\nWrite the Community Note",
    "\n\nWrite the Community Note",
    "\nX post:",
    "\n\nX post:",
]


def _train_test_split(records: list[dict]) -> tuple[list[dict], list[dict]]:
    n = len(records)
    cutoff = int(n * (1 - TEST_FRACTION))
    sorted_recs = sorted(records, key=lambda r: r["note_id"])
    return sorted_recs[:cutoff], sorted_recs[cutoff:]


def _clean(text: str) -> str:
    """Truncate at first stop marker; strip surrounding whitespace."""
    if not text:
        return ""
    # Remove a leading "Write the Community Note:" echo if present
    if text.lstrip().startswith("Write the Community Note"):
        # find the first newline after that header and skip past it
        idx = text.find("\n")
        if idx >= 0:
            text = text[idx + 1:]
    for m in _STOP_MARKERS:
        idx = text.find(m)
        if idx > 0:
            text = text[:idx]
    return text.strip()


def _already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return {json.loads(l)["note_id"] for l in OUT_PATH.open() if l.strip()}


def _gen(tweet_text: str, *, retries: int = 2) -> str | None:
    for attempt in range(retries + 1):
        try:
            r = requests.post(
                ENDPOINT,
                json={"post_text": tweet_text, "max_new_tokens": 220, "temperature": 0.4},
                timeout=120,
            )
            if r.status_code == 200:
                return r.json().get("note_text", "")
            logger.warning("HTTP %d (attempt %d)", r.status_code, attempt + 1)
        except requests.RequestException as e:
            logger.warning("RequestException (attempt %d): %s", attempt + 1, e)
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Modal endpoint can handle ~4-8 concurrent calls comfortably.")
    parser.add_argument("--limit", type=int, default=0, help="Cap test size (0=all)")
    args = parser.parse_args()

    if not TRAIN_PATH.exists():
        sys.exit(f"No training data at {TRAIN_PATH}.")

    with TRAIN_PATH.open() as f:
        records = [json.loads(l) for l in f if l.strip()]
    _, test = _train_test_split(records)
    test_helpful = [r for r in test if r["label"] == "helpful"]
    if args.limit:
        test_helpful = test_helpful[: args.limit]

    done = _already_done()
    todo = [r for r in test_helpful if r["note_id"] not in done]
    logger.info("Fine-tuned eval: %d to process (%d already done)", len(todo), len(done))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    n_done = 0

    def _job(rec: dict):
        raw = _gen(rec["tweet"]["tweet_text"])
        if raw is None:
            return None
        cleaned = _clean(raw)
        return {
            "note_id": rec["note_id"],
            "tweet_id": str(rec["tweet"]["tweet_id"]),
            "model": "qwen25-7b-lora",
            "tweet_text": rec["tweet"]["tweet_text"],
            "target_note": rec["note"]["text"],
            "generated_note": cleaned,
            "generated_raw_len": len(raw),
        }

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, r) for r in todo]
        for f in as_completed(futures):
            res = f.result()
            if res is None:
                continue
            with write_lock:
                with OUT_PATH.open("a") as out:
                    out.write(json.dumps(res) + "\n")
            n_done += 1
            if n_done % 25 == 0:
                logger.info("Generated %d / %d", n_done, len(todo))

    logger.info("Done. generated=%d", n_done)


if __name__ == "__main__":
    main()
