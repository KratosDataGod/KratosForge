"""Mutation-testing follow-up for warehouse compare and deploy (warehouse_compare.py): where a
built-in type's name folds and where it stays a column's name, quoted names with doubled quotes,
the side-by-side cut and uneven changed blocks, the order of states and of deploy steps, the
one-definition check for views, functions, tables and schemas, and the preview's precondition."""

from __future__ import annotations

import pytest

from fabric_desktop import warehouse_compare as wc


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


# ---- tokens: where a SOFT word folds


def test_a_type_after_as_folds_only_before_a_closing_bracket():
    assert wc.tokens("CAST(x AS Date)") == ["cast", "(", "x", "as", "date", ")"]
    # an alias called Date is a name, so its case counts
    assert wc.tokens("SELECT x AS Date FROM t") == ["select", "x", "as", "Date", "from", "t"]


def test_a_type_after_an_opening_bracket_folds_only_before_a_comma():
    assert wc.tokens("CONVERT(Date, x)") == ["convert", "(", "date", ",", "x", ")"]
    assert wc.tokens("SELECT Date, b FROM t") == ["select", "Date", ",", "b", "from", "t"]


def test_a_soft_word_second_in_the_script_is_not_read_as_a_columns_type():
    # prev2 is only looked at from the third token on: "(" first doesn't make Date a type
    assert wc.tokens("(Date") == ["(", "Date"]
    assert wc.tokens("(a Date") == ["(", "a", "date"]


def test_a_soft_word_last_in_the_script_has_nothing_after_it():
    assert wc.tokens("SELECT Count") == ["select", "Count"]
    assert wc.tokens("RETURNS Int") == ["returns", "int"]


def test_doubled_quotes_and_brackets_inside_a_quoted_name():
    assert wc.tokens('"a""b"') == ['[a"b]']
    assert wc.tokens("[a]]b]") == ["[a]b]"]
    assert wc.tokens("[ab]") == ["ab"] and wc.tokens('"ab"') == ["ab"]


def test_strings_keep_their_case_and_quotes():
    assert wc.tokens("SELECT N'Select', 'FROM'") == ["select", "N'Select'", ",", "'FROM'"]


# ---- side by side


def test_the_side_by_side_view_is_cut_after_max_rows():
    left = "\n".join(f"l{i}" for i in range(3001))
    out = wc.side_by_side(left, left)
    assert len(out["rows"]) == 3000 and out["cut"] is True
    exact = "\n".join(f"l{i}" for i in range(3000))
    assert wc.side_by_side(exact, exact)["cut"] is False


def test_a_changed_block_of_uneven_length_pairs_lines_then_leaves_a_side_empty():
    rows = wc.side_by_side("a\nX\nc", "a\nY\nZ\nc")["rows"]
    assert rows == [{"op": "same", "left": "a", "right": "a"},
                    {"op": "changed", "left": "X", "right": "Y"},
                    {"op": "changed", "left": None, "right": "Z"},
                    {"op": "same", "left": "c", "right": "c"}]
    rows = wc.side_by_side("a\nX\nW\nc", "a\nY\nc")["rows"]
    assert rows[1:3] == [{"op": "changed", "left": "X", "right": "Y"},
                         {"op": "changed", "left": "W", "right": None}]


# ---- compare


def _obj(kind: str, name: str, text: str, schema: str = "dbo", path: str = "") -> dict[str, str]:
    out = {"kind": kind, "schema": schema, "name": name, "text": text}
    if path:
        out["path"] = path
    return out


def test_changed_rows_come_before_rows_only_in_the_project_whatever_their_names():
    project = {("view", "dbo", "z"): _obj("view", "z", "CREATE VIEW dbo.z AS SELECT 2 AS a", path="z.sql"),
               ("view", "dbo", "a"): _obj("view", "a", "CREATE VIEW dbo.a AS SELECT 1 AS a", path="a.sql")}
    warehouse = {("view", "dbo", "z"): _obj("view", "z", "CREATE VIEW dbo.z AS SELECT 1 AS a")}
    assert [(r["state"], r["name"]) for r in wc.compare(project, warehouse)] == \
        [("changed", "z"), ("only_project", "a")]


# ---- one definition


def test_a_view_ends_at_its_first_semicolon_even_with_begin_in_it():
    assert wc._one_definition("view", "CREATE VIEW v AS SELECT [begin] FROM t; DELETE FROM t") is None
    assert wc._one_definition("view", "CREATE VIEW v AS SELECT [begin] FROM t;") is not None


def test_a_function_without_a_body_ends_at_its_first_semicolon():
    text = "CREATE FUNCTION f() RETURNS TABLE AS RETURN SELECT 1 AS a; DELETE FROM t"
    assert wc._one_definition("function", text) is None
    body = "CREATE FUNCTION f() RETURNS int AS BEGIN; RETURN 1; END"
    assert wc._one_definition("function", body) == body


def test_a_table_or_schema_file_has_to_start_with_its_own_create():
    assert wc._one_definition("table", "INSERT INTO t (a)") is None
    assert wc._one_definition("schema", "DROP SCHEMA s") is None
    assert wc._one_definition("table", "CREATE TABLE t (a int)") == "CREATE TABLE t (a int)"


