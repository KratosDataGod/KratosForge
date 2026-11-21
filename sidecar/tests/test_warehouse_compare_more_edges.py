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


def test_comments_before_create_are_read_as_before():
    # A /* comment ends at the first */ after which the rest reads, more comments first, so the second
    # CREATE here is the one kept; one that never reaches CREATE isn't a definition.
    assert wc._as_create_or_alter("/*-x*//*/CREATE VIEW v*/create view w AS SELECT 1") == \
        "/*-x*//*/CREATE VIEW v*/CREATE OR ALTER VIEW w AS SELECT 1"
    assert wc._as_create_or_alter("-- a\n/* b */ /* c */ b */\n create  procedure p AS SELECT 1") == \
        "-- a\n/* b */ /* c */ b */\n CREATE OR ALTER PROCEDURE p AS SELECT 1"
    assert wc._as_create_or_alter("/* a */ x CREATE VIEW v AS SELECT 1") is None
    assert wc._as_create_or_alter("-- no line break CREATE VIEW v") is None


def _quickest(text, tries=5):
    import time

    best = float("inf")
    for _ in range(tries):
        started = time.perf_counter()
        assert wc._as_create_or_alter(text) is None
        best = min(best, time.perf_counter() - started)
    return best


def test_many_comments_before_something_that_isnt_create_are_turned_down_in_time_that_grows_with_them():
    # Every way of splitting a run of comments was tried, twice as many for each more */: 26 took 10 s.
    # Now 8 times the comments take about 8 times as long.
    assert _quickest("/**/" * 16 + "x") / max(_quickest("/**/" * 2 + "x"), 50e-6) < 30
    assert _quickest("/*" + "*/--" * 4000 + "x") / max(_quickest("/*" + "*/--" * 500 + "x"), 50e-6) < 30
