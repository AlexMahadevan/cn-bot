from typing import Dict, Any, List
from cnapi.client import CNClient

def _normalize_tags(tags: List[Any]) -> List[str]:
    """
    Ensure every misleading_tag is a plain string.
    Accepts either Enum members or raw strings.
    """
    return [t.value if hasattr(t, "value") else str(t) for t in tags]


def submit_note(
    client: CNClient,
    post_id: str,
    note_text: str,
    classification: str,
    misleading_tags: list,
    trustworthy_sources: bool = True,
    test_mode: bool = True,
) -> Dict[str, Any]:
    """
    Submit a Community Note and return the API response as a dict.
    """
    payload = {
        "test_mode": test_mode,
        "post_id": post_id,
        "info": {
            "text": note_text,
            "classification": classification,
            "misleading_tags": _normalize_tags(misleading_tags),
            "trustworthy_sources": trustworthy_sources,
        },
    }

    return client._request("POST", "https://api.x.com/2/notes", json=payload)
