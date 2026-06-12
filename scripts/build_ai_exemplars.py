#!/usr/bin/env python3
"""Build few-shot exemplars from notes written by OTHER AI Note Writers.

Sharper signal than the general-pool exemplars (build_exemplars.py): these are
notes that X's raters rewarded (CURRENTLY_RATED_HELPFUL) or rejected
(CURRENTLY_RATED_NOT_HELPFUL) specifically from admitted AI bots — i.e. the
exact style bar our bot is judged against. Method follows Indicator's
AI-note-writer analyses: userEnrollment apiEarnedIn -> noteStatusHistory
status -> notes text.

Input CSV comes from the extraction script in the community-notes project:
    ~/python_projects/community-notes/scripts/find_ai_crh_notes.py

Usage:
    PYTHONPATH=src .venv/bin/python scripts/build_ai_exemplars.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "src" / "exemplars.json"
DEFAULT_CSV = Path.home() / "python_projects" / "community-notes" / "data" / "ai_crh_notes.csv"

URL_RE = re.compile(r"https?://\S+")
TAG_COLS = [
    ("factual_error", "misleadingFactualError"),
    ("manipulated_media", "misleadingManipulatedMedia"),
    ("outdated_information", "misleadingOutdatedInformation"),
    ("missing_important_context", "misleadingMissingImportantContext"),
    ("disputed_claim_as_fact", "misleadingUnverifiedClaimAsFact"),
    ("misinterpreted_satire", "misleadingSatire"),
]


def _looks_english(t: str) -> bool:
    t = str(t)
    ascii_ratio = sum(c.isascii() for c in t) / max(len(t), 1)
    common = len(re.findall(r"\b(the|is|are|was|this|that|of|and|in|to)\b", t.lower()))
    return ascii_ratio > 0.95 and common >= 2


def _curate(df: pd.DataFrame, *, n: int, max_per_author: int, seed: int) -> pd.DataFrame:
    s = df["summary"].astype(str)
    df = df[
        s.apply(_looks_english)
        & (s.apply(lambda t: len(URL_RE.findall(t))) == 1)  # match our 1-URL format
        & (s.apply(lambda t: len(URL_RE.sub("X", t))) <= 280)  # URL counts ~1 char
    ].copy()
    # Cap per author for stylistic variety, newest first within each author
    df = df.sort_values("createdAtMillis", ascending=False)
    df = df.groupby("noteAuthorParticipantId", group_keys=False).head(max_per_author)
    return df.sample(min(n, len(df)), random_state=seed)


def _to_exemplar(row: pd.Series) -> dict:
    return {
        "noteId": str(row["noteId"]),
        "tweetId": str(row["tweetId"]),
        "note_text": html.unescape(URL_RE.sub("[URL]", str(row["summary"]).strip()))[:500],
        "status": row["currentStatus"],
        "tags": [t for t, col in TAG_COLS if int(row.get(col, 0) or 0) == 1],
        "trustworthy_sources": bool(int(row.get("trustworthySources", 0) or 0)),
        "created_at": datetime.fromtimestamp(
            row["createdAtMillis"] / 1000, tz=timezone.utc
        ).isoformat(timespec="seconds"),
        "author_type": "ai_note_writer",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--since", default="2026-03-01", help="Only notes created on/after this date.")
    ap.add_argument("--helpful", type=int, default=20)
    ap.add_argument("--unhelpful", type=int, default=10)
    ap.add_argument("--max-per-author", type=int, default=3)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df = df[df["created_at"] >= args.since]
    print(f"{len(df):,} AI notes since {args.since}")

    crh = _curate(df[df["currentStatus"] == "CURRENTLY_RATED_HELPFUL"],
                  n=args.helpful, max_per_author=args.max_per_author, seed=42)
    crnh = _curate(df[df["currentStatus"] == "CURRENTLY_RATED_NOT_HELPFUL"],
                   n=args.unhelpful, max_per_author=args.max_per_author, seed=42)
    print(f"curated: {len(crh)} helpful / {len(crnh)} not-helpful "
          f"(English, single-URL, <=280 discounted, max {args.max_per_author}/author)")

    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "AI Note Writers (apiEarnedIn) in X public data",
        "since": args.since,
        "helpful_count": len(crh),
        "unhelpful_count": len(crnh),
        "helpful": [_to_exemplar(r) for _, r in crh.iterrows()],
        "unhelpful": [_to_exemplar(r) for _, r in crnh.iterrows()],
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
