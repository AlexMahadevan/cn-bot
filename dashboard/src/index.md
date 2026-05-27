---
title: Watching a bot fact-check
toc: false
---

# Watching a bot fact-check

```js
const bot = FileAttachment("./data/bot.json").json();
```

This page shows what an AI Community Notes writer does in public, every day, in detail. The bot is **[@alexcnotes](https://x.com/alexcnotes)** — an AI Note Writer enrolled in X's program. It reads posts X surfaces as candidates for community notes, looks for fact-checks of the claims those posts make, and submits notes when it finds a clean match. It declines most of the time.

I built it. I run it. And I publish every step it takes here so other journalists, researchers, and the public can see how this kind of system actually behaves — not how it's described in a pitch deck.

```js
const t = bot.totals;
```

<div class="grid grid-cols-4" style="margin-top: 2rem;">
  <div class="card">
    <h2>Posts seen</h2>
    <span class="big">${t.posts.toLocaleString()}</span>
    <p>Pulled from X's eligible-posts endpoint.</p>
  </div>
  <div class="card">
    <h2>On-beat</h2>
    <span class="big">${(t.posts - t.off_beat).toLocaleString()}</span>
    <p>About US politicians, federal policy, or election integrity.</p>
  </div>
  <div class="card">
    <h2>Notes written</h2>
    <span class="big">${t.notes_written.toLocaleString()}</span>
    <p>Drafted and validated against the source.</p>
  </div>
  <div class="card">
    <h2>Submitted to X</h2>
    <span class="big">${t.submitted.toLocaleString()}</span>
    <p>In <code>test_mode</code> until the account earns in.</p>
  </div>
</div>

<div class="note">Data refreshes every time the bot runs (currently every two hours). Last update: <code>${bot.generated_at}</code>.</div>

---

## The funnel

Where do posts drop out? Most never get past the first filter. That's by design — the bot's beat is narrow.

```js
Plot.plot({
  marginLeft: 220,
  height: 240,
  x: { label: "Posts", grid: true },
  marks: [
    Plot.barX(bot.funnel, {
      x: "count",
      y: "label",
      sort: { y: null },
      fill: "#2563eb",
    }),
    Plot.text(bot.funnel, {
      x: "count",
      y: "label",
      text: (d) => d.count.toLocaleString(),
      dx: 8,
      textAnchor: "start",
      fill: "currentColor",
    }),
    Plot.ruleX([0]),
  ],
})
```

Step by step, what each stage means:

- **Eligible posts seen.** Whatever X returns when the bot asks for posts it could note. Sports, gaming, celebrity drama, foreign politics — anything.
- **On-beat.** A cheap Claude Haiku call decides whether the post is about US politicians or US political misinformation. Strict by default.
- **Evidence found.** For on-beat posts, the bot searches PolitiFact, the Google Fact Check Tools API, and (if needed) the broader web. A post passes this stage if at least one fact-check or primary source comes back.
- **Note drafted.** A Claude Opus call writes the note prose. The bot only writes when the evidence directly addresses the post's claim. When it doesn't, the bot returns `NO_NOTE`.
- **Submitted to X.** Notes that pass length, URL, and `evaluate_note` pre-flight checks go to X. Still in `test_mode` — X requires it during the AI Note Writer pilot.

---

## Why the bot declines

The bot says no a lot. Here's why, with counts:

```js
Plot.plot({
  marginLeft: 280,
  height: Math.max(180, bot.refusal_buckets.length * 36),
  x: { label: "Refusals", grid: true },
  marks: [
    Plot.barX(bot.refusal_buckets, {
      x: "count",
      y: "bucket",
      sort: { y: "x", reverse: true },
      fill: "#dc2626",
    }),
    Plot.text(bot.refusal_buckets, {
      x: "count",
      y: "bucket",
      text: (d) => d.count.toLocaleString(),
      dx: 8,
      textAnchor: "start",
      fill: "currentColor",
    }),
    Plot.ruleX([0]),
  ],
})
```

The biggest two buckets are by design:

1. **Off-beat.** Most of what X surfaces isn't about US politics. The bot drops it before spending an Opus token on it.
2. **Picker: candidate doesn't match claim.** The bot found a fact-check on a related topic, but it doesn't directly rate *this* post's claim. Rather than stretch, the bot returns nothing.

The other two are safety rails:

3. **Opus declined to write.** The model was given evidence and decided the case wasn't airtight. This is what you want a fact-checking bot to do when in doubt.
4. **URL validator rejected.** A safety check catches notes where Claude tried to write a URL itself. The bot only ever cites URLs that came back from a real search result; if the prose Claude produced contains any `http`, `https`, or domain text, the note is dropped. This is the structural fix for a hallucination problem an earlier version of the bot had.

---

## Every note the bot has submitted

```js
const notes = bot.notes;
```

${notes.length === 0
  ? html`<div class="warning">The bot hasn't submitted any notes yet. Notes will appear here as they're written. The bot is intentionally conservative — when in doubt, it returns no note rather than a wrong one.</div>`
  : html`<div class="note">Every entry below shows the post the bot saw, the note it wrote, and the source it cited. Click through to verify any citation yourself.</div>`}

${html`<div>${notes.map(noteCard)}</div>`}

```js
function noteCard(n) {
  const tagBadges = (n.misleading_tags || []).map(
    t => html`<span class="tag">${t.replace(/_/g, " ")}</span> `
  );
  return html`
  <div class="card note-card">
    <div class="meta">
      <span>${new Date(n.created_at).toLocaleString()}</span>
      <span class="tier-${n.evidence_tier}">${(n.evidence_tier || "").replace(/_/g, " ")}</span>
    </div>
    <div class="post-text">${n.post_text || ""}</div>
    <div class="note-text">
      <strong>Note:</strong> ${n.note_text || ""}
    </div>
    <div class="cite">
      Cites <strong>${n.evidence_publisher || ""}</strong>${n.evidence_rating ? html` rating <em>${n.evidence_rating}</em>` : ""}.
      <a href="${n.evidence_url}" target="_blank" rel="noopener">View source</a>
      · <a href="${n.post_url}" target="_blank" rel="noopener">View post on X</a>
    </div>
    <div class="tags">${tagBadges}</div>
  </div>`;
}
```

---

## Recent refusals

For transparency, here are the most recent posts the bot looked at and decided not to note. Each one is a small judgment call.

```js
const refusals = bot.recent_refusals;
```

${refusals.length === 0
  ? html`<div class="note">No refusals to show yet.</div>`
  : html`<div>${refusals.map(refusalCard)}</div>`}

```js
function refusalCard(r) {
  return html`
  <div class="card refusal-card">
    <div class="meta">
      <span>${new Date(r.created_at).toLocaleString()}</span>
      <span class="bucket-badge">${r.refusal_bucket}</span>
    </div>
    <div class="post-text">${r.post_text || ""}</div>
    <div class="reason"><strong>Why declined:</strong> ${r.refusal_reason || ""}</div>
    ${r.evidence_publisher ? html`<div class="cite">Evidence considered: ${r.evidence_publisher}</div>` : ""}
    <div><a href="${r.post_url}" target="_blank" rel="noopener">View post on X</a></div>
  </div>`;
}
```

---

## Daily activity

```js
Plot.plot({
  height: 280,
  x: { label: "Date", type: "band" },
  y: { label: "Posts", grid: true },
  color: { legend: true, domain: ["off_beat", "refused", "submitted"], range: ["#94a3b8", "#dc2626", "#16a34a"] },
  marks: [
    Plot.barY(
      bot.by_day.flatMap(d => [
        { day: d.day, kind: "off_beat", count: d.posts - d.submitted - d.refused - d.errors > 0 ? 0 : 0 },
        { day: d.day, kind: "refused", count: d.refused },
        { day: d.day, kind: "submitted", count: d.submitted },
      ]),
      { x: "day", y: "count", fill: "kind", sort: { x: null } }
    ),
    Plot.ruleY([0]),
  ],
})
```

<style>
  /* Cards: subtle bg that works in both light and dark mode */
  .card {
    padding: 1rem 1.25rem;
    border-radius: 8px;
    background: color-mix(in srgb, var(--theme-foreground) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--theme-foreground) 10%, transparent);
    margin: 0.5rem 0;
    color: var(--theme-foreground);
  }
  /* Headline metric cards */
  .grid-cols-4 .card h2 {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--theme-foreground-muted);
    margin: 0 0 0.5rem 0;
    font-weight: 600;
  }
  .grid-cols-4 .card .big {
    font-size: 2.5rem;
    font-weight: 700;
    display: block;
    line-height: 1.1;
    color: var(--theme-foreground);
  }
  .grid-cols-4 .card p {
    font-size: 0.85rem;
    color: var(--theme-foreground-muted);
    margin-top: 0.5rem;
  }

  /* Callout boxes */
  .note, .warning {
    padding: 0.75rem 1rem;
    border-radius: 4px;
    margin: 1rem 0;
    font-size: 0.9rem;
    color: var(--theme-foreground);
  }
  .note { background: color-mix(in srgb, #2563eb 10%, transparent); border-left: 3px solid #2563eb; }
  .warning { background: color-mix(in srgb, #eab308 12%, transparent); border-left: 3px solid #eab308; }

  /* Note and refusal cards */
  .note-card, .refusal-card { margin: 1rem 0; color: var(--theme-foreground); }
  .note-card .meta, .refusal-card .meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.85rem;
    color: var(--theme-foreground-muted);
    margin-bottom: 0.5rem;
  }
  .post-text {
    font-style: italic;
    margin: 0.5rem 0;
    padding-left: 0.75rem;
    border-left: 3px solid color-mix(in srgb, var(--theme-foreground) 25%, transparent);
    color: var(--theme-foreground);
  }
  .note-text { margin: 0.75rem 0; color: var(--theme-foreground); }
  .cite { font-size: 0.85rem; color: var(--theme-foreground-muted); margin-top: 0.5rem; }
  .reason { font-size: 0.9rem; margin: 0.5rem 0; color: var(--theme-foreground); }

  /* Tags and badges */
  .tag {
    display: inline-block;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    background: color-mix(in srgb, #dc2626 15%, transparent);
    color: #ef4444;
    font-size: 0.75rem;
    margin-right: 0.25rem;
    font-weight: 500;
  }
  .bucket-badge {
    padding: 0.15rem 0.55rem;
    border-radius: 4px;
    background: color-mix(in srgb, #dc2626 15%, transparent);
    color: #ef4444;
    font-size: 0.75rem;
    font-weight: 500;
  }
  .tier-ifcn_verified { color: #22c55e; font-size: 0.75rem; font-weight: 600; }
  .tier-self_fact_check { color: #eab308; font-size: 0.75rem; font-weight: 600; }
  .tier-primary_source { color: #3b82f6; font-size: 0.75rem; font-weight: 600; }
</style>
