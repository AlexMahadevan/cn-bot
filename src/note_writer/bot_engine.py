"""End-to-end bot runner: fetch posts → write notes → evaluate → submit → log."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set

from cnapi.client import CNClient
from cnapi.evaluate_note import claim_opinion_score, evaluate_note
from cnapi.get_api_eligible_posts import get_posts_eligible_for_notes
from cnapi.get_notes_written import already_noted_post_ids, get_notes_written
from cnapi.submit_note import submit_note
from data_models import NoteResult, Post
from note_writer.config import CLAIM_OPINION_MIN
from note_writer.write_note import research_post_and_write_note
import storage

logger = logging.getLogger(__name__)


class CommunityNotesBot:
    def __init__(self, client: Optional[CNClient] = None):
        self.client = client or CNClient()

    def run(
        self,
        *,
        num_posts: int = 10,
        dry_run: bool = False,
        concurrency: int = 1,
        skip_evaluate: bool = False,
    ) -> List[NoteResult]:
        logger.info("Fetching existing notes...")
        notes = get_notes_written(self.client, test_mode=True)
        already_noted: Set[str] = already_noted_post_ids(notes)
        logger.info("Skipping %d posts already noted.", len(already_noted))

        logger.info("Fetching up to %d candidate posts...", num_posts)
        candidates: List[Post] = get_posts_eligible_for_notes(self.client, max_results=num_posts)
        new_posts = [p for p in candidates if p.post_id not in already_noted]
        logger.info(
            "Got %d candidates, %d new (skipped %d already-noted).",
            len(candidates), len(new_posts), len(candidates) - len(new_posts),
        )

        if not new_posts:
            return []

        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                results = list(pool.map(lambda p: self._process_post(p, dry_run, skip_evaluate), new_posts))
        else:
            results = [self._process_post(p, dry_run, skip_evaluate) for p in new_posts]

        return results

    def _process_post(self, post: Post, dry_run: bool, skip_evaluate: bool) -> NoteResult:
        logger.info("Processing post %s: %r", post.post_id, post.text[:80])
        result: NoteResult = research_post_and_write_note(post)

        evidence = result.evidence[0] if result.evidence else None

        if result.error:
            storage.log_draft(
                post_id=post.post_id, post_text=post.text,
                outcome="error", error=result.error,
            )
            return result

        if not result.note:
            storage.log_draft(
                post_id=post.post_id, post_text=post.text,
                outcome="refused", refusal_reason=result.refusal or "no_note",
                evidence_url=evidence.review_url if evidence else None,
                evidence_rating=evidence.rating if evidence else None,
                evidence_publisher=evidence.publisher_name if evidence else None,
                evidence_tier=evidence.evidence_tier if evidence else None,
            )
            return result

        note = result.note

        # Pre-flight quality gate: score the draft on X's claim/opinion model
        # (free dry-run, does not submit) and skip opinion-like notes so they
        # don't drag the rolling-50 the admission evaluator scores. A genuine API
        # error fails open — we submit rather than lose a good note to a blip.
        if not skip_evaluate:
            try:
                score = claim_opinion_score(
                    evaluate_note(self.client, post_id=note.post_id, note_text=note.note_text)
                )
            except Exception as e:
                logger.warning("evaluate_note failed for %s (submitting anyway): %s", note.post_id, e)
                score = None

            if score is not None:
                logger.info("claim_opinion_score=%.4f for %s", score, note.post_id)
                if score < CLAIM_OPINION_MIN:
                    logger.info(
                        "Gating %s: claim_opinion_score %.4f < %.2f floor (not submitting)",
                        note.post_id, score, CLAIM_OPINION_MIN,
                    )
                    storage.log_draft(
                        post_id=post.post_id, post_text=post.text,
                        outcome="refused",
                        refusal_reason=f"evaluate_note: claim_opinion_score {score:.3f} below {CLAIM_OPINION_MIN:.2f} floor",
                        note_text=note.note_text,
                        evidence_url=note.evidence_url,
                        evidence_rating=evidence.rating if evidence else None,
                        evidence_publisher=evidence.publisher_name if evidence else None,
                        evidence_tier=evidence.evidence_tier if evidence else None,
                        misleading_tags=note.misleading_tags,
                    )
                    result.refusal = (
                        f"claim_opinion_score {score:.3f} below {CLAIM_OPINION_MIN:.2f} floor"
                    )
                    return result

        if dry_run:
            storage.log_draft(
                post_id=post.post_id, post_text=post.text,
                outcome="queued", note_text=note.note_text,
                evidence_url=note.evidence_url,
                evidence_rating=evidence.rating if evidence else None,
                evidence_publisher=evidence.publisher_name if evidence else None,
                evidence_tier=evidence.evidence_tier if evidence else None,
                misleading_tags=note.misleading_tags,
            )
            return result

        # Submit
        try:
            resp = submit_note(
                client=self.client,
                post_id=note.post_id,
                note_text=note.note_text,
                classification=note.classification,
                misleading_tags=note.misleading_tags,
                trustworthy_sources=note.trustworthy_sources,
                test_mode=True,
            )
            logger.info("Submitted note for %s.", note.post_id)
            storage.log_submission(post_id=note.post_id, test_mode=True, response=resp)
            storage.log_draft(
                post_id=post.post_id, post_text=post.text,
                outcome="submitted", note_text=note.note_text,
                evidence_url=note.evidence_url,
                evidence_rating=evidence.rating if evidence else None,
                evidence_publisher=evidence.publisher_name if evidence else None,
                evidence_tier=evidence.evidence_tier if evidence else None,
                misleading_tags=note.misleading_tags,
            )
        except Exception as e:
            logger.error("Submission failed for %s: %s", note.post_id, e)
            result.error = f"Submission failed: {e}"
            storage.log_draft(
                post_id=post.post_id, post_text=post.text,
                outcome="error", error=str(e),
                note_text=note.note_text,
                evidence_url=note.evidence_url,
                evidence_rating=evidence.rating if evidence else None,
                evidence_publisher=evidence.publisher_name if evidence else None,
                evidence_tier=evidence.evidence_tier if evidence else None,
                misleading_tags=note.misleading_tags,
            )
        return result
