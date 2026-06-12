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


# Benchmarks from Alexios Mantzarlis, Indicator, May 26 2026.
# https://indicator.media/p/8-ai-bots-now-write-50-of-x-s-community-notes
AI_BOT_AVERAGE_HELPFULNESS_RATE = 0.129   # 12.9% pooled across 29 AI bots
AI_BOT_MEDIAN_HELPFULNESS_RATE = 0.111    # 11.1% median bot
AI_BOT_TOP_HELPFULNESS_RATE = 0.241       # 24.1% best AI contributor with >=100 notes
HUMAN_AVERAGE_HELPFULNESS_RATE = 0.084    # 8.4% pooled across humans


def _percentile_label(rate: float) -> str:
    if rate >= AI_BOT_TOP_HELPFULNESS_RATE:
        return "TOP-DECILE among AI bots"
    if rate >= AI_BOT_AVERAGE_HELPFULNESS_RATE:
        return "above AI bot average"
    if rate >= HUMAN_AVERAGE_HELPFULNESS_RATE:
        return "below AI bot average, above human average"
    return "below human average"


def main() -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = CNClient()
    notes = get_notes_written(client, test_mode=True)

    status_counts: Counter[str] = Counter()
    helpful_total = not_helpful_total = somewhat_helpful_total = 0

    for n in notes:
        # post_id is nested under `info` in this endpoint's response (a
        # top-level post_id is absent — observed 2026-06-12). The old code
        # logged every snapshot under post_id="" so the rows collapsed and
        # the per-note bucket history was never captured.
        info = n.get("info") or {}
        post_id = str(n.get("post_id") or info.get("post_id") or "")
        test_result = n.get("test_result") or {}
        status = n.get("status") or test_result or "unknown"
        status_counts[str(status)] += 1
        scoring = n.get("scoring_status") or {}
        if isinstance(scoring, dict):
            helpful_total += int(scoring.get("helpful_count", 0) or 0)
            not_helpful_total += int(scoring.get("not_helpful_count", 0) or 0)
            somewhat_helpful_total += int(scoring.get("somewhat_helpful_count", 0) or 0)

        storage.log_rating_snapshot(
            post_id=post_id,
            status=str(status),
            scoring_status={"scoring_status": scoring, "test_result": test_result},
        )

    # Note-level helpfulness rate: CRH / total
    crh_count = status_counts.get("CURRENTLY_RATED_HELPFUL", 0)
    crnh_count = status_counts.get("CURRENTLY_RATED_NOT_HELPFUL", 0)
    total_notes = len(notes)
    helpfulness_rate = crh_count / total_notes if total_notes else 0.0

    print(f"\n=== Ratings snapshot @ {datetime.utcnow().isoformat()}Z ===")
    print(f"Total notes written: {total_notes}")
    print(f"\nStatus breakdown:")
    for status, count in status_counts.most_common():
        print(f"  {status}: {count}")

    print(f"\nHelpfulness rate vs. industry benchmarks (Alexios/Indicator, May 2026):")
    print(f"  @alexcnotes CRH%:         {helpfulness_rate * 100:5.1f}%  →  {_percentile_label(helpfulness_rate)}")
    print(f"  Human average:            {HUMAN_AVERAGE_HELPFULNESS_RATE * 100:5.1f}%")
    print(f"  AI bot pooled average:    {AI_BOT_AVERAGE_HELPFULNESS_RATE * 100:5.1f}%")
    print(f"  AI bot top contributor:   {AI_BOT_TOP_HELPFULNESS_RATE * 100:5.1f}%")

    if total_notes < 20:
        print(f"\n  ⚠ Note: only {total_notes} notes — rate is noisy until we have ~50+ samples.")

    print(f"\nRating totals (across notes with scoring_status):")
    print(f"  Helpful:          {helpful_total}")
    print(f"  Somewhat helpful: {somewhat_helpful_total}")
    print(f"  Not helpful:      {not_helpful_total}")
    print()

    return {
        "total": total_notes,
        "crh_count": crh_count,
        "crnh_count": crnh_count,
        "helpfulness_rate": helpfulness_rate,
        "vs_ai_avg": helpfulness_rate - AI_BOT_AVERAGE_HELPFULNESS_RATE,
        "status_counts": dict(status_counts),
        "helpful": helpful_total,
        "somewhat_helpful": somewhat_helpful_total,
        "not_helpful": not_helpful_total,
    }


if __name__ == "__main__":
    main()
