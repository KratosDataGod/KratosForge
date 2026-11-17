"""Databricks SQL cells that read widgets (spark_sql_params.bind_widgets): ``:name``, ``${name}`` and
``getArgument('name')`` in %sql, %%sql and ``df = %sql ...``, filled from dbutils.widgets as a
Databricks notebook fills them. Widget values are strings, as in Databricks."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_sql_params
from fabric_desktop.dbutils_local import Widgets
from fabric_desktop.notebook import Kernel


@pytest.fixture()
def kernel():
    k = Kernel()
    out = k.run('dbutils.widgets.text("lim", "3")\ndbutils.widgets.text("d", "2026-01-02")')
    assert out["status"] == "ok"
    return k


def result(kernel, code):
    out = kernel.run(code)
    texts = [str(o.get("text") or o.get("evalue") or "") for o in out["outputs"]]
    tables = [o["rows"] for o in out["outputs"] if o.get("type") == "table"]
    return out["status"], (tables[0] if tables else None), " ".join(texts)


@pytest.mark.parametrize("header", ["%sql", "%%sql"])
def test_a_named_marker_is_the_widgets_value_as_a_string(kernel, header):
    status, rows, _ = result(kernel, f"{header}\nSELECT :lim AS v, typeof(:lim) AS t")
    assert status == "ok" and rows == [["3", "string"]]


def test_legacy_dollar_braces_are_replaced_as_written_and_unknown_names_are_kept(kernel):
    status, rows, _ = result(kernel, "%sql\nSELECT ${lim} + 1 AS v, '${d}' AS d, '${not_a_widget}' AS n")
    assert status == "ok" and rows == [[4, "2026-01-02", "${not_a_widget}"]]


def test_get_argument_reads_the_widget_or_its_default(kernel):
    status, rows, _ = result(kernel, "%sql\nSELECT getArgument('lim') AS v, getArgument('x', 'dflt') AS w")
    assert status == "ok" and rows == [["3", "dflt"]]


def test_a_value_set_in_the_bar_is_used(kernel):
    kernel.widget_values = {"lim": "7"}  # what the widgets bar sends with the run
    assert result(kernel, "%sql\nSELECT :lim AS v")[1] == [["7"]]


def test_the_sql_line_magic_reads_widgets_too(kernel):
    out = kernel.run("x = %sql SELECT :lim AS v\nprint(x.collect()[0][0])")
    assert out["status"] == "ok" and out["outputs"][0]["text"].strip() == "3"


@pytest.mark.parametrize("sql, message", [("SELECT :missing AS v", "The SQL uses :missing. No widget named missing"),
                                          ("SELECT getArgument('missing') AS v", "no widget named missing")])
def test_a_widget_that_doesnt_exist_is_named_in_the_error(kernel, sql, message):
    status, _, text = result(kernel, f"%sql\n{sql}")
    assert status == "error" and message in text and "Create it first" in text


def test_markers_in_strings_and_comments_are_left_alone(kernel):
    status, rows, _ = result(kernel, "%sql\nSELECT ':lim' AS s -- :missing")
    assert status == "ok" and rows == [[":lim"]]


def test_a_value_cant_change_the_query():
    w = Widgets()
    w.text("v", "x' OR '1'='1")
    assert spark_sql_params.bind_widgets("SELECT :v", w) == "SELECT 'x\\' OR \\'1\\'=\\'1'"


def test_without_widgets_the_sql_is_unchanged():
    assert spark_sql_params.bind_widgets("SELECT ${a}, :b", None) == "SELECT ${a}, :b"


def test_get_argument_reads_adjacent_strings_as_spark_joins_them():
    """Spark joins 'it' 's' (and so 'it''s') into one string: Apache Spark 3.5.5 gives 'its'."""
    from fabric_desktop import spark_sql_params

    class Widgets:
        defined = {"x": ""}
        passed: dict = {}

        def getArgument(self, name, default=None):
            return default

    assert spark_sql_params.bind_widgets("SELECT getArgument('x', 'it''s') v", Widgets()) == "SELECT 'its' v"


def test_unreadable_sql_goes_to_the_engine_unchanged():
    from fabric_desktop import spark_sql_params

    class Widgets:
        defined: dict = {}
        passed: dict = {}

    assert spark_sql_params.bind_widgets("SELECT 'unterminated", Widgets()) == "SELECT 'unterminated"
