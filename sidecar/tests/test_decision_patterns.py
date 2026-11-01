"""GD-05: the decision patterns page. Each pattern has its parts, and its example runs offline on
this PC and shows what the page says it shows."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "docs" / "decision-patterns.md"


def _patterns() -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"^### (DP-\d\d)\n(.*?)(?=^### |\Z)", PAGE.read_text(encoding="utf-8"),
                                                          re.M | re.S)}


def _run(section: str) -> str:
    (code,) = re.findall(r"```python\n(.*?)```", section, re.S)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<example>", "exec"), {})  # noqa: S102  # the page's own example, offline
    return out.getvalue()


def test_each_pattern_says_when_it_works_and_fails_its_cost_reliability_and_how_to_decide():
    patterns = _patterns()
    assert set(patterns) == {"DP-01", "DP-02"}
    for pid, text in patterns.items():
        for part in ("works when:", "fails when:", "Cost:", "Reliability:", "How to decide:"):
            assert part in text, (pid, part)


def test_the_incremental_example_misses_the_late_row():
    assert "Rows the incremental run missed: [(4,)]" in _run(_patterns()["DP-01"])


def test_the_engine_example_measures_this_pc_only():
    assert re.search(r"^1,000 groups in \d+\.\d\d s on this PC \(measured\)$", _run(_patterns()["DP-02"]), re.M)


def test_the_shipped_patterns_are_the_docs_and_are_served_read_only():
    from fabric_desktop import audit, server
    from fabric_desktop.learn import patterns
    from fabric_desktop.learn_service import METHODS

    assert patterns.load() == patterns.build(PAGE.parent), "run: python -m fabric_desktop.learn.patterns ../docs"
    got = patterns.load()
    assert got["DP-02"]["title"] == "DuckDB on this PC or Spark in Fabric" and got["DP-02"]["language"] == "python"
    assert "```" not in got["DP-01"]["body"] and "\n  " not in got["DP-01"]["body"]   # the example is apart; list items on one line
    assert "learn_patterns" in METHODS <= server.METHODS and not audit.audited("learn_patterns")
