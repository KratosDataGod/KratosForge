"""#1253: Spark SQL that sqlglot can't write exactly in DuckDB SQL is refused, not run without
the part it left out. sqlglot only logs what it drops, such as array_sort's comparator, so the
query used to answer differently from Fabric with no sign of it."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fabric_desktop import spark_compat, sql
from fabric_desktop.engines.contract import Unsupported
from fabric_desktop.engines.local_sql import DuckDBSession

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
COMPARATOR = "SELECT array_sort(array(3, 1, 2), (l, r) -> r - l) AS a"

# DuckDB SQL written with sqlglot's default, which logs and drops what it can't write. Each one
# writes something that isn't run as the person's query.
WARN_ONLY = {
    "engines/local_sql.py": "the first word of a refused statement, for the message",
}


def _literal(node: ast.expr) -> bool:
    """``exp.Literal.string(...)`` or ``exp.convert(...)``: a value sqlglot always writes whole."""
    if not isinstance(node, ast.Call):
        return False
    return ast.unparse(node.func) in ("exp.Literal.string", "exp.convert")


def test_array_sort_with_a_comparator_is_refused_not_sorted_ascending():
    with pytest.raises(ValueError, match="can't run on this PC the way it runs in Fabric, so nothing ran: "
                                         "DuckDB's (ARRAY|LIST)_SORT does not support a comparator"):
        sql.run(COMPARATOR)


def test_a_query_that_translates_exactly_still_runs():
    assert sql.run("SELECT array_sort(array(3, 1, 2)) AS a").column("a").to_pylist() == [[1, 2, 3]]


def test_a_databricks_cell_on_the_duckdb_engine_is_refused_the_same_way():
    session = DuckDBSession.__new__(DuckDBSession)
    with pytest.raises(Unsupported, match="does not support a comparator"):
        session._sql(COMPARATOR, "databricks")


def test_a_pyspark_expression_is_refused_the_same_way():
    with pytest.raises(ValueError, match="does not support a comparator"):
        spark_compat.spark_expr("array_sort(a, (l, r) -> r - l)")


def _unchecked(tree: ast.AST) -> list[int]:
    """Lines of ``x.sql("duckdb")`` / ``x.sql(dialect="duckdb")`` calls that don't raise on a dropped part."""
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sql"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        dialect = node.args[0] if node.args else kw.get("dialect")
        if (isinstance(dialect, ast.Constant) and dialect.value == "duckdb" and "unsupported_level" not in kw
                and not _literal(node.func.value)):
            lines.append(node.lineno)
    return lines


def test_duckdb_sql_is_written_through_the_check():
    found = {}
    for path in PACKAGE.rglob("*.py"):
        lines = _unchecked(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            found[path.relative_to(PACKAGE).as_posix()] = lines
    unexplained = {f: lines for f, lines in found.items() if f not in WARN_ONLY or len(lines) != 1}
    assert not unexplained, f"use sql.duckdb_sql (or list it with a reason in WARN_ONLY): {unexplained}"
    assert set(WARN_ONLY) == set(found), f"listed but no longer there: {set(WARN_ONLY) - set(found)}"
