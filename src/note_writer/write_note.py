"""Note-writing pipeline with hard URL validation.

Anti-hallucination design:
1. Evidence URLs come from Google Fact Check Tools API (verified IFCN sources).
2. Claude writes ONLY the prose of the note — no URL.
3. We append the verified URL programmatically.
4. Validator rejects any draft where Claude tried to write a URL on its own.

Even if Claude tries to invent a citation, the validator catches it before
submission. No URL ever reaches X that didn't come from a real fact check.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from data_models import (
    FactCheckEvidence,
    MisleadingTag,
    NoteResult,
    Post,
    ProposedNote,
)
from note_writer.evidence import gather_for_post
from note_writer.evidence_text import fetch_evidence_text
from note_writer.llm_util import (
    HAIKU_MODEL,
    OPUS_MODEL,
    complete,
    describe_image,
    parse_json,
)
from note_writer import finetune_client
from note_writer.error_check import check_note as check_note_for_hallucination
from note_writer.opinion_check import passes_opinion_filter
from note_writer.relevance_filter import is_on_beat
from note_writer.specificity_check import has_specific_claim

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
NOTE_MAX_CHARS_INCLUDING_URL = 280

_EXEMPLARS_PATH = Path(__file__).resolve().parent.parent / "exemplars.json"


def _load_exemplars_block() -> str:
    """Load and format the few-shot exemplars from src/exemplars.json.

    These are real X Community Notes that human raters have evaluated as
    helpful or not-helpful. They act as style anchors — Claude calibrates
    tone, length, citation framing, and what raters reward against this
    real-world ground truth.
    """
    if not _EXEMPLARS_PATH.exists():
        return ""
    try:
        data = json.loads(_EXEMPLARS_PATH.read_text())
    except Exception as e:
        logger.warning("Failed to load exemplars: %s", e)
        return ""

    def fmt(notes: list, label: str) -> str:
        lines = [f"\n--- {label} ---"]
        for i, n in enumerate(notes, 1):
            tags = ", ".join(n.get("tags", [])) or "(no tags)"
            lines.append(f"[{i}] tags={tags}")
            lines.append(f"    {n.get('note_text', '').strip()}")
        return "\n".join(lines)

    helpful = data.get("helpful", [])
    unhelpful = data.get("unhelpful", [])
    if not helpful and not unhelpful:
        return ""

    return (
        "\n\n## STYLE ANCHORS: real X Community Notes evaluated by raters\n"
        "These are real notes other writers shipped — NOT relevant to the post "
        "you're noting now. They calibrate tone, length, citation phrasing, and "
        "what raters reward. Notice: helpful notes state facts directly with "
        "specific sources, avoid editorializing, and don't lean on 'Publisher X "
        "rated this Y' framing when a direct correction is cleaner."
        f"\n{fmt(helpful, f'CURRENTLY RATED HELPFUL ({len(helpful)} examples — emulate the style')}"
        f"\n{fmt(unhelpful, f'CURRENTLY RATED NOT HELPFUL ({len(unhelpful)} examples — avoid these patterns')}"
    )


_EXEMPLARS_BLOCK = _load_exemplars_block()

_NOTE_WRITER_SYSTEM = """You write Community Notes for X. Your beat: US political misinformation, particularly around the 2026 midterms.

You will be given:
- An X post
- Optional image descriptions
- A single piece of evidence about the post, drawn from one of two tiers:
    (a) IFCN-VERIFIED — a fact-check from PolitiFact, AP, Reuters, FactCheck.org, etc., with an explicit verdict like "False" or "Pants on Fire". Phrase the note as "[Publisher] rated this [verdict]. [Why]."
    (b) PRIMARY SOURCE — official data, government records, or major news coverage that contradicts the post's claim, but with NO formal fact-check verdict. Phrase the note as "Per [publisher], [the actual fact]." or "[Publisher] reports [the actual fact]."

Your job: write the PROSE of a Community Note. The URL is appended programmatically — do NOT write any URL, http://, https://, or domain name yourself. If you write a URL, your note will be rejected.

Rules for the prose:
- Stay neutral and non-partisan. State what the evidence shows, not who is right.
- **DO NOT add specific numbers, percentages, dates, names, or factual claims that are NOT present in the evidence I show you.** The evidence title and rating are your ONLY source — do not draw on your training-data knowledge to fill in numbers or context. If you want to cite a figure and it's not in the evidence, leave the figure out.
- No hedging like "some say" — cite the publisher by name.
- No hashtags, no emojis, no editorializing.
- Keep it tight: the URL we append takes ~80 characters, so prose must be <= 200 characters.
- Do NOT preface with "Community Note:" or similar.
- Do NOT include a URL, even a partial one or domain name.

