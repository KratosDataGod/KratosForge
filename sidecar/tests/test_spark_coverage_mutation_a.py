"""spark_coverage.py paths that its first mutation run (batch 382, docs/quality/mutation-testing.md)
showed no test checked: how a value is made comparable, how an example's status is decided, and the
page markdown() writes. Spark here is a fake where the session doesn't matter."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import spark_coverage as sc


class Row:
    def __init__(self, **kw):
        self.kw = kw

    def asDict(self, recursive=False):
        return dict(self.kw)


@pytest.mark.parametrize("value, want", [
    (None, None), (True, True), ("x", "x"), (7, 7),
    (float("nan"), "float:nan"), (float("inf"), "float:inf"), (float("-inf"), "float:-inf"),
    (1 / 3, "float:0.333333333333"), (2.5, "float:2.5"),
    (Decimal("1.50"), "decimal:1.50"),
    (dt.datetime(2024, 1, 2, 3, 4, 5), "timestamp:2024-01-02T03:04:05"),
    (dt.datetime(2024, 1, 2, 5, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=2))), "timestamp:2024-01-02T03:04:05"),
    (dt.date(2024, 1, 2), "date:2024-01-02"),
    (dt.time(3, 4, 5), "time:03:04:05"),
    (dt.timedelta(minutes=1, seconds=30), "interval:90.0"),
    (b"\x01\xab", "binary:01ab"), (bytearray(b"\x02"), "binary:02"), (memoryview(b"\x03"), "binary:03"),
    ({"b": 2, "a": 1.5}, {"map": [["a", "float:1.5"], ["b", 2]]}),
    (Row(x=1, y=Decimal("2")), {"struct": [["x", 1], ["y", "decimal:2"]]}),
    ([1, (2.0, None)], [1, ["float:2", None]]),
    (complex(1, 2), "complex:(1+2j)"),
])
def test_each_kind_of_value_compares_as_plain_json(value, want):
    assert sc.canonical(value) == want


def test_rows_compare_sorted_and_an_order_free_list_sorted_inside():
    rows = [[[3, 1, 2], "b"], [[2, 1], "a"]]
    assert sc.rows_value(rows) == [[[2, 1], "a"], [[3, 1, 2], "b"]]
    assert sc.rows_value(rows, "collect_set") == [[[1, 2, 3], "b"], [[1, 2], "a"]]  # sorted as JSON text
    assert sc.rows_value([], "collect_set") == []


def test_a_refusal_is_this_pcs_own_message_never_a_duckdb_error():
    assert sc._refusal(NotImplementedError("anything"))
    assert sc._refusal(ValueError("This PC can't run it the way Fabric does"))
    assert not sc._refusal(duckdb.Error("can't on this PC"))
    assert not sc._refusal(ValueError("something else"))


class Spark:
    def __init__(self, answer):
        self.answer, self.ran = answer, []

    def sql(self, query):
        self.ran.append(query)
        if isinstance(self.answer, BaseException):
            raise self.answer
        rows = self.answer
        return type("Df", (), {"collect": lambda self: rows})()


def test_a_function_without_an_example_is_not_checked_with_its_note():
    spark = Spark([[1]])
    assert sc.check({"name": "raise_error"}, spark) == ("not checked", sc.UNCHECKED["raise_error"])
    assert sc.check({"name": "nothing_known"}, spark) == ("not checked", "")
    assert spark.ran == []


def test_a_probe_runs_but_is_never_compared():
    spark = Spark([["ALL", True]])
    assert sc.check({"name": "sql_keywords", "value": [["x"]]}, spark) == ("not checked", "")
    assert spark.ran == [sc.PROBES["sql_keywords"]]


def test_an_example_with_spark_s_value_runs_and_another_value_differs():
    f = {"name": "abs", "example": "SELECT abs(-1)", "value": [[1]]}
    assert sc.check(f, Spark([[1]])) == ("runs", "")
    status, note = sc.check(f, Spark([[2]]))
    assert status == "differs" and note == "[[2]] here, [[1]] in Spark"


def test_a_differs_note_cuts_both_values_to_80_characters():
    f = {"name": "repeat", "example": "SELECT repeat('a', 200)", "value": [["b" * 200]]}
    status, note = sc.check(f, Spark([["a" * 200]]))
    here, spark = note.split(" here, ")
    assert status == "differs" and len(here) == 80 and spark == '[["' + "b" * 77 + " in Spark"


def test_a_varying_function_or_one_without_a_value_is_not_checked_but_still_runs():
    assert sc.check({"name": "uniform", "example": "SELECT uniform(1, 2)", "value": [[1]]}, Spark([[9]])) == ("not checked", "")
    assert sc.check({"name": "abs", "example": "SELECT abs(-1)"}, Spark([[9]])) == ("not checked", "")


def test_an_error_is_refused_or_fails_with_its_first_line():
    f = {"name": "abs", "example": "SELECT abs(-1)", "value": [[1]]}
    assert sc.check(f, Spark(NotImplementedError("abs can't run on this PC\nmore"))) == ("refused", "abs can't run on this PC")
    assert sc.check(f, Spark(duckdb.Error("Binder Error: x" + "y" * 300))) == ("fails", ("Binder Error: x" + "y" * 300)[:160])
    assert sc.check(f, Spark(RuntimeError(""))) == ("fails", "RuntimeError")


def test_a_session_function_refused_says_why_in_fabric():
    f = {"name": "version", "example": "SELECT version()"}
    assert sc.check(f, Spark(NotImplementedError("no"))) == ("refused", sc.SESSION["version"])


def test_an_uncompared_example_that_stops_as_in_spark_is_not_checked():
    f = {"name": "raise_error", "example": "SELECT raise_error('x')", "spark_error": "x"}
    assert sc.check(f, Spark(duckdb.Error("Invalid Input Error: x"))) == ("not checked", sc.UNCHECKED["raise_error"])
    compared = dict(f, value=[[None]])
    assert sc.check(compared, Spark(duckdb.Error("Invalid Input Error: x"))) == ("fails", "Invalid Input Error: x")
    assert sc.check(f, Spark(NotImplementedError("refused on this PC"))) == ("refused", "refused on this PC")
    plain = {"name": "raise_error", "example": "SELECT raise_error('x')"}
    assert sc.check(plain, Spark(duckdb.Error("Invalid Input Error: x"))) == ("fails", "Invalid Input Error: x")


def test_the_page_counts_each_status_and_lists_each_group_in_order():
    rows = [{"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": ""},
            {"name": "a|b", "group": "math_funcs", "example": "", "status": "not checked", "note": "line\nbreak"},
            {"name": "x", "group": "zz_new_funcs", "example": "SELECT x", "status": "refused", "note": "no"},
            {"name": "avg", "group": "agg_funcs", "example": "SELECT avg(1)", "status": "differs", "note": "n"}]
    page = sc.markdown(rows)
    lines = page.split("\n")
    assert lines[0] == "# Spark functions on this PC" and lines[-1] == ""
    assert "| Runs | 1 |" in lines and "| Differs | 1 |" in lines and "| Refused | 1 |" in lines
    assert "| Fails | 0 |" in lines and "| Not checked | 1 |" in lines and "| **All** | **4** |" in lines
    heads = [line for line in lines if line.startswith("## ")]
    assert heads == ["## Aggregate", "## Math", "## Zz new"]
    assert "| `abs` | Runs | `SELECT abs(-1)` |  |" in lines
    assert "| `a\\|b` | Not checked |  | line break |" in lines
    at = lines.index("## Math")
    assert lines[at + 1:at + 4] == ["", "| Function | Status | Example | Note |", "| --- | --- | --- | --- |"]
    assert lines[at + 4].startswith("| `abs` |") and lines[at + 5].startswith("| `a\\|b` |") and lines[at + 6] == ""


def test_importing_the_module_runs_nothing_and_writes_no_page(monkeypatch):
    import importlib.util

    from fabric_desktop import jsonfile, notebook

    def never(*a, **k):
        raise AssertionError("importing spark_coverage ran the page writer")

    monkeypatch.setattr(jsonfile, "write_text", never)
    monkeypatch.setattr(notebook, "Kernel", never)
    spec = importlib.util.spec_from_file_location("fabric_desktop._spark_coverage_import_check", sc.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # the page is the repository's docs/spark-functions.md, beside sidecar/
    assert module.DOC.name == "spark-functions.md" and module.DOC.parent.name == "docs"
    assert (module.DOC.parent.parent / "sidecar" / "fabric_desktop" / "spark_coverage.py").is_file()


def test_a_struct_inside_a_struct_stays_a_struct():
    from duckdb.experimental.spark.sql.types import Row as SparkRow

    assert sc.canonical(SparkRow(a=SparkRow(b=1))) == {"struct": [["a", {"struct": [["b", 1]]}]]}


def test_coverage_lists_each_function_with_its_group_and_example(monkeypatch):
    from fabric_desktop import notebook

    class Kernel:
        namespace = {"spark": "the session"}

    seen = []
    monkeypatch.setattr(notebook, "Kernel", Kernel)
    monkeypatch.setattr(sc, "load", lambda: [{"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)"},
                                             {"name": "sql_keywords"}])
    monkeypatch.setattr(sc, "check", lambda f, spark: seen.append(spark) or ("runs", "ok"))
    assert sc.coverage() == [
        {"name": "abs", "group": "math_funcs", "example": "SELECT abs(-1)", "status": "runs", "note": "ok"},
        {"name": "sql_keywords", "group": "other", "example": sc.PROBES["sql_keywords"], "status": "runs", "note": "ok"}]
    assert seen == ["the session", "the session"]
