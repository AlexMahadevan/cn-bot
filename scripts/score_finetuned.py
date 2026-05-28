#!/usr/bin/env python3
"""Score the fine-tuned model's generations the same way we scored baselines.

Reads data/finetuned.jsonl, judges each (tweet, target, generated) tuple
with Opus 4.7 on factual_accuracy / style_match / opinion_neutrality /
predicted_helpfulness. Writes data/finetuned_scored.jsonl. Prints a
full leaderboard combining baselines + pipeline + fine-tuned.
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

from note_writer.llm_util import OPUS_MODEL, parse_json  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
IN_PATH = REPO / "data" / "finetuned.jsonl"
OUT_PATH = REPO / "data" / "finetuned_scored.jsonl"
logger = logging.getLogger(__name__)


class NoteScore(BaseModel):
    factual_accuracy: int = Field(description="0-5 vs gold")
    style_match: int = Field(description="0-5 — direct, citation-shaped")
    opinion_neutrality: int = Field(description="0-5 — free of editorializing")
    predicted_helpfulness: int = Field(description="0-5 — would raters mark CRH?")
    notes: str


_JUDGE_SYSTEM = """Score a Community Note draft against the gold-standard helpful note.

Axes (0-5 each):
- factual_accuracy: same factual ground as gold = 5; contradicts/fabricates = 0
- style_match: direct, named source, citation-shaped = 5; essay/rambling = 0
- opinion_neutrality: pure factual = 5; full of editorializing = 0
- predicted_helpfulness: would X raters CRH this? 5 = yes, 0 = ignored or NRH

Gold standard is a reference for FACTS, not phrasing. Different words for the same correction = fine. Different facts = factual_accuracy hit. Return JSON."""


def _already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return {json.loads(l)["note_id"] for l in OUT_PATH.open() if l.strip()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    rows = [json.loads(l) for l in IN_PATH.open() if l.strip()]
    done = _already_done()
    todo = [r for r in rows if r["note_id"] not in done]
    logger.info("To score: %d (skipping %d already done)", len(todo), len(done))

    lock = threading.Lock()
    n_done = 0

    def _job(rec: dict):
        if not rec.get("generated_note"):
            return None
        try:
            v = parse_json(
                user_prompt=(
                    f"X POST:\n{rec['tweet_text'].strip()}\n\n"
                    f"GOLD-STANDARD NOTE:\n{rec['target_note'].strip()}\n\n"
                    f"FINE-TUNED NOTE:\n{rec['generated_note'].strip()}\n\n"
                    "Score the fine-tuned note."
                ),
                schema=NoteScore,
                system=_JUDGE_SYSTEM,
                model=OPUS_MODEL,
                max_tokens=800,
            )
        except Exception as e:
            logger.warning("Judge failed for %s: %s", rec["note_id"], e)
            return None

        out = {
            "note_id": rec["note_id"],
            "tweet_id": rec.get("tweet_id"),
            "model": "qwen25-7b-lora",
            "factual_accuracy": v.factual_accuracy,
            "style_match": v.style_match,
            "opinion_neutrality": v.opinion_neutrality,
            "predicted_helpfulness": v.predicted_helpfulness,
            "notes": v.notes,
        }
        with lock:
            with OUT_PATH.open("a") as f:
                f.write(json.dumps(out) + "\n")
        return out

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, r) for r in todo]
        for f in as_completed(futures):
            if f.result() is not None:
                n_done += 1

    logger.info("Done. scored=%d", n_done)
    leaderboard()


def leaderboard() -> None:
    """Print the full leaderboard: baselines + pipeline + fine-tuned."""
    pipeline_path = REPO / "data" / "pipeline_scored.jsonl"
    baselines_path = REPO / "data" / "baselines_scored.jsonl"

    # Pipeline scores
    pl = [json.loads(l) for l in pipeline_path.open() if l.strip()] if pipeline_path.exists() else []
    ft = [json.loads(l) for l in OUT_PATH.open() if l.strip()] if OUT_PATH.exists() else []

    # Baseline scores
    bl = [json.loads(l) for l in baselines_path.open() if l.strip()] if baselines_path.exists() else []
    by_model_axis: dict[str, dict[str, list[int]]] = {}
    for r in bl:
        for s in r["scores"]:
            m = r["label_to_model"].get(s["candidate_id"])
            if not m:
                continue
            by_model_axis.setdefault(m, {a: [] for a in ("factual_accuracy", "style_match", "opinion_neutrality", "predicted_helpfulness")})
            for a in by_model_axis[m]:
                by_model_axis[m][a].append(int(s[a]))

    def avg(rows: list[dict], key: str) -> float:
        vals = [r[key] for r in rows if key in r]
        return sum(vals) / len(vals) if vals else 0.0

    print()
    print("=" * 78)
    print("FULL LEADERBOARD — baselines + pipeline + fine-tuned")
    print("=" * 78)
    print()
    print(f"  {'model':22s}  {'fact':>5s}  {'style':>5s}  {'neutr':>5s}  {'pred-h':>6s}   n  ")
    print("  " + "-" * 60)
    if pl:
        print(f"  {'pipeline (bot, full)':22s}  {avg(pl,'factual_accuracy'):.2f}   {avg(pl,'style_match'):.2f}   {avg(pl,'opinion_neutrality'):.2f}   {avg(pl,'predicted_helpfulness'):.2f}    {len(pl):3d}")
    if ft:
        print(f"  {'qwen25-7b-lora':22s}  {avg(ft,'factual_accuracy'):.2f}   {avg(ft,'style_match'):.2f}   {avg(ft,'opinion_neutrality'):.2f}   {avg(ft,'predicted_helpfulness'):.2f}    {len(ft):3d}")
    for model in sorted(by_model_axis.keys()):
        axes = by_model_axis[model]
        print(f"  {model + ' (zero-shot)':22s}  {sum(axes['factual_accuracy'])/len(axes['factual_accuracy']):.2f}   {sum(axes['style_match'])/len(axes['style_match']):.2f}   {sum(axes['opinion_neutrality'])/len(axes['opinion_neutrality']):.2f}   {sum(axes['predicted_helpfulness'])/len(axes['predicted_helpfulness']):.2f}    {len(axes['factual_accuracy']):3d}")
    print()


if __name__ == "__main__":
    main()
