"""Mutation batch 366: databricks_coverage.py (docs/quality/mutation-testing.md). With the tests that
read docs/databricks-functions.md left out, nothing checked how a listed check's value, pattern or
error is judged, how a probe's errors are reported, which listed checks run here, or markdown()
itself. Most of these use a stand-in for ``run`` so no SQL is needed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop import databricks_coverage as dc
from fabric_desktop import spark_coverage as sc

SIDECAR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def con():
    return dc.connect()


def fake_run(monkeypatch, answer):
    """``run`` replaced: ``answer(code)`` gives the rows or raises."""
    asked: list[str] = []

    def run(code, _con):
        asked.append(code)
        return answer(code)
    monkeypatch.setattr(dc, "run", run)
    return asked


# ---- the listed checks that run on this PC


@pytest.mark.parametrize("name", ["cube", "current_timezone", "getdate", "monotonically_increasing_id", "raise_error"])
def test_these_listed_checks_give_databricks_answer_here(con, name):
    assert dc._listed(dc.DATABRICKS_CHECKS[name], con)[0] == "runs"


def test_an_unordered_aggregate_has_its_list_sorted(con):
    assert dc.run("SELECT collect_set(x) FROM VALUES (3), (1), (2) t(x)", con) == [[[1, 2, 3]]]
    assert dc.run("SELECT array(3, 1, 2)", con) == [[[3, 1, 2]]]


# ---- probes


def test_a_function_is_probed_with_up_to_four_null_arguments(monkeypatch):
    def answer(code):
        if code.count("NULL") == 4:
            return [[None]]
        raise RuntimeError("Binder Error: no such overload")
    asked = fake_run(monkeypatch, answer)
    assert dc.probe({"name": "f"}, None) == ("not checked", "")
    assert asked[-1] == "SELECT f(NULL, NULL, NULL, NULL)"

    def five(code):
        if code.count("NULL") == 5:
            return [[None]]
        raise RuntimeError("Binder Error: no such overload")
    asked = fake_run(monkeypatch, five)
    assert dc.probe({"name": "f"}, None)[0] == "fails"
    assert asked == ["SELECT f()", "SELECT f(NULL)", "SELECT f(NULL, NULL)", "SELECT f(NULL, NULL, NULL)",
                     "SELECT f(NULL, NULL, NULL, NULL)"]


def test_a_refused_probe_gives_the_refusals_first_line_cut_to_160(monkeypatch):
    def answer(code):
        if "NULL" in code:
            raise NotImplementedError("r" * 200 + "\nmore")
        raise RuntimeError("Binder Error")
    fake_run(monkeypatch, answer)
    assert dc.probe({"name": "f"}, None) == ("refused", "r" * 160)


def test_a_failing_probe_gives_its_first_error_cut_to_160(monkeypatch):
    def answer(code):
        raise RuntimeError("e" * 200 + "\nmore")
    fake_run(monkeypatch, answer)
    assert dc.probe({"name": "f", "probe": "SELECT f(1 WITHIN x)"}, None) == ("fails", "e" * 160)


# ---- errors and listed checks


def test_a_stop_is_refused_or_fails_with_its_first_line_or_its_type():
    assert dc._stopped(NotImplementedError("No f on this PC.\nmore")) == ("refused", "No f on this PC.")
    assert dc._stopped(RuntimeError("x" * 200)) == ("fails", "x" * 160)
    assert dc._stopped(RuntimeError("  ")) == ("fails", "RuntimeError")


def test_a_listed_error_runs_only_when_the_message_matches_and_isnt_a_refusal(monkeypatch):
    listed = {"example": "SELECT raise_error('m')", "error": "custom message", "note": "n"}

    def stop(exc):
        def answer(_code):
            raise exc
        return answer
    fake_run(monkeypatch, stop(RuntimeError("custom message here")))
    assert dc._listed(listed, None) == ("runs", "n")
    fake_run(monkeypatch, stop(RuntimeError("another")))
    assert dc._listed(listed, None) == ("fails", "another")
    fake_run(monkeypatch, stop(NotImplementedError("custom message on this PC")))
    assert dc._listed(listed, None) == ("refused", "custom message on this PC")
    fake_run(monkeypatch, lambda _code: [["ok"]])
    assert dc._listed({"example": "x", "error": "m"}, None) == ("differs", '[["ok"]] here; Databricks stops with an error')
    assert dc._listed(listed, None) == ("differs", "n")


def test_a_pattern_needs_a_value_and_every_value_to_match(monkeypatch):
    listed = {"example": "x", "pattern": r"\d+"}
    fake_run(monkeypatch, lambda _code: [])
    assert dc._listed(listed, None) == ("differs", '[] here, "\\\\d+" in Databricks')
    fake_run(monkeypatch, lambda _code: [["12"], ["3"]])
    assert dc._listed(listed, None) == ("runs", "")
    fake_run(monkeypatch, lambda _code: [["12", "a"]])
    assert dc._listed(listed, None)[0] == "differs"


def test_a_listed_value_is_compared_in_any_order_and_shown_cut_to_80(monkeypatch):
    fake_run(monkeypatch, lambda _code: [[1], [2]])
    assert dc._listed({"example": "x", "value": [[2], [1]]}, None) == ("runs", "")
    fake_run(monkeypatch, lambda _code: [["t" * 100]])
    status, note = dc._listed({"example": "x", "value": [["s" * 100]]}, None)
    assert status == "differs"
    assert note == '[["' + "t" * 77 + ' here, [["' + "s" * 77 + " in Databricks"


# ---- coverage and the page


def test_an_example_is_the_listed_ones_then_the_functions_own_or_none():
    assert dc._example({"name": "getdate", "example": "other"}) == dc.DATABRICKS_CHECKS["getdate"]["example"]
    assert dc._example({"name": "f", "example": "SELECT f()"}) == "SELECT f()"
    assert dc._example({"name": "f"}) == ""


def test_coverage_gives_a_spark_function_without_a_group_other(monkeypatch):
    monkeypatch.setattr(dc, "connect", lambda: None)
    monkeypatch.setattr(sc, "load", lambda: [{"name": "f"}])
    monkeypatch.setattr(dc, "load", lambda: [])
    assert dc.coverage() == [{"name": "f", "group": "other", "example": "", "status": "not checked", "note": ""}]


def test_markdown_counts_statuses_and_puts_databricks_groups_last():
    rows = [{"name": "a", "group": "databricks:ip", "example": "", "status": "runs", "note": ""},
            {"name": "b", "group": "math_funcs", "example": "SELECT b()", "status": "fails", "note": "x|y\nz"},
            {"name": "c", "group": "databricks:zz", "example": "", "status": "fails", "note": ""},
            {"name": "d", "group": "new_funcs", "example": "", "status": "differs", "note": ""}]
    page = dc.markdown(rows).split("\n")
    at = page.index("| Status | Functions |")
    assert page[at + 2:at + 8] == ["| Runs | 1 |", "| Differs | 1 |", "| Refused | 0 |", "| Fails | 2 |",
                                   "| Not checked | 0 |", "| **All** | **4** |"]
    assert [line for line in page if line.startswith("## ")] == [
        "## Math", "## New", "## Databricks only: IP addresses", "## Databricks:zz"]
    assert "| `b` | Fails | `SELECT b()` | x\\|y z |" in page
    assert "| `a` | Runs |  |  |" in page
    assert page[-1] == ""


def test_the_page_is_docs_databricks_functions_md_at_the_repository_root():
    assert dc.DOC == SIDECAR.parent / "docs" / "databricks-functions.md"


def test_importing_the_module_prints_and_writes_nothing():
    got = subprocess.run([sys.executable, "-c", "import fabric_desktop.databricks_coverage"], cwd=SIDECAR,
                         capture_output=True, text=True, timeout=300)
    assert got.returncode == 0 and got.stdout == "" and got.stderr == ""


def test_a_spark_function_that_differs_is_shown_both_ways_cut_to_80(monkeypatch):
    fake_run(monkeypatch, lambda _code: [["t" * 100]])
    status, note = dc.check({"name": "f", "example": "SELECT f()", "value": [["s" * 100]]}, None)
    assert status == "differs"
    assert note == '[["' + "t" * 77 + ' here, [["' + "s" * 77 + " in Databricks"
