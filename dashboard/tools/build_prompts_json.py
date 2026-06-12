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
import os
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
        "summary": "The first gate. A cheap Claude Haiku call keeps only posts that "
                   "carry a specific, checkable factual claim. (While the bot earns "
                   "into Community Notes it considers any topic to build a track "
                   "record; its standing beat is US political misinformation.) Most "
                   "posts — jokes, pure opinion, vague takes — drop out right here.",
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


def _active_beat() -> str:
    """The beat mode the bot runs under, same default the code uses (config.py)."""
    return os.getenv("CN_BOT_BEAT_MODE", "broad").strip().lower()


def extract_string_const(path: Path, name: str) -> str | None:
    """Return the string value bound to `name`, following one common layer of
    indirection so the published prompt matches what the bot actually runs:

      - direct literal:   _SYSTEM = "..."                       → the literal
      - alias:            _SYSTEM = _SYSTEM_BROAD               → resolve target
      - beat conditional: _SYSTEM = _A if BEAT_MODE == "x" else _B
                          → resolve whichever branch the active beat selects

    Only module-level assignments are considered (function-local vars with the
    same name can't shadow). Returns None if it can't resolve to a str literal.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigns: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigns[tgt.id] = node.value

    def ifexp_true(test: ast.expr) -> bool:
        # Evaluate `<Name> == "<const>"` against the active beat; default True.
        if (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.left, ast.Name)
                and isinstance(test.comparators[0], ast.Constant)):
            return _active_beat() == test.comparators[0].value
        return True

    def resolve(node: ast.expr | None, depth: int = 0) -> str | None:
        if node is None or depth > 6:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return resolve(assigns.get(node.id), depth + 1)
        if isinstance(node, ast.IfExp):
            return resolve(node.body if ifexp_true(node.test) else node.orelse, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # e.g. _NOTE_WRITER_SYSTEM = _BEAT_LINE + """..."""
            left = resolve(node.left, depth + 1)
            right = resolve(node.right, depth + 1)
            if left is not None and right is not None:
                return left + right
        return None

    return resolve(assigns.get(name))


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
