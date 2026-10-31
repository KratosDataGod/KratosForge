"""pyproject.toml allows only the DuckDB major version the engine's tests pass on (#1340).

constraints.txt pins one version for the app, but anyone installing the engine into their own Python
gets whatever pyproject allows. On DuckDB 2.0, 79 engine tests fail (#1341), so pyproject stays
below 2 until that story makes the suite pass on 2.0 and CI runs it there.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

SIDECAR = Path(__file__).resolve().parents[1]


def _allowed():
    deps = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    return next(Requirement(d) for d in deps if Requirement(d).name == "duckdb").specifier


def _pinned() -> Version:
    [pin] = re.findall(r"^duckdb==(\S+)", (SIDECAR / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return Version(pin)


def test_pyproject_allows_the_pinned_duckdb():
    assert _pinned() in _allowed()


def test_pyproject_allows_no_major_version_beyond_the_pinned_one():
    nxt = Version(f"{_pinned().major + 1}.0.0")
    assert not _allowed().contains(nxt)
    assert not _allowed().contains(f"{nxt.major}.0.0rc1", prereleases=True)
