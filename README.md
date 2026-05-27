# cn-bot — AI Community Notes writer for @alexcnotes

An AI Note Writer enrolled in X's Community Notes program. Reads candidate posts, looks for fact-checks of the claims they make, and submits notes when it finds a clean match. Built and run by [Alex Mahadevan](https://www.poynter.org/author/alex-mahadevan/) at Poynter.

Public audit dashboard: *(coming soon — built with Observable Framework, source under `dashboard/`)*

## What this is

A pipeline that does, roughly:

1. Pull posts from `/2/notes/search/posts_eligible_for_notes`.
2. Filter to US politics (Claude Haiku triage).
3. Search PolitiFact, Google Fact Check Tools API, and (only when needed) Anthropic web_search against IFCN signatories.
4. Pick the best-matching fact check (Claude Haiku).
5. Fetch the actual article text so Claude has the publisher's own words to ground the note.
6. Draft prose with Claude Opus 4.7. The model writes only words — code substitutes the verified URL.
7. Validate length, URL, and a hallucination guard that rejects any prose containing a URL or domain-with-path.
8. Dry-run score via X's `evaluate_note` endpoint.
9. Submit via `POST /2/notes` in `test_mode` (required during the AI Note Writer pilot).
10. Log every step to SQLite.

The single architectural invariant: **the bot will never cite a URL that came from Claude's own text output.** Every URL on every note traces back to a real search-result hit. Claude writes the words; code writes the citation.

## Layout

| Path | What |
|---|---|
| `src/main.py` | CLI entrypoint |
| `src/note_writer/` | The pipeline — relevance filter, evidence search, note writer, validators |
| `src/cnapi/` | X Community Notes API client (eligible posts, evaluate, submit, notes_written) |
| `src/storage.py` | SQLite audit trail |
| `src/stats.py` | Quick-look dashboard from the command line |
| `src/monitor.py` | Daily ratings pull from `/2/notes/search/notes_written` |
| `dashboard/` | Observable Framework public dashboard |

## Configure

Copy `.env.example` to `.env` and fill in:

```
X_API_KEY=
X_API_KEY_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=
ANTHROPIC_API_KEY=
FACT_CHECK_API_KEY=
```

Then:

```
uv sync
uv run src/main.py --num-posts 25 --dry-run
```

## License

MIT.
