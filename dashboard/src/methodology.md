---
title: How it works
---

# How the bot works

The whole bot is a pipeline, run every two hours by a launchd job on a Mac. A run does roughly this:

1. **Ask X for posts.** Hit `/2/notes/search/posts_eligible_for_notes` and get back up to 25 posts X thinks are noteworthy.
2. **Skip what's already noted.** Compare against notes the bot has already written so it doesn't duplicate.
3. **Relevance filter.** A small, fast Claude Haiku call decides whether the post is about US politicians, federal/state policy, election integrity, or political misinformation. If not, drop it. This stage is cheap, so it's strict by default — better to skip a maybe than spend money on a no.
4. **Image context.** If the post has photos, Claude describes them. Images that look manipulated or AI-generated get flagged.
5. **Evidence search.** For posts that pass the filter, the bot queries multiple sources in order:
   - **PolitiFact direct** — scrape `/search` results and pair each fact-check URL with its truth-o-meter rating.
   - **Google Fact Check Tools API** — searches ClaimReview structured data from IFCN signatories.
   - **Anthropic web_search constrained to IFCN domains** — only fires if the two above return nothing.
   - **Broad web search across primary sources** — gov data, major newsrooms, IFCN sites. Only fires if everything above returns nothing. Each candidate's evidence tier is recorded so you can see, on every note, which path produced it.
6. **Pick the best-matching candidate.** A Haiku call reads all the candidates and picks the single one whose claim most directly matches the post. If none match, return nothing.
7. **Fetch the actual fact-check article.** The bot reads the cited article — usually the `og:description` or PolitiFact's "If Your Time is Short" summary — and passes that text to the note writer. This is the grounding step. Without it, Claude fills in detail from its training data, which can be wrong.
8. **Write the prose.** A Claude Opus call drafts the note. The prompt forbids URLs in the prose. Opus produces just the words; the bot appends the verified URL programmatically.
9. **Validate.** Two checks:
   - The prose must not contain `http`, `https`, or any domain-like text. Any URL the model tried to write means it's hallucinating, and the note is dropped.
   - The final rendered note (prose + URL) must be under 280 characters and contain exactly one URL — the one we appended.
10. **Tag.** A second Haiku call classifies the misleading-content tags X expects (`factual_error`, `manipulated_media`, etc.).
11. **Pre-flight scoring.** X exposes `/2/evaluate_note` — a free dry-run scoring endpoint. The bot calls it before actually submitting. If X's scorer would reject the note, the bot drops it.
12. **Submit.** `POST /2/notes` with `test_mode: true`. X requires test mode during the AI Note Writer pilot; submitted notes aren't publicly visible until the account "earns in" through community ratings.
13. **Log everything.** SQLite captures every step — post seen, refusal reason, evidence considered, note written, X's response, later rating outcomes. This dashboard reads that log directly.

## The thing the bot will never do

The bot will never cite a URL that came from Claude's own text output. Every URL on every note traces back to a real search-result hit from one of the evidence sources. Claude writes the words; code writes the citation. If that invariant ever breaks, every note ever shipped is suspect.

This is the structural fix for a hallucination problem the bot's earlier version had — Claude would fabricate plausible-looking URLs that didn't exist, or paraphrase real URLs into nonexistent ones. The current design makes that failure mode impossible by construction.

## Why some claims won't get noted

Three categories of post the bot can't help with, even if a human reader would call them misleading:

- **Predictions and opinions.** "He's going to lose the midterms" isn't a factual claim.
- **Claims with no coverage anywhere yet.** Fact-checks aren't the only evidence the bot will accept — when no IFCN signatory has weighed in yet, it falls back to government data (BLS, CBO, Treasury), primary records (Congress.gov, agency filings) and reporting from major newsrooms. Notes shipped through that route are tagged `self_fact_check` or `primary_source` in the dashboard. But in the first hour or two of a viral claim, sometimes literally nothing exists yet.
- **Claims that need fresh original reporting.** Anything where verification means calling people, not searching the web.

The bot is designed to fill the gap between "the false claim is still spreading" and "someone has already published evidence against it." When that evidence is a PolitiFact rating, the bot cites it. When it's a BLS jobs report or a court filing, the bot cites that. It's not designed to do journalism — but it's also not limited to citing other fact-checkers.

## Source code

[github.com/AlexMahadevan/cn-bot](https://github.com/AlexMahadevan/cn-bot). The code is the spec — anything ambiguous in the description above, check the code.
