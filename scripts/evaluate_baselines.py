#!/usr/bin/env python3
"""Establish the bar that a fine-tuned model needs to beat.

Reads data/cn_training.jsonl, holds out a fixed test split, then runs
each baseline model on every test tweet and writes generated notes to
data/baselines.jsonl. Scoring (LLM-as-judge against the actual helpful
note) happens in a separate step so we can iterate on rubrics without
re-running expensive generations.

Baselines:
  - opus-4-7
  - sonnet-4-6
  - haiku-4-5
  - (later) llama-3.1-8b zero-shot, via Modal

Usage:
    .venv/bin/python scripts/evaluate_baselines.py --models opus sonnet haiku --limit 100
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import HAIKU_MODEL, OPUS_MODEL, complete  # noqa: E402
from note_writer.multivendor_clients import ALL_MODELS as MULTIVENDOR_MODELS  # noqa: E402
from note_writer.multivendor_clients import generate_note as multivendor_generate  # noqa: E402

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
TRAIN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "baselines.jsonl"

ANTHROPIC_MODELS = {
    "opus": OPUS_MODEL,
    "sonnet": "claude-sonnet-4-6",
    "haiku": HAIKU_MODEL,
}

# Aliases the CLI accepts. Anthropic models route through `complete()`;
# everything in MULTIVENDOR_MODELS routes through multivendor_generate().
MODEL_IDS = {**ANTHROPIC_MODELS, **{k: k for k in MULTIVENDOR_MODELS}}

# Hold out a fixed last-N for test so we don't leak training examples.
# When we fine-tune, training uses train-set indices, eval uses test-set.
TEST_FRACTION = 0.1


_BASELINE_SYSTEM = """You are writing a Community Note for an X post. Your goal is to produce a note that X readers would rate "helpful."

Style guidelines (learned from real CRH notes):
- Direct factual correction; state what's true with a specific source.
- No "Publisher X rated this Y" framing unless that IS the cleanest correction.
- 1-2 sentences. Aim for 180-260 characters of prose.
- Be neutral. No editorial language. Cite primary sources or major news.
- The note text itself ends with one or more source URLs.

Now write the note for the post below. Return ONLY the note text — no preface, no explanation."""


def _read_jsonl(path: Path) -> Iterable[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _train_test_split(records: list[dict]) -> tuple[list[dict], list[dict]]:
    n = len(records)
    cutoff = int(n * (1 - TEST_FRACTION))
    # Deterministic: order by note_id so the same split holds across runs
    records_sorted = sorted(records, key=lambda r: r["note_id"])
    return records_sorted[:cutoff], records_sorted[cutoff:]


def _already_scored() -> set[tuple[str, str]]:
    """Returns (note_id, model) pairs already written."""
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
                done.add((rec["note_id"], rec["model"]))
            except Exception:
                continue
    return done


def _generate_note(model_alias: str, tweet_text: str) -> str:
    user_prompt = f"X post:\n\"\"\"\n{tweet_text}\n\"\"\"\n\nWrite the Community Note."
    if model_alias in ANTHROPIC_MODELS:
        return complete(
            user_prompt=user_prompt,
            system=_BASELINE_SYSTEM,
            model=ANTHROPIC_MODELS[model_alias],
            max_tokens=400,
            adaptive_thinking=(model_alias == "opus"),
            effort="medium",
        ).strip()
    if model_alias in MULTIVENDOR_MODELS:
        return multivendor_generate(
            model_alias=model_alias,
            system=_BASELINE_SYSTEM,
            user=user_prompt,
        ).strip()
    raise ValueError(f"Unknown model alias: {model_alias}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=list(MODEL_IDS.keys()),
                        default=["opus", "sonnet", "haiku"])
    parser.add_argument("--limit", type=int, default=200,
                        help="Cap test-set size for cost control during iteration.")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="Concurrent worker threads (Anthropic API calls in parallel).")
    args = parser.parse_args()

    if not TRAIN_PATH.exists():
        sys.exit(f"Training data not found at {TRAIN_PATH}. Run build_training_set.py first.")

    records = list(_read_jsonl(TRAIN_PATH))
    _, test = _train_test_split(records)
    # Only evaluate against examples whose label is 'helpful' — these are the ones
    # whose notes the model needs to match in style and accuracy.
    test_helpful = [r for r in test if r["label"] == "helpful"][: args.limit]
    logger.info("Test set: %d helpful examples (from %d total test rows)", len(test_helpful), len(test))

    done = _already_scored()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Build the work queue
    jobs = []
    for rec in test_helpful:
        for m in args.models:
            if (rec["note_id"], m) not in done:
                jobs.append((rec, m))
    n_skip = len(args.models) * len(test_helpful) - len(jobs)
    logger.info("Queued %d generations (%d skipped — already done)", len(jobs), n_skip)

    write_lock = threading.Lock()
    n_run = 0

    def _worker(job):
        rec, m = job
        tweet_text = rec["tweet"]["tweet_text"]
        try:
            generated = _generate_note(m, tweet_text)
        except Exception as e:
            logger.warning("%s failed for %s: %s", m, rec["note_id"], e)
            return None
        record = {
            "note_id": rec["note_id"],
            "tweet_id": rec["tweet"]["tweet_id"],
            "model": m,
            "tweet_text": tweet_text,
            "target_note": rec["note"]["text"],
            "generated_note": generated,
            "target_tags": rec["note"]["tags"],
        }
        with write_lock:
            with OUT_PATH.open("a") as out:
                out.write(json.dumps(record) + "\n")
        return m

    # Concurrent execution. 10 workers = ~10x speedup since calls are I/O bound.
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_worker, j) for j in jobs]
        for f in as_completed(futures):
            result = f.result()
            if result is not None:
                n_run += 1
                if n_run % 25 == 0:
                    logger.info("Generated %d / %d", n_run, len(jobs))

    logger.info("Done. generated=%d skipped=%d", n_run, n_skip)
    logger.info("Output: %s", OUT_PATH)


if __name__ == "__main__":
    main()
