"""The project's own tests (meta/testing.py): a file that can't be imported, a test that errors rather
than fails, custom code that names no test, and a failure with no traceback."""

import sys
from types import SimpleNamespace

import pytest

from fabric_desktop.meta import testing


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tests").mkdir()
    refs = [
        ("hook after_clean", "hooks:clean", {}),  # names no test
        ("custom check", "checks:rows", {"test": "tests/test_a.py::test_errors"}),
    ]
    entity = SimpleNamespace(name="silver.orders", python_refs=lambda: iter(refs))
    yield SimpleNamespace(
        root=tmp_path,
        entities={"silver.orders": entity},
        purge_code=lambda: None,
        version="v3",
    )
    for mod in [m for m in sys.modules if m == "tests" or m.startswith("tests.")]:
        del sys.modules[mod]


def test_import_errors_and_test_errors_are_reported(project):
    (project.root / "tests" / "test_a.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n\n"
        "def test_errors():\n    raise KeyError('no such column')\n",
        encoding="utf-8",
    )
    (project.root / "tests" / "test_b.py").write_text(
        "import not_a_module_here_cov\n", encoding="utf-8"
    )
    result = testing.run(project)
    by = {t["id"]: t for t in result["tests"]}
    assert by["tests/test_a.py::test_passes"]["status"] == "passed"
    err = by["tests/test_a.py::test_errors"]
    assert err["status"] == "error"
    assert err["message"].startswith("KeyError: 'no such column' (line 5:")
    assert err["used_by"] == ["silver.orders custom check checks:rows"]
    imp = by["tests/test_b.py"]
    assert imp["name"] == "(import)" and imp["status"] == "error"
    assert imp["message"].startswith("ModuleNotFoundError: ")
    assert result["passed"] == 1 and result["failed"] == 2 and result["missing"] == []
    assert result["version"] == "v3"
    assert testing.gate_errors(result)[0].startswith(
        "test tests/test_a.py::test_errors errored: KeyError"
    )


def test_referenced_skips_custom_code_that_names_no_test(project):
    assert testing.referenced(project) == {
        "tests/test_a.py::test_errors": ["silver.orders custom check checks:rows"]
    }


def test_failure_without_a_traceback_is_its_text_alone():
    assert testing._failure(ValueError("bad value")) == "ValueError: bad value"
    assert testing._failure(AssertionError()) == "AssertionError"
