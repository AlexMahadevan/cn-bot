"""One-off cost meter: wrap the Anthropic client, run a real dry-run pass, and
report actual $ spend per pipeline stage. Settles 'is it the writing or the
reviewing?' with measured tokens instead of estimates.

    PYTHONPATH=src .venv/bin/python scripts/measure_cost.py --num-posts 40
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

import dotenv

dotenv.load_dotenv()

import note_writer.llm_util as llm  # noqa: E402
from note_writer.bot_engine import CommunityNotesBot  # noqa: E402

# $/MTok (input, output). web_search billed separately at $10/1000 requests.
# NOTE: Opus 4.7 is $5/$25 — an earlier version of this script had it at
# $15/$75, which overstated the writer's cost share 3x.
PRICES = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
WEB_SEARCH_COST = 0.01  # per request

records: list[dict] = []


def _stage(kwargs: dict) -> str:
    model = kwargs.get("model", "")
    tools = kwargs.get("tools")
    if tools:
        # Both web_search stages use max_uses=2 now — disambiguate by prompt.
        msgs = kwargs.get("messages", []) or []
        first = msgs[0].get("content", "") if msgs else ""
        if isinstance(first, str) and "IFCN-accredited" in first:
            return "web_search_evidence"
        return "self_fact_check (web_search)"
    # image?
    for m in kwargs.get("messages", []) or []:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "image":
                    return "image_description"
    sysp = kwargs.get("system")
    s = sysp if isinstance(sysp, str) else (json.dumps(sysp) if sysp else "")
    if "opus" in model:
        return "OPUS WRITER (note prose)"
    if "TOPIC-FILTERING" in s or "EARN-IN phase" in s:
        return "relevance_filter"
    if "specific, falsifiable factual claim" in s:
        return "specificity_gate"
    if "OPINION or SPECULATION" in s:
        return "opinion_filter"
    if "FABRICATED HARD SPECIFICS" in s:
        return "hallucination_check"
    # no-system Haiku calls — disambiguate by user prompt
    u = ""
    msgs = kwargs.get("messages", []) or []
    if msgs and isinstance(msgs[0].get("content"), str):
        u = msgs[0]["content"]
    if "misleading-content tags" in u:
        return "tag_classifier"
    if "candidate evidence sources" in u:
        return "evidence_picker"
    return f"other:{model}"


def _record(kwargs, resp):
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    # Prompt caching splits the prompt into three buckets: input_tokens is the
    # UNCACHED remainder only. Cache writes bill at 1.25x input price, cache
    # reads at 0.1x — fold both into an effective input-token count so the
    # meter stays honest now that llm_util marks system prompts cacheable.
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    eff_in = in_tok + 1.25 * cache_w + 0.1 * cache_r
    stu = getattr(usage, "server_tool_use", None)
    websearch = getattr(stu, "web_search_requests", 0) or 0 if stu else 0
    records.append({
        "stage": _stage(kwargs),
        "model": kwargs.get("model", ""),
        "in": eff_in,
        "out": out_tok,
        "web": websearch,
    })


def main(num_posts: int) -> None:
    c = llm.client()
    orig = c.messages.create

    def wrapped(*a, **k):
        resp = orig(*a, **k)
        try:
            _record(k, resp)
        except Exception as e:  # never let metering break the run
            print("meter error:", e)
        return resp

    c.messages.create = wrapped

    bot = CommunityNotesBot()
    results = bot.run(num_posts=num_posts, dry_run=True, concurrency=1)

    notes = sum(1 for r in results if r.note)
    processed = len(results)

    agg = defaultdict(lambda: {"n": 0, "in": 0, "out": 0, "web": 0, "cost": 0.0})
    total = 0.0
    for r in records:
        in_p, out_p = PRICES.get(r["model"], (1.0, 5.0))
        cost = r["in"] / 1e6 * in_p + r["out"] / 1e6 * out_p + r["web"] * WEB_SEARCH_COST
        a = agg[r["stage"]]
        a["n"] += 1
        a["in"] += r["in"]
        a["out"] += r["out"]
        a["web"] += r["web"]
        a["cost"] += cost
        total += cost

    print("\n" + "=" * 88)
    print(f"PROCESSED {processed} new posts | notes drafted: {notes} | total API calls: {len(records)}")
    print(f"MEASURED SPEND: ${total:.4f}  (~${total/processed:.4f}/post, ~${total/max(notes,1):.3f}/note)")
    print("=" * 88)
    print(f"{'stage':<32}{'calls':>6}{'in_tok':>10}{'out_tok':>9}{'web':>5}{'cost$':>9}{'%':>7}")
    print("-" * 88)
    for stage, a in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        pct = 100 * a["cost"] / total if total else 0
        print(f"{stage:<32}{a['n']:>6}{int(a['in']):>10}{a['out']:>9}{a['web']:>5}{a['cost']:>9.4f}{pct:>6.1f}%")
    print("-" * 88)
    print(f"{'TOTAL':<32}{len(records):>6}{'':>10}{'':>9}{'':>5}{total:>9.4f}{100:>6.1f}%")
    print(f"\nDAILY PROJECTION (100 posts/day): ~${total/processed*100:.2f}/day")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--num-posts", type=int, default=40)
    main(p.parse_args().num_posts)
