# X Community Notes API — test_mode & the AI Note Writer earn-in

This doc explains how the AI Note Writer pilot actually works, so we don't
misread the bot's status (it's easy to do — see the gotcha at the bottom).

Everything here is sourced from X's own docs plus what we observe live from
the API. Facts are tagged **[documented]** (from X), **[observed]** (from our
own API responses), or **[project]** (our framing / writeup).

---

## test_mode — and why it's hardcoded `true`

> **[documented]** "Currently, `test_mode` must be set to `true` for all
> requests. **Test notes are not publicly visible.**"
> — https://docs.x.com/x-api/community-notes/quickstart

During the pilot there is no choice: every request must be test_mode. That's
why `test_mode=True` is hardcoded in **all five** of our API call sites:

| Call site (`src/cnapi/`) | Endpoint | test_mode effect |
|---|---|---|
| `get_api_eligible_posts.py` | `GET /2/notes/search/posts_eligible_for_notes` | returns candidate posts to note |
| `evaluate_note.py` | `POST /2/evaluate_note` | dry-run scores a draft (free) — does **not** submit |
| `submit_note.py` | `POST /2/notes` | submits a note that is **not shown publicly** |
| `get_notes_written.py` | `GET /2/notes/search/notes_written` | lists our notes + their evaluation outcomes |

`src/note_writer/bot_engine.py` and `src/monitor.py` likewise pass
`test_mode=True`.

**Consequence that matters:** because test notes are never shown publicly,
they are **never seen or rated by human contributors**. So they cannot become
`CURRENTLY_RATED_HELPFUL` and cannot accrue Helpful / Not-Helpful counts. A
0.0% "Helpfulness rate" in test_mode is **structurally guaranteed**, not a
performance signal.

---

## How an AI Note Writer earns in

**[documented, 2026-10]** X's engineers published the rule in full in
Miller et al., *Community-Driven API and AI Writer Design for Openly Scaling
Community Notes* (arXiv:2609.40067, Sept/Oct 2026):

> "AI writers earn in by fulfilling three note quality checks based on their
> 50 most recently submitted notes."

The three checks, over those 50 notes:

| Check | Threshold |
|---|---|
| **UrlValidity** | at least 95% of notes have URLs that resolve to HTTP 200 after redirects |
| **HarassmentAbuse** | at least 98% of notes are not classified as harassment or abuse |
| **ClaimOpinion** | at least 30% of notes score above the bottom 20% of CRH note scores, **and** no more than 30% score below the bottom 5% of CRH note scores |

There is no random draw in the paper's account (the older guide text said
"randomly selected"). Of 65 client accounts that began earn-in, 32 earned
writing ability, 19 remain active; of the 33 that did not, **13 never
submitted the 50 required notes**, 5 failed the ClaimOpinion checks, and 15
completed earn-in but never published.

So the gate is: submit 50 notes in test_mode, keep all three checks green
over the most recent 50, and `scoring_status.has_access` flips to `true`.
`src/monitor.py` prints this scorecard daily. **[project]** The writeup calls
the cost of a bad note here the "earn-in tax": a weak note drags the
rolling-50 and pushes admission further away, which is why the bot refuses
rather than ships.

### Why we stalled for four months (resolved 2026-10-19)

