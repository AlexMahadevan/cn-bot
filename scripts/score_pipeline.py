#!/usr/bin/env python3
"""Score the pipeline's notes using the same judge as score_baselines.py.

Reads data/pipeline.jsonl, for each row where outcome == 'note' submits
(tweet, gold_note, generated_note) to Opus 4.7 as judge. Same axes as
the baseline judge: factual_accuracy, style_match, opinion_neutrality,
predicted_helpfulness. Treats the pipeline as a fourth model alongside
opus/sonnet/haiku for direct comparison.

Writes data/pipeline_scored.jsonl, prints aggregate report.

Usage:
    .venv/bin/python scripts/score_pipeline.py --concurrency 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import OPUS_MODEL, parse_json  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PIPELINE_PATH = REPO / "data" / "pipeline.jsonl"
SCORED_PATH = REPO / "data" / "pipeline_scored.jsonl"
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


def _already_scored() -> set[str]:
    if not SCORED_PATH.exists():
        return set()
    return {json.loads(l)["note_id"] for l in SCORED_PATH.open() if l.strip()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    if not PIPELINE_PATH.exists():
        sys.exit(f"No pipeline data at {PIPELINE_PATH}. Run evaluate_pipeline.py first.")

    with PIPELINE_PATH.open() as f:
        rows = [json.loads(l) for l in f if l.strip()]
    notes = [r for r in rows if r.get("outcome") == "note"]
    logger.info("Pipeline produced %d notes (of %d total)", len(notes), len(rows))

    done = _already_scored()
    todo = [r for r in notes if r["note_id"] not in done]
    logger.info("To score: %d (skipping %d already done)", len(todo), len(done))

    write_lock = threading.Lock()
    n_done = 0

    def _job(rec: dict):
        user_prompt = (
            f"X POST:\n{rec['tweet_text'].strip()}\n\n"
            f"GOLD-STANDARD NOTE:\n{rec['target_note'].strip()}\n\n"
            f"PIPELINE NOTE:\n{rec['generated_note'].strip()}\n\n"
            "Score the pipeline note on the four axes."
        )
        try:
            verdict = parse_json(
                user_prompt=user_prompt,
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
            "tweet_id": rec["tweet_id"],
            "model": "pipeline",
            "evidence_tier": rec.get("evidence_tier"),
            "evidence_publisher": rec.get("evidence_publisher"),
            "factual_accuracy": verdict.factual_accuracy,
            "style_match": verdict.style_match,
            "opinion_neutrality": verdict.opinion_neutrality,
            "predicted_helpfulness": verdict.predicted_helpfulness,
            "notes": verdict.notes,
        }
        with write_lock:
            with SCORED_PATH.open("a") as f:
                f.write(json.dumps(out) + "\n")
        return out

    SCORED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, r) for r in todo]
        for f in as_completed(futures):
            if f.result() is not None:
                n_done += 1

    logger.info("Done. scored=%d", n_done)
    print_report()


def print_report() -> None:
    if not SCORED_PATH.exists():
        print("No scored data.")
        return
    rows = [json.loads(l) for l in SCORED_PATH.open() if l.strip()]
    if not rows:
        print("No scored rows.")
        return

    def avg(key):
        return sum(r[key] for r in rows) / len(rows)

    print()
    print("=" * 70)
    print(f"PIPELINE SCORING REPORT  ({len(rows)} notes shipped by the pipeline)")
    print("=" * 70)
    print(f"  factual_accuracy:       {avg('factual_accuracy'):.2f} / 5")
    print(f"  style_match:            {avg('style_match'):.2f} / 5")
    print(f"  opinion_neutrality:     {avg('opinion_neutrality'):.2f} / 5")
    print(f"  predicted_helpfulness:  {avg('predicted_helpfulness'):.2f} / 5")
    print()
    print("Comparison vs. baselines (from data/baselines_scored.jsonl):")
    print("  model     | factual | style | neutr | pred-h ")
    print("  ----------|---------|-------|-------|--------")
    print(f"  pipeline  | {avg('factual_accuracy'):.2f}    | {avg('style_match'):.2f}  | {avg('opinion_neutrality'):.2f}  | {avg('predicted_helpfulness'):.2f}")
    print(f"  opus      | 2.80    | 3.31  | 4.11  | 2.39")
    print(f"  sonnet    | 2.45    | 2.80  | 3.70  | 1.94")
    print(f"  haiku     | 1.91    | 2.10  | 3.55  | 1.31")
    print()
    print("Caveat: pipeline n=%d only (vs n=332 for baselines)." % len(rows))


if __name__ == "__main__":
    main()
