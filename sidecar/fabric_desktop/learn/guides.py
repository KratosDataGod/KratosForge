"""Explain and Show example beside a best-practice finding (LX-09): each rule's own section of the
guide, shipped inside the app so it works offline and sends nothing anywhere.

The guides are the docs (docs/notebook-best-practices.md, docs/sql-best-practices.md): one section
per rule, ``### NB003``, with ``Why:``, an optional example fenced in code with ``# Before`` and
``# After`` (``--`` in SQL), and ``When it's fine:``, ``Safe to leave:`` or ``Do this instead:``.
``rule_guides.json`` is those sections, read into fields. The docs stay the one place they're
written: CI fails while the JSON differs from them (tests/test_learn_guides.py), and

    python -m fabric_desktop.learn.guides ../docs

writes it again.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

FILE = Path(__file__).resolve().parent / "rule_guides.json"
DOCS = ("notebook-best-practices.md", "sql-best-practices.md")

_RULE = re.compile(r"^(NB|SQ)\d{3}$")
_FIELDS = {"Why:": "why", "When it's fine:": "fine", "Safe to leave:": "fine", "Do this instead:": "instead",
           "Source:": None}
_MARK = re.compile(r"^\s*(#|--)\s*(Before|After)\b:?\s*(.*)$")  # "# After: copy it once" keeps its note


def parse(markdown: str) -> dict[str, dict[str, Any]]:
    """{rule id: {why, fine, instead, language, before, after}} from one best-practices page."""
    out: dict[str, dict[str, Any]] = {}
    for section in re.split(r"^### ", markdown, flags=re.M)[1:]:
        head, _, body = section.partition("\n")
        rule_id = head.strip()
        if not _RULE.match(rule_id):
            continue
        out[rule_id] = _section(re.split(r"^## ", body, flags=re.M)[0])
    return out


def _section(body: str) -> dict[str, Any]:
    guide: dict[str, Any] = {"why": "", "fine": "", "instead": "", "language": "", "before": "", "after": ""}
    field: str | None = None
    fence: list[str] | None = None
    for line in body.splitlines():
        if line.startswith("```"):
            if fence is None:
                fence, guide["language"] = [], line[3:].strip()
            else:
                _example(guide, fence)
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue
        text = line.strip()
        if not text or text.startswith("**"):
            continue
        label = next((k for k in _FIELDS if text.startswith(k)), None)
        if label is not None:
            field, text = _FIELDS[label], text[len(label):].strip()
        if field:
            guide[field] = f"{guide[field]} {text}".strip()
    for key in ("why", "fine", "instead"):  # "Why: the engine..." reads as "The engine..." on its own
        guide[key] = guide[key][:1].upper() + guide[key][1:]
    return guide


def _example(guide: dict[str, Any], lines: list[str]) -> None:
    part: str | None = None
    kept: dict[str, list[str]] = {"before": [], "after": []}
    for line in lines:
        mark = _MARK.match(line)
        if mark:
            part = mark.group(2).lower()
            if mark.group(3):
                kept[part].append(f"{mark.group(1)} {mark.group(3)}")
        elif part:
            kept[part].append(line)
    guide["before"], guide["after"] = "\n".join(kept["before"]).strip("\n"), "\n".join(kept["after"]).strip("\n")


def build(docs: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in DOCS:
        out.update(parse((docs / name).read_text(encoding="utf-8")))
    return dict(sorted(out.items()))


def load() -> dict[str, dict[str, Any]]:
    """The shipped guides, by rule id."""
    return json.loads(FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    FILE.write_text(json.dumps(build(Path(sys.argv[1])), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
