"""The sqlglot the engine runs on has every expression class the engine names.

sqlglot 30.24 folded ``exp.SortArray`` into ``exp.ArraySort``, so importing sql.py (and with it
service.py) failed there. pyproject.toml caps sqlglot below 30.24 until sql.py handles both shapes.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import sqlglot
from packaging.requirements import Requirement
from sqlglot import exp

SIDECAR = Path(__file__).resolve().parents[1]


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
    assert not spec.contains("30.24.0"), "30.24 has no exp.SortArray: sql.py must handle ArraySort's asc first"
