#!/usr/bin/env python3
"""Observable Framework data loader.

Reads notes.db (the bot's audit log) and emits a JSON object the dashboard
consumes. Runs at build time — `observable build` invokes this script
and writes its stdout to bot.json.

Schema of emitted JSON:
{
  "generated_at": "...ISO timestamp...",
  "totals": {posts, off_beat, evidence_searched, evidence_found, notes_written, submitted},
  "funnel": [{label, count}],
  "by_day": [{day, posts, off_beat, refused, submitted, errors}],
  "refusal_buckets": [{bucket, count}],
  "evidence_tiers": [{tier, count}],
  "notes": [{post_id, created_at, post_text, note_text, evidence_url, evidence_publisher, evidence_rating, evidence_tier, misleading_tags, outcome}],
  "recent_refusals": [{post_id, created_at, post_text, refusal_reason}]
}
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# The DB lives at template-api-note-writer/data/notes.db.
# This script is at template-api-note-writer/dashboard/src/data/bot.json.py.
HERE = Path(__file__).resolve()
DB_PATH = Path(os.environ.get("CN_BOT_DB", HERE.parent.parent.parent.parent / "data" / "notes.db"))


def bucket_refusal(reason: str | None) -> str:
    if not reason:
        return "Other"
    r = reason.lower()
    if r.startswith("off-beat"):
        return "Off-beat (not US politics)"
    if "no matching ifcn" in r or "no fact-check" in r.lower():
        return "No fact-check found"
    if "none directly address" in r or "candidates" in r:
        return "Picker: candidate doesn't match claim"
    if "model declined" in r:
        return "Opus declined to write"
    if "url" in r:
        return "URL validator rejected (hallucination guard)"
    if "too long" in r or "chars (limit" in r:
        return "Too long (over 280 chars)"
    return "Other"


def main() -> None:
    if not DB_PATH.exists():
        # No DB yet — emit a minimal empty payload so the dashboard builds.
        print(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "totals": {"posts": 0, "off_beat": 0, "evidence_searched": 0,
                       "evidence_found": 0, "notes_written": 0, "submitted": 0},
            "funnel": [],
            "by_day": [],
            "refusal_buckets": [],
            "evidence_tiers": [],
            "notes": [],
            "recent_refusals": [],
            "db_missing": True,
        }))
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = list(conn.execute("SELECT * FROM drafts ORDER BY created_at DESC"))
        subs = list(conn.execute("SELECT * FROM submissions ORDER BY submitted_at DESC"))

    posts = len(rows)
    off_beat = sum(1 for r in rows if (r["refusal_reason"] or "").lower().startswith("off-beat"))
    evidence_searched = posts - off_beat
    evidence_found = sum(1 for r in rows if r["evidence_url"])
    notes_written = sum(1 for r in rows if r["outcome"] in ("submitted", "queued"))
    submitted = sum(1 for r in rows if r["outcome"] == "submitted")

    funnel = [
        {"label": "Eligible posts seen", "count": posts},
        {"label": "On-beat (US politics)", "count": evidence_searched},
        {"label": "Evidence found", "count": evidence_found},
        {"label": "Note drafted", "count": notes_written},
        {"label": "Submitted to X", "count": submitted},
    ]

    # Per-day rollup
    by_day_counters: dict[str, Counter[str]] = {}
    for r in rows:
        day = r["created_at"][:10]
        c = by_day_counters.setdefault(day, Counter())
        c[r["outcome"]] += 1
        c["posts"] += 1
    by_day = [
        {
            "day": day,
            "posts": c["posts"],
            "submitted": c.get("submitted", 0),
            "queued": c.get("queued", 0),
            "refused": c.get("refused", 0),
            "errors": c.get("error", 0),
        }
        for day, c in sorted(by_day_counters.items())
    ]

    # Refusal buckets
    refusal_counter: Counter[str] = Counter()
    for r in rows:
        if r["outcome"] == "refused":
            refusal_counter[bucket_refusal(r["refusal_reason"])] += 1
    refusal_buckets = [
        {"bucket": b, "count": c}
        for b, c in refusal_counter.most_common()
    ]

    # Evidence tier breakdown (among posts that got past relevance filter)
    tier_counter = Counter(r["evidence_tier"] for r in rows if r["evidence_tier"])
    evidence_tiers = [
        {"tier": t, "count": c}
        for t, c in tier_counter.most_common()
    ]

    # Full notes that we wrote (submitted or queued)
    notes_out = []
    for r in rows:
        if r["outcome"] not in ("submitted", "queued"):
            continue
        try:
            tags = json.loads(r["misleading_tags"]) if r["misleading_tags"] else []
        except Exception:
            tags = []
        notes_out.append({
            "post_id": r["post_id"],
            "post_url": f"https://x.com/i/web/status/{r['post_id']}",
            "created_at": r["created_at"],
            "outcome": r["outcome"],
            "post_text": r["post_text"],
            "note_text": r["note_text"],
            "evidence_url": r["evidence_url"],
            "evidence_publisher": r["evidence_publisher"],
            "evidence_rating": r["evidence_rating"],
            "evidence_tier": r["evidence_tier"],
            "misleading_tags": tags,
        })

    # A sample of recent refusals (useful for transparency — shows what we declined)
    recent_refusals = []
    for r in rows[:60]:
        if r["outcome"] != "refused":
            continue
        recent_refusals.append({
            "post_id": r["post_id"],
            "post_url": f"https://x.com/i/web/status/{r['post_id']}",
            "created_at": r["created_at"],
            "post_text": r["post_text"],
            "refusal_bucket": bucket_refusal(r["refusal_reason"]),
            "refusal_reason": r["refusal_reason"],
            "evidence_tier": r["evidence_tier"],
            "evidence_publisher": r["evidence_publisher"],
        })
        if len(recent_refusals) >= 25:
            break

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "posts": posts,
            "off_beat": off_beat,
            "evidence_searched": evidence_searched,
            "evidence_found": evidence_found,
            "notes_written": notes_written,
            "submitted": submitted,
        },
        "funnel": funnel,
        "by_day": by_day,
        "refusal_buckets": refusal_buckets,
        "evidence_tiers": evidence_tiers,
        "notes": notes_out,
        "recent_refusals": recent_refusals,
    }

    json.dump(payload, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    main()
