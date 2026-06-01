#!/usr/bin/env python3
"""Extract the bot's live system prompts for the public "How it decides" page.

Reads the real prompt constants out of src/note_writer/*.py via AST — no
imports, no side effects, stdlib only — and emits JSON the dashboard renders.
Because it parses the actual source, the published prompts cannot drift from
what the bot runs. Regenerated on every refresh.sh.

Output: JSON array of {order, gate, file, const, summary, prompt}.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "src" / "note_writer"

# Editorial metadata (titles + one-line "what this gate does"). The prompt TEXT
# below comes straight from source, so only this framing is hand-authored.
GATES = [
    {
        "file": "relevance_filter.py", "const": "_SYSTEM",
        "gate": "1. Relevance filter",
        "summary": "The first gate. A cheap Claude Haiku call decides whether the "
                   "post is about US politics or political misinformation. Most "
                   "posts drop out right here — the bot's beat is narrow by design.",
    },
    {
        "file": "specificity_check.py", "const": "_SYSTEM",
        "gate": "2. Specificity gate",
        "summary": "Is there a specific, falsifiable claim that can be checked "
                   "without watching a linked video or image? Vague takes, pure "
                   "opinion, and 'click the link to see' posts are refused.",
    },
    {
        "file": "write_note.py", "const": "_NOTE_WRITER_SYSTEM",
        "gate": "3. Note writer",
        "summary": "Writes the prose of the note — and ONLY the prose. It never "
                   "writes the URL; code appends the verified fact-check link "
                   "afterward. At runtime, 20 real helpful + 10 unhelpful Community "
                   "Notes are appended as style exemplars.",
    },
    {
        "file": "opinion_check.py", "const": "_SYSTEM",
        "gate": "4. Opinion filter",
        "summary": "Scores the draft for how much it reads as opinion or "
                   "speculation versus pure factual correction. Editorializing "
                   "notes get rejected.",
    },
    {
        "file": "error_check.py", "const": "_SYSTEM",
        "gate": "5. Hallucination check",
        "summary": "Compares the draft against the source article, looking only "
                   "for fabricated hard specifics — numbers, names, dates the note "
                   "asserts but the article doesn't actually support.",
    },
]


def extract_string_const(path: Path, name: str) -> str | None:
    """Return the value of the first `name = \"...\"` string-literal assignment."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Name) and tgt.id == name
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    return node.value.value
    return None


def main() -> None:
    out = []
    for i, g in enumerate(GATES, 1):
        prompt = extract_string_const(SRC / g["file"], g["const"])
        if prompt is None:
            print(f"WARN: could not extract {g['const']} from {g['file']}", file=sys.stderr)
            continue
        out.append({
            "order": i, "gate": g["gate"], "file": g["file"],
            "const": g["const"], "summary": g["summary"], "prompt": prompt.strip(),
        })
    if len(out) != len(GATES):
        print(f"WARN: extracted {len(out)}/{len(GATES)} prompts", file=sys.stderr)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
