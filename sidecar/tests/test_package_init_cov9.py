"""fabric_desktop/__init__.py: the package still imports when the DuckDB NUL guard can't be imported (no
DuckDB), and installs the guard when it can. The package's own code is run in a fresh namespace, so the
imported package is left as it is."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import fabric_desktop


def _run_init() -> dict:
    source = Path(fabric_desktop.__file__).read_text(encoding="utf-8")
    ns = {"__name__": "fabric_desktop", "__package__": "fabric_desktop", "__file__": fabric_desktop.__file__}
    exec(compile(source, fabric_desktop.__file__, "exec"), ns)  # noqa: S102 - the package's own source
    return ns


def test_without_duckdb_the_package_imports_and_guards_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "fabric_desktop.duckdb_nul", None)  # import of it raises ImportError
    monkeypatch.delattr(fabric_desktop, "duckdb_nul", raising=False)
    ns = _run_init()
    assert ns["__version__"] == fabric_desktop.__version__
    assert "duckdb_nul" not in ns


def test_with_the_guard_it_is_installed(monkeypatch):
    calls = []
    fake = SimpleNamespace(install=lambda: calls.append("install"))
    monkeypatch.setitem(sys.modules, "fabric_desktop.duckdb_nul", fake)
    monkeypatch.setattr(fabric_desktop, "duckdb_nul", fake, raising=False)
    ns = _run_init()
    assert ns["duckdb_nul"] is fake and calls == ["install"]
