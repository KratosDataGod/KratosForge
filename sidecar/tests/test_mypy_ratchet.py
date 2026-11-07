"""Tests for the CI type check's ratchet (.github/scripts/mypy_ratchet.py). mypy itself isn't run here."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mypy_ratchet", ROOT / ".github" / "scripts" / "mypy_ratchet.py")
mypy_ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mypy_ratchet)

OUTPUT = """\
fabric_desktop/cli.py:223: error: Incompatible types in assignment (expression has type "int", variable has type "str")  [assignment]
fabric_desktop/cli.py:223: note: See https://mypy.readthedocs.io
fabric_desktop/cli.py:301:5: error: Incompatible types in assignment  [assignment]
fabric_desktop\\agent.py:12: error: "Foo" has no attribute "bar"  [attr-defined]
fabric_desktop/agent.py:40: error: An error without a code
"""


def test_errors_are_counted_per_file_and_code_and_notes_are_not():
    assert mypy_ratchet.count(OUTPUT) == {"fabric_desktop/agent.py": {"attr-defined": 1, "misc": 1},
                                          "fabric_desktop/cli.py": {"assignment": 2}}


def test_more_errors_of_a_kind_fail_and_fewer_are_only_reported():
    baseline = {"fabric_desktop/cli.py": {"assignment": 2, "arg-type": 3}}
    now = {"fabric_desktop/cli.py": {"assignment": 3, "arg-type": 1}, "fabric_desktop/new.py": {"misc": 1}}
    worse, better = mypy_ratchet.compare(now, baseline)
    assert worse == ["fabric_desktop/cli.py: 3 [assignment] errors, 2 before", "fabric_desktop/new.py: 1 [misc] errors, 0 before"]
    assert better == ["fabric_desktop/cli.py: 1 [arg-type] errors, 3 before"]
    assert mypy_ratchet.compare(baseline, baseline) == ([], [])


def test_the_baseline_is_a_count_per_file_and_code():
    baseline = mypy_ratchet.load()
    assert all(f.startswith("fabric_desktop/") and "_vendor" not in f for f in baseline)
    assert all(codes and isinstance(n, int) and n > 0 for codes in baseline.values() for n in codes.values())
    assert not (ROOT / "sidecar" / "mypy-baseline.json").exists()  # replaced by one file per source file


def test_with_an_empty_baseline_any_new_error_fails_the_check(tmp_path, monkeypatch, capsys):
    # Every module is typed now, so the baseline is empty; one new error must still turn CI red.
    monkeypatch.setattr(mypy_ratchet, "BASELINE", tmp_path)
    monkeypatch.setattr(mypy_ratchet, "load", lambda folder=tmp_path: {})
    monkeypatch.setattr(mypy_ratchet, "run_mypy", lambda: "fabric_desktop/cli.py:1: error: Bad  [assignment]\n")
    assert mypy_ratchet.main([]) == 1
    assert "fabric_desktop/cli.py: 1 [assignment] errors, 0 before" in capsys.readouterr().out
    monkeypatch.setattr(mypy_ratchet, "run_mypy", lambda: "Success: no issues found in 1 source file\n")
    assert mypy_ratchet.main([]) == 0


def test_each_source_file_has_its_own_baseline_file(tmp_path):
    counts = {"fabric_desktop/cli.py": {"assignment": 2}, "fabric_desktop/meta/store.py": {"union-attr": 1}}
    mypy_ratchet.save(counts, tmp_path)
    assert json.loads((tmp_path / "fabric_desktop" / "meta" / "store.py.json").read_text()) == {"union-attr": 1}
    assert mypy_ratchet.load(tmp_path) == counts
    # A module cleared of errors loses its file (and an emptied folder goes too); the others are untouched.
    mypy_ratchet.save({"fabric_desktop/cli.py": {"assignment": 1}}, tmp_path)
    assert mypy_ratchet.load(tmp_path) == {"fabric_desktop/cli.py": {"assignment": 1}}
    assert not (tmp_path / "fabric_desktop" / "meta").exists()


def test_a_deleted_baseline_file_means_that_module_must_stay_clean(tmp_path):
    mypy_ratchet.save({"fabric_desktop/cli.py": {"assignment": 2}, "fabric_desktop/agent.py": {"misc": 1}}, tmp_path)
    (tmp_path / "fabric_desktop" / "agent.py.json").unlink()
    worse, _ = mypy_ratchet.compare({"fabric_desktop/agent.py": {"misc": 1}}, mypy_ratchet.load(tmp_path))
    assert worse == ["fabric_desktop/agent.py: 1 [misc] errors, 0 before"]


def test_a_run_that_reports_no_type_errors_but_exits_1_is_not_counted_as_clean():
    missing = "/usr/bin/python3: No module named mypy\n"
    assert "isn't installed" in mypy_ratchet.problem(1, "", missing)
    assert "exit 2" in mypy_ratchet.problem(2, "", "mypy.ini: bad setting\n")
    assert "exit 1" in mypy_ratchet.problem(1, "", "something else\n")
    assert mypy_ratchet.problem(1, OUTPUT, "") == ""
    assert mypy_ratchet.problem(0, "", "") == ""
