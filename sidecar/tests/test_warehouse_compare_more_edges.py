"""Warehouse compare and the deploy plan: text that isn't a CREATE TABLE, columns in another order,
lines only in the warehouse, scripts that aren't one definition, and a changed schema."""

from fabric_desktop import warehouse_compare as wc


def test_a_table_word_not_after_create_has_no_columns_and_no_detail():
    assert wc.columns("DROP TABLE dbo.t (a int)") is None
    assert wc.table_detail("DROP TABLE dbo.t (a int)", "CREATE TABLE dbo.t (a int)") == []


def test_the_same_columns_in_another_order_say_so():
    assert wc.table_detail("CREATE TABLE t (a int, b int)", "CREATE TABLE t (b int, a int)") == \
        ["the columns are in another order"]


def test_lines_only_in_the_warehouse_show_as_removed():
    out = wc.side_by_side("a\nb\nc", "a\nc")
    assert out["cut"] is False and {"op": "removed", "left": "b", "right": None} in out["rows"]


def test_scripts_that_are_not_one_definition_are_not_offered():
    assert wc._one_definition("view", "SELECT 1 AS x") is None             # no CREATE VIEW at the start
    assert wc._one_definition("table", "ALTER TABLE t ADD c int") is None  # not CREATE TABLE
    assert wc._one_definition("table", "CREATE TABLE t (a int") is None    # the column list never closes


def test_a_changed_schema_is_shown_but_never_offered():
    row = {"kind": "schema", "schema": "sales", "name": "sales", "state": "changed",
           "project_text": "CREATE SCHEMA sales", "warehouse_text": "CREATE SCHEMA sales AUTHORIZATION dbo"}
    [step] = wc.deploy_plan([row])
    assert step["action"] == "alter" and step["sql"] == "" and not step["ticked"] and not step["offered"]
    assert step["why"] == "a schema can't be changed, only created or dropped"
