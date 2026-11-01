"""pyproject.toml allows only the DuckDB major versions the engine's tests pass on (#1340, #1341).

constraints.txt pins one version for the app, but anyone installing the engine into their own Python
gets whatever pyproject allows. CI runs the whole engine suite on the pinned version and again on
NEXT (ci.yml), so pyproject allows those two majors and none beyond.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

SIDECAR = Path(__file__).resolve().parents[1]

# The next major, on which CI also runs the whole engine suite (ci.yml, #1341).
NEXT = "2.0.0"


def _allowed():
    deps = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    return next(Requirement(d) for d in deps if Requirement(d).name == "duckdb").specifier


def _pinned() -> Version:
    [pin] = re.findall(r"^duckdb==(\S+)", (SIDECAR / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return Version(pin)


def test_pyproject_allows_the_pinned_duckdb():
    assert _pinned() in _allowed()


def test_pyproject_allows_the_next_major_ci_tests():
    assert Version(NEXT).major == _pinned().major + 1 and _allowed().contains(NEXT)


def test_pyproject_allows_no_major_version_beyond_the_ones_ci_tests():
    beyond = Version(f"{Version(NEXT).major + 1}.0.0")
    assert not _allowed().contains(beyond)
    assert not _allowed().contains(f"{beyond.major}.0.0rc1", prereleases=True)


def test_ci_runs_the_suite_on_the_next_major():
    ci = (SIDECAR.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python tests/test_duckdb_version.py" in ci


if __name__ == "__main__":
    print(NEXT)
