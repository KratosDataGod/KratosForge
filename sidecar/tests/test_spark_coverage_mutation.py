"""Mutation batch 365: spark_coverage.py (docs/quality/mutation-testing.md). With the tests that read
docs/spark-functions.md left out, nothing checked how values are compared (NaN, infinities, instants,
structs, unordered lists), how an example that stops is told apart, or markdown() itself. These do,
with a stand-in Spark session, so no example runs here."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import duckdb

from fabric_desktop import spark_coverage as sc

SIDECAR = Path(__file__).resolve().parents[1]


class Row:
    """Enough of pyspark's Row: asDict(recursive=True) turns nested rows into dicts."""

    def __init__(self, **fields):
        self.fields = fields

    def asDict(self, recursive=False):  # noqa: N802 - pyspark's name
        return {k: (v.asDict(True) if recursive and isinstance(v, Row) else v) for k, v in self.fields.items()}


class Spark:
    def __init__(self, rows=None, error: BaseException | None = None):
        self.rows, self.error, self.asked = rows or [], error, []

    def sql(self, example):
        self.asked.append(example)
        if self.error is not None:
            raise self.error
        return type("Df", (), {"collect": lambda _self: list(self.rows)})()


# ---- canonical values


def test_nan_and_the_infinities_have_names_of_their_own():
    assert sc.canonical(float("nan")) == "float:nan"
    assert sc.canonical(float("inf")) == "float:inf"
    assert sc.canonical(float("-inf")) == "float:-inf"


def test_an_instant_with_a_zone_is_compared_in_utc():
    at = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert sc.canonical(at) == "timestamp:2026-01-02T01:04:05"


def test_maps_rows_and_other_values():
    assert sc.canonical({"b": 2, "a": 1}) == {"map": [["a", 1], ["b", 2]]}
    inner = Row(x=1)
    assert sc.canonical(Row(s=inner, n=2)) == {"struct": [["s", {"struct": [["x", 1]]}], ["n", 2]]}
    assert sc.canonical(complex(1, 2)) == "complex:(1+2j)"


def test_only_an_unordered_function_has_its_lists_sorted():
    rows = [([3, 1, 2],)]
    assert sc.rows_value(rows, "collect_set") == [[[1, 2, 3]]]
    assert sc.rows_value(rows, "collect_list") == [[[3, 1, 2]]]


# ---- refusals and checks


def test_a_refusal_is_this_pcs_own_message_and_not_duckdbs():
    assert sc._refusal(NotImplementedError("x")) is True
    assert sc._refusal(RuntimeError("It can't run on this PC the way it runs in Fabric")) is True
    assert sc._refusal(duckdb.Error("can't run on this PC")) is False
    assert sc._refusal(RuntimeError("Binder Error")) is False


def test_a_function_without_an_example_isnt_checked_and_says_why_when_known():
    assert sc.check({"name": "no_example"}, Spark()) == ("not checked", "")
    assert sc.check({"name": "raise_error"}, Spark()) == ("not checked", sc.UNCHECKED["raise_error"])


def test_a_probe_runs_but_isnt_compared():
    spark = Spark(rows=[("ACTION", False)])
    assert sc.check({"name": "sql_keywords", "value": [["x"]]}, spark) == ("not checked", "")
    assert spark.asked == [sc.PROBES["sql_keywords"]]


def test_an_example_that_stops_is_refused_or_fails_with_its_first_line():
    f = {"name": "f", "example": "SELECT f()", "value": [[1]]}
    assert sc.check(f, Spark(error=NotImplementedError("No f on this PC.\nmore"))) == ("refused", "No f on this PC.")
    assert sc.check(f, Spark(error=RuntimeError("x" * 200 + "\nmore"))) == ("fails", "x" * 160)
    assert sc.check(f, Spark(error=RuntimeError(""))) == ("fails", "RuntimeError")
    # a session function names what Fabric gives rather than the error
    sess = {"name": "current_user", "example": "SELECT current_user()", "value": [["u"]]}
    assert sc.check(sess, Spark(error=NotImplementedError("no"))) == ("refused", sc.SESSION["current_user"])


