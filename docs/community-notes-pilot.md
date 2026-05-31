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

> **[documented]** "Among the most recent 50 notes submitted in test_mode, AI
> Note Writers that have passed the admission criteria will be automatically
> and randomly selected for admission... a sufficient number of an AI Note
> Writer's recent notes will have to achieve a sufficient score from the
> [automated] evaluator."
> — Community Notes guide, "AI Note Writers"
> (https://communitynotes.x.com/guide/en/api/overview)

So the earn-in gate is:

1. Submit notes in test_mode.
2. X's **open-source automated note evaluator** scores each one.
3. Admission looks at your **most recent 50** test_mode notes. If enough of
   them score well enough, you become eligible and may be **randomly selected**
   for admission.
4. Only *after* admission do your notes start showing publicly and getting
   rated Helpful by contributors. **That's** when CRH% becomes meaningful.

**The signal to watch before admission is the evaluator score on the last 50
notes — not Helpful ratings.** **[project]** The writeup calls the cost of a
bad note here the "earn-in tax": low-quality notes drag the rolling-50 score
and push admission further away, which is why the bot is built to refuse rather
than ship a weak note.

---

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

As of 2026-05-30, `src/monitor.py` against the live API showed 43 notes under
the writer:

- **29** `unknown` (not yet evaluated)
- **14** evaluated — **all** `High` on `UrlValidity` and `HarassmentAbuse`;
  `ClaimOpinion` split 6 `High` / 8 `Medium`.

Correct read: the bot's evaluated notes are **passing the automated gates**
cleanly (the anti-hallucination + URL discipline is working). We are **not yet
admitted**, and that is expected — there's no Helpful-rating signal in
test_mode, and we likely need more of the rolling-50 evaluated and scoring well
before the random admission draw can pick us.

### ⚠️ Gotcha for future sessions (and the dashboard/monitor)

`src/monitor.py` prints a "Helpfulness rate vs. industry benchmarks" block
comparing our CRH% against human (8.4%) / AI-average (12.9%) / top (24.1%)
rates. **In test_mode that comparison is meaningless** — CRH% is always 0
because test notes are never public. Don't report "below human average" as if
the notes are doing badly. Those benchmarks only apply *after* admission. The
pre-admission scoreboard is the evaluator buckets above.

---

## Open question worth tracking

The live API reports **43** notes_written but our local audit DB
(`data/notes.db`) logged only **14** submissions. The 14 evaluated ones match
ours exactly (6 High + 8 Medium on ClaimOpinion = 14); the 29 `unknown` are
notes X attributes to the writer that aren't in our log. Possible logging
blind spot (earlier/pilot submissions, or a state we don't record). Worth
reconciling before the writeup quotes any counts.

---

## References

- Quickstart (test_mode rule): https://docs.x.com/x-api/community-notes/quickstart
- Endpoints overview: https://docs.x.com/x-api/community-notes/introduction
- evaluate_note: https://docs.x.com/x-api/community-notes/evaluate-a-community-note
- AI Note Writers / admission: https://communitynotes.x.com/guide/en/api/overview
- Mantzarlis, "8 AI bots now write 50% of X's Community Notes" (Indicator, May 26 2026)
