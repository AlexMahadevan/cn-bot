#!/usr/bin/env python3
"""Build the fine-tuning dataset for the Community Notes generator.

Pulls today's CN public-data snapshot, filters to political CRH+CRNH notes
from the last 12 months, fetches the original tweet text via X's
syndication endpoint, and writes data/cn_training.jsonl — one example
per line, ready for LoRA training.

Resumable: if the script is interrupted, re-running picks up where it
left off by skipping noteIds already present in the output file.

Usage:
    .venv/bin/python scripts/build_training_set.py --target 5000
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "data" / "cn_training.jsonl"
BASE = "https://ton.twimg.com/birdwatch-public-data"
SYND = "https://cdn.syndication.twimg.com/tweet-result"
USER_AGENT = "Mozilla/5.0 (compatible; CN-Bot-Research/1.0; +https://www.poynter.org)"

# Same political keyword filter as build_exemplars.py — keeps the dataset
# focused on US politics, our bot's beat.
US_POLITICAL_KEYWORDS = [
    "trump", "biden", "harris", "obama", "clinton", "vance", "kamala",
    "kennedy", "rfk", "rfk jr", "hegseth", "parnell",
    "congress", "senate", "house ", "representative", "senator", "rep.",
    "speaker", "majority leader", "minority leader",
    "department of war", "department of defense", "pentagon", "doj", "fbi",
    "ice", "cbp", "dhs", "irs", "treasury", "epa", "fda", "cdc", "hhs",
    "secretary of state", "attorney general", "white house",
    "election", "midterm", "republican", "democrat", " gop ", " dem ",
    "primary", "caucus", "ballot", "voter", "voting", "gerrymander",
    "redistricting", "electoral", "congressional",
    "immigration", "border wall", "tariff",
]

NON_US_NEGATIVE = [
    "uk labour", "uk conservative", "downing street", "westminster", "prime minister",
    "canadian", "australian", "german chancellor", "french president",
]

CRH = "CURRENTLY_RATED_HELPFUL"
CRNH = "CURRENTLY_RATED_NOT_HELPFUL"


def _download(url: str) -> bytes:
    print(f"  fetching {url}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def _load_zip_tsv(data: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        with z.open(z.namelist()[0]) as f:
            return pd.read_csv(f, sep="\t", low_memory=False)


def _matches_us_politics(text) -> bool:
    if not isinstance(text, str):
        return False
    lo = " " + text.lower() + " "
    if any(neg in lo for neg in NON_US_NEGATIVE):
        return False
    return any(kw in lo for kw in US_POLITICAL_KEYWORDS)


def _fetch_tweet(tweet_id: str, *, sleep: float = 0.7) -> dict | None:
    """Fetch a single tweet via X's syndication endpoint (no auth)."""
    url = f"{SYND}?id={tweet_id}&token=4"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        time.sleep(sleep)
        if not data.get("text"):
            return None
        return {
            "tweet_id": str(data.get("id_str") or tweet_id),
            "tweet_text": data["text"],
            "tweet_author_screen_name": (data.get("user") or {}).get("screen_name"),
            "tweet_author_name": (data.get("user") or {}).get("name"),
            "tweet_created_at": data.get("created_at"),
            "tweet_favorite_count": data.get("favorite_count"),
            "has_media": bool(data.get("mediaDetails")),
            # The existing CN context, if any — useful for label cross-check
            "existing_cn_context": (data.get("birdwatch_pivot") or {}).get("subtitle", {}).get("text"),
        }
    except urllib.error.HTTPError as e:
        if e.code in (404, 403, 410):
            # Tweet deleted or made private — skip silently
            return None
        print(f"    HTTP {e.code} for {tweet_id}", flush=True)
        time.sleep(sleep * 2)
        return None
    except Exception as e:
        print(f"    err for {tweet_id}: {type(e).__name__}", flush=True)
        time.sleep(sleep * 2)
        return None


