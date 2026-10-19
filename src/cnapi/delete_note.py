"""Delete one of our own notes: DELETE /2/notes/{note_id}.

Learned from X's open-source Community Writer (xai-org/community-writer,
src/cnapi/delete_note.py); the endpoint is not in the public quickstart. X's
paper (arXiv:2609.40067) says deleted notes still count toward submission
totals and the daily writing limit, so deletion only protects the hit rate
(CRH-CRNH)/(CRH+CRNH+NMR), never the quota.

Not wired to any policy yet. Before admission there are no ratings to act on.
After admission X's own writers delete notes trending toward Not Helpful using
real-time ratings; smaller writers only see the public data dump (~2-day lag).
"""

from __future__ import annotations

from cnapi.client import CNClient


def delete_note(client: CNClient, note_id: str) -> bool:
    resp = client._request("DELETE", f"https://api.x.com/2/notes/{note_id}")
    return bool((resp.get("data") or {}).get("deleted", False))
