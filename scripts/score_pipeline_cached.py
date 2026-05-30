#!/usr/bin/env python3
"""Score cross-writer pipeline outputs against the gold-standard CRH note.

Reads data/pipeline_cached.jsonl (one row per (post_id, writer) pair) and
data/pipeline.jsonl (for tweet_text + target_note keyed by tweet_id).
For each shipped note across all writers, asks the judge to score it on
the four standard axes.

Outputs:
- data/pipeline_cached_scored.jsonl — per-row judge scores
- prints per-writer aggregate report

Usage:
    .venv/bin/python scripts/score_pipeline_cached.py \\
        --concurrency 8 \\
        --judge opus     # or gpt-5 for the bias cross-check
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import OPUS_MODEL, parse_json  # noqa: E402
from note_writer.multivendor_clients import generate_note as multivendor_generate  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CACHED_PATH = REPO / "data" / "pipeline_cached.jsonl"
PIPELINE_PATH = REPO / "data" / "pipeline.jsonl"
SCORED_PATH = REPO / "data" / "pipeline_cached_scored.jsonl"
logger = logging.getLogger(__name__)


class NoteScore(BaseModel):
    factual_accuracy: int = Field(description="0-5 vs. gold-standard CRH note")
    style_match: int = Field(description="0-5 — direct, named-source, citation-shaped")
    opinion_neutrality: int = Field(description="0-5 — free of editorializing/speculation")
    predicted_helpfulness: int = Field(description="0-5 — would X raters mark CRH?")
    notes: str


_JUDGE_SYSTEM = """You are scoring a Community Note candidate against the gold-standard helpful note that X raters approved.

Score on four 0-5 axes:

- **factual_accuracy:** Does the candidate state facts consistent with the gold-standard's claims? Same factual ground = 5; contradicts/fabricates = 0.
- **style_match:** Tight, direct, named source, citation-shaped (like real CRH notes)? 5 = indistinguishable from a real helpful note.
- **opinion_neutrality:** Avoids editorializing ("dangerous", "misleading framing") and speculation ("appears", "may have")? 5 = pure factual.
- **predicted_helpfulness:** Best-guess CRH probability if X raters saw this note on the post.

Gold standard is a reference for FACTS, not phrasing. Candidates can use different words for the same correction — what matters is whether they make the same corrective claim with the same factual basis.

