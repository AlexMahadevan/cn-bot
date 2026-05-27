# Baseline LLM evaluation report

Built by `scripts/score_baselines.py`. Each held-out test tweet was given to 3 Claude models with a generic "write a CN note for this post" prompt (no evidence, no exemplars, no validators). The generated note was then compared to the actual CRH note for that tweet by Opus 4.7 acting as judge, with model labels anonymized as A/B/C in the prompt.

## Score table

Scale: 0-5 on each axis. Wins = best-candidate-picks in head-to-head.

| Model       | Factual | Style | Neutrality | Predicted helpful | Wins |
|-------------|---------|-------|------------|-------------------|------|
| Haiku 4.5   | 1.91    | 2.10  | 3.55       | 1.31              | 11%  |
| Sonnet 4.6  | 2.45    | 2.80  | 3.70       | 1.94              | 29%  |
| Opus 4.7    | 2.80    | 3.31  | 4.11       | 2.39              | 61%  |

## Test set

- 332 held-out tweets (10% holdout from 4,948-record dataset)
- All test tweets had a known CRH note as gold standard
- Source: public X Community Notes data, 2026/05/27 snapshot
- Filter: US political content, MISINFORMED_OR_POTENTIALLY_MISLEADING classification, last 12 months

## Interpretation

Zero-shot LLM generation, even with frontier models and a calibrated style hint in the system prompt, produces notes that score poorly against real CRH notes. The predicted-helpfulness axis is the most relevant for our purposes — even Opus 4.7's notes would score 2.39/5 on rated-helpful likelihood, meaning a CN rater shown one of these notes alongside the gold-standard CRH note would be unlikely to prefer Opus's output.

This is the bar a fine-tuned model must clear, and it suggests three legitimate avenues for improvement:

1. **Fine-tuning on the CRH corpus** — direct style + task adaptation.
2. **Evidence retrieval** — supplying the model with the article that would let it ground the claim (this is what the bot does in production).
3. **Validator stack** — opinion filter + hallucination check + URL validator (this is what the bot does in production).

The paper's quantitative contribution is to measure (1), (2), and (3) separately and show their additive value.

## Reproducibility

- Generation: `scripts/evaluate_baselines.py --limit 500`
- Scoring: `scripts/score_baselines.py --concurrency 10`
- Output: `data/baselines.jsonl` + `data/baselines_scored.jsonl`
