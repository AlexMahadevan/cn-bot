#!/usr/bin/env python3
"""Score the baseline generations against the gold-standard helpful note.

For each test tweet, Opus 4.7 acts as judge: rates the 3 model outputs
on factual accuracy, style match, opinion-neutrality, and predicted
helpfulness — and ranks them pairwise. Model names are anonymized in
the prompt to reduce self-preference bias.

Writes data/baselines_scored.jsonl + prints aggregate report.

Usage:
    .venv/bin/python scripts/score_baselines.py --concurrency 10
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import OPUS_MODEL, parse_json  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BASELINES_PATH = REPO / "data" / "baselines.jsonl"
SCORED_PATH = REPO / "data" / "baselines_scored.jsonl"
logger = logging.getLogger(__name__)


class CandidateScore(BaseModel):
    candidate_id: str = Field(description="Identifier from the prompt (A, B, or C)")
    factual_accuracy: int = Field(description="0-5: does the note state factually correct claims?")
    style_match: int = Field(description="0-5: is the note tight, direct, and citation-shaped (like real CRH notes)?")
    opinion_neutrality: int = Field(description="0-5: does the note avoid editorializing and speculative language?")
    predicted_helpfulness: int = Field(description="0-5: would X raters mark this note helpful?")
    notes: str = Field(description="One-sentence assessment.")


class JudgeVerdict(BaseModel):
    scores: list[CandidateScore]
    best_candidate_id: str = Field(description="A, B, or C — the candidate most likely to be rated CRH.")
    reason: str = Field(description="Why this candidate won.")


_JUDGE_SYSTEM = """You are evaluating Community Note draft candidates against a gold-standard helpful note that X raters approved. Score each candidate on four 0-5 axes and pick the best.

Scoring axes:

- **factual_accuracy (0-5):** Does the candidate state facts that match the gold-standard note's underlying claims? 5 = same factual ground; 0 = contradicts or fabricates.
- **style_match (0-5):** Does it read like a real CRH note — direct correction, named source, tight prose, ends with a citation? 5 = indistinguishable from a real helpful note; 0 = essay-shaped or rambling.
- **opinion_neutrality (0-5):** Does it avoid editorializing ("dangerous", "misleading framing", "Trump is trying to...") and speculative language ("appears to", "may have")? 5 = pure factual; 0 = full of judgmental language.
- **predicted_helpfulness (0-5):** Best-guess CRH probability. 5 = will almost certainly be rated helpful; 0 = will be ignored or rated not helpful.

The "gold standard" is provided as a reference for what factual content rated helpful. Candidates can use different phrasing — what matters is whether they make the SAME corrective claim with the SAME factual basis.

