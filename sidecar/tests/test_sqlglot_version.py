"""The sqlglot versions pyproject.toml allows are the ones the engine's SQL tests pass on (#1252).

constraints.txt pins one version for the app; anyone installing the engine into their own Python
gets whatever pyproject allows. CI runs the SQL tests on both ends of ``TESTED`` (run this file to
print them), so a version pyproject allows has been tested. To widen the range, run the SQL tests on
the new end, then change pyproject and ``TESTED`` together.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import sqlglot
from packaging.requirements import Requirement
from packaging.version import Version
from sqlglot import exp

SIDECAR = Path(__file__).resolve().parents[1]
TESTED = ("30.22.0", "30.27.0")  # oldest and newest sqlglot the SQL tests pass on


def _requirement(name: str) -> Requirement:
    deps = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    return next(Requirement(d) for d in deps if Requirement(d).name == name)


def test_every_sqlglot_expression_the_engine_names_exists():
    names = {m for path in (SIDECAR / "fabric_desktop").rglob("*.py")
             for m in re.findall(r"\bexp\.([A-Z][A-Za-z0-9]+)\b", path.read_text(encoding="utf-8"))}
    missing = sorted(n for n in names if not hasattr(exp, n))
    assert not missing, f"sqlglot {sqlglot.__version__} has no exp.{', exp.'.join(missing)}"


def test_the_pinned_sqlglot_is_one_pyproject_allows():
    pin = re.search(r"^sqlglot==(\S+)$", (SIDECAR / "constraints.txt").read_text(encoding="utf-8"), re.M).group(1)
    spec = _requirement("sqlglot").specifier
    assert spec.contains(pin), f"constraints.txt pins sqlglot {pin}, outside pyproject's {spec}"
    assert Version(TESTED[0]) <= Version(pin) <= Version(TESTED[1]), f"the pin {pin} is outside TESTED {TESTED}"


def test_pyproject_allows_exactly_the_tested_range():
    spec = _requirement("sqlglot").specifier
    oldest, newest = (Version(v) for v in TESTED)
    assert spec.contains(str(oldest)) and spec.contains(str(newest)), f"{spec} refuses a tested end {TESTED}"
    below = f"{oldest.major}.{oldest.minor - 1}.0"
    above = f"{newest.major}.{newest.minor + 1}.0"
    assert not spec.contains(below), f"{spec} allows {below}, older than any tested sqlglot"
    assert not spec.contains(above), f"{spec} allows {above}, newer than any tested sqlglot"


if __name__ == "__main__":
    print(*TESTED)
