"""SQLite audit trail for every post seen, draft written, and submission.

Located at template-api-note-writer/data/notes.db. One file, two tables.
Survives bot restarts and gives us a debuggable history for tuning.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from note_writer.config import BEAT_MODE, NOTE_WRITER_MODEL

DB_PATH = Path(__file__).parent.parent / "data" / "notes.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    post_id          TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    post_text        TEXT NOT NULL,
    outcome          TEXT NOT NULL,            -- 'submitted' | 'queued' | 'refused' | 'error'
    refusal_reason   TEXT,
    error            TEXT,
    note_text        TEXT,
    evidence_url     TEXT,
    evidence_rating  TEXT,
    evidence_publisher TEXT,
    evidence_tier    TEXT,                      -- 'ifcn_verified' | 'self_fact_check'
    misleading_tags  TEXT,                      -- JSON list
    beat_mode        TEXT,                      -- 'broad' | 'us_politics' (earn-in phase marker)
    writer_model     TEXT                       -- model that wrote the note prose
);

-- Migrate existing dbs that predate later columns
-- (sqlite ignores the column if it already exists)

CREATE TABLE IF NOT EXISTS submissions (
    post_id      TEXT PRIMARY KEY,
    submitted_at TEXT NOT NULL,
    test_mode    INTEGER NOT NULL,
    response     TEXT NOT NULL                  -- raw JSON
);

CREATE TABLE IF NOT EXISTS rating_snapshots (
    post_id           TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    status            TEXT,
    scoring_status    TEXT,
    PRIMARY KEY (post_id, captured_at)
);
"""


def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA)
        # Idempotent column adds — sqlite has no IF NOT EXISTS for ALTER TABLE
        cols = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
        for col in ("evidence_tier", "beat_mode", "writer_model"):
            if col not in cols:
                conn.execute(f"ALTER TABLE drafts ADD COLUMN {col} TEXT")


@contextmanager
def connect():
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_draft(
    *,
    post_id: str,
    post_text: str,
    outcome: str,
    note_text: Optional[str] = None,
    evidence_url: Optional[str] = None,
    evidence_rating: Optional[str] = None,
    evidence_publisher: Optional[str] = None,
    evidence_tier: Optional[str] = None,
    misleading_tags: Optional[list] = None,
    refusal_reason: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Insert or replace a draft row — but never overwrite a 'submitted'
    outcome with something downstream (e.g., a later run refused).
    """
    with connect() as conn:
        existing = conn.execute(
            "SELECT outcome FROM drafts WHERE post_id = ?", (post_id,)
        ).fetchone()
        # Once a post is marked submitted, that's the canonical record.
        # Don't let a re-processed pass overwrite it with a refusal.
        if existing and existing["outcome"] == "submitted" and outcome != "submitted":
            return
        conn.execute(
            """INSERT OR REPLACE INTO drafts
               (post_id, created_at, post_text, outcome, refusal_reason, error,
                note_text, evidence_url, evidence_rating, evidence_publisher,
                evidence_tier, misleading_tags, beat_mode, writer_model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                post_id,
                _now(),
                post_text,
                outcome,
                refusal_reason,
                error,
                note_text,
                evidence_url,
                evidence_rating,
                evidence_publisher,
                evidence_tier,
                json.dumps([str(t) for t in (misleading_tags or [])]),
                BEAT_MODE,
                NOTE_WRITER_MODEL,
            ),
        )


def log_submission(*, post_id: str, test_mode: bool, response: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO submissions (post_id, submitted_at, test_mode, response) VALUES (?, ?, ?, ?)",
            (post_id, _now(), 1 if test_mode else 0, json.dumps(response)),
        )


def log_rating_snapshot(*, post_id: str, status: Optional[str], scoring_status: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rating_snapshots (post_id, captured_at, status, scoring_status) VALUES (?, ?, ?, ?)",
            (post_id, _now(), status, json.dumps(scoring_status) if scoring_status else None),
        )


def recent_drafts(limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(conn.execute(
            "SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?", (limit,)
        ))
