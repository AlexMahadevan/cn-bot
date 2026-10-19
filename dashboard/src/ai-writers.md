---
title: AI writers across X
---

# Who is writing Community Notes now?

```js
const d = await FileAttachment("./data/ai_writers.json").json();
const s30 = d.shares["30d"];
const s90 = d.shares["90d"];
const c = d.concentration;
const fmtPct = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
const fmtInt = (v) => (v == null ? "—" : v.toLocaleString());
```

This page steps back from one bot and looks at all of them. X publishes its full Community Notes dataset every day, and that data says which contributors are AI Note Writers: their enrollment state is recorded as `apiEarnedIn`. Join that to every note and its current rating status and you can watch the balance between human and machine note-writing shift week by week.

Three kinds of authors appear below. **AI writers** are accounts admitted through X's AI Note Writer API. **Collaborative notes** are drafted by an AI when someone requests a note and then refined by human contributors, a format X introduced in February 2026. **Human writers** are everyone else. Notes on this page are counted on the day they were written, using data X says is complete as of <code>${d.as_of}</code>.

<div class="grid grid-cols-4" style="margin-top: 1.5rem;">
  <div class="card">
    <h2>AI writers admitted</h2>
    <span class="big">${fmtInt(d.enrollment.api_earned_in)}</span>
    <p>Accounts with <code>apiEarnedIn</code> status. ${fmtInt(c.writers_active_7d)} wrote a note in the past week.</p>
  </div>
  <div class="card">
    <h2>AI share of notes</h2>
    <span class="big">${fmtPct((s30.ai.share_notes ?? 0) + (s30.collab.share_notes ?? 0))}</span>
    <p>Of ${fmtInt(s30.total_notes)} notes written in the last 30 days, including collaborative notes.</p>
  </div>
  <div class="card">
    <h2>AI share of Helpful notes</h2>
    <span class="big">${fmtPct((s30.ai.share_crh ?? 0) + (s30.collab.share_crh ?? 0))}</span>
    <p>Of ${fmtInt(s30.total_crh)} notes rated Helpful in the same window.</p>
  </div>
  <div class="card">
    <h2>Six writers' share</h2>
    <span class="big">${fmtPct(c.top6_share_notes_30d)}</span>
    <p>The six most prolific AI writers' share of all AI notes in the last 30 days. They hold ${fmtPct(c.top6_share_crh_30d)} of AI Helpful notes.</p>
  </div>
</div>

<div class="note">X's dump dated <code>${d.dump_date}</code>, rebuilt locally once a day. Deleted notes are included in the counts because the status-history file keeps them. The bot this site follows, Kind Raspberry Chickadee, does not appear in this data yet: its notes are still in test mode, and test notes are never published.</div>

```js
// Tidy the weekly rows for Plot, dropping the trailing partial week.
const TYPE_LABEL = { ai: "AI writers", collab: "Collaborative (AI + human)", human: "Human writers" };
const TYPE_ORDER = ["ai", "collab", "human"];
const COLOR = { domain: TYPE_ORDER.map((t) => TYPE_LABEL[t]), range: ["#2a78d6", "#eb6834", "#898781"] };
const asOf = new Date(d.as_of + "T00:00:00Z");
const weeks = d.weekly.filter((w) => {
  const start = new Date(w.period + "T00:00:00Z");
  return start.getTime() + 6 * 86400e3 <= asOf.getTime();
});
const weeklyLong = weeks.flatMap((w) =>
  TYPE_ORDER.map((t) => ({
    week: new Date(w.period + "T00:00:00Z"),
    type: TYPE_LABEL[t],
    notes: w[`notes_${t}`],
    helpful: w[`crh_${t}`],
    helpful_rate: w[`crh_rate_${t}`],
    mature: w.mature,
  }))
);
const shareLong = weeks.flatMap((w) => [
  { week: new Date(w.period + "T00:00:00Z"), measure: "Share of all notes written", pct: w.ai_share_notes },
  { week: new Date(w.period + "T00:00:00Z"), measure: "Share of notes rated Helpful", pct: w.ai_share_crh },
]);
```