def test_an_uncompared_example_that_stops_as_spark_does_isnt_checked():
    f = {"name": "raise_error", "example": "SELECT raise_error('m')", "spark_error": "m"}
    assert sc.check(f, Spark(error=RuntimeError("m"))) == ("not checked", sc.UNCHECKED["raise_error"])
    # without spark_error it fails as any other error
    assert sc.check({"name": "g", "example": "SELECT g()"}, Spark(error=RuntimeError("boom"))) == ("fails", "boom")
    # and a varying function that is refused stays refused
    v = {"name": "uniform", "example": "SELECT uniform(1, 2)", "spark_error": "x"}
    assert sc.check(v, Spark(error=NotImplementedError("No uniform on this PC.")))[0] == "refused"


def test_a_varying_example_that_runs_isnt_compared():
    assert sc.check({"name": "uniform", "example": "SELECT uniform(1, 2)", "value": [[1]]}, Spark(rows=[(7,)])) == (
        "not checked", "")


def test_a_different_value_is_shown_both_ways_cut_to_80():
    f = {"name": "f", "example": "SELECT f()", "value": [["s" * 100]]}
    status, note = sc.check(f, Spark(rows=[("t" * 100,)]))
    assert status == "differs"
    assert note == f'{"[[" + chr(34) + "t" * 77} here, {"[[" + chr(34) + "s" * 77} in Spark'
    assert sc.check({"name": "f", "example": "SELECT f()", "value": [[1]]}, Spark(rows=[(1,)])) == ("runs", "")


def test_coverage_lists_each_function_with_its_group_example_and_status(monkeypatch):
    import fabric_desktop.notebook as nb
    spark = Spark(rows=[(1,)])
    monkeypatch.setattr(nb, "Kernel", lambda: type("K", (), {"namespace": {"spark": spark}})())
    monkeypatch.setattr(sc, "load", lambda: [{"name": "one", "example": "SELECT 1", "value": [[1]], "group": "math_funcs"},
                                             {"name": "sql_keywords"}])
    assert sc.coverage() == [
        {"name": "one", "group": "math_funcs", "example": "SELECT 1", "status": "runs", "note": ""},
        {"name": "sql_keywords", "group": "other", "example": sc.PROBES["sql_keywords"], "status": "not checked",
         "note": ""}]


# ---- the page


def test_markdown_counts_each_status_and_lists_groups_in_order():
    rows = [{"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": ""},
            {"name": "x", "group": "zz_new_funcs", "example": "", "status": "differs", "note": "a|b\nc"},
            {"name": "concat", "group": "string_funcs", "example": "SELECT 'a' || 'b'", "status": "fails", "note": ""}]
    page = sc.markdown(rows).split("\n")
    assert page[page.index("| Status | Functions |") + 2:page.index("| Status | Functions |") + 8] == [
        "| Runs | 1 |", "| Differs | 1 |", "| Refused | 0 |", "| Fails | 1 |", "| Not checked | 0 |", "| **All** | **3** |"]
    heads = [line for line in page if line.startswith("## ")]
    assert heads == ["## Math", "## String", "## Zz new"]
    assert "| `abs` | Runs | `SELECT abs(-1)` |  |" in page
    assert "| `x` | Differs |  | a\\|b c |" in page
    assert "| `concat` | Fails | `SELECT 'a' \\|\\| 'b'` |  |" in page
    assert page[-1] == ""


def test_the_page_is_docs_spark_functions_md_at_the_repository_root():
    assert sc.DOC == SIDECAR.parent / "docs" / "spark-functions.md"


def test_importing_the_module_prints_and_writes_nothing():
    got = subprocess.run([sys.executable, "-c", "import fabric_desktop.spark_coverage"], cwd=SIDECAR,
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0 and got.stdout == "" and got.stderr == ""
