---
title: What X surfaces to bots
---

# What X surfaces to AI Note Writers

```js
const pool = await FileAttachment("./data/candidate_pool.json").json();
const s = pool.summary;
```

When the Kind Raspberry Chickadee bot asks X for posts it can write notes on, it hits the [`/2/notes/search/posts_eligible_for_notes`](https://docs.x.com/x-api/community-notes/search-for-posts-eligible-for-community-notes) endpoint. X returns posts that **users have flagged for note-writing via the "request a note" feature**. Not algorithmically selected. User-driven.

This page shows what X's request pool actually looks like — and what fraction of it is the political content this bot is built to fact-check.

<div class="grid grid-cols-3" style="margin-top: 2rem;">
  <div class="card">
    <h2>Total candidates seen</h2>
    <span class="big">${s ? pool.total.toLocaleString() : "—"}</span>
    <p>Unique posts X has surfaced to the bot via the eligible-posts endpoint.</p>
  </div>
  <div class="card">
    <h2>% political (by topic)</h2>
    <span class="big">${s ? s.politics_share_category.toFixed(1) + "%" : "—"}</span>
    <p>US politics + foreign politics combined.</p>
  </div>
  <div class="card">
    <h2>% US politics (the bot's beat)</h2>
    <span class="big">${s ? s.us_politics_share.toFixed(1) + "%" : "—"}</span>
    <p>Ship rate within US political slice: ${s ? s.us_politics_ship_rate.toFixed(1) + "%" : "—"}.</p>
  </div>
</div>

## What X actually surfaces

```js
Plot.plot({
  marginLeft: 160,
  height: 320,
  x: { label: "% of all candidates", grid: true, domain: [0, 30] },
  marks: [
    Plot.barX(pool.categories, {
      x: "share_pct",
      y: "label",
      sort: { y: "x", reverse: true },
      fill: d => d.category === "us_politics" ? "#2563eb" : d.category === "foreign_politics" ? "#60a5fa" : "#94a3b8",
    }),
    Plot.text(pool.categories, {
      x: "share_pct",
      y: "label",
      text: d => `${d.share_pct}% (${d.count})`,
      dx: 6,
      textAnchor: "start",
      fill: "currentColor",
    }),
    Plot.ruleX([0]),
  ],
})
```

Blue bars are political (the bot's potential beat). Grey is everything else.

Three things to notice:

- **Almost a quarter of what X surfaces (23.3%) is just snark, vague reactions, or emotional posts with no factual claim at all.** A bot can't fact-check "I smell a rat" no matter how good its model is.
- **US politics is the largest single category at 27.3%.** That sounds high, but most of it is opinion or commentary, not falsifiable claims — which is why the bot's ship rate on this slice is only ${s ? s.us_politics_ship_rate.toFixed(1) + "%" : ""}.
- **Foreign politics is 14.6%.** The bot ignores those by design (its beat is US), but they're a meaningful chunk of the global "request a note" demand signal.

## Ship rate by category

```js
Plot.plot({
  marginLeft: 160,
  height: 320,
  x: { label: "Bot ship rate %", grid: true },
  marks: [
    Plot.barX(pool.categories.filter(d => d.count >= 30), {
      x: "ship_rate_pct",
      y: "label",
      sort: { y: "x", reverse: true },
      fill: d => d.category === "us_politics" ? "#16a34a" : "#94a3b8",
    }),
    Plot.text(pool.categories.filter(d => d.count >= 30), {
      x: "ship_rate_pct",
      y: "label",
      text: d => `${d.ship_rate_pct}% (${d.shipped}/${d.count})`,
      dx: 6,
      textAnchor: "start",
      fill: "currentColor",
    }),
    Plot.ruleX([0]),
  ],
})
```

Only categories with at least 30 candidates shown. The bot's relevance filter is doing what it should — non-political categories ship at near-zero rates, because they get dropped before the writer step.

## How this compares to the academic baseline

The [CHI 2026 paper *"Request a Note: How the Request Function Shapes X's Community Notes System"*](https://arxiv.org/html/2509.09956v1) studied 98,685 requested posts and found the topic split below. The paper allowed multi-topic classification (posts could count in multiple buckets), so percentages sum to >100. Our classification picks one dominant topic per post, so they don't.

| Topic | Academic paper (n=98,685) | This bot (n=${s ? pool.total : "—"}) |
|---|---|---|
| Politics | 37% | **${s ? s.politics_share_claim_flag.toFixed(1) + "%" : "—"}** (claim-bearing) / ${s ? s.politics_share_category.toFixed(1) + "%" : "—"} (category) |
| Finance / business | 32.6% | ${s ? pool.categories.find(c => c.category === "finance_business")?.share_pct + "%" : "—"} |
| Entertainment | 26.9% | ${s ? pool.categories.find(c => c.category === "entertainment")?.share_pct + "%" : "—"} |
| Science / tech | 13.5% | ${s ? (pool.categories.find(c => c.category === "science_health")?.share_pct + pool.categories.find(c => c.category === "gaming_tech")?.share_pct).toFixed(1) + "%" : "—"} |

**The political share replicates almost exactly.** The other categories diverge — the paper's multi-topic counts inflate non-political categories; our single-topic classifier doesn't. But the structural finding holds: **roughly one-in-three to one-in-four requested posts is political.**

The same paper found something the dashboard wants to surface:

> **"Posts tagged as political content showed 28.4% lower odds of receiving community notes compared to non-political posts."**

That's a finding about *all writers*, not just this bot — and it explains a lot. The Community Notes system is structurally biased against the topic this bot is built for. Low submission counts aren't a bot failure; they're the realistic output of fact-checking the slice of X's request pool that's actively *harder* to get notes shipped on.

## Methodology

- **Source:** every unique post X has surfaced to the bot via `/2/notes/search/posts_eligible_for_notes`, deduplicated by post ID. As of this snapshot, n = ${s ? pool.total : "—"}.
- **Classifier:** Claude Haiku 4.5 with a structured-output prompt categorizing each post into one of nine topics (US politics, foreign politics, finance/business, entertainment, sports, gaming/tech, science/health, personal/lifestyle, other). One dominant category per post.
- **Ship rate:** computed from the bot's local audit log — what fraction of candidates in each category became submitted notes.
- **Refresh cadence:** the script is idempotent and re-runs on new candidates whenever the dashboard data is refreshed.

Replication script: [`scripts/classify_candidate_pool.py`](https://github.com/AlexMahadevan/cn-bot/blob/main/scripts/classify_candidate_pool.py). Raw classifications: `data/candidate_pool_analysis.jsonl` in the repo.

<style>
  .grid-cols-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
  .card {
    padding: 1rem 1.25rem;
    border-radius: 8px;
    background: color-mix(in srgb, var(--theme-foreground) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--theme-foreground) 10%, transparent);
    color: var(--theme-foreground);
  }
  .card h2 {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--theme-foreground-muted);
    margin: 0 0 0.5rem 0;
    font-weight: 600;
  }
  .card .big {
    font-size: 2.5rem;
    font-weight: 700;
    display: block;
    line-height: 1.1;
    color: var(--theme-foreground);
  }
  .card p {
    font-size: 0.85rem;
    color: var(--theme-foreground-muted);
    margin-top: 0.5rem;
  }
</style>