## Notes written each week, by who wrote them

Each column is one week of note-writing on X since the AI Note Writer pilot opened in July 2025. Human writers are shown in gray because the question on this page is how much of the stack the machines now fill.

```js
Plot.plot({
  height: 340,
  marginLeft: 56,
  x: { label: null, type: "utc" },
  y: { label: "Notes written per week", grid: true, tickFormat: "~s" },
  color: { ...COLOR, legend: true },
  marks: [
    Plot.rectY(weeklyLong, Plot.stackY({
      x: "week", y: "notes", fill: "type", order: COLOR.domain,
      interval: "week", insetLeft: 1, insetRight: 1, stroke: "white", strokeWidth: 1,
      tip: true, title: (r) => `${r.type}\n${r.notes.toLocaleString()} notes`,
    })),
    Plot.ruleY([0]),
  ],
})
```

## The share of notes that come from AI

The gap between the two lines is the story. AI writers submit a large share of all notes, and a different share of the notes that actually clear the rating process and show on posts. The second line counts only notes currently rated Helpful, so the most recent weeks will keep moving as ratings arrive.

```js
Plot.plot({
  height: 300,
  marginLeft: 48,
  marginRight: 120,
  x: { label: null, type: "utc" },
  y: { label: "Percent of all notes", grid: true, domain: [0, 100] },
  color: { domain: ["Share of all notes written", "Share of notes rated Helpful"], range: ["#898781", "#2a78d6"], legend: true },
  marks: [
    Plot.lineY(shareLong, { x: "week", y: "pct", stroke: "measure", strokeWidth: 2, curve: "monotone-x", tip: true,
      title: (r) => `${r.measure}\n${r.pct == null ? "—" : r.pct.toFixed(1) + "%"}` }),
    Plot.text(
      ["Share of all notes written", "Share of notes rated Helpful"].map((m) => shareLong.filter((r) => r.measure === m && r.pct != null).at(-1)).filter(Boolean),
      { x: "week", y: "pct", text: (r) => `${r.pct.toFixed(0)}%`, dx: 8, textAnchor: "start", fill: "currentColor" }
    ),
    Plot.ruleY([0]),
  ],
})
```

## How often each kind of note is rated Helpful

Helpful rates are only shown for weeks whose notes have had at least two weeks to collect ratings, and only where a group wrote at least 20 notes that week. Most notes of every kind never reach a final status at all; the long-run figure across Community Notes is close to nine in ten stuck at "Needs more ratings."

```js
Plot.plot({
  height: 300,
  marginLeft: 48,
  x: { label: null, type: "utc" },
  y: { label: "Percent of that week's notes now rated Helpful", grid: true },
  color: { ...COLOR, legend: true },
  marks: [
    Plot.lineY(weeklyLong.filter((r) => r.helpful_rate != null), {
      x: "week", y: "helpful_rate", stroke: "type", strokeWidth: 2, curve: "monotone-x", tip: true,
      title: (r) => `${r.type}\n${r.helpful_rate.toFixed(1)}% Helpful`,
    }),
    Plot.ruleY([0]),
  ],
})
```

<div class="grid grid-cols-3" style="margin-top: 1rem;">
  <div class="card">
    <h2>AI writers, 90 days</h2>
    <span class="big">${fmtPct(s90.ai.crh_rate)}</span>
    <p>of ${fmtInt(s90.ai.notes)} notes rated Helpful so far. ${fmtInt(s90.ai.deleted)} were deleted by their writers.</p>
  </div>
  <div class="card">
    <h2>Collaborative, 90 days</h2>
    <span class="big">${fmtPct(s90.collab.crh_rate)}</span>
    <p>of ${fmtInt(s90.collab.notes)} notes rated Helpful so far.</p>
  </div>
  <div class="card">
    <h2>Human writers, 90 days</h2>
    <span class="big">${fmtPct(s90.human.crh_rate)}</span>
    <p>of ${fmtInt(s90.human.notes)} notes rated Helpful so far.</p>
  </div>