X attributes **76** notes to this writer: our 47 logged submissions (May 27 to
June 17, 2026) plus **29 orphans** from the original template-bot run on
**July 4-8, 2025** (note ids 1941…-1942…, texts like "Climate change is real …
see https://www.politi"). Those 29 were never evaluated: empty `test_result`,
empty `status`. They are the "open question" below, now closed.

The bot stopped on June 17 with 47 clean notes, so the 50-note window still
held **3 orphans**. Reading an unevaluated note as failing every check, the
window scored **47/50 = 94% on UrlValidity (needs 95%)** and 94% on
HarassmentAbuse (needs 98%). ClaimOpinion was fine (33 High, 12 Medium, 2 Low:
66% ≥ 30% and 4% ≤ 30%). Three more clean notes push the last orphans out and
the window reads 50/50 on both. That is the whole fix; everything else is
making sure the next notes are clean (see the URL liveness gate below).

### What happens after admission

- **Quota ladder** (paper, Appendix A): new writers with fewer than 20 total
  notes get **10 notes/day**. Then the limit follows **hit rate** =
  (CRH − CRNH) / (CRH + CRNH + NMR) over a recency window (the 14-day
  calculation ignores NMR notes with fewer than 10 ratings): a 10% hit rate
  allows ~50/day, 20% allows the 500/day maximum. **Killswitch:** if 8 of the
  last 10 resolved notes are CRNH, the limit drops to 2/day; 3 of the last 5
  CRNH → 5/day.
- **Feeds:** writers start on the **Small** feed (~430 posts/day). Access to
  Large/XL/XXL needs ≥100 notes written, hit rate ≥5%, CRNH rate ≤10%; XXL
  also needs net CRH ≥100 over 90 days.
- **Deletion:** `DELETE /2/notes/{id}` exists (`src/cnapi/delete_note.py`).
  Deleted notes still count toward totals and the daily limit; deletion only
  protects hit rate. X's own six writers delete ~20% of what they submit using
  real-time ratings; real-time ratings require ≥100 net CRH over 90 days, so
  small writers only have the public data dump (~2-day lag).
- **Competition:** six X-operated Community Writer accounts produced >90% of
  AI-writer notes and 55% of Helpful notes in September 2026 (Indicator,
  2026-10-16). X open-sourced that writer: github.com/xai-org/community-writer.
  Its prompts and rejector design are a free syllabus on what gets rated
  Helpful. Local clone: `~/python_projects/_external/community-writer-src`.

### URL liveness gate (added 2026-10-19)

`src/note_writer/url_liveness.py` GETs the evidence URL (browser UA, follows
redirects, no body) and the engine refuses the note unless the final status
is **200**. Before this, the evidence-text fetch was best-effort and a 403 from
a bot-blocking publisher (Bloomberg, WSJ, most paywalls) was only a warning; a
dry run that day drafted a note citing a bloomberg.com URL that 403s. One such
note in the window is 2% of the 5% UrlValidity budget. x.com URLs pass through.

## What the evaluator checks (the `evaluation_outcome` buckets)

`get_notes_written` returns, per evaluated note, an `evaluation_outcome` array.
Each entry is an `evaluator_type` with an `evaluator_score_bucket`.

**[observed]** Buckets we see in production, and what each appears to check:

| `evaluator_type` | What it checks | Good bucket |
|---|---|---|
| `UrlValidity` | the cited URL resolves / returns a healthy HTTP status | **High** |
| `HarassmentAbuse` | the note is free of harassing / abusive language | **High** |
| `ClaimOpinion` | the note addresses a checkable **claim**, not an opinion | **High** (Medium is weaker) |

**[documented]** The `evaluate_note` endpoint response also exposes a numeric
`claim_opinion_score` (double).
— https://docs.x.com/x-api/community-notes/evaluate-a-community-note

`evaluator_score_bucket` takes `High` / `Medium` / `Low`. Notes not yet scored
come back with status `unknown`.

---

## Reading our own status correctly

Run `PYTHONPATH=src .venv/bin/python src/monitor.py`. It prints:

1. `ADMITTED (scoring_status.has_access)` — the only admission signal. Two
   false alarms were called from UI screens in May 2026; never call it from
   anything else.
2. The **EARN-IN SCORECARD** over the 50 most recent notes with PASS/FAIL per
   check (strict reading: unevaluated = fail) plus an evaluated-only view.
3. After admission: status counts, hit rate, and the quota ladder.

As of 2026-10-19 (before the relaunch): 76 notes, 47 evaluated (all High on
UrlValidity and HarassmentAbuse; ClaimOpinion 33 High / 12 Medium / 2 Low),
29 unevaluated orphans, 3 of them inside the window → UrlValidity 94%, FAIL.
Scorecards are appended to `data/earn_in_scorecards.jsonl`; the admission
flip drops `data/EARNED_IN.flag` and fires a macOS notification.

### ⚠️ Gotcha for future sessions (and the dashboard)

In test_mode a 0% "Helpfulness rate" is structurally guaranteed because test
notes are never public. Don't report "below human average" as if the notes
are doing badly. The pre-admission scoreboard is the evaluator scorecard above.

## Open question — closed 2026-10-19

The 29 notes X counts that `notes.db` never logged are the **July 4-8, 2025**
submissions from the original, unmodified template bot (the run whose
hallucinated URLs prompted the rewrite). They predate the audit DB. They were
never evaluated and now only matter as window ballast until 3 clean notes
push them out.

## References

- Quickstart (test_mode rule): https://docs.x.com/x-api/community-notes/quickstart
- Endpoints overview: https://docs.x.com/x-api/community-notes/introduction
- evaluate_note: https://docs.x.com/x-api/community-notes/evaluate-a-community-note
- AI Note Writers / admission: https://communitynotes.x.com/guide/en/api/overview
- Miller et al. 2026, Community-Driven API and AI Writer Design for Openly Scaling Community Notes: https://arxiv.org/abs/2609.40067
- X's open-source Community Writer: https://github.com/xai-org/community-writer
- DiResta, Mantzarlis, Silverman, Indicator briefing (2026-10-16): https://indicator.media/p/briefing-x-handed-fact-checking-to-the-crowd-then-it-became-the-crowd
- Mantzarlis, "8 AI bots now write 50% of X's Community Notes" (Indicator, May 26 2026)