def _already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
                done.add(rec["note_id"])
            except Exception:
                continue
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="Snapshot date YYYY/MM/DD. Defaults to today.")
    parser.add_argument("--target", type=int, default=5000,
                        help="Target number of fetched (tweet, note) pairs.")
    parser.add_argument("--helpful-ratio", type=float, default=0.7,
                        help="Fraction of dataset that should be CRH. Rest is CRNH.")
    parser.add_argument("--age-months", type=int, default=12,
                        help="Only consider notes created within last N months.")
    args = parser.parse_args()

    snapshot = args.date or date.today().strftime("%Y/%m/%d")
    print(f"Snapshot: {snapshot}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    already_done = _already_done()
    if already_done:
        print(f"Resuming — {len(already_done)} examples already in {OUT_PATH.name}")

    print("Downloading noteStatusHistory...")
    status = _load_zip_tsv(_download(f"{BASE}/{snapshot}/noteStatusHistory/noteStatusHistory-00000.zip"))
    print(f"  {len(status):,} status rows")

    print("Downloading notes (notes-00000 + historical notes-00001)...")
    notes_chunks = []
    for chunk_idx in range(10):  # CN currently has 2 chunks; allow growth
        url = f"{BASE}/{snapshot}/notes/notes-{chunk_idx:05d}.zip"
        try:
            data = _download(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            raise
        notes_chunks.append(_load_zip_tsv(data))
        print(f"  chunk {chunk_idx}: {len(notes_chunks[-1]):,} rows")
    notes = pd.concat(notes_chunks, ignore_index=True)
    print(f"  total notes loaded: {len(notes):,}")

    # Join and filter
    joined = notes.merge(
        status[["noteId", "currentStatus"]], on="noteId", how="inner"
    )
    cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=30 * args.age_months)).timestamp() * 1000
    j = joined[joined["createdAtMillis"] >= cutoff_ms]
    j = j[j["classification"] == "MISINFORMED_OR_POTENTIALLY_MISLEADING"]
    j = j[j["summary"].apply(_matches_us_politics)]
    j = j[j["currentStatus"].isin([CRH, CRNH])]
    print(f"  filtered to {len(j):,} political CRH+CRNH from last {args.age_months}mo")

    # Stratified sample by status
    helpful_target = int(args.target * args.helpful_ratio)
    unhelpful_target = args.target - helpful_target
    helpful_pool = j[j["currentStatus"] == CRH]
    unhelpful_pool = j[j["currentStatus"] == CRNH]
    print(f"  CRH pool: {len(helpful_pool):,} | CRNH pool: {len(unhelpful_pool):,}")
    print(f"  targets: {helpful_target} helpful, {unhelpful_target} unhelpful")

    # Oversample slightly to account for deleted/private tweets (~15% loss)
    helpful = helpful_pool.sample(
        min(int(helpful_target * 1.2), len(helpful_pool)), random_state=42
    )
    unhelpful = unhelpful_pool.sample(
        min(int(unhelpful_target * 1.2), len(unhelpful_pool)), random_state=42
    )

    queue = pd.concat([helpful, unhelpful]).sample(frac=1, random_state=42)
    print(f"  fetch queue: {len(queue):,} candidate notes")

    # Fetch loop
    fetched = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()
    with OUT_PATH.open("a") as out:
        for _, row in queue.iterrows():
            note_id = str(row["noteId"])
            if note_id in already_done:
                skipped += 1
                continue

            tw = _fetch_tweet(str(row["tweetId"]))
            if tw is None:
                failed += 1
                continue

            label = "helpful" if row["currentStatus"] == CRH else "unhelpful"
            tags = [
                t for t, col in [
                    ("factual_error", "misleadingFactualError"),
                    ("manipulated_media", "misleadingManipulatedMedia"),
                    ("outdated_information", "misleadingOutdatedInformation"),
                    ("missing_important_context", "misleadingMissingImportantContext"),
                    ("disputed_claim_as_fact", "misleadingUnverifiedClaimAsFact"),
                    ("misinterpreted_satire", "misleadingSatire"),
                ]
                if int(row.get(col, 0) or 0) == 1
            ]
            record = {
                "note_id": note_id,
                "label": label,
                "tweet": tw,
                "note": {
                    "text": str(row["summary"]),
                    "classification": str(row["classification"]),
                    "tags": tags,
                    "trustworthy_sources": bool(int(row.get("trustworthySources", 0) or 0)),
                    "created_at": datetime.fromtimestamp(row["createdAtMillis"] / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
                },
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
            fetched += 1
            if fetched % 25 == 0:
                elapsed = time.monotonic() - t0
                rate = fetched / max(elapsed, 1)
                print(f"  fetched {fetched} (skipped {skipped}, failed {failed}) — {rate:.1f}/s", flush=True)
            if fetched >= args.target:
                break

    print(f"\nDone. fetched={fetched}, skipped={skipped}, failed={failed}")
    print(f"Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
