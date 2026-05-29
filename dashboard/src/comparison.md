---
title: How this bot compares
---

# How this bot compares to other AI Community Notes writers

Eight AI bots now write about half of all Community Notes on X. My friend Alexios Mantzarlis [reported that finding](https://www.indicator.media/p/8-ai-bots-now-write-50-of-xs-community) at Indicator earlier this year. Most of those bots are built on Grok, which has privileged access to X's data — its `x_search` tool can read the full thread under any post in a way no other model can.

I wanted to know two things:

1. **Could you build a competitive bot without Grok?**
2. **Does the architecture around the model matter more than the model itself?**

To answer them I ran 332 held-out US-political X posts through every major frontier model, plus our full bot pipeline. Here's what came back.

## The cross-vendor leaderboard

Each model wrote a Community Note for each of 332 test posts, zero-shot. Claude Opus 4.7 scored every result against the actual rated-helpful note that X users approved for that post. Labels were anonymized so the judge didn't know which model wrote which note.

| Model | Vendor | Factual | Style | Neutrality | Pred-helpful | Wins |
|---|---|---|---|---|---|---|
| **Claude Opus 4.7** | Anthropic | 2.94 | 3.35 | 4.17 | **2.44** | 20% |
| **GPT-5** | OpenAI | 2.82 | 3.54 | 4.17 | **2.40** | 19% |
| Grok-4-fast | xAI | 2.90 | 3.49 | 4.36 | 2.23 | 12% |
| Grok-4 | xAI | 2.85 | 3.47 | 4.38 | 2.20 | 11% |
| Claude Sonnet 4.6 | Anthropic | 2.65 | 2.87 | 3.86 | 2.01 | 8% |
| Gemini 2.5 Pro | Google | 2.21 | 2.98 | 3.60 | 1.92 | 15% |
| Gemini 2.5 Flash | Google | 2.14 | 3.24 | 4.22 | 1.76 | 6% |
| GPT-5-mini | OpenAI | 2.14 | 2.87 | 3.79 | 1.61 | 6% |
| Claude Haiku 4.5 | Anthropic | 2.05 | 2.25 | 3.75 | 1.37 | 2% |

Scores on 0–5. Pred-helpful is the headline column — the judge's estimate of whether real X raters would mark the note helpful. Wins is how often the judge picked that model's note as the single best of all nine candidates.

## Three things I didn't expect

**Opus 4.7 and GPT-5 are statistically tied at the top.** 2.44 vs 2.40 predicted-helpfulness, 20% vs 19% wins. Choosing between the two frontier giants here is a question of cost and availability, not quality.

**Grok-4-fast beats Grok-4.** The cheap xAI variant outperforms the full frontier model on every axis. The likely explanation: Grok-4's heavier reasoning produces wordier, less Community-Notes-shaped output. This is probably why the eight bots Alexios identified all use Grok-4-fast — it's the right xAI model for this task, not a downgrade.

**Gemini 2.5 Pro underperforms Sonnet 4.6.** Google's frontier model lands below Anthropic's mid-tier. On this specific task — terse factual correction with a citation — model size and training cost don't predict performance.

## Architecture beats model choice

The leaderboard above is zero-shot. No retrieval, no validators, just the model writing a note from a tweet. That's a fair comparison for "which model is best at note-writing," but it's not how a working bot operates. A working bot retrieves evidence, picks the best match, checks for opinion language, validates URLs, and decides whether to ship at all.

I ran 332 test tweets through the full @alexcnotes pipeline with Claude Opus 4.7 as the writer. Six tweets passed all the filters and got notes; 326 were refused — most appropriately, for being off-beat. The six that shipped scored like this against the same judge:

| Setup | Factual | Style | Neutrality | Pred-helpful | n |
|---|---|---|---|---|---|
| **Pipeline + Opus 4.7** | **3.50** | **4.17** | **4.33** | **2.83** | 6 |
| Opus 4.7 zero-shot | 2.94 | 3.35 | 4.17 | 2.44 | 332 |
| GPT-5 zero-shot | 2.82 | 3.54 | 4.17 | 2.40 | 332 |

The pipeline pulls Opus from 2.44 to 2.83 — a bigger lift than the gap between any two frontier models on the leaderboard. **The architecture is doing more work than the model.**

That's the finding I want journalists and newsrooms to take away. If you're shopping for an AI fact-checking system, the model brand on the box matters less than the retrieval, validation and refusal logic around it.

## What about a fine-tuned model?

I also fine-tuned a Qwen 2.5 7B model on 3,237 real helpful Community Notes — the kind of "small, cheap, specialized" approach a newsroom might try if it didn't want to pay frontier prices forever. Then I dropped it into the pipeline as the writer.

| Setup | Factual | Pred-helpful | n |
|---|---|---|---|
| Pipeline + Opus 4.7 | 3.50 | 2.83 | 6 |
| Pipeline + Qwen 7B (fine-tuned) | 1.00 | 0.55 | 11 |
| Qwen 7B (fine-tuned, no pipeline) | 0.82 | 0.47 | 331 |

The fine-tuned model learned the *style* of helpful notes — direct correction, ends with a URL — but never learned to ground claims in evidence. When the pipeline handed it retrieved evidence at inference time, it partly copied and partly confabulated. The architecture couldn't rescue the model.

The cost savings of the small model are real (~$0.002 per note vs ~$0.05 for Opus) but meaningless when the notes are wrong. To make a fine-tuned model competitive, you'd need a substantially larger base model (70B+), evidence-aware training data, and meaningful iteration. That's a research project, not a weekend.

## How much does each setup cost per helpful note?

This is the operational question a newsroom actually cares about. Using the predicted-helpfulness scores as a proxy for "what fraction of shipped notes would be rated helpful":

| Setup | $ / attempt | Ship rate | $ / published | $ / helpful |
|---|---|---|---|---|
| Pipeline + Opus 4.7 (ours, measured) | ~$0.08 | 1.8% | ~$4.40 | **~$8** |
| MIT's pipeline (estimated) | ~$0.02 | ~2–5% | ~$0.40–1.00 | **~$1–2** |
| Grok-only bots (estimated) | ~$0.005 | ~10–30% | ~$0.02–0.05 | **~$0.05–0.50** |

The estimates for MIT's pipeline and Grok-only bots are extrapolations from their published open-source architectures. The numbers for our setup are measured.

There's a real economic trade-off here. Our pipeline produces the highest-quality output and the cleanest paper trail. But it's 5–10× more expensive per helpful note than a leaner Grok-based pipeline. If you're trying to write 50,000 notes a month, that gap matters. If you're trying to write 50 high-trust notes a month, the gap is rounding error.

## What the bot can do that the leaderboard can't show

A zero-shot leaderboard rewards models for sounding right. It doesn't measure:

- Whether the citation URL exists
- Whether the cited source actually supports the claim being made
- Whether the bot would refuse a post that's vague, satirical, or opinion-coded
- Whether the bot would survive a real-world misclassification

Those are the things the architecture handles, and they're the things X raters punish notes for getting wrong. The dashboard's [funnel](./) and [refusals](./) show those checks in operation. The leaderboard shows what the model brings; the dashboard shows what the bot does with it.

## Methodology in brief

- **Dataset.** Public Community Notes data, 2026/05/27 snapshot from `ton.twimg.com`. Filtered to US political content, `MISINFORMED_OR_POTENTIALLY_MISLEADING` classification, last 12 months, `CURRENTLY_RATED_HELPFUL` only. 4,948 records.
- **Test set.** Deterministic 90/10 split sorted by note_id. 332 helpful notes held out for evaluation.
- **Zero-shot protocol.** Same prompt to all 9 models. Frontier models (Opus, GPT-5, Gemini Pro, Grok-4) allowed thinking. Cheap models (Sonnet, Haiku, GPT-5-mini, Gemini Flash, Grok-4-fast) configured for minimal reasoning. Mirrors each vendor's default tier behavior.
- **Judge.** Claude Opus 4.7 scored all 9 candidates per tweet in one call, candidates labeled A–I and randomly ordered per tweet to mitigate position and self-preference bias.
- **Axes.** Factual accuracy, style match, opinion neutrality, predicted helpfulness — all 0–5.

A full methodology defense, including known limitations and what a fairer evaluation would do, will appear in the paper.

Source code: [github.com/AlexMahadevan/cn-bot](https://github.com/AlexMahadevan/cn-bot). Replication scripts and raw scores are in the repo.