def test_a_schema_may_have_only_authorization_and_its_owner_after_its_name():
    assert wc._one_definition("schema", "CREATE SCHEMA s") == "CREATE SCHEMA s"
    assert wc._one_definition("schema", "CREATE SCHEMA s AUTHORIZATION dbo") == "CREATE SCHEMA s AUTHORIZATION dbo"
    assert wc._one_definition("schema", "CREATE SCHEMA s AUTHORIZATION") is None
    assert wc._one_definition("schema", "CREATE SCHEMA s x AUTHORIZATION") is None
    assert wc._one_definition("schema", "CREATE SCHEMA s AUTHORIZATION dbo x") is None


def test_a_table_with_no_column_list_is_not_one_definition():
    assert wc._one_definition("table", "CREATE TABLE t") is None
    assert wc._one_definition("table", "CREATE TABLE dbo.t") is None
    assert wc._one_definition("table", "CREATE TABLE dbo.t AS SELECT 1 AS a") is None


# ---- precondition


def test_the_precondition_has_columns_only_for_a_table_that_exists():
    new_table = {"kind": "table", "schema": "dbo", "name": "t", "state": "only_project"}
    assert wc.precondition(new_table) == {"kind": "table", "schema": "dbo", "name": "t", "exists": False}
    schema = {"kind": "schema", "schema": "s", "name": "s", "state": "only_warehouse",
              "warehouse_text": "CREATE SCHEMA s"}
    assert "columns" not in wc.precondition(schema) and "definition" not in wc.precondition(schema)
    table = {"kind": "table", "schema": "dbo", "name": "t", "state": "changed",
             "warehouse_text": "CREATE TABLE dbo.t (a int, [b c] int)"}
    assert wc.precondition(table)["columns"] == ["a", "b c"]


# ---- deploy plan


def _row(kind: str, name: str, state: str, project: str = "", warehouse: str = "", schema: str = "dbo"):
    return {"kind": kind, "schema": schema, "name": name, "state": state, "path": f"{name}.sql",
            "project_text": project, "warehouse_text": warehouse}


def test_a_changed_view_that_is_not_one_definition_is_an_alter_that_is_not_offered():
    [step] = wc.deploy_plan([_row("view", "v", "changed", "CREATE VIEW v AS SELECT 1 AS a\nGO\nSELECT 2",
                                  "CREATE VIEW v AS SELECT 2 AS a")])
    assert step["action"] == "alter" and not step["offered"] and step["why"] == wc.ONE_DEFINITION
    [step] = wc.deploy_plan([_row("view", "v", "only_project", "SELECT 1")])
    assert step["action"] == "create" and not step["offered"]


def test_why_a_change_is_offered():
    [view] = wc.deploy_plan([_row("view", "v", "only_project", "CREATE VIEW v AS SELECT 1 AS a")])
    [table] = wc.deploy_plan([_row("table", "t", "only_project", "CREATE TABLE t (a int)")])
    [alter] = wc.deploy_plan([_row("view", "v", "changed", "CREATE VIEW v AS SELECT 1 AS a",
                                   "CREATE VIEW v AS SELECT 2 AS a")])
    assert view["why"] == "new" and table["why"] == "new table, empty"
    assert alter["why"] == "definition changes" and alter["sql"].startswith("CREATE OR ALTER VIEW")


def test_deploy_steps_run_in_dependency_order_whatever_their_names():
    rows = [
        _row("schema", "a_schema", "changed", "CREATE SCHEMA a_schema", "CREATE SCHEMA a_schema AUTHORIZATION x",
             schema="a_schema"),
        _row("schema", "z_schema", "only_warehouse", warehouse="CREATE SCHEMA z_schema", schema="z_schema"),
        _row("table", "a_dropped", "only_warehouse", warehouse="CREATE TABLE a_dropped (a int)"),
        _row("function", "b_dropped", "only_warehouse", warehouse="CREATE FUNCTION b_dropped() RETURNS int AS BEGIN RETURN 1 END"),
        _row("view", "c_dropped", "only_warehouse", warehouse="CREATE VIEW c_dropped AS SELECT 1 AS a"),
        _row("procedure", "d_dropped", "only_warehouse", warehouse="CREATE PROCEDURE d_dropped AS SELECT 1"),
        _row("procedure", "e_altered", "changed", "CREATE PROCEDURE e_altered AS SELECT 2",
             "CREATE PROCEDURE e_altered AS SELECT 1"),
        _row("procedure", "f_new", "only_project", "CREATE PROCEDURE f_new AS SELECT 1"),
        _row("view", "g_new", "only_project", "CREATE VIEW g_new AS SELECT 1 AS a"),
        _row("function", "h_altered", "changed", "CREATE FUNCTION h_altered() RETURNS int AS BEGIN RETURN 2 END",
             "CREATE FUNCTION h_altered() RETURNS int AS BEGIN RETURN 1 END"),
        _row("function", "i_new", "only_project", "CREATE FUNCTION i_new() RETURNS int AS BEGIN RETURN 1 END"),
        _row("table", "j_new", "only_project", "CREATE TABLE j_new (a int)"),
        _row("schema", "k_schema", "only_project", "CREATE SCHEMA k_schema", schema="k_schema"),
    ]
    plan = wc.deploy_plan(rows)
    assert [(s["action"], s["kind"], s["name"]) for s in plan] == [
        ("create", "schema", "k_schema"), ("create", "table", "j_new"), ("alter", "function", "h_altered"),
        ("create", "function", "i_new"), ("create", "view", "g_new"), ("alter", "procedure", "e_altered"),
        ("create", "procedure", "f_new"), ("drop", "procedure", "d_dropped"), ("drop", "view", "c_dropped"),
        ("drop", "function", "b_dropped"), ("drop", "table", "a_dropped"), ("drop", "schema", "z_schema"),
        ("alter", "schema", "a_schema")]