If the evidence doesn't clearly support a note on this exact post — for example, the source is about a related but distinct claim, or it doesn't actually contradict the post — respond with exactly "NO_NOTE" and nothing else. Be strict: only write a note when you would stake your professional reputation on the evidence's relevance.

For PRIMARY SOURCE evidence specifically: you are NOT giving a verdict. You are pointing at what the actual data shows. If the evidence doesn't speak directly to the post's specific claim, return NO_NOTE.

Examples of good notes (just the prose, URL appended after):
- "PolitiFact rated this Pants on Fire. The figure cited has no support in Congressional Budget Office data and was fabricated by an anonymous social post."
- "AP Fact Check found this image was generated by AI. The original photo, taken in 2019, shows a different scene entirely."
- "Per Treasury data, year-to-date tariff revenue averaged about $250M/day in 2025, not the $2 billion/day figure in this post."
- "Per the CBO, the actual federal deficit projection is $1.9 trillion for FY2025 — not the figure shown here."

Return only the prose, nothing else. No quotes around it. No prefix."""

# Inject the few-shot exemplars at module load so the system prompt is built once.
if _EXEMPLARS_BLOCK:
    _NOTE_WRITER_SYSTEM = _NOTE_WRITER_SYSTEM + _EXEMPLARS_BLOCK
    logger.info("Loaded note-writer exemplars (%d chars)", len(_EXEMPLARS_BLOCK))


def _validate_prose(text: str) -> tuple[bool, str]:
    """Reject prose that contains a URL or a domain-with-path.

    Publisher names like 'FactCheck.org' or 'NPR.org' are allowed — they're
    naming the source, not citing a URL. The validator only fires on actual
    URLs (http/https/://), on www. prefixes, or on domain.tld/path patterns
    that look like real URLs without the scheme.
    """
    if not text or not text.strip():
        return False, "Empty note prose."
    if text.strip().upper() == "NO_NOTE":
        return False, "Model declined."
    if URL_RE.search(text):
        return False, f"Prose contains a URL (hallucination guard): {URL_RE.search(text).group(0)}"
    # http/https/scheme separator anywhere
    if re.search(r"\b(https?|www\.)", text, re.IGNORECASE) or "://" in text:
        return False, "Prose contains a URL or scheme prefix (hallucination guard)."
    # A domain followed by a path (e.g., 'politifact.com/factchecks/...') —
    # the slash is the giveaway that it's a URL, not a brand name like
    # 'FactCheck.org' or 'NPR.org' which we WANT to permit as publisher names.
    if re.search(r"\b[a-z0-9-]+\.(com|org|net|gov|edu|io|us|co)/[A-Za-z0-9]", text, re.IGNORECASE):
        return False, "Prose contains a domain with path (hallucination guard)."
    return True, ""


def _render_note(prose: str, evidence_url: str) -> str:
    """Join Claude's prose with the verified URL."""
    return f"{prose.strip()} {evidence_url}"


def _validate_final_note(note_text: str, evidence_url: str) -> tuple[bool, str]:
    """Final-form validator: exactly one URL, and it's our evidence URL."""
    urls = URL_RE.findall(note_text)
    if len(urls) == 0:
        return False, "Final note has no URL."
    if len(urls) > 1:
        return False, f"Final note has {len(urls)} URLs (expected 1)."
    # Strip trailing punctuation for comparison
    final_url = urls[0].rstrip(".,;:!?)")
    expected = evidence_url.rstrip(".,;:!?)")
    if final_url != expected:
        return False, f"URL mismatch: note has {final_url!r}, evidence had {expected!r}."
    body_len = len(URL_RE.sub("", note_text).strip())
    total_len = len(note_text)
    if total_len > NOTE_MAX_CHARS_INCLUDING_URL:
        return False, f"Note is {total_len} chars (limit {NOTE_MAX_CHARS_INCLUDING_URL})."
    if body_len < 30:
        return False, "Note prose is too short to be substantive."
    return True, ""


def _pick_best_evidence(
    post: Post, images_summary: str, candidates: List[FactCheckEvidence]
) -> Optional[FactCheckEvidence]:
    """Ask Claude to pick the single best-matching fact check, or none."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _fmt_candidate(i: int, c: FactCheckEvidence) -> str:
        # Different tiers display differently — IFCN evidence has rating + claim_text;
        # primary sources have a title + snippet but no formal verdict; X-posts are
        # original quotes/photos the misleading post may be misrepresenting.
        tier_label = {
            "ifcn_verified": "IFCN fact-check (has explicit verdict)",
            "primary_source": "Primary source (gov data / official record)",
            "self_fact_check": "News or primary source (no formal verdict)",
            "x_post": "Original X post the current post may be misrepresenting",
        }.get(c.evidence_tier, c.evidence_tier or "evidence")
        lines = [
            f"[{i}] {tier_label}",
            f"    Publisher: {c.publisher_name}",
        ]
        if c.rating:
            lines.append(f"    Rating: {c.rating}")
        if c.claim_text:
            lines.append(f"    Claim/title: {c.claim_text[:200]}")
        elif c.review_title:
            lines.append(f"    Title: {c.review_title[:200]}")
        if c.snippet and c.snippet != c.claim_text:
            lines.append(f"    Snippet: {c.snippet[:200]}")
        lines.append(f"    URL: {c.review_url}")
        return "\n".join(lines)

    options = "\n\n".join(_fmt_candidate(i, c) for i, c in enumerate(candidates))

    class _Pick(BaseModel):  # type: ignore
        best_index: int = -1
        reason: str = ""

    try:
        pick = parse_json(
            user_prompt=(
                "Below is an X post and a list of candidate evidence sources. "
                "Pick the SINGLE candidate that would let a Community Note "
                "writer directly correct or contextualize the post's main "
                "factual claim.\n\n"
                "Candidates come in several tiers — all are valid:\n"
                "- IFCN FACT-CHECK: a verdict like 'False' / 'Pants on Fire'. "
                "  Match when the fact-check addresses the same specific claim.\n"
                "- PRIMARY SOURCE (gov data, news, official record): cite when "
                "  the source contains the actual fact that contradicts the "
                "  post (e.g., the real Treasury figure, the actual quote, "
                "  the official report). The note doesn't need a 'rating' — "
                "  it can just say 'Per [source], the actual figure is X.'\n"
                "- ORIGINAL X POST: cite when the current post is "
                "  misrepresenting another X post (a misattributed quote, "
                "  a doctored screenshot, a missing-context reply).\n\n"
                "Picking criteria — choose a candidate that:\n"
                "- Speaks directly to the post's specific factual claim, AND\n"
                "- Would settle the question for a reader who clicked through.\n\n"
                "REJECT (return best_index=-1) when:\n"
                "- All candidates are about the same TOPIC but a different specific claim\n"
                "- All candidates are about the same PERSON but a different statement\n"
                "- You're not sure whether any match — when in doubt, return -1\n\n"
                "It is far more harmful to pick a loose match (the bot will "
                "write a wrong note) than to return -1.\n\n"
                f"Post claim: {post.text}\n\n"
                + (f"Images:\n{images_summary}\n\n" if images_summary else "")
                + f"Candidates:\n{options}\n\n"
                "Return JSON: {\"best_index\": <int>, \"reason\": \"...\"}"
            ),
            schema=_Pick,
            model=HAIKU_MODEL,
            max_tokens=300,
        )
        if 0 <= pick.best_index < len(candidates):
            logger.info("Picked evidence [%d]: %s", pick.best_index, pick.reason)
            return candidates[pick.best_index]
        logger.info("No candidate matches: %s", pick.reason)
        return None
    except Exception as e:
        logger.warning("Evidence picker failed (%s); using top candidate.", e)
        return candidates[0]


def _classify_tags(post: Post, evidence: FactCheckEvidence, note_text: str) -> List[MisleadingTag]:
    """Classify misleading_tags. Uses Haiku + structured outputs."""

    class _Tags(BaseModel):  # type: ignore
        tags: List[str]

    rating_line = (
        f"Fact check rating: {evidence.rating} ({evidence.publisher_name})"
        if evidence.rating
        else f"Source: {evidence.publisher_name} (primary source — no formal verdict)"
    )
    try:
        result = parse_json(
            user_prompt=(
                "Given the post, the evidence that contradicts it, and the proposed Community Note, "
                "return the misleading-content tags that apply. Choose from: "
                "factual_error, manipulated_media, outdated_information, missing_important_context, "
                "disputed_claim_as_fact, misinterpreted_satire, other. Return at least one tag.\n\n"
                f"Post: {post.text}\n\n"
                f"{rating_line}\n"
                f"Evidence claim/title: {evidence.claim_text}\n\n"
                f"Proposed note: {note_text}\n\n"
                "Return JSON: {\"tags\": [\"factual_error\", ...]}"
            ),
            schema=_Tags,
            model=HAIKU_MODEL,
            max_tokens=200,
        )
        tags = [MisleadingTag(t) for t in result.tags if t in MisleadingTag.__members__]
        return tags or [MisleadingTag.factual_error]
    except Exception as e:
        logger.warning("Tag classifier failed (%s); defaulting to factual_error.", e)
        return [MisleadingTag.factual_error]


def _summarize_images(post: Post) -> str:
    summary = ""
    for i, media in enumerate(post.media or []):
        if media.media_type == "photo" and media.url:
            try:
                desc = describe_image(media.url)
                summary += f"Image {i + 1}: {desc}\n"
            except Exception as e:
                logger.warning("Image description failed: %s", e)
    return summary


def research_post_and_write_note(post: Post) -> NoteResult:
    """Full pipeline: filter → specificity gate → search → write → validate → render."""
    # 1. Relevance filter (cheap, fast)
    on_beat, reason = is_on_beat(post.text)
    if not on_beat:
        logger.info("Off-beat post %s: %s", post.post_id, reason)
        return NoteResult(post=post, refusal=f"Off-beat: {reason}")

    # 2. Specificity gate — does the post TEXT make a falsifiable claim?
    # This blocks posts like "Option for ICE riots. Just saying. [video]" whose
    # claim lives in unseen media. Without this gate, the bot can keyword-match
    # and write a note about something the post never actually asserted.
    has_claim, claim_text, claim_reason = has_specific_claim(post.text)
    if not has_claim:
        logger.info("No specific claim in post %s: %s", post.post_id, claim_reason)
        return NoteResult(post=post, refusal=f"No specific falsifiable claim in post text: {claim_reason}")

    # 3. Image context
    images_summary = _summarize_images(post)

    # 3. Search for verified fact checks across all evidence sources
    candidates = gather_for_post(post.text)
    if not candidates:
        return NoteResult(post=post, refusal="No matching IFCN fact check found.")

    # 4. Pick the single best match
    evidence = _pick_best_evidence(post, images_summary, candidates)
    if evidence is None:
        return NoteResult(
            post=post,
            refusal="Fact checks found, but none directly address this post's claim.",
            evidence=candidates,
        )

    # 5. Fetch the actual article text so Claude has real publisher words to ground
    # the note in (instead of synthesizing detail from training data).
    article_text = fetch_evidence_text(evidence.review_url)
    if article_text:
        logger.info("Fetched evidence text (%d chars) from %s", len(article_text), evidence.publisher_site)

    # Ask Claude to write the PROSE only (no URL).
    # Prompt shape depends on evidence tier.
    if evidence.evidence_tier == "ifcn_verified":
        evidence_block = (
            "Evidence tier: IFCN-VERIFIED (fact-check with explicit verdict)\n"
            f"- Publisher: {evidence.publisher_name}\n"
            f"- Rating: {evidence.rating or '(unknown)'}\n"
            f"- Claim assessed: {evidence.claim_text}\n"
            f"- Review title: {evidence.review_title or '(none)'}\n"
            f"- Review date: {evidence.review_date or '(unknown)'}\n"
            + (f"- Article text:\n  {article_text}\n" if article_text else "")
            + "\nCRITICAL: Paraphrase ONLY from the article text above. "
            "Do not introduce specific figures, dates, or factual details that "
            "are not in the article text."
        )
    else:
        # primary_source or self_fact_check
        evidence_block = (
            "Evidence tier: PRIMARY SOURCE (no formal fact-check verdict)\n"
            f"- Publisher: {evidence.publisher_name}\n"
            f"- Title: {evidence.review_title or '(none)'}\n"
            + (f"- Article text:\n  {article_text}\n" if article_text else f"- Snippet: {evidence.snippet or '(none)'}\n")
            + "\nIMPORTANT: This source did NOT issue a fact-check verdict. "
            "Only write a note if the article text directly contradicts or corrects "
            "the post's specific factual claim. Frame as 'Per [publisher], [the actual fact].' "
            "Paraphrase ONLY from the article text above — do not introduce details "
            "not in the article. If the article merely covers the topic without "
            "contradicting the post, return NO_NOTE."
        )

    # The URL we'll append takes a known number of chars, plus 1 space.
    prose_budget = max(60, NOTE_MAX_CHARS_INCLUDING_URL - len(evidence.review_url) - 1)

    user_prompt = (
        f"X post:\n{post.text}\n\n"
        + (f"Image descriptions:\n{images_summary}\n\n" if images_summary else "")
        + evidence_block
        + f"\n\nWrite the Community Note PROSE only (no URL — we append it). "
        f"HARD LIMIT: your prose must be {prose_budget} characters or fewer "
        f"(including spaces and punctuation). The URL we append is "
        f"{len(evidence.review_url)} characters, and we add 1 space, so anything "
        f"longer than {prose_budget} chars will be rejected. Count carefully. "
        "Return only the prose, or NO_NOTE."
    )

    # Generator: by default Claude Opus 4.7. If CN_BOT_USE_FINETUNED=true is
    # set in the env AND the Modal endpoint URL is configured, route through
    # the fine-tuned Qwen 2.5 7B instead. Same downstream validators run
    # either way, so swapping generators is safe — bad output gets caught.
    use_qwen = os.getenv("CN_BOT_USE_FINETUNED", "").strip().lower() in ("1", "true", "yes")
    if use_qwen and finetune_client.is_available():
        # Tell Qwen the exact char budget so its prose fits with the URL appended.
        prose_budget_for_qwen = max(60, NOTE_MAX_CHARS_INCLUDING_URL - len(evidence.review_url) - 1)
        prose = finetune_client.generate_note(
            post_text=post.text,
            evidence_text=article_text or evidence.review_title or evidence.claim_text or "",
            max_chars=prose_budget_for_qwen,
            max_new_tokens=220,
            temperature=0.3,
        )
        if prose is None:
            # Fall through to Opus if the fine-tuned endpoint failed.
            logger.warning("Fine-tuned endpoint returned None for %s; falling back to Opus", post.post_id)
            use_qwen = False

    if not use_qwen or not finetune_client.is_available():
        try:
            prose = complete(
                user_prompt=user_prompt,
                system=_NOTE_WRITER_SYSTEM,
                model=OPUS_MODEL,
                max_tokens=600,
                effort="high",
            )
        except Exception as e:
            return NoteResult(post=post, error=f"LLM error: {e}", evidence=[evidence])

    # 6. Validate prose has no URL (hallucination guard)
    ok, why = _validate_prose(prose)
    if not ok:
        logger.warning("Prose rejected for post %s: %s\nProse: %s", post.post_id, why, prose)
        return NoteResult(post=post, refusal=why, evidence=[evidence])

    # 7. ClaimOpinion-style filter — does the prose read as opinion/speculation?
    # Per Alexios's reporting on David Rand's findings, X's ClaimOpinion
    # classifier systematically downscores political notes that read as opinion.
    # We approximate it with a Haiku classifier and reject opinion-shaped drafts.
    ok, why = passes_opinion_filter(prose)
    if not ok:
        logger.warning("Opinion filter rejected post %s: %s", post.post_id, why)
        return NoteResult(post=post, refusal=f"Opinion filter: {why}", evidence=[evidence])

    # 8. Render final note with the VERIFIED URL appended
    note_text = _render_note(prose, evidence.review_url)

    # 9. Final-form validator (length, single URL, exact URL match)
    ok, why = _validate_final_note(note_text, evidence.review_url)
    if not ok:
        logger.warning("Final note rejected for post %s: %s", post.post_id, why)
        return NoteResult(post=post, refusal=why, evidence=[evidence])

    # 10. Hallucination check: does the note state specifics not in the article?
    # Catches the Craigslist-class failure where Claude embellishes from training data.
    if article_text:
        ok, why, unsupported = check_note_for_hallucination(
            prose,
            article_text,
            publisher=evidence.publisher_name,
            rating=evidence.rating,
        )
        if not ok:
            logger.warning(
                "Hallucination check rejected post %s: %s\nUnsupported: %s",
                post.post_id, why, unsupported,
            )
            return NoteResult(
                post=post,
                refusal=f"Hallucination check: {why} (unsupported: {unsupported})",
                evidence=[evidence],
            )

    # 11. Classify misleading_tags
    tags = _classify_tags(post, evidence, note_text)

    return NoteResult(
        post=post,
        note=ProposedNote(
            post_id=post.post_id,
            note_text=note_text,
            classification="misinformed_or_potentially_misleading",
            misleading_tags=tags,
            trustworthy_sources=True,
            confidence=1.0,
            evidence_url=evidence.review_url,
        ),
        evidence=[evidence],
    )
