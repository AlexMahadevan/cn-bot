---
title: The prompts
---

# The exact prompts the bot runs

Most AI Community Notes writers are black boxes. This one isn't. Below are the **actual system prompts** that drive every decision Kind Raspberry Chickadee makes — pulled straight from the source code at build time, so what you read here is exactly what the bot ran, not a cleaned-up paraphrase.

A note travels through five model-driven gates. Four of them can *stop* a note; only one writes anything. Between the specificity gate and the writer, the bot also searches PolitiFact, the Google Fact Check Tools API, IFCN signatories, and linked X posts, then asks the model to pick the single best-matching fact check (that picker's prompt is assembled per-post from the candidate list, so it isn't shown verbatim here).

The one rule that ties it together: **the writer never produces a URL.** It writes only words; code attaches the verified fact-check link. Any draft whose prose contains a URL is thrown out. That's the anti-hallucination invariant — Claude writes the claim, code writes the citation.

```js
const prompts = await FileAttachment("./data/prompts.json").json();
```

```js
for (const g of prompts) {
  display(html`<div class="card" style="margin: 1.25rem 0;">
    <h3 style="margin-top: 0;">${g.gate}</h3>
    <p style="margin-bottom: 0.5rem;">${g.summary}</p>
    <details>
      <summary style="cursor: pointer; color: var(--theme-foreground-muted);">
        Show the exact prompt &mdash; <code>src/note_writer/${g.file}</code>
      </summary>
      <pre style="white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.5; background: var(--theme-background-alt, #f7f7f7); padding: 1rem; border-radius: 8px; margin-top: 0.75rem;">${g.prompt}</pre>
    </details>
  </div>`);
}
```

These prompts are regenerated from the source on every dashboard refresh, so they can't fall out of sync with the running bot. The full pipeline — evidence search, the best-evidence picker, the URL validator — lives in the open-source repo: [github.com/AlexMahadevan/cn-bot](https://github.com/AlexMahadevan/cn-bot).
