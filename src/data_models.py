from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class MisleadingTag(str, Enum):
    factual_error = "factual_error"
    manipulated_media = "manipulated_media"
    outdated_information = "outdated_information"
    missing_important_context = "missing_important_context"
    disputed_claim_as_fact = "disputed_claim_as_fact"
    misinterpreted_satire = "misinterpreted_satire"
    other = "other"


class Media(BaseModel):
    media_key: Optional[str] = None
    media_type: Optional[str] = None
    url: Optional[str] = None
    preview_image_url: Optional[str] = None


class Post(BaseModel):
    """A post returned by /2/notes/search/posts_eligible_for_notes.

    post_id is a string — X snowflake IDs exceed JS safe-integer range.
    Other fields are optional because the endpoint may omit them.
    """

    model_config = ConfigDict(extra="ignore")

    post_id: str
    text: str
    author_id: Optional[str] = None
    created_at: Optional[datetime] = None
    media: List[Media] = []


class FactCheckEvidence(BaseModel):
    """A single piece of evidence about a claim.

    `evidence_tier` records HOW we sourced it — used downstream to choose
    note phrasing and to audit which tier produced which notes.
        - ifcn_verified: IFCN signatory fact-check with explicit verdict
        - primary_source: official data, transcripts, or originating reports
        - self_fact_check: Claude's evaluation of broader news/web sources
    """

    claim_text: str
    claimant: Optional[str] = None
    claim_date: Optional[str] = None
    publisher_name: str
    publisher_site: Optional[str] = None
    review_url: str
    review_title: Optional[str] = None
    review_date: Optional[str] = None
    rating: Optional[str] = None
    snippet: Optional[str] = None
    evidence_tier: str = "ifcn_verified"


class ProposedNote(BaseModel):
    post_id: str
    note_text: str
    classification: str = "misinformed_or_potentially_misleading"
    misleading_tags: List[MisleadingTag] = []
    trustworthy_sources: bool = True
    confidence: float = 0.0
    evidence_url: str = ""


class NoteResult(BaseModel):
    post: Post
    note: Optional[ProposedNote] = None
    refusal: Optional[str] = None
    error: Optional[str] = None
    evidence: List[FactCheckEvidence] = []
