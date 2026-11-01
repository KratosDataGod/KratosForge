"""Decision patterns (GD-05): a choice with no single right answer, explained where it's made, such as
the engine picker. Shipped inside the app, like the rule guides, so it works offline.

The patterns are written in docs/decision-patterns.md, one section per pattern, ``### DP-01``, with a
bold title line, the explanation in Markdown and one fenced example that runs on this PC.
``patterns.json`` is those sections, read into fields. CI fails while the JSON differs from the docs
(tests/test_decision_patterns.py), and

    python -m fabric_desktop.learn.patterns ../docs

writes it again.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

FILE = Path(__file__).resolve().parent / "patterns.json"
DOC = "decision-patterns.md"
_SECTION = re.compile(r"^### (DP-\d\d)\n(.*?)(?=^##|\Z)", re.M | re.S)
_FENCE = re.compile(r"^```(\w*)\n(.*?)^```\n?", re.M | re.S)


def parse(markdown: str) -> dict[str, dict[str, Any]]:
    """{pattern id: {title, body, language, example}} from the decision patterns page."""
    out: dict[str, dict[str, Any]] = {}
    for m in _SECTION.finditer(markdown):
        text = m.group(2).strip("\n")
        title = re.match(r"\*\*(.+?)\*\*\n", text)
        if title:
            text = text[title.end():]
        fence = _FENCE.search(text)
        body = (text[:fence.start()] + text[fence.end():]) if fence else text
        out[m.group(1)] = {"title": title.group(1) if title else m.group(1),
                           "body": re.sub(r"\n{3,}", "\n\n", re.sub(r"\n  (?=\S)", " ", body)).strip(),  # a list item on one line
                           "language": fence.group(1) if fence else "", "example": fence.group(2).rstrip() if fence else ""}
    return out


def build(docs: Path) -> dict[str, dict[str, Any]]:
    return dict(sorted(parse((docs / DOC).read_text(encoding="utf-8")).items()))


def load() -> dict[str, dict[str, Any]]:
    """The shipped patterns, by id."""
    return json.loads(FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    FILE.write_text(json.dumps(build(Path(sys.argv[1])), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
