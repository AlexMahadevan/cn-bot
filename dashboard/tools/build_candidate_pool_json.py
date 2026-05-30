#!/usr/bin/env python3
"""Observable Framework data loader for the candidate-pool analysis.

Reads data/candidate_pool_analysis.jsonl (produced by
scripts/classify_candidate_pool.py) and emits aggregated JSON for the
"What X surfaces" dashboard page.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
# This builder is at template-api-note-writer/dashboard/tools/build_candidate_pool_json.py.
# Source data is at template-api-note-writer/data/candidate_pool_analysis.jsonl.
SRC = Path(os.environ.get(
    "CN_CANDIDATE_POOL_PATH",
    HERE.parent.parent.parent / "data" / "candidate_pool_analysis.jsonl",
))

CATEGORY_LABELS = {
    "us_politics": "US politics",
    "foreign_politics": "Foreign politics",
    "personal_lifestyle": "Personal / lifestyle",
    "entertainment": "Entertainment",
    "sports": "Sports",
    "gaming_tech": "Gaming / tech",
    "finance_business": "Finance / business",
    "science_health": "Science / health",
    "other": "Other",
}

# CHI 2026 paper: Li, Zhang, Bakker — "Request a Note: How the Request Function
# Shapes X's Community Notes System." n=98,685 English-language requested posts.
# Topics aren't mutually exclusive in the paper, so % summed to >100.
ACADEMIC_BASELINE = {
    "politics_combined": 37.0,
    "finance_business": 32.6,
    "entertainment": 26.9,
    "science_health": 13.5,
}


def main() -> None:
    if not SRC.exists():
        print(json.dumps({"db_missing": True, "categories": [], "total": 0}))
        return

    cat_counts: Counter[str] = Counter()
    ship_by_cat: Counter[str] = Counter()
    pol_flag_counts: Counter[bool] = Counter()
    total = 0

    with SRC.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            total += 1
            cat = r.get("category") or "other"
            cat_counts[cat] += 1
            if r.get("outcome") == "submitted":
                ship_by_cat[cat] += 1
            pol_flag_counts[bool(r.get("is_political_misinfo_candidate"))] += 1

    rows = []
    for cat, n in cat_counts.most_common():
        rows.append({
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat),
            "count": n,
            "share_pct": round(n / total * 100, 1) if total else 0,
            "shipped": ship_by_cat[cat],
            "ship_rate_pct": round(ship_by_cat[cat] / n * 100, 2) if n else 0,
        })

    politics_share = (cat_counts["us_politics"] + cat_counts["foreign_politics"]) / total * 100 if total else 0

    output = {
        "total": total,
        "categories": rows,
        "summary": {
            "politics_share_category": round(politics_share, 1),
            "politics_share_claim_flag": round(
                pol_flag_counts[True] / total * 100 if total else 0, 1
            ),
            "us_politics_share": round(cat_counts["us_politics"] / total * 100, 1) if total else 0,
            "us_politics_ship_rate": round(
                ship_by_cat["us_politics"] / cat_counts["us_politics"] * 100
                if cat_counts["us_politics"] else 0, 2
            ),
            "overall_ship_rate": round(
                sum(ship_by_cat.values()) / total * 100 if total else 0, 2
            ),
        },
        "academic_baseline": ACADEMIC_BASELINE,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
