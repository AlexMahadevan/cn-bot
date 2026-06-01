#!/usr/bin/env python3
"""Stage A of the cross-writer benchmark rebuild: grow data/pipeline_cache.jsonl.

The held-out test split (used by evaluate_pipeline.py) is exhausted — all 332
helpful test tweets are processed and only 19 reached the writer stage. To get
a defensible n for the cross-writer comparison we need more cached evidence
packages, so this runs the retrieval pipeline over the *train* split of the
same labeled corpus.

This is sound for the WRITER comparison: the frontier writers don't train on
anything, so train-split tweets aren't leaked. We exclude the few-shot
exemplars (which ARE baked into the prompt) to avoid leakage, and skip any
note_id already processed in pipeline.jsonl. Setting CN_BOT_PIPELINE_CACHE_FILE
makes the pipeline dump each pre-writer evidence package to the cache.

Outputs (append, resumable): data/pipeline.jsonl  (+ the cache via the env hook)

Usage:
    .venv/bin/python scripts/build_pipeline_cache_expanded.py \
        --target-cache 85 --limit 1600 --concurrency 5
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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# The cache hook must be set before the pipeline runs.
CACHE_FILE = REPO / "data" / "pipeline_cache.jsonl"
os.environ.setdefault("CN_BOT_PIPELINE_CACHE_FILE", str(CACHE_FILE))

from data_models import Post  # noqa: E402
from note_writer.write_note import research_post_and_write_note  # noqa: E402

TRAIN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "pipeline.jsonl"
EXEMPLARS_PATH = REPO / "src" / "exemplars.json"
TEST_FRACTION = 0.1
logger = logging.getLogger("build_cache")


def _train_split(records: list[dict]) -> list[dict]:
    cutoff = int(len(records) * (1 - TEST_FRACTION))
    return sorted(records, key=lambda r: r["note_id"])[:cutoff]


def _exemplar_ids() -> tuple[set, set]:
    if not EXEMPLARS_PATH.exists():
        return set(), set()
    d = json.load(EXEMPLARS_PATH.open())
    ex = (d.get("helpful") or []) + (d.get("unhelpful") or [])
    return ({str(e.get("noteId")) for e in ex if e.get("noteId")},
            {str(e.get("tweetId")) for e in ex if e.get("tweetId")})


def _already_done() -> set:
    done = set()
    if OUT_PATH.exists():
        for line in OUT_PATH.open():
            try:
                done.add(str(json.loads(line)["note_id"]))
            except Exception:
                continue
    return done


def _cache_count() -> int:
    if not CACHE_FILE.exists():
        return 0
    n = 0
    for line in CACHE_FILE.open():
        if not line.strip():
            continue
        try:
            json.loads(line)
            n += 1
        except Exception:
            pass  # tolerate (shouldn't happen now that the write is locked)
    return n


def _process(rec: dict) -> dict:
    post = Post(post_id=str(rec["tweet"]["tweet_id"]), text=rec["tweet"]["tweet_text"])
    base = {
        "note_id": rec["note_id"],
        "tweet_id": str(rec["tweet"]["tweet_id"]),
        "tweet_text": rec["tweet"]["tweet_text"],
        "target_note": rec["note"]["text"],
    }
    try:
        result = research_post_and_write_note(post)
    except Exception as e:
        return {**base, "outcome": "error", "error": f"{type(e).__name__}: {e}"}
    if result.note:
        return {
            **base, "outcome": "note",
            "generated_note": result.note.note_text,
            "evidence_url": result.note.evidence_url,
            "evidence_tier": result.evidence[0].evidence_tier if result.evidence else None,
            "evidence_publisher": result.evidence[0].publisher_name if result.evidence else None,
        }
    return {**base, "outcome": "refused",
            "refusal": result.refusal or result.error or "(unspecified)"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1600, help="Max train items to process")
    ap.add_argument("--target-cache", type=int, default=85,
                    help="Stop submitting once the cache reaches this many records")
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    records = [json.loads(l) for l in TRAIN_PATH.open() if l.strip()]
    train = [r for r in _train_split(records) if r.get("label") == "helpful"]
    ex_notes, ex_tweets = _exemplar_ids()
    done = _already_done()
    pool = [r for r in train
            if str(r["note_id"]) not in done
            and str(r["note_id"]) not in ex_notes
            and str(r["tweet"]["tweet_id"]) not in ex_tweets][: args.limit]

    start_cache = _cache_count()
    logger.info("Train-helpful pool: %d (excl. %d exemplars + %d already-done). "
                "Cache now: %d. Target: %d. Max items: %d.",
                len(pool), len(ex_notes), len(done), start_cache, args.target_cache, args.limit)

    write_lock = threading.Lock()
    stop = threading.Event()
    n_done = n_ship = n_err = 0

    def _job(rec):
        if stop.is_set():
            return None
        try:
            return _process(rec)
        except Exception as e:
            logger.error("worker crash %s: %s", rec.get("note_id"), e)
            return None

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool_ex:
        futures = [pool_ex.submit(_job, r) for r in pool]
        for f in as_completed(futures):
            res = f.result()
            if res is None:
                continue
            with write_lock:
                with OUT_PATH.open("a") as out:
                    out.write(json.dumps(res) + "\n")
            n_done += 1
            n_ship += res["outcome"] == "note"
            n_err += res["outcome"] == "error"
            if n_done % 25 == 0:
                logger.info("processed %d/%d | shipped %d | errors %d | cache %d",
                            n_done, len(pool), n_ship, n_err, _cache_count())
            if not stop.is_set() and _cache_count() >= args.target_cache:
                logger.info("Reached target cache (%d) — stopping remaining submissions.",
                            args.target_cache)
                stop.set()

    logger.info("DONE. processed=%d shipped=%d errors=%d | cache now=%d (was %d)",
                n_done, n_ship, n_err, _cache_count(), start_cache)


if __name__ == "__main__":
    main()
