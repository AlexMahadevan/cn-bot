"""Resolve and fetch text from URLs embedded in X posts.

The bot's specificity gate explicitly rejects posts whose claim lives in
linked media — because images and videos can't be fact-checked safely from
the post text alone. But many posts ARE plain news-article shares: a
headline-style claim with a t.co URL pointing at AP/Reuters/etc.

This module follows those links, fetches the article text, and returns it
so the specificity gate can evaluate the post against what the article
actually says — without loosening the gate's rules about unfetched media.

Skipped domains: social media (twitter/x, fb, instagram, reddit), video
(youtube/youtu.be, tiktok), and image hosts (imgur, i.redd.it). Everything
else gets fetched as a potential news article.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests

from note_writer.evidence_text import fetch_evidence_text

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Domains we never follow — claim is in unfetchable media OR another social post.
# Social-media posts get handled by x_post_evidence; image hosts can't be
# turned into article text; video sites require transcription we don't do.
_SKIP_DOMAINS = (
    "twitter.com", "x.com", "fb.com", "facebook.com",
    "instagram.com", "reddit.com",
    "tiktok.com", "youtube.com", "youtu.be",
    "imgur.com", "i.redd.it", "i.imgur.com",
    "t.me",  # telegram
)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RESOLVE_TIMEOUT = 8
_MAX_LINKS_PER_POST = 3
_MAX_ARTICLE_CHARS = 1500


_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']refresh["'][^>]+content=["'][^"']*URL=([^"'>]+)["']""",
    re.IGNORECASE,
)
_LOCATION_REPLACE_RE = re.compile(
    r"""location\.replace\(["']([^"']+)["']\)""",
    re.IGNORECASE,
)


def _resolve_url(url: str) -> Optional[str]:
    """Follow redirects (e.g. t.co → real URL). Handles both HTTP 3xx and
    HTML meta-refresh / JS location.replace patterns that t.co uses for
    some links. Returns final URL or None on network error."""
    try:
        # GET (not HEAD) — many publishers reject HEAD; we need the body
        # anyway if t.co uses meta-refresh.
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=_RESOLVE_TIMEOUT,
            headers={"User-Agent": _BROWSER_UA},
            stream=True,
        )
        final_url = resp.url or url

        # If we already left t.co via HTTP redirect, we're done.
        # (Parenthesization matters: hostname can be None, and the old
        # `x not in hostname or ""` parsed as `(x not in hostname) or ""`,
        # which raised TypeError on None and killed link expansion for the
        # whole post.)
        if "t.co" not in (urlparse(final_url).hostname or ""):
            resp.close()
            return final_url

        # Still on t.co — parse body for meta-refresh / location.replace
        # Read just the first ~4KB; the redirect is always in <head>
        try:
            body = next(resp.iter_content(chunk_size=4096), b"").decode(
                "utf-8", errors="ignore"
            )
        finally:
            resp.close()

        m = _META_REFRESH_RE.search(body) or _LOCATION_REPLACE_RE.search(body)
        if m:
            # Unescape JS-style escaping like https:\/\/twitter.com\/...
            target = m.group(1).replace("\\/", "/")
            return target

        return final_url
    except requests.RequestException as e:
        logger.debug("URL resolve failed for %s: %s", url, e)
        return None


def _should_skip(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return True
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in _SKIP_DOMAINS)


def expand_post_links(post_text: str) -> Optional[str]:
    """Find linked news articles in a post, fetch their text, return as
    a single concatenated context string. Returns None if no links resolved
    to fetchable article content."""
    if not post_text:
        return None
    urls = _URL_RE.findall(post_text)
    if not urls:
        return None

    # Strip trailing punctuation that often follows URLs in tweet text
    urls = [u.rstrip(".,;:!?)]}\"'") for u in urls][:_MAX_LINKS_PER_POST]

    pieces: list[str] = []
    seen_finals: set[str] = set()
    for raw_url in urls:
        final_url = _resolve_url(raw_url)
        if not final_url:
            continue
        # Dedupe — if two links resolve to the same article, skip the dupe
        canonical = final_url.split("#", 1)[0].split("?", 1)[0]
        if canonical in seen_finals:
            continue
        if _should_skip(final_url):
            logger.debug("Skipping non-article URL: %s", final_url)
            continue
        article_text = fetch_evidence_text(final_url, max_chars=_MAX_ARTICLE_CHARS)
        if not article_text or len(article_text) < 60:
            continue
        seen_finals.add(canonical)
        try:
            host = urlparse(final_url).hostname or final_url
        except Exception:
            host = final_url
        pieces.append(f"[Linked article — {host}]\n{article_text}")

    if not pieces:
        return None
    return "\n\n".join(pieces)
