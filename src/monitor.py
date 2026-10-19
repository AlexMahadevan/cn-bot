"""Daily earn-in / ratings monitor.

Pulls every note we've written, snapshots X's evaluation of each into SQLite,
and prints the one scoreboard that matters before admission: the EARN-IN
SCORECARD over our 50 most recently submitted notes. The criteria are X's own
(Miller et al. 2026, arXiv:2609.40067, "AI writers earn in by fulfilling three
note quality checks based on their 50 most recently submitted notes"):

  UrlValidity      >= 95% of notes cite a URL that resolves to HTTP 200
  HarassmentAbuse  >= 98% of notes are not classified as harassment/abuse
  ClaimOpinion     >= 30% of notes score above the bottom 20% of CRH scores
                   and <= 30% score below the bottom 5% of CRH scores

`get_notes_written` exposes these as High/Medium/Low buckets per evaluator.
We read High = passes, Low = below the bottom 5%, and a note with NO
evaluation_outcome as failing every check (strict). That strict reading is
what explains our 4-month stall: 29 notes from the July 2025 template run were
never evaluated, and 3 of them still sat inside the 50-window on 2026-10-19
(47/50 = 94% < 95% on UrlValidity). The "evaluated-only" line is printed too.

Admission is detected ONLY by scoring_status.has_access flipping to true
(see memory: two false alarms were called from UI screens in May 2026). On
the flip this script fires a macOS notification and drops data/EARNED_IN.flag.

After admission the pre-admission block goes quiet and the post-admission
block takes over: note status counts, hit rate (CRH-CRNH)/(CRH+CRNH+NMR), and
X's quota ladder (10/day for new writers; killswitch to 2/day if 8 of the last
10 resolved notes are CRNH).
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from cnapi.client import CNClient
from cnapi.get_notes_written import get_notes_written
import storage

WINDOW = 50
STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "earn_in_state.json"
SCORECARD_LOG = Path(__file__).resolve().parent.parent / "data" / "earn_in_scorecards.jsonl"
FLAG_PATH = Path(__file__).resolve().parent.parent / "data" / "EARNED_IN.flag"

# Benchmarks from Alexios Mantzarlis, Indicator, May 26 2026 — only meaningful
# AFTER admission (test notes are never rated by humans).
AI_BOT_AVERAGE_HELPFULNESS_RATE = 0.129
HUMAN_AVERAGE_HELPFULNESS_RATE = 0.084
AI_BOT_TOP_HELPFULNESS_RATE = 0.241

CRH = "CURRENTLY_RATED_HELPFUL"
CRNH = "CURRENTLY_RATED_NOT_HELPFUL"
NMR = "NEEDS_MORE_RATINGS"


def _buckets(note: Dict[str, Any]) -> Dict[str, str]:
    eo = (note.get("test_result") or {}).get("evaluation_outcome") or []
    return {e["evaluator_type"]: e["evaluator_score_bucket"] for e in eo}


def earn_in_scorecard(notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score the WINDOW most recent notes (by note id, a time-ordered snowflake)."""
    ordered = sorted(notes, key=lambda n: int(n["id"]), reverse=True)
    win = ordered[:WINDOW]
    n = len(win)
    unevaluated = 0
    c: Counter = Counter()
    for note in win:
        b = _buckets(note)
        if not b:
            unevaluated += 1
            continue
        for k, v in b.items():
            c[(k, v)] += 1
    evaluated = n - unevaluated

    def pct(x: int, d: int) -> float:
        return (x / d) if d else 0.0

    url_hi = c[("UrlValidity", "High")]
    har_hi = c[("HarassmentAbuse", "High")]
    co_hi = c[("ClaimOpinion", "High")]
    co_md = c[("ClaimOpinion", "Medium")]
    co_lo = c[("ClaimOpinion", "Low")]

    checks = {
        # strict: unevaluated notes count as failing (denominator = window size)
        "url_validity": {"value": pct(url_hi, n), "need": 0.95, "op": ">=", "pass": pct(url_hi, n) >= 0.95},
        "harassment_abuse": {"value": pct(har_hi, n), "need": 0.98, "op": ">=", "pass": pct(har_hi, n) >= 0.98},
        "claim_opinion_high": {"value": pct(co_hi, n), "need": 0.30, "op": ">=", "pass": pct(co_hi, n) >= 0.30},
        "claim_opinion_low": {"value": pct(co_lo, n), "need": 0.30, "op": "<=", "pass": pct(co_lo, n) <= 0.30},
    }
    return {
        "window": n,
        "unevaluated_in_window": unevaluated,
        "evaluated_in_window": evaluated,
        "counts": {"url_high": url_hi, "harass_high": har_hi, "co_high": co_hi, "co_medium": co_md, "co_low": co_lo},
        "checks": checks,
        "all_pass_strict": all(ch["pass"] for ch in checks.values()),
        "evaluated_only": {
            "url_validity": pct(url_hi, evaluated),
            "harassment_abuse": pct(har_hi, evaluated),
            "claim_opinion_high": pct(co_hi, evaluated),
            "claim_opinion_low": pct(co_lo, evaluated),
        },
        "clean_notes_needed_to_flush_unevaluated": unevaluated,
        "total_notes_written": len(notes),
    }


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _notify_mac(title: str, body: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}" sound name "Glass"'],
            check=False, timeout=10,
        )
    except Exception:
        pass


