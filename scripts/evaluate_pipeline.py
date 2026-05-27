#!/usr/bin/env python3
"""Score the FULL bot pipeline on the same held-out test set as the baselines.

The baseline eval gave each LLM just a tweet and asked for a note. This
eval routes each test tweet through the actual bot — relevance filter,
specificity check, evidence search (PolitiFact + Fact Check Tools API),
best-evidence picker, article fetch, Opus 4.7 note write, opinion filter,
hallucination check, URL validator.

That gives us the headline comparison: does our architecture beat raw
Opus 4.7? If the answer is yes, the paper's core contribution is the
architecture, not the choice of underlying model.

Writes data/pipeline.jsonl. The pre-existing score_baselines.py format
is compatible — we can join across both files and rank everything together.

Usage:
    .venv/bin/python scripts/evaluate_pipeline.py --concurrency 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_models import Post  # noqa: E402
from note_writer.write_note import research_post_and_write_note  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TRAIN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "pipeline.jsonl"
logger = logging.getLogger(__name__)

# Same train/test split as evaluate_baselines.py so the test sets line up.
TEST_FRACTION = 0.1


def _train_test_split(records: list[dict]) -> tuple[list[dict], list[dict]]:
    n = len(records)
    cutoff = int(n * (1 - TEST_FRACTION))
    sorted_recs = sorted(records, key=lambda r: r["note_id"])
    return sorted_recs[:cutoff], sorted_recs[cutoff:]


def _already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                done.add(json.loads(line)["note_id"])
            except Exception:
                continue
    return done


def _process(rec: dict) -> dict:
    """Run the full pipeline on one test record. Always returns a result dict,
    even on refusal/error, with the outcome recorded."""
    post = Post(
        post_id=str(rec["tweet"]["tweet_id"]),
        text=rec["tweet"]["tweet_text"],
    )
    try:
        result = research_post_and_write_note(post)
    except Exception as e:
        return {
            "note_id": rec["note_id"],
            "tweet_id": str(rec["tweet"]["tweet_id"]),
            "outcome": "error",
            "error": f"{type(e).__name__}: {e}",
            "tweet_text": rec["tweet"]["tweet_text"],
            "target_note": rec["note"]["text"],
        }

    if result.note:
        return {
            "note_id": rec["note_id"],
            "tweet_id": str(rec["tweet"]["tweet_id"]),
            "outcome": "note",
            "generated_note": result.note.note_text,
            "evidence_url": result.note.evidence_url,
            "evidence_tier": result.evidence[0].evidence_tier if result.evidence else None,
            "evidence_publisher": result.evidence[0].publisher_name if result.evidence else None,
            "tweet_text": rec["tweet"]["tweet_text"],
            "target_note": rec["note"]["text"],
        }
    return {
        "note_id": rec["note_id"],
        "tweet_id": str(rec["tweet"]["tweet_id"]),
        "outcome": "refused",
        "refusal": result.refusal or result.error or "(unspecified)",
        "tweet_text": rec["tweet"]["tweet_text"],
        "target_note": rec["note"]["text"],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Quiet Anthropic HTTP noise; it's per-call and pollutes the log
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Parallel pipeline workers. Lower than baseline eval because pipeline is heavier and hits external rate-limited APIs.")
    parser.add_argument("--limit", type=int, default=0, help="Cap rows (0 = all)")
    args = parser.parse_args()

    if not TRAIN_PATH.exists():
        sys.exit(f"No training data at {TRAIN_PATH}. Run build_training_set.py first.")

    with TRAIN_PATH.open() as f:
        records = [json.loads(l) for l in f if l.strip()]

    _, test = _train_test_split(records)
    test_helpful = [r for r in test if r["label"] == "helpful"]
    if args.limit:
        test_helpful = test_helpful[: args.limit]

    done = _already_done()
    todo = [r for r in test_helpful if r["note_id"] not in done]
    logger.info("Pipeline eval: %d to process (%d already done)", len(todo), len(done))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    n_done = 0

    def _job(rec: dict):
        try:
            return _process(rec)
        except Exception as e:
            logger.error("Worker crashed on %s: %s", rec["note_id"], e)
            return None

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
            if n_done % 10 == 0:
                logger.info("Processed %d / %d", n_done, len(todo))

    logger.info("Done. processed=%d", n_done)
    print_summary()


def print_summary() -> None:
    if not OUT_PATH.exists():
        print("No pipeline data yet.")
        return
    rows = [json.loads(l) for l in OUT_PATH.open() if l.strip()]
    outcomes = {"note": 0, "refused": 0, "error": 0}
    refusal_buckets = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        if r["outcome"] == "refused":
            bucket = (r.get("refusal") or "").split(":")[0][:50]
            refusal_buckets[bucket] = refusal_buckets.get(bucket, 0) + 1

    print()
    print("=" * 70)
    print(f"PIPELINE EVAL SUMMARY  ({len(rows)} test items)")
    print("=" * 70)
    for k, v in outcomes.items():
        pct = v / len(rows) * 100 if rows else 0
        print(f"  {k:10s} {v:4d}  ({pct:.0f}%)")
    if refusal_buckets:
        print()
        print("Refusal reasons:")
        for b, c in sorted(refusal_buckets.items(), key=lambda x: -x[1]):
            print(f"  {c:4d}  {b}")
    print()


if __name__ == "__main__":
    main()
