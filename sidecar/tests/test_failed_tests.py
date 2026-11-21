"""Tests for the failed-test table on CI's job summary page (.github/scripts/failed_tests.py)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("failed_tests", ROOT / ".github" / "scripts" / "failed_tests.py")
failed_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(failed_tests)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" tests="3">
  <testcase classname="tests.test_a" name="test_passes" time="0.1"/>
  <testcase classname="tests.test_a" name="test_fails" time="0.1">
    <failure message="AssertionError: assert 1 == 2">long traceback</failure></testcase>
  <testcase classname="tests.test_b" name="test_teardown" time="0.1">
    <error message="failed on teardown with &quot;OSError: in use&quot;">trace</error></testcase>
</testsuite></testsuites>
"""

TAP = """TAP version 13
# Subtest: outer
    # Subtest: inner fails
    not ok 1 - inner fails
      ---
      duration_ms: 1.2
      location: '/home/runner/work/KratosForge/KratosForge/app/tests/browser/x.test.cjs:58:5'
      failureType: 'testCodeFailure'
      error: |-
        Expected values to be strictly equal:

        1 !== 2

      code: 'ERR_ASSERTION'
      ...
    1..1
not ok 1 - outer
  ---
  location: '/home/runner/work/KratosForge/KratosForge/app/tests/browser/x.test.cjs:57:1'
  failureType: 'subtestsFailed'
  error: '1 subtest failed'
  ...
ok 2 - passes
  ---
  duration_ms: 0.2
  ...
not ok 3 - times out
  ---
  location: 'D:\\a\\KratosForge\\KratosForge\\vscode\\test\\channel.test.cjs:12:1'
  error: 'test timed out after 10000ms'
  ...
"""

CARGO = """running 3 tests
test update::checks_sha ... ok
test policy::refuses_downgrade ... FAILED
test cookies::expire ... FAILED

failures:

---- policy::refuses_downgrade stdout ----

thread 'policy::refuses_downgrade' panicked at src/policy.rs:88:9:
assertion `left == right` failed
  left: 1
 right: 2

---- cookies::expire stdout ----
some output without a panic line

failures:
    policy::refuses_downgrade
    cookies::expire
"""


def test_junit_names_each_failure_and_error_by_file(tmp_path):
    f = tmp_path / "junit.xml"
    f.write_text(JUNIT, encoding="utf-8")
    assert failed_tests.junit(f) == [
        ("tests/test_a.py::test_fails", "AssertionError: assert 1 == 2"),
        ("tests/test_b.py::test_teardown", 'failed on teardown with "OSError: in use"'),
    ]


def test_tap_names_the_subtest_not_its_parent_and_keeps_the_assertion(tmp_path):
    f = tmp_path / "run.tap"
    f.write_text(TAP, encoding="utf-8")
    assert failed_tests.tap(f) == [
        ("app/tests/browser/x.test.cjs:58 inner fails", "Expected values to be strictly equal: 1 !== 2"),
        ("vscode/test/channel.test.cjs:12 times out", "test timed out after 10000ms"),
    ]


def test_cargo_names_each_failure_with_its_panic(tmp_path):
    f = tmp_path / "cargo.txt"
    f.write_text(CARGO, encoding="utf-8")
    assert failed_tests.cargo(f) == [
        ("policy::refuses_downgrade", "assertion `left == right` failed (src/policy.rs:88:9)"),
        ("cookies::expire", ""),
    ]


def test_the_table_escapes_cells_and_caps_its_rows():
    text = failed_tests.table([("a|b", "x <script> `y`")] + [(f"t{i}", "e") for i in range(60)], "UI")
    assert text.startswith("### UI: 61 failed\n")
    assert "| `a\\|b` | x &lt;script> 'y' |" in text
    assert text.count("\n| `t") == failed_tests.MAX_ROWS - 1
    assert "11 more failed" in text
    assert failed_tests.table([], "UI") == ""


def test_main_writes_the_summary_skips_missing_files_and_reports_unreadable_ones(tmp_path, monkeypatch):
    good, broken = tmp_path / "good.xml", tmp_path / "broken.xml"
    good.write_text(JUNIT, encoding="utf-8")
    broken.write_text("<testsuites><testcase", encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("FAILED_TESTS_TITLE", "Engine tests on windows-latest")
    assert failed_tests.main(["junit", str(good), str(broken), str(tmp_path / "missing.xml")]) == 0
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("### Engine tests on windows-latest: 3 failed")
    assert "| `broken.xml` | couldn't be read" in text


def test_main_writes_nothing_when_nothing_failed(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert failed_tests.main(["tap", str(tmp_path / "missing.tap")]) == 0
    assert not summary.exists()


@pytest.mark.parametrize("argv", [[], ["junit"], ["xml", "a.xml"]])
def test_main_explains_its_usage(argv, capsys):
    assert failed_tests.main(argv) == 2
    assert "failed_tests.py junit" in capsys.readouterr().err
