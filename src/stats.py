"""Quick-glance dashboard for the CN bot.

Usage:
    PYTHONPATH=src .venv/bin/python src/stats.py            # last 24h summary
    PYTHONPATH=src .venv/bin/python src/stats.py --days 7   # last week
    PYTHONPATH=src .venv/bin/python src/stats.py --notes    # show actual note text

Reads notes.db. No network calls.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import storage


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="Lookback window in days (default 1)")
    parser.add_argument("--notes", action="store_true", help="Print the actual text of recent notes")
    args = parser.parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(timespec="seconds")

    storage._ensure_db()
    with sqlite3.connect(storage.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = list(conn.execute(
            "SELECT * FROM drafts WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ))
        subs = list(conn.execute(
            "SELECT post_id, submitted_at, response FROM submissions "
            "WHERE submitted_at >= ? ORDER BY submitted_at DESC",
            (cutoff,),
        ))
        ratings = list(conn.execute(
            "SELECT * FROM rating_snapshots WHERE captured_at >= ? "
            "ORDER BY captured_at DESC",
            (cutoff,),
        ))

    _print_header(f"BOT ACTIVITY — last {args.days} day{'s' if args.days != 1 else ''}")
    print(f"Posts processed:  {len(rows)}")

    if not rows:
        print("\nNo activity in this window. Bot may not have run yet.")
        return

    outcomes = Counter(r["outcome"] for r in rows)
    print("\nOutcomes:")
    for outcome, count in outcomes.most_common():
        pct = count * 100 / len(rows)
        print(f"  {outcome:12} {count:4}  ({pct:5.1f}%)")

    # Evidence tier breakdown (only for posts that got to evidence)
    tiered = [r for r in rows if r["evidence_tier"]]
    if tiered:
        print("\nEvidence tier of posts that produced notes or were refused on evidence:")
        for tier, count in Counter(r["evidence_tier"] for r in tiered).most_common():
            print(f"  {tier:20} {count}")

    # Refusal-reason breakdown
    refused = [r for r in rows if r["outcome"] == "refused"]
    if refused:
        reasons = Counter()
        for r in refused:
            reason = (r["refusal_reason"] or "")[:60]
            # Bucket the most common types
            if reason.startswith("Off-beat"):
                reasons["Off-beat (politics filter)"] += 1
            elif "No matching IFCN" in reason:
                reasons["No fact-check found"] += 1
            elif "none directly address" in reason:
                reasons["Picker: candidate doesn't match claim"] += 1
            elif "Model declined" in reason:
                reasons["Opus declined to write"] += 1
            elif "URL" in reason or "URL mismatch" in reason:
                reasons["URL validator rejection (hallucination guard)"] += 1
            elif "Note is" in reason and "chars" in reason:
                reasons["Too long (over 280 char)"] += 1
            else:
                reasons["other"] += 1
        print("\nRefusal reasons:")
        for reason, count in reasons.most_common():
            print(f"  {count:4}  {reason}")

    # Submitted / queued notes
    notes = [r for r in rows if r["outcome"] in ("submitted", "queued")]
    if notes:
        _print_header(f"NOTES WRITTEN ({len(notes)})")
        for r in notes:
            print(f"\n[{r['outcome'].upper()}] {r['created_at']}  post_id={r['post_id']}")
            print(f"  Post:   {r['post_text'][:120]}...")
            if args.notes and r["note_text"]:
                print(f"  Note:   {r['note_text']}")
            print(f"  Tier:   {r['evidence_tier']}  ({r['evidence_publisher']} — {r['evidence_rating'] or '(no rating)'})")
            print(f"  URL:    {r['evidence_url']}")
            tags = json.loads(r["misleading_tags"]) if r["misleading_tags"] else []
            if tags:
                print(f"  Tags:   {tags}")

    # X-side rating outcomes
    if subs:
        _print_header(f"X SUBMISSIONS ({len(subs)})")
        for s in subs:
            try:
                resp = json.loads(s["response"])
            except Exception:
                resp = {}
            note_id = (resp.get("data") or {}).get("note_id") or "?"
            status = (resp.get("data") or {}).get("status") or "?"
            print(f"  {s['submitted_at']}  post={s['post_id']}  note_id={note_id}  status={status}")

    if ratings:
        _print_header(f"RATING SNAPSHOTS ({len(ratings)})")
        for r in ratings[:10]:
            status = r["status"] or "?"
            print(f"  {r['captured_at']}  post={r['post_id']}  status={status}")

    # Daily breakdown (helps spot trends)
    if args.days >= 3:
        _print_header("DAILY BREAKDOWN")
        by_day: dict[str, Counter] = {}
        for r in rows:
            day = r["created_at"][:10]
            by_day.setdefault(day, Counter())[r["outcome"]] += 1
        print(f"  {'date':12} {'total':>6} {'submitted':>10} {'refused':>8} {'errors':>7}")
        for day in sorted(by_day.keys(), reverse=True):
            c = by_day[day]
            total = sum(c.values())
            print(f"  {day}  {total:6}  {c.get('submitted',0):10}  {c.get('refused',0):8}  {c.get('error',0):7}")

    print()


if __name__ == "__main__":
    main()
