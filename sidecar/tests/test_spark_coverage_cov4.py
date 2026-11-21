"""spark_coverage.py: canonical values for special floats and other cells, and check()'s statuses with a
fake Spark session, without starting a notebook kernel."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fabric_desktop import spark_coverage as sc


class Rows:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.queries = rows or [], error, []

    def sql(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self

    def collect(self):
        return self.rows


@pytest.mark.parametrize("value, expected", [
    (float("nan"), "float:nan"),
    (float("inf"), "float:inf"),
    (float("-inf"), "float:-inf"),
    (0.1 + 0.2, "float:0.3"),
    (True, True),
    (Decimal("1.50"), "decimal:1.50"),
    (dt.datetime(2024, 1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=1))), "timestamp:2024-01-01T00:00:00"),
    (dt.date(2024, 2, 29), "date:2024-02-29"),
    (dt.time(1, 2, 3), "time:01:02:03"),
    (dt.timedelta(minutes=1), "interval:60.0"),
    (memoryview(b"\x01\xff"), "binary:01ff"),
    ({"b": 1, "a": None}, {"map": [["a", None], ["b", 1]]}),
    ((1, [2]), [1, [2]]),
    (complex(1, 2), "complex:(1+2j)"),
])
def test_canonical(value, expected):
    assert sc.canonical(value) == expected


def test_a_row_is_a_struct_of_names_and_values():
    class Row(tuple):
        def asDict(self, recursive=False):
            return {"x": self[0], "y": self[1]}
    assert sc.canonical(Row((1, 2.5))) == {"struct": [["x", 1], ["y", "float:2.5"]]}


def test_a_function_with_no_example_or_probe_is_not_checked_and_nothing_runs():
    spark = Rows()
    assert sc.check({"name": "current_user"}, spark) == ("not checked", sc.UNCHECKED["current_user"])
    assert sc.check({"name": "no_such", "example": ""}, spark) == ("not checked", "")
    assert spark.queries == []


def test_a_value_other_than_sparks_differs_with_both_values_in_the_note():
    spark = Rows([(2,)])
    status, note = sc.check({"name": "abs", "example": "SELECT abs(-1)", "value": [[1]]}, spark)
    assert status == "differs" and note == "[[2]] here, [[1]] in Spark"
    assert sc.check({"name": "abs", "example": "SELECT abs(-1)", "value": [[2]]}, spark) == ("runs", "")


def test_a_probe_runs_but_isnt_compared():
    spark = Rows([("ADD", False)])
    assert sc.check({"name": "sql_keywords", "value": [["x"]]}, spark) == ("not checked", "")
    assert spark.queries == [sc.PROBES["sql_keywords"]]


@pytest.mark.parametrize("error, status, note", [
    (NotImplementedError("x can't run on this PC"), "refused", "x can't run on this PC"),
    (RuntimeError("Can't run on this PC the way it does in Fabric"), "refused", "Can't run on this PC the way it does in Fabric"),
    (RuntimeError("\n"), "fails", "RuntimeError"),  # an error with no message is named by its type
])
def test_errors_are_refusals_or_failures(error, status, note):
    assert sc.check({"name": "abs", "example": "SELECT abs(-1)", "value": [[1]]}, Rows(error=error)) == (status, note)


def test_a_stop_spark_also_makes_isnt_a_failure_and_session_notes_win():
    raised = Rows(error=RuntimeError("boom"))
    assert sc.check({"name": "raise_error", "example": "SELECT raise_error('x')", "spark_error": "x"}, raised) == \
        ("not checked", sc.UNCHECKED["raise_error"])
    assert sc.check({"name": "version", "example": "SELECT version()", "value": [["3.5"]]},
                    Rows(error=NotImplementedError("no"))) == ("refused", sc.SESSION["version"])


def test_order_free_lists_compare_sorted():
    assert sc.rows_value([([3, 1, 2],)], "collect_set") == [[[1, 2, 3]]]
    assert sc.rows_value([([3, 1, 2],)], "collect_list") == [[[3, 1, 2]]]


def test_markdown_counts_and_escapes():
    rows = [{"name": "a|b", "group": "zz_new_funcs", "example": "", "status": "differs", "note": "x\ny"},
            {"name": "abs", "group": "math_funcs", "example": "SELECT 1", "status": "runs", "note": ""}]
    text = sc.markdown(rows)
    assert "| Differs | 1 |" in text and "| Runs | 1 |" in text and "| **All** | **2** |" in text
    assert "## Zz new" in text and "## Math" in text
    assert "| `a\\|b` | Differs |  | x y |" in text
