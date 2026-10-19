#!/usr/bin/env python3
"""Build dashboard/src/data/ai_writers.json from X's public Community Notes data.

Tracks AI note writing across ALL of X (not just this bot), using the daily
public dump at https://x.com/i/communitynotes/download-data:

  userEnrollment     -> who is an AI writer: enrollmentState == "apiEarnedIn"
  noteStatusHistory  -> every note ever (incl. deleted), author, created time,
                        current status (CRH / CRNH / NMR)
  notes              -> note still exists (not deleted), isCollaborativeNote

Author type per note:  collab  if isCollaborativeNote == 1 (AI draft, human-refined)
                       ai      if the author is apiEarnedIn
                       human   otherwise

Streams the zipped TSVs over HTTP in chunks (never written to disk) — same
approach as community-notes/scripts/find_ai_crh_notes.py — and emits small
weekly/daily aggregates + a per-writer table. ~5 min. Run from the bot venv
(needs pandas):

    .venv/bin/python dashboard/tools/build_ai_writers_json.py [--max-age-hours 20]

With --max-age-hours it exits early if the existing snapshot is fresher.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve()
OUT = HERE.parent.parent / "src" / "data" / "ai_writers.json"
BASE = "https://ton.twimg.com/birdwatch-public-data"
CHUNK = 500_000
START = dt.datetime(2025, 7, 1, tzinfo=dt.timezone.utc)  # AI Note Writer API pilot launch
START_MS = int(START.timestamp() * 1000)
CRH, CRNH, NMR = "CURRENTLY_RATED_HELPFUL", "CURRENTLY_RATED_NOT_HELPFUL", "NEEDS_MORE_RATINGS"
TYPES = ("ai", "collab", "human")


def log(msg: str) -> None:
    print(f"[ai-writers] {msg}", file=sys.stderr, flush=True)


def find_date() -> dt.date:
    today = dt.date.today()
    for back in range(0, 6):
        d = today - dt.timedelta(days=back)
        url = f"{BASE}/{d:%Y/%m/%d}/userEnrollment/userEnrollment-00000.zip"
        try:
            pd.read_csv(url, sep="\t", nrows=5)
            log(f"using dump dated {d}")
            return d
        except Exception as e:
            log(f"{d}: {type(e).__name__}")
    sys.exit("No public data found in the last 6 days.")


def stream(date: dt.date, kind: str, prefix: str, usecols=None):
    for shard in range(0, 40):
        url = f"{BASE}/{date:%Y/%m/%d}/{kind}/{prefix}-{shard:05d}.zip"
        try:
            reader = pd.read_csv(url, sep="\t", chunksize=CHUNK, low_memory=False, usecols=usecols)
            first = next(reader)
        except Exception:
            if shard == 0:
                raise
            return
        log(f"{kind} shard {shard:05d}")
        yield first
        yield from reader


def week_start(day: dt.date) -> str:
    return (day - dt.timedelta(days=day.weekday())).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-hours", type=float, default=None)
    args = ap.parse_args()

    if args.max_age_hours is not None and OUT.exists():
        age_h = (time.time() - OUT.stat().st_mtime) / 3600
        if age_h < args.max_age_hours:
            log(f"snapshot is {age_h:.1f}h old (< {args.max_age_hours}h); skipping")
            return

    t0 = time.time()
    date = find_date()
    as_of = date - dt.timedelta(days=2)  # dumps include notes as of ~48h before release

    # 1. AI writers
    ai_ids: set[str] = set()
    enroll_counts: Counter = Counter()
    for chunk in stream(date, "userEnrollment", "userEnrollment", usecols=["participantId", "enrollmentState"]):
        enroll_counts.update(chunk["enrollmentState"].value_counts().to_dict())
        ai_ids.update(chunk.loc[chunk["enrollmentState"] == "apiEarnedIn", "participantId"].astype(str))
    log(f"{len(ai_ids)} apiEarnedIn writers; enrollment states: {dict(enroll_counts)}")
    if not ai_ids:
        sys.exit("No apiEarnedIn participants — schema changed?")

    # 2. Every note since the pilot launched (status history keeps deleted notes)
    cols = ["noteId", "noteAuthorParticipantId", "createdAtMillis", "currentStatus"]
    frames = []
    n_scanned = 0
    for chunk in stream(date, "noteStatusHistory", "noteStatusHistory", usecols=cols):
        n_scanned += len(chunk)
        m = chunk[chunk["createdAtMillis"] >= START_MS]
        if len(m):
            frames.append(m)
    sh = pd.concat(frames, ignore_index=True)
    sh["noteId"] = sh["noteId"].astype(str)
    sh["pid"] = sh["noteAuthorParticipantId"].astype(str)
    log(f"statusHistory: {n_scanned:,} rows scanned, {len(sh):,} notes since {START.date()}")

    # 3. Which of those still exist, and which are collaborative
    # The notes file's author column is `noteAuthorParticipantId` in practice
    # (the schema doc says `participantId`); accept either.
    cols = ["noteId", "participantId", "noteAuthorParticipantId", "createdAtMillis", "isCollaborativeNote"]
    present: dict[str, tuple[str, int]] = {}
    n_scanned = 0
    for chunk in stream(date, "notes", "notes", usecols=lambda c: c in cols):
        n_scanned += len(chunk)
        m = chunk[chunk["createdAtMillis"] >= START_MS]
        if not len(m):
            continue
        if "isCollaborativeNote" not in m.columns:
            m = m.assign(isCollaborativeNote=0)
        author_col = "noteAuthorParticipantId" if "noteAuthorParticipantId" in m.columns else "participantId"
        collab = m["isCollaborativeNote"].fillna(0).astype(int)
        for nid, pid, c in zip(m["noteId"].astype(str), m[author_col].astype(str), collab):
            present[nid] = (pid, int(c))
    log(f"notes: {n_scanned:,} rows scanned, {len(present):,} existing notes since launch")

    # 4. Classify
    sh["exists"] = sh["noteId"].map(lambda n: n in present)
    sh["notes_pid"] = sh["noteId"].map(lambda n: present.get(n, ("", 0))[0])
    sh["collab"] = sh["noteId"].map(lambda n: present.get(n, ("", 0))[1])
    ai_via_status = sh["pid"].isin(ai_ids)
    ai_via_notes = sh["notes_pid"].isin(ai_ids)
    log(f"AI match via statusHistory id: {int(ai_via_status.sum()):,}; via notes id: {int(ai_via_notes.sum()):,}")
    sh["type"] = "human"
    sh.loc[ai_via_status | ai_via_notes, "type"] = "ai"
    sh.loc[sh["collab"] == 1, "type"] = "collab"
    # the AI author id to group by (status-history id; fall back to notes id)
    sh["ai_pid"] = sh["pid"].where(ai_via_status, sh["notes_pid"])
    sh["day"] = pd.to_datetime(sh["createdAtMillis"], unit="ms", utc=True).dt.date
    sh = sh[sh["day"] <= as_of]
    sh["crh"] = (sh["currentStatus"] == CRH).astype(int)
    sh["crnh"] = (sh["currentStatus"] == CRNH).astype(int)
    sh["nmr"] = (sh["currentStatus"] == NMR).astype(int)
    sh["deleted"] = (~sh["exists"]).astype(int)

    # 5. Aggregates
    def agg(frame: pd.DataFrame, key) -> list[dict]:
        rows = []
        g = frame.groupby([key, "type"]).agg(notes=("noteId", "size"), crh=("crh", "sum"),
                                               crnh=("crnh", "sum"), nmr=("nmr", "sum"),
                                               deleted=("deleted", "sum")).reset_index()
        for k, sub in g.groupby(key):
            row = {"period": str(k)}
            for t in TYPES:
                r = sub[sub["type"] == t]
                for col in ("notes", "crh", "crnh", "nmr", "deleted"):
                    row[f"{col}_{t}"] = int(r[col].sum()) if len(r) else 0
            rows.append(row)
        return rows

    sh["week"] = sh["day"].map(week_start)
    weekly = agg(sh, "week")
    # Rates only for weeks whose notes have had >=14 days to be rated.
    rate_cutoff = (as_of - dt.timedelta(days=14))
    for row in weekly:
        wk = dt.date.fromisoformat(row["period"])
        mature = wk + dt.timedelta(days=6) <= rate_cutoff
        row["mature"] = bool(mature)
        for t in TYPES:
            n = row[f"notes_{t}"]
            row[f"crh_rate_{t}"] = round(100 * row[f"crh_{t}"] / n, 2) if (mature and n >= 20) else None
        tot_crh = sum(row[f"crh_{t}"] for t in TYPES)
        tot_notes = sum(row[f"notes_{t}"] for t in TYPES)
        row["ai_share_notes"] = round(100 * (row["notes_ai"] + row["notes_collab"]) / tot_notes, 2) if tot_notes else None
        row["ai_share_crh"] = round(100 * (row["crh_ai"] + row["crh_collab"]) / tot_crh, 2) if tot_crh else None
    daily_90 = agg(sh[sh["day"] >= as_of - dt.timedelta(days=90)], "day")

    def shares(frame: pd.DataFrame) -> dict:
        out = {}
        tot_notes = len(frame); tot_crh = int(frame["crh"].sum())
        for t in TYPES:
            f = frame[frame["type"] == t]
            out[t] = {
                "notes": int(len(f)), "crh": int(f["crh"].sum()), "crnh": int(f["crnh"].sum()),
                "nmr": int(f["nmr"].sum()), "deleted": int(f["deleted"].sum()),
                "share_notes": round(100 * len(f) / tot_notes, 2) if tot_notes else None,
                "share_crh": round(100 * f["crh"].sum() / tot_crh, 2) if tot_crh else None,
                "crh_rate": round(100 * f["crh"].sum() / len(f), 2) if len(f) else None,
            }
        out["total_notes"] = tot_notes; out["total_crh"] = tot_crh
        return out

    windows = {
        "30d": sh[sh["day"] > as_of - dt.timedelta(days=30)],
        "90d": sh[sh["day"] > as_of - dt.timedelta(days=90)],
        "all": sh,
    }
    share_out = {k: shares(v) for k, v in windows.items()}

    # 6. Per-writer table (AI + collab authored by apiEarnedIn ids)
    ai = sh[sh["ai_pid"].isin(ai_ids)]
    recent = ai[ai["day"] > as_of - dt.timedelta(days=30)]
    mature_ai = ai[ai["day"] <= rate_cutoff]
    writers = []
    g_all = ai.groupby("ai_pid")
    g_recent = recent.groupby("ai_pid")
    g_mature = mature_ai.groupby("ai_pid")
    for pid, f in g_all:
        r = g_recent.get_group(pid) if pid in g_recent.groups else f.iloc[0:0]
        m = g_mature.get_group(pid) if pid in g_mature.groups else f.iloc[0:0]
        resolved = int(m["crh"].sum() + m["crnh"].sum() + m["nmr"].sum())
        writers.append({
            "id": pid[:10],
            "notes_total": int(len(f)), "crh_total": int(f["crh"].sum()),
            "notes_30d": int(len(r)), "crh_30d": int(r["crh"].sum()), "crnh_30d": int(r["crnh"].sum()),
            "deleted_30d": int(r["deleted"].sum()),
            "crh_rate_mature": round(100 * m["crh"].sum() / len(m), 1) if len(m) >= 20 else None,
            "hit_rate_mature": round(100 * (m["crh"].sum() - m["crnh"].sum()) / resolved, 1) if resolved >= 20 else None,
            "deletion_rate_mature": round(100 * m["deleted"].sum() / len(m), 1) if len(m) >= 20 else None,
            "first_day": str(f["day"].min()), "last_day": str(f["day"].max()),
            "active_7d": bool(f["day"].max() > as_of - dt.timedelta(days=7)),
        })
    writers.sort(key=lambda w: (-w["notes_30d"], -w["notes_total"]))
    top6 = writers[:6]
    ai_notes_30 = int(len(recent)); ai_crh_30 = int(recent["crh"].sum())
    concentration = {
        "top6_share_notes_30d": round(100 * sum(w["notes_30d"] for w in top6) / ai_notes_30, 1) if ai_notes_30 else None,
        "top6_share_crh_30d": round(100 * sum(w["crh_30d"] for w in top6) / ai_crh_30, 1) if ai_crh_30 else None,
        "writers_active_30d": sum(1 for w in writers if w["notes_30d"] > 0),
        "writers_active_7d": sum(1 for w in writers if w["active_7d"]),
        "writers_ever": len(writers),
    }

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dump_date": date.isoformat(),
        "as_of": as_of.isoformat(),
        "start": START.date().isoformat(),
        "enrollment": {"api_earned_in": len(ai_ids), **{k: int(v) for k, v in enroll_counts.items()}},
        "shares": share_out,
        "concentration": concentration,
        "weekly": weekly,
        "daily_90d": daily_90,
        "writers": writers[:40],
        "build_seconds": round(time.time() - t0),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    log(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB) in {out['build_seconds']}s; "
        f"30d AI share of notes {share_out['30d']['ai']['share_notes']}%, of CRH {share_out['30d']['ai']['share_crh']}%; "
        f"top6 share of AI notes {concentration['top6_share_notes_30d']}%")


if __name__ == "__main__":
    main()
