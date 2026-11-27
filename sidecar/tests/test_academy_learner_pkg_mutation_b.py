"""Mutation batch 417: academy/learner_pkg.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import learner_pkg as lp


def test_a_rewrite_whose_text_is_gone_is_an_error(monkeypatch):
    monkeypatch.setitem(lp.REWRITES, "sql.py", (("this text isn't in sql.py", "x"),))
    with pytest.raises(ValueError, match="sql.py: expected"):
        lp.text("sql.py")


def test_only_the_standard_library_counts_as_standard():
    assert lp._stdlib("__future__") and lp._stdlib("json")
    assert not lp._stdlib("numpy") and not lp._stdlib("fabric_desktop")


def test_the_package_can_be_built_twice_in_the_same_place(tmp_path):
    lp.build(tmp_path)
    assert lp.build(tmp_path) == tmp_path
    assert (tmp_path / lp.PACKAGE / "sql.py").read_text(encoding="utf-8") == lp.text("sql.py")


def _sources(tmp_path, monkeypatch, body: str) -> None:
    (tmp_path / "a.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr(lp, "SOURCES", {"a.py": tmp_path / "a.py"})
    monkeypatch.setattr(lp, "REWRITES", {})


def test_a_lazy_engine_only_import_is_fine_only_inside_a_function(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, "def f():\n    from . import lakehouse\n")
    assert lp.import_problems() == []
    _sources(tmp_path, monkeypatch, "from . import lakehouse\n")
    assert lp.import_problems() == ["a.py: imports lakehouse, which isn't in kf_learner"]


def test_another_engine_import_inside_a_function_is_still_a_problem(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, "def f():\n    from . import jsonfile\n")
    assert lp.import_problems() == ["a.py: imports jsonfile, which isn't in kf_learner"]