Return JSON."""


def _judge_anthropic(user_prompt: str) -> NoteScore | None:
    return parse_json(
        user_prompt=user_prompt,
        schema=NoteScore,
        system=_JUDGE_SYSTEM,
        model=OPUS_MODEL,
        max_tokens=1500,
    )


def _judge_multivendor(model_alias: str, user_prompt: str) -> NoteScore | None:
    """Use a non-Anthropic judge for the bias cross-check. Asks the model
    for JSON in the user prompt; parses with Pydantic."""
    raw = multivendor_generate(
        model_alias=model_alias,
        system=_JUDGE_SYSTEM + "\n\nReturn ONLY a JSON object with keys factual_accuracy, style_match, opinion_neutrality, predicted_helpfulness, notes. No prose around it.",
        user=user_prompt,
        max_tokens=2000,
    )
    # Strip code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return NoteScore.model_validate_json(raw)


def _already_scored(judge: str) -> set[tuple[str, str]]:
    """Returns (post_id, writer) pairs already scored under this judge."""
    if not SCORED_PATH.exists():
        return set()
    done = set()
    with SCORED_PATH.open() as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("judge") == judge:
                    done.add((r["post_id"], r["writer"]))
            except Exception:
                continue
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--judge", default="opus", choices=["opus", "gpt-5", "gemini-pro"],
                        help="Which model judges. Use gpt-5 or gemini-pro for self-preference bias check.")
    args = parser.parse_args()

    if not CACHED_PATH.exists():
        sys.exit(f"No cached eval data at {CACHED_PATH}.")
    if not PIPELINE_PATH.exists():
        sys.exit(f"No pipeline.jsonl at {PIPELINE_PATH}.")

    # Build a map tweet_id → (tweet_text, target_note) from pipeline.jsonl
    tweet_map: dict[str, dict] = {}
    with PIPELINE_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            tweet_map[str(r["tweet_id"])] = {
                "tweet_text": r["tweet_text"],
                "target_note": r["target_note"],
                "note_id": r["note_id"],
            }

    # Read cached eval rows, filter to shipped notes
    with CACHED_PATH.open() as f:
        rows = [json.loads(l) for l in f if l.strip()]
    shipped = [r for r in rows if r.get("shipped")]
    logger.info("Total cached rows: %d, shipped: %d", len(rows), len(shipped))

    done = _already_scored(args.judge)
    todo = [r for r in shipped if (r["post_id"], r["writer"]) not in done]
    logger.info("To score (judge=%s): %d (skipping %d already done)", args.judge, len(todo), len(done))

    SCORED_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    n_done = 0

    def _job(rec: dict):
        post_id = str(rec["post_id"])
        meta = tweet_map.get(post_id)
        if not meta:
            logger.warning("No tweet_map entry for post_id=%s", post_id)
            return None
        user_prompt = (
            f"X POST:\n{meta['tweet_text'].strip()}\n\n"
            f"GOLD-STANDARD NOTE:\n{meta['target_note'].strip()}\n\n"
            f"PIPELINE NOTE (writer={rec['writer']}):\n{rec['final_note'].strip()}\n\n"
            "Score the pipeline note on the four axes."
        )
        try:
            if args.judge == "opus":
                score = _judge_anthropic(user_prompt)
            else:
                score = _judge_multivendor(args.judge, user_prompt)
        except Exception as e:
            logger.warning("Judge %s failed for %s/%s: %s", args.judge, post_id, rec["writer"], e)
            return None

        record = {
            "post_id": post_id,
            "writer": rec["writer"],
            "judge": args.judge,
            "note_id": meta["note_id"],
            **score.model_dump(),
        }
        with write_lock:
            with SCORED_PATH.open("a") as out:
                out.write(json.dumps(record) + "\n")
        return record

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, r) for r in todo]
        for f in as_completed(futures):
            r = f.result()
            if r:
                n_done += 1
                if n_done % 10 == 0:
                    logger.info("Scored %d / %d", n_done, len(todo))

    # Aggregate report
    by_writer = defaultdict(lambda: defaultdict(list))
    with SCORED_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("judge") != args.judge:
                continue
            for axis in ("factual_accuracy", "style_match", "opinion_neutrality", "predicted_helpfulness"):
                by_writer[r["writer"]][axis].append(int(r[axis]))

    print()
    print("=" * 80)
    print(f"CACHED PIPELINE SCORING — judge={args.judge}")
    print("=" * 80)
    print(f"{'writer':14s}  {'fact':>5s}  {'style':>5s}  {'neutr':>5s}  {'pred-h':>6s}  {'n':>3s}")
    print("-" * 60)
    # Sort by pred-helpfulness desc
    rows_sorted = sorted(
        by_writer.items(),
        key=lambda kv: -sum(kv[1]["predicted_helpfulness"]) / max(1, len(kv[1]["predicted_helpfulness"])),
    )
    for writer, axes in rows_sorted:
        n = len(axes["factual_accuracy"])
        f_ = sum(axes["factual_accuracy"]) / max(1, n)
        s_ = sum(axes["style_match"]) / max(1, n)
        nt = sum(axes["opinion_neutrality"]) / max(1, n)
        p_ = sum(axes["predicted_helpfulness"]) / max(1, n)
        print(f"{writer:14s}  {f_:5.2f}  {s_:5.2f}  {nt:5.2f}  {p_:6.2f}  {n:3d}")
    print()


if __name__ == "__main__":
    main()
