#!/usr/bin/env python3
"""Build the few-shot exemplars from the public Community Notes dataset.

Pulls today's noteStatusHistory + notes snapshot, joins on noteId, filters
to recent CRH/CRNH political notes, samples a curated set, and writes
src/exemplars.json. The note writer loads that file into its system
prompt as cached few-shot context — the practical equivalent of
fine-tuning at our scale.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/build_exemplars.py
    # Or with a specific snapshot date:
    PYTHONPATH=src .venv/bin/python scripts/build_exemplars.py --date 2026/05/27
"""

from __future__ import annotations

import argparse
import io
import json
import re
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "src" / "exemplars.json"
BASE = "https://ton.twimg.com/birdwatch-public-data"

# Keywords that indicate US political content. Match against the note's
# `summary` (the note text). Broad enough to catch the beat — narrower than
# all-politics so we don't pull in foreign elections.
US_POLITICAL_KEYWORDS = [
    # Presidents / former candidates / current admin
    "trump", "biden", "harris", "obama", "clinton", "vance", "kamala",
    "kennedy", "rfk", "rfk jr", "hegseth", "parnell",
    # Congress / branches
    "congress", "senate", "house ", "representative", "senator", "rep.",
    "speaker", "majority leader", "minority leader",
    # Cabinet / agencies
    "department of war", "department of defense", "pentagon", "doj", "fbi",
    "ice", "cbp", "dhs", "irs", "treasury", "epa", "fda", "cdc", "hhs",
    "secretary of state", "attorney general", "white house",
    # Elections / parties
    "election", "midterm", "republican", "democrat", " gop ", " dem ",
    "primary", "caucus", "ballot", "voter", "voting", "gerrymander",
    "redistricting", "electoral", "congressional",
    # Policy categories US-coded
    "immigration", "border wall", "tariff",
]

# Republic. Words we treat as "definitely NOT US political" to filter out
# noise that contains a keyword by accident.
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


def _load_zip_tsv(data: bytes, **read_csv_kwargs) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        with z.open(z.namelist()[0]) as f:
            return pd.read_csv(f, sep="\t", low_memory=False, **read_csv_kwargs)


def _matches_us_politics(text: str) -> bool:
    if not isinstance(text, str):
        return False
    lo = " " + text.lower() + " "
    if any(neg in lo for neg in NON_US_NEGATIVE):
        return False
    return any(kw in lo for kw in US_POLITICAL_KEYWORDS)


def _strip_urls(text: str) -> str:
    """Replace URLs with [URL] placeholder for cleaner exemplar text."""
    return re.sub(r"https?://\S+", "[URL]", text)


def _build_payload(notes: pd.DataFrame, status: pd.DataFrame, *, max_helpful: int, max_unhelpful: int, snapshot: str) -> dict:
    print(f"  joining {len(notes)} notes with {len(status)} status rows")
    joined = notes.merge(
        status[["noteId", "currentStatus", "timestampMillisOfCurrentStatus"]],
        on="noteId",
        how="inner",
    )
    print(f"  joined: {len(joined)}")

    # Restrict to last 90 days by createdAtMillis
    ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp() * 1000
    recent = joined[joined["createdAtMillis"] >= ninety_days_ago]
    print(f"  last 90 days: {len(recent)}")

    # Only notes whose classification is MISINFORMED_OR_POTENTIALLY_MISLEADING
    # (these correspond to "the bot's class" — making a misleading-rating note)
    misleading = recent[recent["classification"] == "MISINFORMED_OR_POTENTIALLY_MISLEADING"]
    print(f"  misleading-classification: {len(misleading)}")

    # Filter to US politics by summary keywords
    is_political = misleading["summary"].apply(_matches_us_politics)
    political = misleading[is_political]
    print(f"  US-political: {len(political)}")

    # Pull CRH and CRNH samples
    crh = political[political["currentStatus"] == CRH].copy()
    crnh = political[political["currentStatus"] == CRNH].copy()
    print(f"  CRH political: {len(crh)} | CRNH political: {len(crnh)}")

    # For helpful: prefer trustworthySources=1 (more in line with bot output style)
    crh_trusted = crh[crh["trustworthySources"] == 1]
    if len(crh_trusted) >= max_helpful:
        crh = crh_trusted
        print(f"  using only trustworthySources=1 CRH ({len(crh_trusted)} available)")

    # Random sample, but deterministic with seed for reproducibility
    helpful_sample = crh.sample(min(max_helpful, len(crh)), random_state=42)
    unhelpful_sample = crnh.sample(min(max_unhelpful, len(crnh)), random_state=42)

    def _to_exemplar(row: pd.Series) -> dict:
        return {
            "noteId": str(row["noteId"]),
            "tweetId": str(row["tweetId"]),
            "note_text": _strip_urls(str(row["summary"]).strip())[:500],
            "status": row["currentStatus"],
            "tags": [
                t for t, col in [
                    ("factual_error", "misleadingFactualError"),
                    ("manipulated_media", "misleadingManipulatedMedia"),
                    ("outdated_information", "misleadingOutdatedInformation"),
                    ("missing_important_context", "misleadingMissingImportantContext"),
                    ("disputed_claim_as_fact", "misleadingUnverifiedClaimAsFact"),
                    ("misinterpreted_satire", "misleadingSatire"),
                ]
                if int(row.get(col, 0) or 0) == 1
            ],
            "trustworthy_sources": bool(int(row.get("trustworthySources", 0) or 0)),
            "created_at": datetime.fromtimestamp(row["createdAtMillis"] / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
        }

    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_date": snapshot,
        "helpful_count": len(helpful_sample),
        "unhelpful_count": len(unhelpful_sample),
        "helpful": [_to_exemplar(r) for _, r in helpful_sample.iterrows()],
        "unhelpful": [_to_exemplar(r) for _, r in unhelpful_sample.iterrows()],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="Snapshot date YYYY/MM/DD. Defaults to today.")
    parser.add_argument("--helpful", type=int, default=20)
    parser.add_argument("--unhelpful", type=int, default=10)
    args = parser.parse_args()

    snapshot = args.date or date.today().strftime("%Y/%m/%d")
    print(f"Snapshot: {snapshot}")

    print("Downloading noteStatusHistory...")
    status = _load_zip_tsv(_download(f"{BASE}/{snapshot}/noteStatusHistory/noteStatusHistory-00000.zip"))
    print(f"  loaded {len(status)} status rows")

    print("Downloading notes...")
    notes = _load_zip_tsv(_download(f"{BASE}/{snapshot}/notes/notes-00000.zip"))
    print(f"  loaded {len(notes)} notes")

    payload = _build_payload(
        notes, status,
        max_helpful=args.helpful,
        max_unhelpful=args.unhelpful,
        snapshot=snapshot,
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT_PATH}")
    print(f"  {payload['helpful_count']} helpful exemplars, {payload['unhelpful_count']} unhelpful")


if __name__ == "__main__":
    main()