Output JSON with one CandidateScore per candidate and a best_candidate_id pick."""


def _score_one(record: dict, *, candidates: list[tuple[str, str]]) -> dict | None:
    """candidates is [(id_label, generated_note), ...]; id_label is shuffled A/B/C."""
    candidate_block = "\n\n".join(
        f"CANDIDATE {label}:\n{text.strip()}"
        for label, text in candidates
    )
    user_prompt = (
        f"X POST:\n{record['tweet_text'].strip()}\n\n"
        f"GOLD-STANDARD NOTE (rated helpful by X users):\n{record['target_note'].strip()}\n\n"
        f"{candidate_block}\n\n"
        "Score each candidate on the four axes and pick the best."
    )
    try:
        verdict: JudgeVerdict = parse_json(
            user_prompt=user_prompt,
            schema=JudgeVerdict,
            system=_JUDGE_SYSTEM,
            model=OPUS_MODEL,
            max_tokens=1500,
        )
    except Exception as e:
        logger.warning("Judge failed for note %s: %s", record.get("note_id"), e)
        return None

    return {
        "note_id": record["note_id"],
        "tweet_id": record["tweet_id"],
        "candidate_to_model": {label: model for label, (_, model) in zip([c[0] for c in candidates], [(t, m) for t, m in [(label, model) for label, model in zip([c[0] for c in candidates], record["_models_by_label"])]])},
        "verdict": verdict.model_dump(),
    }


def _by_note_id(records: list[dict]) -> dict[str, dict[str, dict]]:
    """Group baseline records by note_id, then by model.

    Returns {note_id: {"target_note": ..., "tweet_text": ..., "models": {model: generated_note}}}
    """
    grouped: dict[str, dict] = {}
    for r in records:
        nid = r["note_id"]
        if nid not in grouped:
            grouped[nid] = {
                "note_id": nid,
                "tweet_id": r["tweet_id"],
                "tweet_text": r["tweet_text"],
                "target_note": r["target_note"],
                "models": {},
            }
        grouped[nid]["models"][r["model"]] = r["generated_note"]
    return grouped


def _already_done() -> set[str]:
    if not SCORED_PATH.exists():
        return set()
    done = set()
    with SCORED_PATH.open() as f:
        for line in f:
            try:
                done.add(json.loads(line)["note_id"])
            except Exception:
                continue
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="Cap rows scored (0 = all).")
    args = parser.parse_args()

    if not BASELINES_PATH.exists():
        sys.exit(f"No baselines at {BASELINES_PATH}. Run evaluate_baselines.py first.")

    with BASELINES_PATH.open() as f:
        records = [json.loads(line) for line in f if line.strip()]

    grouped = _by_note_id(records)
    logger.info("%d unique note_ids in baselines (%d total rows)", len(grouped), len(records))

    done = _already_done()
    todo = [g for nid, g in grouped.items() if nid not in done]
    if args.limit:
        todo = todo[: args.limit]
    logger.info("To score: %d (skipping %d already done)", len(todo), len(done))

    rng = random.Random(42)
    write_lock = threading.Lock()
    n_done = 0

    def _job(item: dict):
        models = list(item["models"].keys())
        rng_local = random.Random(item["note_id"])
        rng_local.shuffle(models)
        # Support up to 26 candidates (A-Z) so cross-vendor leaderboards
        # can score all models head-to-head in one judge call.
        labels = [chr(ord("A") + i) for i in range(len(models))]
        candidates = [(label, item["models"][m]) for label, m in zip(labels, models)]
        label_to_model = dict(zip(labels, models))

        prompt_record = {
            "tweet_text": item["tweet_text"],
            "target_note": item["target_note"],
        }
        candidate_block = "\n\n".join(f"CANDIDATE {l}:\n{t.strip()}" for l, t in candidates)
        user_prompt = (
            f"X POST:\n{prompt_record['tweet_text'].strip()}\n\n"
            f"GOLD-STANDARD NOTE:\n{prompt_record['target_note'].strip()}\n\n"
            f"{candidate_block}\n\n"
            "Score each candidate on the four axes and pick the best."
        )
        try:
            verdict = parse_json(
                user_prompt=user_prompt,
                schema=JudgeVerdict,
                system=_JUDGE_SYSTEM,
                model=OPUS_MODEL,
                max_tokens=4000,
            )
        except Exception as e:
            logger.warning("Judge failed for %s: %s", item["note_id"], e)
            return None

        record = {
            "note_id": item["note_id"],
            "tweet_id": item["tweet_id"],
            "label_to_model": label_to_model,
            "best_label": verdict.best_candidate_id,
            "best_model": label_to_model.get(verdict.best_candidate_id),
            "reason": verdict.reason,
            "scores": [s.model_dump() for s in verdict.scores],
        }
        with write_lock:
            with SCORED_PATH.open("a") as out:
                out.write(json.dumps(record) + "\n")
        return record

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, item) for item in todo]
        for f in as_completed(futures):
            if f.result() is not None:
                n_done += 1
                if n_done % 25 == 0:
                    logger.info("Scored %d / %d", n_done, len(todo))

    logger.info("Done. scored=%d", n_done)
    print_report()


def print_report() -> None:
    """Aggregate scores from data/baselines_scored.jsonl and print a leaderboard."""
    if not SCORED_PATH.exists():
        print("No scored data yet.")
        return

    by_model_axis: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    wins = Counter()
    total_rows = 0

    with SCORED_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            total_rows += 1
            label_to_model = r["label_to_model"]
            if r.get("best_model"):
                wins[r["best_model"]] += 1
            for s in r["scores"]:
                model = label_to_model.get(s["candidate_id"])
                if not model:
                    continue
                for axis in ("factual_accuracy", "style_match", "opinion_neutrality", "predicted_helpfulness"):
                    by_model_axis[model][axis].append(int(s[axis]))

    print()
    print("=" * 78)
    print(f"BASELINE SCORING REPORT  ({total_rows} test items judged by Opus 4.7)")
    print("=" * 78)
    print()
    print(f"{'model':10s}  {'fact':>5s}  {'style':>5s}  {'neutr':>5s}  {'pred-h':>6s}  {'wins':>6s}")
    print("-" * 50)
    for model in sorted(by_model_axis.keys()):
        axes = by_model_axis[model]
        f_ = sum(axes['factual_accuracy']) / max(1, len(axes['factual_accuracy']))
        s_ = sum(axes['style_match']) / max(1, len(axes['style_match']))
        n_ = sum(axes['opinion_neutrality']) / max(1, len(axes['opinion_neutrality']))
        p_ = sum(axes['predicted_helpfulness']) / max(1, len(axes['predicted_helpfulness']))
        w = wins[model]
        pct = w / total_rows * 100 if total_rows else 0
        print(f"{model:10s}  {f_:5.2f}  {s_:5.2f}  {n_:5.2f}  {p_:6.2f}  {w:4d} ({pct:.0f}%)")
    print()


if __name__ == "__main__":
    main()
