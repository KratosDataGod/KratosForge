"""The project's own tests (meta/testing.py): what mutation testing found unchecked. Loading clears every
tests.* module so edits count, the seconds each test and the run took, which references are missing, and
the line a failure names when no frame is in a test file."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import testing


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tests").mkdir()
    refs = [("custom check", f"checks:c{i}", {"test": t}) for i, t in enumerate([
        "tests/test_a.py::test_passes", "tests/test_a.py", "tests/test_b.py::test_cant_be_listed",
        "tests/test_zz.py", "tests/test_zz.py::test_x"])]
    entity = SimpleNamespace(name="silver.orders", python_refs=lambda: iter(refs))
    (tmp_path / "tests" / "test_a.py").write_text("def test_passes():\n    assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("import not_a_module_here_mut\n", encoding="utf-8")
    yield SimpleNamespace(root=tmp_path, entities={"silver.orders": entity}, purge_code=lambda: None, version="v1")
    for mod in [m for m in sys.modules if m == "tests" or m.startswith("tests.")]:
        del sys.modules[mod]


def test_loading_a_test_file_clears_every_tests_module_first(project):
    sys.modules["tests.helper_left_over_mut"] = SimpleNamespace()
    testing.run(project)
    assert "tests.helper_left_over_mut" not in sys.modules


def test_missing_are_references_to_files_that_arent_there(project):
    # A test in a file that can't be imported can't be listed: the import error stands for it.
    assert testing.run(project)["missing"] == ["tests/test_zz.py", "tests/test_zz.py::test_x"]


def test_seconds_are_per_test_to_the_millisecond_and_the_run_to_the_hundredth(project, monkeypatch):
    tick = [100 + i * 0.1255 for i in range(100)]   # the clock is far from 0, as a real monotonic clock is
    ticks = iter(tick)
    monkeypatch.setattr(testing, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    result = testing.run(project)
    [passes] = [t for t in result["tests"] if t["name"] == "test_passes"]
    assert passes["seconds"] == round(tick[2] - tick[1], 3) and 0.12 < passes["seconds"] < 0.13
    assert result["seconds"] == round(tick[3] - tick[0], 2) == 0.38


def test_a_failure_with_no_frame_in_a_test_file_names_its_last_line():
    code = compile("def go():\n    try:\n        inner()\n    except Exception as e:\n        return e\n\n\n"
                   "def inner():\n    raise ValueError('bad')\n", "/lib/helper_mut.py", "exec")
    scope: dict = {}
    exec(code, scope)  # noqa: S102 - code written here, so no frame of it is under tests/
    assert testing._failure(scope["go"]()) == "ValueError: bad (line 9)"
