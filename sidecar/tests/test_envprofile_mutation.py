"""Mutation batch 64: envprofile.py (see docs/quality/mutation-testing.md).

Pins what the mutation run showed no test held: a cell that isn't Python still counts as parsed, the
line of a dynamic import on a cell's first line after a blank one, which except clauses make an
import optional, an import required anywhere stays required, the This PC summary with a missing
module, and a distribution with no version left out of the runtime."""

from __future__ import annotations

import pytest

from fabric_desktop import envprofile


def test_a_cell_that_is_not_python_counts_as_parsed_with_no_imports():
    assert envprofile.imports("%%sql\nselect 1") == {"parsed": True, "modules": [], "dynamic": []}


def test_a_dynamic_import_after_a_blank_first_line_is_on_line_two():
    assert envprofile.imports("\nimportlib.import_module('a')")["dynamic"] == [2]
    assert envprofile.imports("importlib.import_module('a')")["dynamic"] == [1]


def test_a_pip_line_below_blank_lines_is_reported_on_its_own_line():
    assert envprofile.imports("\n%pip install x")["dynamic"] == [2]
    assert envprofile.imports("x = 1\n\n%pip install x")["dynamic"] == [3]


@pytest.mark.parametrize("handler", ["except:", "except ImportError:", "except ModuleNotFoundError:",
                                     "except Exception:", "except (ValueError, ImportError):",
                                     "except (ModuleNotFoundError, KeyError) as e:"])
def test_an_import_with_an_import_fallback_is_optional(handler):
    found = envprofile.imports(f"try:\n    import pkg_a\n{handler}\n    pkg_a = None")
    assert found["modules"] == [{"module": "pkg_a", "line": 2, "optional": True}]


@pytest.mark.parametrize("handler", ["except ValueError:", "except (KeyError, OSError):"])
def test_an_import_whose_handler_catches_something_else_is_required(handler):
    found = envprofile.imports(f"try:\n    import pkg_a\n{handler}\n    pass")
    assert found["modules"] == [{"module": "pkg_a", "line": 2, "optional": False}]


@pytest.mark.parametrize("code", ["import pkg_a\ndef f():\n    import pkg_a",
                                  "def f():\n    import pkg_a\nimport pkg_a"])
def test_a_module_required_anywhere_is_required(code):
    (found,) = envprofile.imports(code)["modules"]
    assert found["module"] == "pkg_a" and found["optional"] is False


def test_a_module_imported_only_inside_functions_stays_optional():
    (found,) = envprofile.imports("def f():\n    import pkg_a\ndef g():\n    import pkg_a")["modules"]
    assert found["optional"] is True


def test_this_pc_is_not_ok_when_a_required_module_is_missing_though_every_cell_parsed(monkeypatch):
    monkeypatch.setattr(envprofile, "on_this_pc", lambda m: {"status": "missing"})
    out = envprofile.check([{"type": "code", "source": "import pkg_missing_here"}])
    assert out["unparsed"] == [] and out["pc"]["missing"] == ["pkg_missing_here"]
    assert out["pc"]["ok"] is False


def test_this_pc_is_ok_when_only_an_optional_module_is_missing(monkeypatch):
    monkeypatch.setattr(envprofile, "on_this_pc", lambda m: {"status": "missing"})
    out = envprofile.check([{"type": "code", "source": "try:\n    import pkg_x\nexcept ImportError:\n    pass"}])
    assert out["pc"] == {**out["pc"], "ok": True, "missing": []}


def test_a_distribution_with_no_version_is_left_out_of_the_runtime(monkeypatch):
    found = {"good": ("Good-Dist", "1.0"), "noversion": ("Broken-Dist", None), "absent": (None, None)}
    monkeypatch.setattr(envprofile, "_distribution", lambda m: found[m])
    assert envprofile.runtime(["good", "noversion", "absent"])["packages"] == {"good-dist": "1.0"}
