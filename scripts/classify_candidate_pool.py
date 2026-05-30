#!/usr/bin/env python3
"""Classify all candidate posts X has surfaced to the bot into topic categories.

Reads every unique candidate from the drafts table, asks Haiku to classify it
into one of nine topic categories, writes results to
data/candidate_pool_analysis.jsonl. Idempotent — already-classified posts
are skipped on re-run.

The point: answer empirically what X actually surfaces to AI Note Writers
via the eligible-posts endpoint. The CHI 2026 paper found 37% politics,
32.6% finance, 26.9% entertainment, 13.5% sci/tech across 98K requested
posts. This script lets us replicate against our own sample.

Usage:
    .venv/bin/python scripts/classify_candidate_pool.py --concurrency 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from note_writer.llm_util import HAIKU_MODEL, parse_json  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

logger = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "data" / "notes.db"
OUT_PATH = REPO / "data" / "candidate_pool_analysis.jsonl"


CATEGORIES = [
    "us_politics",          # US elections, US politicians, federal/state policy, election integrity
    "foreign_politics",     # non-US political claims (UK, EU, Israel, Russia/Ukraine, etc.)
    "finance_business",     # markets, stocks, crypto, companies, economy
    "entertainment",        # celebrities, music, film, TV, anime, gossip
    "sports",               # actual sports outcomes, athletes, leagues
    "gaming_tech",          # video games, consumer tech, gadgets
    "science_health",       # vaccines, climate, medicine, science findings
    "personal_lifestyle",   # relationships, emotional content, vague reactions, generic snark
    "other",                # uncategorizable, religious, philosophical, etc.
]


class Classification(BaseModel):
    category: str = Field(description=f"One of: {', '.join(CATEGORIES)}")
    is_political_misinfo_candidate: bool = Field(
        description="Does this post make a specific factual claim about politics that could be fact-checked? "
                    "Includes US AND foreign political claims."
    )
    reasoning: str = Field(description="One short sentence explaining the category choice.")


_SYSTEM = """You are classifying X posts to understand what X's Community Notes "request a note" endpoint surfaces. Be conservative — pick the dominant topic, not adjacent ones.

Categories (pick ONE):
- us_politics: US elections, US politicians/officials, federal or state policy, US election integrity, US domestic political controversies
- foreign_politics: non-US political claims — UK politics, EU, Israel/Palestine, Russia/Ukraine, Latin America, any foreign government
- finance_business: stocks, markets, crypto, specific company claims, economic indicators
- entertainment: celebrities, music artists, films, TV, anime, gossip, K-pop, awards
- sports: sports outcomes, athletes, leagues, specific games or matches
- gaming_tech: video games, gaming culture, consumer tech, gadgets, AI hype
- science_health: vaccines, climate, medicine, COVID, science findings
- personal_lifestyle: relationships, emotional content, vague reactions, generic snark with no specific claim
- other: religious, philosophical, art, uncategorizable

If a post is just a vague reaction ("This is wild" / "I smell a rat" / a single emoji) with no clear topic, pick personal_lifestyle.

If a post is on a SUBSTANTIVE political topic (US or foreign), set is_political_misinfo_candidate=true. Otherwise false.

Return JSON matching the schema."""


def _already_classified() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                done.add(json.loads(line)["post_id"])
            except Exception:
                continue
    return done


def _classify(post_id: str, post_text: str) -> dict | None:
    """Classify one post via Haiku. Returns the record dict or None on failure."""
    user_prompt = f"X post:\n\"\"\"\n{post_text.strip()[:1000]}\n\"\"\"\n\nClassify."
    try:
        result: Classification = parse_json(
            user_prompt=user_prompt,
            schema=Classification,
            system=_SYSTEM,
            model=HAIKU_MODEL,
            max_tokens=300,
        )
    except Exception as e:
        logger.warning("Classification failed for %s: %s", post_id, e)
        return None
    cat = result.category if result.category in CATEGORIES else "other"
    return {
        "post_id": post_id,
        "post_text": post_text[:300],
        "category": cat,
        "is_political_misinfo_candidate": result.is_political_misinfo_candidate,
        "reasoning": result.reasoning,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    # Pull all unique candidates from drafts table
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT post_id, post_text, created_at, outcome FROM drafts "
        "WHERE post_text IS NOT NULL AND length(post_text) > 0 "
        "GROUP BY post_id "  # dedup by post_id
    ).fetchall()
    candidates = [
        {"post_id": str(r[0]), "post_text": r[1], "created_at": r[2], "outcome": r[3]}
        for r in rows
    ]
    logger.info("Loaded %d unique candidate posts from drafts table", len(candidates))

    done = _already_classified()
    todo = [c for c in candidates if c["post_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    logger.info("To classify: %d (skipping %d already done)", len(todo), len(done))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_lock = threading.Lock()
    n_done = 0

    def _job(c):
        result = _classify(c["post_id"], c["post_text"])
        if not result:
            return None
        # Carry forward draft metadata
        result["created_at"] = c["created_at"]
        result["outcome"] = c["outcome"]
        with write_lock:
            with OUT_PATH.open("a") as f:
                f.write(json.dumps(result) + "\n")
        return result

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, c) for c in todo]
        for f in as_completed(futures):
            r = f.result()
            if r is not None:
                n_done += 1
                if n_done % 50 == 0:
                    logger.info("Classified %d / %d", n_done, len(todo))

    logger.info("Done. Classified: %d. Output: %s", n_done, OUT_PATH)


if __name__ == "__main__":
    main()
