#!/usr/bin/env python3
"""Paired A/B: old writer prompt vs new (AI-CRH exemplars + 270-char budget),
scored by X's own free evaluate_note endpoint (claim_opinion_score — the same
model family behind the ClaimOpinion admission bucket).

Design: for each cached pre-writer state in data/pipeline_cache.jsonl, generate
prose twice with the live writer model:
  OLD — the cached system_prompt + cached user_prompt, verbatim (what the bot
        ran on June 1: human-CRH exemplars, ~200-char budget).
  NEW — the current _NOTE_WRITER_SYSTEM (AI-written CRH/CRNH exemplars,
        beat-aware first line) + the cached user_prompt with the budget
        instruction swapped for the 270-char version.
Evidence block is byte-identical between arms, so the comparison isolates the
prompt change. Both arms validate prose and render with the same verified URL,
then get scored by evaluate_note on the original post_id.

    PYTHONPATH=src .venv/bin/python scripts/ab_prompt_eval.py --n 30
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from cnapi.client import CNClient  # noqa: E402
from cnapi.evaluate_note import claim_opinion_score, evaluate_note  # noqa: E402
from note_writer import write_note as wn  # noqa: E402
from note_writer.config import NOTE_WRITER_MODEL  # noqa: E402
from note_writer.llm_util import complete  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "pipeline_cache.jsonl"
OUT = REPO / "data" / "ab_prompt_scores.jsonl"

NEW_BUDGET = wn.NOTE_MAX_CHARS_INCLUDING_URL - 10
_HARD_LIMIT_RE = re.compile(r"HARD LIMIT:.*$", re.DOTALL)
NEW_TAIL = (
    f"HARD LIMIT: your prose must be {NEW_BUDGET} characters or fewer "
    "(including spaces and punctuation); anything longer will be rejected. "
    "Use the room for concrete specifics from the evidence, but don't pad. "
    "Return only the prose, or NO_NOTE."
)


def generate(system: str, user_prompt: str) -> str | None:
    """Run the live writer config; return validated prose or None."""
    try:
        prose = complete(
            user_prompt=user_prompt, system=system,
            model=NOTE_WRITER_MODEL, max_tokens=2000, effort="high",
        )
    except Exception as e:
        print(f"    writer error: {e}")
        return None
    ok, _ = wn._validate_prose(prose)
    return prose if ok else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    records = [json.loads(l) for l in CACHE.open()]
    random.Random(42).shuffle(records)
    records = records[: args.n]
    print(f"{len(records)} cached pipeline states; writer={NOTE_WRITER_MODEL}")

    # Generate per-arm in sequence so each arm's system prompt stays
    # prompt-cached across calls.
    proses: dict[str, dict[str, str | None]] = {r["post_id"]: {} for r in records}
    for arm in ("old", "new"):
        print(f"\n=== generating arm: {arm} ===")
        for i, r in enumerate(records):
            if arm == "old":
                system, user = r["system_prompt"], r["user_prompt"]
            else:
                system = wn._NOTE_WRITER_SYSTEM
                user = _HARD_LIMIT_RE.sub(NEW_TAIL, r["user_prompt"])
            proses[r["post_id"]][arm] = generate(system, user)
            print(f"  [{arm} {i+1}/{len(records)}] {'ok' if proses[r['post_id']][arm] else 'NO_NOTE/invalid'}")

    # Score both arms with X's evaluate_note (free, does not submit)
    client = CNClient()
    rows = []
    print("\n=== scoring with evaluate_note ===")
    for r in records:
        pid = r["post_id"]
        url = r["evidence"]["review_url"]
        row = {"post_id": pid}
        for arm in ("old", "new"):
            prose = proses[pid][arm]
            row[f"{arm}_prose"] = prose
            row[f"{arm}_score"] = None
            if prose:
                note_text = wn._render_note(prose, url)
                row[f"{arm}_len_raw"] = len(note_text)
                try:
                    row[f"{arm}_score"] = claim_opinion_score(
                        evaluate_note(client, post_id=pid, note_text=note_text)
                    )
                except Exception as e:
                    row[f"{arm}_err"] = str(e)[:120]
                time.sleep(0.5)
        rows.append(row)
        o, n = row.get("old_score"), row.get("new_score")
        print(f"  {pid}: old={o if o is None else round(o,3)}  new={n if n is None else round(n,3)}")

    OUT.write_text("\n".join(json.dumps(x) for x in rows) + "\n")

    paired = [(x["old_score"], x["new_score"]) for x in rows
              if x.get("old_score") is not None and x.get("new_score") is not None]
    print(f"\n{'='*60}\nPAIRED RESULTS ({len(paired)} posts scored in both arms)")
    if paired:
        old_m = sum(p[0] for p in paired) / len(paired)
        new_m = sum(p[1] for p in paired) / len(paired)
        wins = sum(1 for o, n in paired if n > o)
        floor = 0.5
        print(f"  mean claim_opinion_score : old {old_m:+.4f}  new {new_m:+.4f}  (delta {new_m-old_m:+.4f})")
        print(f"  new beats old on         : {wins}/{len(paired)} posts")
        print(f"  share >= {floor} gate floor : old {sum(1 for o,_ in paired if o>=floor)}/{len(paired)}"
              f"  new {sum(1 for _,n in paired if n>=floor)}/{len(paired)}")
    n_old = sum(1 for x in rows if x.get("old_prose"))
    n_new = sum(1 for x in rows if x.get("new_prose"))
    print(f"  notes written (vs NO_NOTE): old {n_old}/{len(rows)}  new {n_new}/{len(rows)}")
    avg_len = lambda k: (lambda v: sum(v)/len(v) if v else 0)([x[k] for x in rows if x.get(k)])
    print(f"  avg raw note length       : old {avg_len('old_len_raw'):.0f}  new {avg_len('new_len_raw'):.0f}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