</div>

## The writers

Every AI writer is pseudonymous in the public data, so the table shows the first ten characters of each participant ID. Rows are sorted by notes written in the last 30 days. The rate columns use only notes old enough to have been rated, and a dash means the writer has not written 20 such notes. The deletion column counts notes that appear in X's status history but no longer appear in the notes file, which is how a writer withdraws a note before it is rated Not Helpful.

X disclosed in October 2026 that it runs six AI writers itself, under the names celebrated-ocean-seagull, cheery-nutmeg-towhee, great-mountain-brushturkey, zappy-winter-thicketbird, neighborly-rhubarb-chickadee and diligent-brook-thrasher. The public data does not include those aliases, so this table cannot label them. Renée DiResta and Alexios Mantzarlis [reported](https://indicator.media/p/briefing-x-handed-fact-checking-to-the-crowd-then-it-became-the-crowd) that the six X-run accounts wrote more than 90 percent of AI notes in September 2026, which lines up with the concentration at the top of this table.

```js
Inputs.table(d.writers, {
  columns: ["id", "notes_30d", "crh_30d", "crnh_30d", "deleted_30d", "crh_rate_mature", "hit_rate_mature", "deletion_rate_mature", "notes_total", "first_day", "active_7d"],
  header: {
    id: "Writer", notes_30d: "Notes, 30d", crh_30d: "Helpful, 30d", crnh_30d: "Not Helpful, 30d", deleted_30d: "Deleted, 30d",
    crh_rate_mature: "Helpful rate", hit_rate_mature: "Hit rate", deletion_rate_mature: "Deletion rate",
    notes_total: "Notes, all time", first_day: "First note", active_7d: "Active this week",
  },
  format: {
    crh_rate_mature: (v) => (v == null ? "—" : `${v}%`),
    hit_rate_mature: (v) => (v == null ? "—" : `${v}%`),
    deletion_rate_mature: (v) => (v == null ? "—" : `${v}%`),
    active_7d: (v) => (v ? "yes" : "no"),
  },
  sort: "notes_30d", reverse: true, rows: 20, select: false,
})
```

The hit rate is the number X uses to set each writer's daily quota: Helpful minus Not Helpful, divided by all notes that have been resolved or are still waiting on ratings. X's paper says a 10 percent hit rate buys about 50 notes a day and 20 percent buys the maximum of 500.

<details>
<summary>Weekly data table</summary>

```js
Inputs.table(weeks.map((w) => ({
  week: w.period, notes_ai: w.notes_ai, notes_collab: w.notes_collab, notes_human: w.notes_human,
  helpful_ai: w.crh_ai, helpful_collab: w.crh_collab, helpful_human: w.crh_human,
  ai_share_notes: w.ai_share_notes, ai_share_helpful: w.ai_share_crh,
})), { rows: 15, select: false })
```

</details>

## How this is built

The numbers come from three files in X's [public Community Notes data](https://x.com/i/communitynotes/download-data): the user enrollment file identifies AI writers, the note status history file lists every note with its current rating status and keeps deleted notes, and the notes file supplies the collaborative-note flag and tells us which notes still exist. A note counts as AI-written when its author is an `apiEarnedIn` account, as collaborative when X flags it so, and as human otherwise. A note counts as Helpful when its current status is `CURRENTLY_RATED_HELPFUL` on the day of the dump, so older weeks are more settled than recent ones. The builder script is `dashboard/tools/build_ai_writers_json.py` in the [source repository](https://github.com/AlexMahadevan/cn-bot). The approach follows Indicator's reporting on AI note writers and X's own paper on the AI Note Writer API ([arXiv:2609.40067](https://arxiv.org/abs/2609.40067)).
