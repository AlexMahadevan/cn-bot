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
from pathlib import Path
from typing import Iterable

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import HAIKU_MODEL, OPUS_MODEL, complete  # noqa: E402

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
TRAIN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "baselines.jsonl"

MODEL_IDS = {
    "opus": OPUS_MODEL,
    "sonnet": "claude-sonnet-4-6",
    "haiku": HAIKU_MODEL,
}

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
    model_id = MODEL_IDS[model_alias]
    user_prompt = f"X post:\n\"\"\"\n{tweet_text}\n\"\"\"\n\nWrite the Community Note."
    return complete(
        user_prompt=user_prompt,
        system=_BASELINE_SYSTEM,
        model=model_id,
        max_tokens=400,
        adaptive_thinking=(model_alias == "opus"),  # Opus 4.7 supports adaptive; others don't need
        effort="medium",
    ).strip()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=list(MODEL_IDS.keys()),
                        default=["opus", "sonnet", "haiku"])
    parser.add_argument("--limit", type=int, default=200,
                        help="Cap test-set size for cost control during iteration.")
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
    n_run = 0
    n_skip = 0

    with OUT_PATH.open("a") as out:
        for rec in test_helpful:
            tweet_text = rec["tweet"]["tweet_text"]
            target_note = rec["note"]["text"]
            for m in args.models:
                if (rec["note_id"], m) in done:
                    n_skip += 1
                    continue
                try:
                    generated = _generate_note(m, tweet_text)
                except Exception as e:
                    logger.warning("%s failed for %s: %s", m, rec["note_id"], e)
                    continue
                out.write(json.dumps({
                    "note_id": rec["note_id"],
                    "tweet_id": rec["tweet"]["tweet_id"],
                    "model": m,
                    "tweet_text": tweet_text,
                    "target_note": target_note,
                    "generated_note": generated,
                    "target_tags": rec["note"]["tags"],
                }) + "\n")
                out.flush()
                n_run += 1
                if n_run % 10 == 0:
                    logger.info("Generated %d (skipped %d already done)", n_run, n_skip)

    logger.info("Done. generated=%d skipped=%d", n_run, n_skip)
    logger.info("Output: %s", OUT_PATH)


if __name__ == "__main__":
    main()
