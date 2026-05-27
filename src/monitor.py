"""Daily earn-in / ratings monitor.

Pulls notes we've written, summarizes how X is rating them, and logs to SQLite.
Designed to run from cron once a day. Output also goes to stdout so launchd
captures it; you can pipe to Obsidian or wherever.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict

from cnapi.client import CNClient
from cnapi.get_notes_written import get_notes_written
import storage


def main() -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = CNClient()
    notes = get_notes_written(client, test_mode=True)

    status_counts: Counter[str] = Counter()
    helpful_total = not_helpful_total = somewhat_helpful_total = 0

    for n in notes:
        status = n.get("status") or n.get("test_result") or "unknown"
        status_counts[str(status)] += 1
        scoring = n.get("scoring_status") or {}
        if isinstance(scoring, dict):
            helpful_total += int(scoring.get("helpful_count", 0) or 0)
            not_helpful_total += int(scoring.get("not_helpful_count", 0) or 0)
            somewhat_helpful_total += int(scoring.get("somewhat_helpful_count", 0) or 0)

        storage.log_rating_snapshot(
            post_id=str(n.get("post_id", "")),
            status=str(status),
            scoring_status=scoring,
        )

    print(f"\n=== Ratings snapshot @ {datetime.utcnow().isoformat()}Z ===")
    print(f"Total notes written: {len(notes)}")
    print("Status breakdown:")
    for status, count in status_counts.most_common():
        print(f"  {status}: {count}")
    print("Rating totals (across notes with scoring_status):")
    print(f"  Helpful:          {helpful_total}")
    print(f"  Somewhat helpful: {somewhat_helpful_total}")
    print(f"  Not helpful:      {not_helpful_total}")
    print()

    return {
        "total": len(notes),
        "status_counts": dict(status_counts),
        "helpful": helpful_total,
        "somewhat_helpful": somewhat_helpful_total,
        "not_helpful": not_helpful_total,
    }


if __name__ == "__main__":
    main()
