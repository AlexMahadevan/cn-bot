#!/usr/bin/env python3
"""Build the evidence-aware training set: (tweet, article, note) triples.

Reads cn_training.jsonl (which has tweet + note for each helpful row), then
for each note extracts the cited URL, fetches the article text, and writes
a new training file with the triple format the next fine-tune needs.

Why this exists: the first Qwen fine-tune was trained on (tweet → note)
pairs and learned the style of helpful notes without learning how to use
evidence. When the pipeline injected evidence at inference time, the model
treated it as out-of-distribution input — partly copying it, partly
ignoring it. Training on triples teaches the model: given this tweet AND
this article, paraphrase the article into a helpful note.

Usage:
    .venv/bin/python scripts/build_training_set_v2.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import re as _re
import requests as _requests
from note_writer.evidence_text import fetch_evidence_text as _fetch_default  # noqa: E402

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_OG_DESC = _re.compile(
    r"""<meta[^>]+property=["']og:description["'][^>]+content=["']([^"'>]+)["']""",
    _re.IGNORECASE,
)
_META_DESC = _re.compile(
    r"""<meta[^>]+name=["']description["'][^>]+content=["']([^"'>]+)["']""",
    _re.IGNORECASE,
)
_TAG_STRIP = _re.compile(r"<[^>]+>")
_WS = _re.compile(r"\s+")


def _strip(text: str) -> str:
    text = _TAG_STRIP.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _extract_text(html: str, *, max_chars: int = 1200) -> str | None:
    for pattern in (_OG_DESC, _META_DESC):
        m = pattern.search(html)
        if m:
            text = _strip(m.group(1))
            if text and len(text) >= 40:
                return text[:max_chars]
    return None


def _try_url(url: str, *, max_chars: int = 1200) -> str | None:
    try:
        r = _requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=12)
        r.raise_for_status()
    except Exception:
        return None
    return _extract_text(r.text, max_chars=max_chars)


def _try_wayback(original_url: str, *, max_chars: int = 1200) -> str | None:
    """Look up the original URL in the Wayback Machine. Many publishers
    that block direct crawling are archived freely on Wayback."""
    try:
        api = f"https://archive.org/wayback/available?url={original_url}"
        r = _requests.get(api, timeout=10)
        r.raise_for_status()
        data = r.json()
        snap = (
            (data.get("archived_snapshots") or {})
            .get("closest", {})
            .get("url")
        )
        if not snap:
            return None
        return _try_url(snap, max_chars=max_chars)
    except Exception:
        return None


def fetch_evidence_text(url: str, *, max_chars: int = 1200) -> str | None:
    """Robust fetcher: try the default (PolitiFact-aware) extractor first,
    then a browser-UA direct fetch, then Wayback Machine fallback."""
    # 1. Default extractor (handles PolitiFact "If your time is short" too)
    text = _fetch_default(url, max_chars=max_chars)
    if text:
        return text
    # 2. Browser-UA direct fetch — beats simple bot-blockers
    text = _try_url(url, max_chars=max_chars)
    if text:
        return text
    # 3. Wayback Machine — beats hard 403 blockers (NYT, WSJ, etc.)
    return _try_wayback(url, max_chars=max_chars)

REPO = Path(__file__).resolve().parent.parent
IN_PATH = REPO / "data" / "cn_training.jsonl"
OUT_PATH = REPO / "data" / "cn_training_v2.jsonl"
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

# IFCN signatories + reputable primary sources. Notes that cite these tend
# to be the highest-quality training examples.
PREFERRED_PUBLISHERS = (
    "politifact.com",
    "factcheck.org",
    "apnews.com",
    "reuters.com",
    "washingtonpost.com",
    "snopes.com",
    "leadstories.com",
    "usatoday.com",
    "factcheck.afp.com",
    "checkyourfact.com",
    "fullfact.org",
    "nytimes.com",
    "wsj.com",
    "treasury.gov",
    "bls.gov",
    "cbo.gov",
    "congress.gov",
    "whitehouse.gov",
    "house.gov",
    "senate.gov",
)


# URLs we exclude as evidence — they're social-media self-references, not articles.
# A note citing a Twitter post is usually pointing at another tweet for context;
# we can't fetch tweet text via the article-extractor pipeline. Facebook is similar.
EXCLUDED_DOMAINS = (
    "twitter.com", "x.com", "fb.com", "facebook.com", "instagram.com",
    "tiktok.com", "youtube.com", "youtu.be", "reddit.com", "t.co",
    "imgur.com", "i.redd.it",
)


def _pick_url(note_text: str) -> str | None:
    """Pick the best citation URL from a note's text. Prefer IFCN signatories
    + gov sources; fall back to first non-social URL."""
    urls = URL_RE.findall(note_text)
    if not urls:
        return None
    urls = [u.rstrip(".,;:!?)\"'") for u in urls]
    # Drop social-media self-references — these can't be processed as articles
    urls = [u for u in urls if not any(d in u.lower() for d in EXCLUDED_DOMAINS)]
    if not urls:
        return None
    for url in urls:
        if any(pub in url.lower() for pub in PREFERRED_PUBLISHERS):
            return url
    return urls[0]


def _already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = set()
    with OUT_PATH.open() as f:
        for line in f:
            try:
                done.add(json.loads(line)["note_id"])
            except Exception:
                continue
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10,
                        help="Parallel article fetches.")
    parser.add_argument("--limit", type=int, default=0, help="Cap rows (0=all)")
    args = parser.parse_args()

    if not IN_PATH.exists():
        sys.exit(f"No source data at {IN_PATH}. Run build_training_set.py first.")

    rows = [json.loads(l) for l in IN_PATH.open() if l.strip()]
    helpful = [r for r in rows if r["label"] == "helpful"]
    logger.info("Loaded %d total rows, %d helpful", len(rows), len(helpful))

    done = _already_done()
    todo = [r for r in helpful if r["note_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    logger.info("To process: %d (skipping %d already done)", len(todo), len(done))

    write_lock = threading.Lock()
    n_done = 0
    n_no_url = 0
    n_no_article = 0

    def _job(rec: dict):
        nonlocal n_no_url, n_no_article
        note_text = rec["note"]["text"]
        url = _pick_url(note_text)
        if not url:
            with write_lock:
                n_no_url += 1
            return None
        article = fetch_evidence_text(url, max_chars=1200)
        if not article or len(article) < 60:
            with write_lock:
                n_no_article += 1
            return None
        return {
            "note_id": rec["note_id"],
            "tweet": rec["tweet"],
            "evidence": {
                "url": url,
                "text": article,
            },
            "note": rec["note"],
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_job, r) for r in todo]
        for f in as_completed(futures):
            res = f.result()
            if res is None:
                continue
            with write_lock:
                with OUT_PATH.open("a") as out:
                    out.write(json.dumps(res) + "\n")
            n_done += 1
            if n_done % 50 == 0:
                logger.info("Built %d triples (no_url=%d, no_article=%d)", n_done, n_no_url, n_no_article)

    logger.info("Done. triples=%d  no_url=%d  no_article=%d", n_done, n_no_url, n_no_article)
    logger.info("Output: %s", OUT_PATH)


if __name__ == "__main__":
    main()
