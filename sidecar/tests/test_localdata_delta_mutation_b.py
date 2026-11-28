"""Local Delta: a CREATE TABLE in another format is not a Delta write, and a table read as of a
version keeps its schema, or None without one (localdata/delta.py)."""

from __future__ import annotations

import sqlglot

from fabric_desktop.localdata import delta as D


def test_a_create_table_using_parquet_is_not_a_delta_write():
    tree = sqlglot.parse_one("CREATE TABLE s.t USING PARQUET AS SELECT 1 AS a", read="spark")
    assert D.parse_tree(tree, lambda *a: True) is None
    tree = sqlglot.parse_one("CREATE TABLE s.t USING DELTA AS SELECT 1 AS a", read="spark")
    assert D.parse_tree(tree, lambda *a: True) is not None


def test_a_read_as_of_a_version_keeps_the_tables_schema():
    _, found = D.rewrite_as_of("SELECT * FROM sales.orders AT (VERSION => 1)")
    assert (found[0].schema, found[0].table, found[0].version) == ("sales", "orders", 1)
    _, found = D.rewrite_as_of("SELECT * FROM orders AT (VERSION => 2)")
    assert found[0].schema is None
