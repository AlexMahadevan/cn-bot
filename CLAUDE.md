# CLAUDE.md

Guidance for Claude Code working in this repo (the `cn-bot` / `@alexcnotes`
project — an AI Community Notes writer for X, built by Alex Mahadevan at
Poynter). Read this first; it'll save you from the mistakes below.

## What this is

A pipeline that pulls posts eligible for Community Notes, filters to US
politics, finds a matching IFCN fact-check, drafts a note with Claude, and
submits it to X — **always in test_mode**, as the AI Note Writer pilot
requires. There's an Observable Framework dashboard (`dashboard/`) published to
GitHub Pages as a public audit trail.

See `README.md` for the 10-step pipeline narrative.

## ⚠️ Read before reporting on bot performance

The bot runs in **test_mode**, which is hardcoded `true` everywhere and is
mandatory during the pilot. **Test notes are never shown publicly, so they
never get rated Helpful by contributors.** Therefore:

- A **0% "Helpfulness rate" / 0 CRH is normal and expected** — not a sign the
  notes are bad. `src/monitor.py` prints a CRH%-vs-industry-benchmark block
  that is **meaningless in test_mode**. Do not report "below human average."
- The real pre-admission signal is the **automated evaluator's scores** on the
  **most recent 50 notes** (the `evaluation_outcome` buckets: `UrlValidity`,
  `HarassmentAbuse`, `ClaimOpinion`, each `High`/`Medium`/`Low`). Admission to
  writing public notes is gated on those scores, then a random draw.

Full explanation, sources, and the current status read-out:
**`docs/community-notes-pilot.md`** — read it before answering any "how's the
bot doing / has it earned in?" question.

## The one architectural invariant — do not break it

**The bot will never cite a URL that came from Claude's text output.** Claude
writes only prose; code substitutes the verified URL from a real search-result
hit. The hallucination guard (`src/note_writer/error_check.py` and the
validators) rejects any draft prose containing a URL or domain-with-path. If
you touch the note-writing or validation path, preserve this.

## Layout

| Path | What |
|---|---|
| `src/main.py` | CLI entrypoint — runs the bot once |
| `src/note_writer/bot_engine.py` | Orchestrates the pipeline per post |
| `src/note_writer/` | Stages: relevance_filter, specificity_check, evidence*, opinion_check, write_note, error_check |
| `src/cnapi/` | X Community Notes API client (all calls pass `test_mode=True`) |
| `src/storage.py` | SQLite audit trail → `data/notes.db` |
| `src/monitor.py` | Live ratings/earn-in pull from `notes_written` (see caveat above) |
| `src/stats.py` | CLI quick-look stats |
| `dashboard/` | Observable Framework public dashboard |
| `dashboard/tools/build_bot_json.py` | Builds `bot.json` snapshot from `notes.db` |
| `dashboard/tools/refresh.sh` | Regenerate snapshot + commit (then `git push`) |
| `scripts/` | One-off eval / training / fine-tune scripts (not the live bot) |
| `docs/community-notes-pilot.md` | test_mode + earn-in reference |

## Commands

```bash
# Run the bot once (real submit in test_mode). --num-posts default 10.
PYTHONPATH=src .venv/bin/python src/main.py --num-posts 50

# Dry run — draft + evaluate but DON'T submit.
PYTHONPATH=src .venv/bin/python src/main.py --num-posts 25 --dry-run

# Or via uv (per README):
uv run src/main.py --num-posts 25 --dry-run

# Live earn-in / ratings status from X:
PYTHONPATH=src .venv/bin/python src/monitor.py

# Refresh the public dashboard from the latest DB, then push:
./dashboard/tools/refresh.sh && git push
```

The launchd wrapper `scripts/run-bot.sh` runs `--num-posts 50` then touches the
dashboard loader to bust its cache.

## Dashboard publish flow

`refresh.sh` rebuilds `dashboard/src/data/bot.json` (+ `candidate_pool.json`)
from `data/notes.db` and commits. A `git push` to `main` triggers the GitHub
Pages deploy (`.github/workflows/deploy-dashboard.yml`). The public site only
reflects what's been committed + pushed — fresh DB rows don't appear until you
refresh and push.

## Conventions & gotchas

- Python ≥3.12; deps in `pyproject.toml`; env in `.env` (X OAuth1 keys,
  `ANTHROPIC_API_KEY`, `FACT_CHECK_API_KEY`). Never commit `.env`.
- The X API uses OAuth1 (`src/cnapi/client.py`) with naive 429 back-off.
- Self-fact-check `web_search` against IFCN signatories frequently logs
  `400 ... domains are not accessible to our user agent` — that's an Anthropic
  web_search crawler limitation, expected and non-fatal; the bot falls back to
  PolitiFact + Google Fact Check Tools.
- The note count X reports (`notes_written`) can exceed what `notes.db` logged
  — see the open reconciliation question in `docs/community-notes-pilot.md`.
- The git remote is `github.com/AlexMahadevan/cn-bot` (branch `main`); local
  dir is named `template-api-note-writer`.
- Commit/push only when asked.

## Project context

The work targets a Poynter.org piece + arXiv preprint for Global Fact 2026
(late June). See `WRITEUP_OUTLINE.md` for the argument and the planned
experiments (baseline / pipeline / fine-tune evals under `scripts/` + `data/`).
