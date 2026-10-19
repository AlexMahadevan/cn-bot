"""Side-by-side dry run of the note pipeline under two model configurations.

Usage (two phases, no DB writes at any point):

  # 1. snapshot a fresh eligible pool once, so both arms see identical posts
  PYTHONPATH=src .venv/bin/python scripts/ab_model_swap.py fetch --n 60 --out data/ab_posts.json

  # 2. run one arm per process (models are chosen at import time from env)
  CN_BOT_OPUS_MODEL=claude-opus-4-7 CN_BOT_HAIKU_MODEL=claude-haiku-4-5 \\
    PYTHONPATH=src .venv/bin/python scripts/ab_model_swap.py run --posts data/ab_posts.json --out data/ab_old.jsonl
  PYTHONPATH=src .venv/bin/python scripts/ab_model_swap.py run --posts data/ab_posts.json --out data/ab_new.jsonl

  # 3. compare
  PYTHONPATH=src .venv/bin/python scripts/ab_model_swap.py compare data/ab_old.jsonl data/ab_new.jsonl

Each arm runs research_post_and_write_note() (the real pipeline, no storage),
then scores any resulting note with X's evaluate_note (the same ClaimOpinion
model the earn-in gate uses), and records per-post outcome, refusal stage,
score, and the Anthropic token usage seen by this process.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


def _stage(refusal: str | None, error: str | None) -> str:
    if error:
        return "error"
    if not refusal:
        return "note"
    r = refusal.lower()
    if r.startswith("off-beat"):
        return "relevance"
    if "no specific falsifiable claim" in r:
        return "specificity"
    if "no matching" in r or "none directly address" in r:
        return "evidence"
    if "model declined" in r or r == "no_note":
        return "writer_declined"
    if "opinion" in r:
        return "opinion"
    if "hallucination" in r or "url" in r:
        return "validator"
    return "other"


def cmd_fetch(args):
    from cnapi.client import CNClient
    from cnapi.get_api_eligible_posts import get_posts_eligible_for_notes
    from cnapi.get_notes_written import already_noted_post_ids, get_notes_written

    client = CNClient()
    noted = already_noted_post_ids(get_notes_written(client, test_mode=True))
    posts = [p for p in get_posts_eligible_for_notes(client, max_results=100) if p.post_id not in noted]
    posts = posts[: args.n]
    json.dump([p.model_dump(mode="json") for p in posts], open(args.out, "w"), indent=1)
    print(f"saved {len(posts)} posts -> {args.out}")


def cmd_run(args):
    from data_models import Post
    from note_writer.config import NOTE_WRITER_MODEL, WRITER_EFFORT
    from note_writer.llm_util import HAIKU_MODEL
    from note_writer.write_note import research_post_and_write_note
    from cnapi.client import CNClient
    from cnapi.evaluate_note import claim_opinion_score, evaluate_note

    posts = [Post.model_validate(p) for p in json.load(open(args.posts))]
    client = CNClient()
    cfg = {"writer": NOTE_WRITER_MODEL, "haiku": HAIKU_MODEL, "effort": WRITER_EFFORT}
    print(f"arm config: {cfg}  posts: {len(posts)}", file=sys.stderr)

    def one(post):
        t0 = time.time()
        res = research_post_and_write_note(post)
        row = {
            "post_id": post.post_id,
            "stage": _stage(res.refusal, res.error),
            "refusal": (res.refusal or "")[:200],
            "error": (res.error or "")[:200],
            "note_text": res.note.note_text if res.note else None,
            "evidence_url": res.note.evidence_url if res.note else None,
            "evidence_tier": res.evidence[0].evidence_tier if res.evidence else None,
            "claim_opinion_score": None,
            "secs": round(time.time() - t0, 1),
        }
        if res.note:
            try:
                row["claim_opinion_score"] = claim_opinion_score(
                    evaluate_note(client, post_id=post.post_id, note_text=res.note.note_text)
                )
            except Exception as e:  # expired posts 403; record and move on
                row["evaluate_error"] = str(e)[:120]
        return row

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(one, posts))
    with open(args.out, "w") as f:
        f.write(json.dumps({"_config": cfg}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")
    notes = [r for r in rows if r["stage"] == "note"]
    print(f"done: {len(rows)} posts, {len(notes)} notes -> {args.out}", file=sys.stderr)


def _load(path):
    rows = [json.loads(l) for l in open(path)]
    cfg = rows[0].get("_config", {}) if rows and "_config" in rows[0] else {}
    return cfg, [r for r in rows if "_config" not in r]


def cmd_compare(args):
    arms = [(_load(p), p) for p in args.files]
    stages = ["relevance", "specificity", "evidence", "writer_declined", "opinion", "validator", "other", "error", "note"]
    print(f"{'':<16}" + "".join(f"{p.split('/')[-1]:>18}" for _, p in arms))
    for st in stages:
        print(f"{st:<16}" + "".join(f"{sum(1 for r in rows if r['stage']==st):>18}" for (cfg, rows), _ in arms))
    print("-" * (16 + 18 * len(arms)))
    for label, fn in [
        ("notes w/ score", lambda rows: sum(1 for r in rows if r["claim_opinion_score"] is not None)),
        ("CO mean", lambda rows: round(statistics.mean(s), 3) if (s := [r["claim_opinion_score"] for r in rows if r["claim_opinion_score"] is not None]) else None),
        ("CO min", lambda rows: round(min(s), 3) if (s := [r["claim_opinion_score"] for r in rows if r["claim_opinion_score"] is not None]) else None),
        ("CO >= 0.5 (gate)", lambda rows: sum(1 for r in rows if (r["claim_opinion_score"] or -9) >= 0.5)),
        ("mean secs/post", lambda rows: round(statistics.mean(r["secs"] for r in rows), 1) if rows else None),
    ]:
        print(f"{label:<16}" + "".join(f"{str(fn(rows)):>18}" for (cfg, rows), _ in arms))
    print()
    for (cfg, rows), p in arms:
        print(f"== {p}: {cfg}")
        for r in rows:
            if r["stage"] == "note":
                print(f"  [{r['claim_opinion_score']}] ({r['evidence_tier']}) {r['note_text'][:150]}")
        errs = [r for r in rows if r["stage"] == "error"]
        for r in errs[:5]:
            print(f"  ERROR {r['post_id']}: {r['error'][:160]}")
    # agreement on the same posts
    if len(arms) == 2:
        a = {r["post_id"]: r["stage"] for r in arms[0][0][1]}
        b = {r["post_id"]: r["stage"] for r in arms[1][0][1]}
        same = sum(1 for k in a if a[k] == b.get(k))
        print(f"\nsame stage outcome on {same}/{len(a)} posts")
        for k in a:
            if a[k] != b.get(k):
                print(f"  {k}: {a[k]} -> {b.get(k)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--n", type=int, default=60); f.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("--posts", required=True); r.add_argument("--out", required=True); r.add_argument("--concurrency", type=int, default=6)
    c = sub.add_parser("compare"); c.add_argument("files", nargs="+")
    a = ap.parse_args()
    {"fetch": cmd_fetch, "run": cmd_run, "compare": cmd_compare}[a.cmd](a)
