"""Mutation batch 274: the project test run in meta/testing.py, where test_meta_testing_cov2.py didn't
look: tmp_path and defaults, which references are missing, the seconds it reports, old tests.*
modules, and where a failure with no test frame is placed."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import testing


def _project(root, refs=()):
    (root / "tests").mkdir(exist_ok=True)
    entity = SimpleNamespace(name="silver.orders", python_refs=lambda: iter(list(refs)))
    return SimpleNamespace(root=root, entities={"silver.orders": entity}, purge_code=lambda: None, version="v1")


@pytest.fixture(autouse=True)
def _forget_project_tests():
    yield
    for mod in [m for m in sys.modules if m == "tests" or m.startswith("tests.")]:
        del sys.modules[mod]


def test_a_test_gets_tmp_path_and_keeps_its_own_defaults(tmp_path):
    p = _project(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_folder(tmp_path):\n    assert tmp_path.is_dir()\n\n"
        "def test_default(tmp_path, n=2):\n    assert n == 2\n", encoding="utf-8")
    result = testing.run(p)
    assert [t["status"] for t in result["tests"]] == ["passed", "passed"]


def test_only_references_to_a_file_that_isnt_there_are_missing(tmp_path):
    refs = [("check", "a", {"test": "tests/test_a.py::test_one"}),
            ("check", "b", {"test": "tests/test_a.py::test_gone"}),  # its file runs, so it isn't missing
            ("check", "c", {"test": "tests/test_a.py"}),
            ("check", "d", {"test": "tests/test_x.py::test_t"})]
    p = _project(tmp_path, refs)
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    assert testing.run(p)["missing"] == ["tests/test_x.py::test_t"]


def test_old_project_test_modules_are_forgotten_before_a_file_is_loaded(tmp_path):
    p = _project(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    sys.modules["tests.stale_helper_mut274"] = SimpleNamespace()
    testing.run(p)
    assert "tests.stale_helper_mut274" not in sys.modules


def _clock(monkeypatch, *readings):
    it = iter(readings)
    monkeypatch.setattr(testing, "time", SimpleNamespace(monotonic=lambda: next(it)))


def test_seconds_are_the_time_taken_rounded(tmp_path, monkeypatch):
    p = _project(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    _clock(monkeypatch, 100.0, 110.0, 111.23456, 120.4567)  # start, test start, test end, run end
    result = testing.run(p)
    assert result["tests"][0]["seconds"] == 1.235
    assert result["seconds"] == 20.46


def test_a_failure_with_no_test_frame_is_placed_at_its_deepest_line():
    code = compile("def catch():\n    try:\n        outer()\n    except Exception as e:\n        return e\n"
                   "def outer():\n    inner()\n"
                   "def inner():\n    raise ValueError('deep')\n", "/elsewhere/helpers.py", "exec")
    ns: dict = {}
    exec(code, ns)
    assert testing._failure(ns["catch"]()) == "ValueError: deep (line 9)"