def _total_ratings(scoring: Dict[str, Any]) -> int:
    """Sum helpful/somewhat/not-helpful counts across X's per-model factor buckets."""
    total = 0
    for entry in scoring.get("rating_counts_per_model", []) or []:
        value = entry.get("value", {}) or {}
        for bucket_name in ("negative_factor_bucket_counts", "neutral_factor_bucket_counts", "positive_factor_bucket_counts"):
            b = value.get(bucket_name, {}) or {}
            total += int(b.get("helpful_count", 0) or 0)
            total += int(b.get("somewhat_helpful_count", 0) or 0)
            total += int(b.get("not_helpful_count", 0) or 0)
    # Older/flat shape, if X ever returns it
    for k in ("helpful_count", "somewhat_helpful_count", "not_helpful_count"):
        total += int(scoring.get(k, 0) or 0)
    return total


def main() -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = CNClient()
    notes = get_notes_written(client, test_mode=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- persist per-note snapshots (unchanged behaviour) ---
    has_access = False
    status_counts: Counter = Counter()
    for n in notes:
        info = n.get("info") or {}
        post_id = str(n.get("post_id") or info.get("post_id") or "")
        test_result = n.get("test_result") or {}
        scoring = n.get("scoring_status") or {}
        status = n.get("status") or ""
        status_counts[status or "(test_mode: no public status)"] += 1
        if isinstance(scoring, dict) and scoring.get("has_access"):
            has_access = True
        storage.log_rating_snapshot(
            post_id=post_id,
            status=status or (str(test_result) if test_result else "unknown"),
            scoring_status={"scoring_status": scoring, "test_result": test_result},
        )

    card = earn_in_scorecard(notes)

    print(f"\n=== @alexcnotes monitor @ {now} ===")
    print(f"Total notes X attributes to this writer: {len(notes)}")
    print(f"ADMITTED (scoring_status.has_access): {has_access}")

    # --- admission flip detection ---
    state = _load_state()
    prev_access = bool(state.get("has_access", False))
    if has_access and not prev_access:
        msg = "scoring_status.has_access flipped to TRUE — @alexcnotes is admitted to write public notes."
        print("\n" + "#" * 78 + f"\n### {msg}\n" + "#" * 78)
        FLAG_PATH.write_text(f"{now}\n{msg}\n")
        _notify_mac("Community Notes bot", "has_access is TRUE. The bot earned in.")
    state.update({"has_access": has_access, "checked_at": now, "last_scorecard": card})
    _save_state(state)

    # --- pre-admission scoreboard ---
    print(f"\nEARN-IN SCORECARD (X's three checks over the {card['window']} most recent notes)")
    print(f"  evaluated: {card['evaluated_in_window']}   unevaluated (count as FAIL): {card['unevaluated_in_window']}")
    labels = {
        "url_validity": "UrlValidity  High",
        "harassment_abuse": "Harassment   High",
        "claim_opinion_high": "ClaimOpinion High",
        "claim_opinion_low": "ClaimOpinion Low ",
    }
    for key, ch in card["checks"].items():
        mark = "PASS" if ch["pass"] else "FAIL"
        print(f"  {labels[key]}: {ch['value']*100:5.1f}%  (need {ch['op']} {ch['need']*100:.0f}%)  {mark}")
    eo = card["evaluated_only"]
    print(f"  evaluated-only view: url {eo['url_validity']*100:.0f}% | harass {eo['harassment_abuse']*100:.0f}% | "
          f"CO high {eo['claim_opinion_high']*100:.0f}% | CO low {eo['claim_opinion_low']*100:.0f}%")
    print(f"  counts: {card['counts']}")
    if card["all_pass_strict"]:
        print("  => window passes all three checks on the strict reading.")
    else:
        print(f"  => NOT passing on the strict reading; {card['clean_notes_needed_to_flush_unevaluated']} more clean "
              f"note(s) push the unevaluated ones out of the window.")

    # --- post-admission block ---
    if has_access:
        crh = status_counts.get(CRH, 0)
        crnh = status_counts.get(CRNH, 0)
        nmr = status_counts.get(NMR, 0)
        denom = crh + crnh + nmr
        hit_rate = (crh - crnh) / denom if denom else 0.0
        print("\nPOST-ADMISSION")
        print(f"  status counts: {dict(status_counts)}")
        print(f"  hit rate (CRH-CRNH)/(CRH+CRNH+NMR): {hit_rate*100:.1f}%  "
              f"(X: 10% ≈ 50 notes/day, 20% = 500/day; <20 total notes = 10/day)")
        rated = [(n, _total_ratings(n.get("scoring_status") or {})) for n in notes]
        with_ratings = sum(1 for _, t in rated if t > 0)
        print(f"  notes with any ratings visible: {with_ratings}")
        helpful_rate = crh / len(notes) if notes else 0.0
        print(f"  CRH share vs Indicator benchmarks: {helpful_rate*100:.1f}% "
              f"(human avg {HUMAN_AVERAGE_HELPFULNESS_RATE*100:.1f}%, AI avg {AI_BOT_AVERAGE_HELPFULNESS_RATE*100:.1f}%, "
              f"AI top {AI_BOT_TOP_HELPFULNESS_RATE*100:.1f}%)")
    else:
        print("\n(test_mode: notes are never shown publicly, so Helpful ratings are structurally 0 until admission.)")
    print()

    SCORECARD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SCORECARD_LOG.open("a") as f:
        f.write(json.dumps({"at": now, "has_access": has_access, **card}) + "\n")

    return {"total": len(notes), "has_access": has_access, "scorecard": card, "status_counts": dict(status_counts)}


if __name__ == "__main__":
    main()
