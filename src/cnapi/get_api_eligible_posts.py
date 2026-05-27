from __future__ import annotations

import urllib.parse
from typing import List

from cnapi.client import CNClient
from data_models import Media, Post


def get_posts_eligible_for_notes(
    client: CNClient,
    max_results: int = 100,
    test_mode: bool = True,
) -> List[Post]:
    """Return candidate posts the bot is eligible to note.

    Docs: https://docs.x.com/x-api/community-notes/quickstart
    """
    qs = {"test_mode": str(test_mode).lower(), "max_results": str(max_results)}
    url = "https://api.x.com/2/notes/search/posts_eligible_for_notes?" + urllib.parse.urlencode(qs)
    resp = client._request("GET", url)

    posts: List[Post] = []
    for p in resp.get("data", []):
        media_list = [
            Media(
                media_key=m.get("media_key"),
                media_type=m.get("type"),
                url=m.get("url"),
                preview_image_url=m.get("preview_image_url"),
            )
            for m in p.get("media", [])
        ]
        posts.append(
            Post(
                post_id=str(p["id"]),
                text=p.get("text", ""),
                author_id=p.get("author_id"),
                created_at=p.get("created_at"),
                media=media_list,
            )
        )
    return posts
