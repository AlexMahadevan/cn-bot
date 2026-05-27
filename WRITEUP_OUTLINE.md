# Building an AI Community Notes writer to fix what's broken with AI Community Notes writers

**Working title.** Targets a Poynter.org piece (~2,500 words) and an arXiv companion preprint (~4,000 words + appendix). Goal: ship by Global Fact 2026 (late June).

## The headline

> Most of the AI bots that now write half of X's Community Notes avoid politics. The few that try get downscored by X's own classifier. I built @alexcnotes to do the opposite — and what I learned shows what's actually wrong with how we're scaling AI fact-checking.

## Structure

### 1. The setup (~400 words)
- Open with the @alexcnotes RFK Jr/Tylenol note. Show the post, the note, the result.
- Pivot to Alexios's May 26 piece: 8 bots write 50.3% of CN, but they avoid politics. ClaimOpinion classifier penalizes political claims. Bots concentrate in Grok-dependence and English-only output.
- Thesis: "We can build a CN bot that targets US politics, uses something other than Grok, doesn't hallucinate, and ships defensible notes. Here's what it took and what we found."

### 2. What's broken with the current bots (~500 words)
- **Politics avoidance.** Cite Alexios: AI cohort only fact-checked 5 political accounts vs humans' 43. Why this is bad for the program's mission.
- **Grok concentration.** A majority of bots use Grok because Grok can search X. Single point of failure / bias.
- **English-only.** 87% English vs 57% for humans.
- **Hallucination.** Alexios mentions "outrageous errors" — relate to known LLM fact-grounding research.
- **Note-writing >> note-rating bottleneck.** 89% of notes never get enough ratings. CN is becoming a write-mostly system.

### 3. What we built (~700 words)
- Architecture diagram: post → relevance filter → specificity gate → evidence search (PolitiFact + Google FCT + IFCN web_search + linked X posts) → best-evidence picker → article fetch → Opus 4.7 prose → opinion filter → URL validator → hallucination check → evaluate_note dry-run → submit
- **The anti-hallucination invariant** (the single most important architectural decision): "Every URL on every note traces back to a real search-result hit. Claude writes the words; code writes the citation."
- The four Alexios-inspired gates:
  - **Specificity gate** (catches the "Option for ICE riots [video]" failure mode)
  - **Opinion-detector** (ClaimOpinion proxy — addresses Rand's finding)
  - **Hallucination check** (against article text, with metadata exemption)
  - **X-as-source tier** (closes the citation-X gap without Grok)
- **Few-shot exemplars from public CN data** — 20 helpful + 10 unhelpful real notes injected as style anchors

### 4. What we measured (~700 words)
- **Baseline experiment** (332 held-out test items, scored by Opus 4.7 with anonymized model labels):
  - Haiku 4.5 zero-shot: factual 1.91 / style 2.10 / neutrality 3.55 / predicted-helpful 1.31 / wins 11%
  - Sonnet 4.6 zero-shot: factual 2.45 / style 2.80 / neutrality 3.70 / predicted-helpful 1.94 / wins 29%
  - Opus 4.7 zero-shot: factual 2.80 / style 3.31 / neutrality 4.11 / predicted-helpful 2.39 / wins 61%
- **Headline finding:** Even Opus 4.7, zero-shot, scores 2.39/5 on predicted-helpfulness. Frontier-LLM-just-asked-to-write-a-note is bad.
- **Pipeline experiment** (TODO when credit restored): same 332 items run through the full bot pipeline. Hypothesis: notes that ship through the pipeline score significantly higher across all axes. The architecture > the model.
- **Fine-tune experiment** (TODO): Qwen 2.5 7B LoRA-tuned on 3,237 CRH political notes. Hypothesis: matches Opus on style/predicted-helpful at ~25x lower cost-per-note.
- **Real-world operational data:** N weeks of @alexcnotes activity. Notes shipped, refusal breakdown, CRH rate vs Alexios's 12.9% AI-average / 24.1% top-decile.

### 5. The Craigslist note: what went wrong, what we fixed (~400 words)
- Honest accounting of the bad note that shipped early in development.
- Why the picker was too loose (we'd loosened it after zero notes shipped).
- The specificity gate added in response.
- The lesson: even with an anti-hallucination architecture, you can write a coherent note about a claim the post never made. Fix is upstream filtering, not downstream validation.

### 6. What's still hard (~300 words)
- Recency lag — fact-checkers publish 24-72h after a viral claim. Bot can only note claims with existing fact-checks. Misses a lot.
- Polarizing topics get filtered by the bridging algorithm regardless of note quality (Celeste Labedz's "popularity contest pretending to be a fact checker" critique).
- LLM cost — Opus per-note cost is ~25x the fine-tuned alternative. Operational scale needs the fine-tune.
- The earn-in tax — every bad note costs the account weeks. Strong incentive to write nothing rather than write wrong.

### 7. Reproducing this (~300 words)
- Source: github.com/AlexMahadevan/cn-bot (MIT)
- Methodology for the eval is open. The dataset stays closed for now (per our judgment about responsible release).
- Anyone can run an AI Note Writer with X API access. The architecture transfers.

### 8. What I'd do differently (~200 words)
- Started with the validators, not the volume push. The loose-picker incident wouldn't have happened.
- Should have built the eval harness before the production schedule.
- Should have set Anthropic credit alerts (real failure mode).
- Should have wired the dashboard data rebuild into the cron from day one.

## Appendix (preprint only)

- Full pipeline diagram
- Prompt corpus (all system prompts the bot uses)
- Schema for the JSONL training set
- Statistical methodology for the scoring eval
- Full per-model score distributions, not just means
- Detailed refusal-bucket breakdowns

## Citations

- Mantzarlis, "8 AI bots now write 50% of X's Community Notes" (Indicator, May 26 2026)
- Li & Bakker, "AI Fact-Checking in the Wild" (arXiv 2604.02592, Apr 2026)
- Li et al., "Scaling Human Judgment in Community Notes with LLMs" (arXiv 2506.24118)
- Wu et al., "Beyond the Crowd: LLM-Augmented Community Notes for Governing Health Misinformation" (arXiv 2510.11423)
- Mohammadi et al., "From Birdwatch to Community Notes, from Twitter to X" (arXiv 2510.09585)
- Robinson, "X Is Using AI Fact-Checkers" (CJR, Nov 6 2025)
- Mantzarlis, "An AI bot is now the top contributor to Community Notes on X" (Indicator, Nov 18 2025)

## Status

| Section | What we need | Status |
|---|---|---|
| Setup, intro, what's broken | Just writing | Ready |
| Architecture, the four gates, exemplars | Just writing — code is shipped | Ready |
| Baseline eval | 332 items × 3 models scored | **Done** |
| Pipeline eval | 332 items through bot | Blocked — Anthropic credit |
| Fine-tune eval | Modal training run | Blocked — Modal setup |
| Operational data | 2-4 weeks of @alexcnotes activity | In progress (when bot resumes) |
| Craigslist case study | Just writing | Ready |
