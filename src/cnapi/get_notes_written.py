"""
Fetch the notes we (this authenticated user) have written in test‑mode.

We avoid the `note.fields` parameter because the endpoint only accepts
id, status and test_result.  We defensively access `post_id` with .get()
so the code never crashes if the field is absent.
"""

import time, urllib.parse
from typing import List, Dict, Any, Set

from cnapi.client import CNClient


def get_notes_written(
    client: CNClient,
    test_mode: bool = True,
    max_results: int = 100,
) -> List[Dict[str, Any]]:
    """
    Return a flat list of our own Community Notes (test‑mode).
    """
    notes: List[Dict[str, Any]] = []
    next_token: str | None = None
    base = "https://api.x.com/2/notes/search/notes_written"

    while True:
        qs = {
            "test_mode": str(test_mode).lower(),
            "max_results": str(max_results),
        }
        if next_token:
            qs["pagination_token"] = next_token

        url = f"{base}?{urllib.parse.urlencode(qs)}"
        page = client._request("GET", url)
        notes.extend(page.get("data", []))

        next_token = page.get("meta", {}).get("next_token")
        if not next_token:
            break

        # stay well under 90 requests / 15 minutes
        time.sleep(0.6)

    return notes


def already_noted_post_ids(notes: List[Dict[str, Any]]) -> Set[str]:
    """
    Build a set of post_ids we’ve already noted (field may be missing).
    """
    return {n.get("post_id") for n in notes if n.get("post_id")}
