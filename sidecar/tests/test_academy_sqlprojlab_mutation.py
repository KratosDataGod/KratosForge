"""academy/sqlprojlab.py paths that its first mutation run (batch 429, docs/quality/mutation/batch-429.md)
showed no test checked. Everything here runs in-process on an in-memory DuckDB: no learner process starts
and nothing reaches the network."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import sqlprojlab as sp
from fabric_desktop.warehouse_project_service import MAX_DEPTH

T_A = "CREATE TABLE dbo.A (x INT NOT NULL, y VARCHAR(10) NULL);\n"
V_A = "CREATE VIEW dbo.vA AS SELECT x FROM dbo.A;\n"


def _write(tmp_path, files, seed=None, solution=""):
    ex_dir, grading = tmp_path / "ex", tmp_path / "grading"
    ex_dir.mkdir()
    grading.mkdir()
    project = {}
    for n, (rel, text) in enumerate(files.items()):
        (ex_dir / f"f{n}.sql").write_text(text, encoding="utf-8")
        project[rel] = f"f{n}.sql"
    ex = {"id": "E1", "files": sorted(f"f{n}.sql" for n in range(len(files))), "project": project, "validation": []}
    if seed is not None:
        (ex_dir / "seed.sql").write_text(seed, encoding="utf-8")
        ex["seed"] = "seed.sql"
        ex["files"].append("seed.sql")
    (grading / "solution.sql").write_text(solution, encoding="utf-8")
    return ex_dir, grading, ex


# ---- project_path

def test_project_path_takes_backslashes_and_spaces_round_it():
    assert sp.project_path("  dbo\\Tables\\A.sql  ") == "dbo/Tables/A.sql"
    assert sp.project_path("dbo/Tables/A.SQL") == "dbo/Tables/A.SQL"


def test_project_path_depth_limit_is_max_depth_folders():
    deep = "/".join(["d"] * MAX_DEPTH) + "/A.sql"
    assert sp.project_path(deep) == deep
    with pytest.raises(sp.Refused, match="isn't a .sql file path"):
        sp.project_path("d/" + deep)


@pytest.mark.parametrize("rel", ["", "   ", "dbo/Tables/A", "dbo/.hidden/A.sql"])
def test_project_path_refuses(rel):
    with pytest.raises(sp.Refused, match="such as dbo/Tables/DimCustomer.sql"):
        sp.project_path(rel)


# ---- parse

def test_parse_reads_bom_crlf_and_trims_blank_lines():
    text = "﻿-- notes\r\n\r\n-- file: dbo/Tables/A.sql\r\n\r\nCREATE TABLE dbo.A (x INT);\r\n\r\n\r\n"
    assert sp.parse(text) == {"dbo/Tables/A.sql": "CREATE TABLE dbo.A (x INT);\n"}


def test_parse_keeps_the_order_and_a_file_after_a_delete():
    got = sp.parse("-- delete: a.sql\n-- FILE: b.sql\nB\n-- file:c.sql\nC1\nC2")
    assert list(got) == ["a.sql", "b.sql", "c.sql"]
    assert got == {"a.sql": None, "b.sql": "B\n", "c.sql": "C1\nC2\n"}


def test_parse_empty_and_comments_only_is_no_change():
    assert sp.parse("") == {}
    assert sp.parse("-- just a note\n\n   \n") == {}


def test_parse_names_the_line_of_an_unmarked_statement():
    with pytest.raises(sp.Refused) as err:
        sp.parse("-- one\n\nSELECT 1;")
    assert str(err.value).startswith("Line 3: start each file")


def test_parse_names_the_line_of_a_bad_path():
    with pytest.raises(sp.Refused) as err:
        sp.parse("-- file: a.sql\nx\n-- file: b.txt")
    assert str(err.value).startswith("Line 3: 'b.txt' isn't a .sql file path")


@pytest.mark.parametrize("text", ["-- delete: a.sql\n-- delete: a.sql", "-- file: a.sql\n-- delete: a.sql",
                                  "-- delete: a.sql\n-- file: a.sql\nx"])
def test_parse_refuses_a_path_twice_whatever_the_section(text):
    with pytest.raises(sp.Refused) as err:
        sp.parse(text)
    assert str(err.value) == "Line 2: a.sql is in the change set twice."


def test_parse_an_empty_file_section_still_counts():
    assert sp.parse("-- file: a.sql\n-- file: b.sql\nB") == {"a.sql": "\n", "b.sql": "B\n"}


def test_parse_size_limit_is_on_bytes_and_exactly_1_mb_is_read():
    at, mb = "-- file: a.sql\n", 1024 * 1024
    text = at + "x" * (mb - len(at))
    assert len(sp.parse(text)["a.sql"]) == mb - len(at) + 1
    with pytest.raises(sp.Refused):
        sp.parse(text + "x")
    with pytest.raises(sp.Refused) as err:
        sp.parse(at + "é" * ((mb - len(at)) // 2 + 1))
    assert str(err.value) == "The change set is over 1 MB; a change set is much smaller."


# ---- apply

def test_apply_changes_adds_and_deletes_and_leaves_the_project_alone():
    project = {"a.sql": "A", "b.sql": "B"}
    got = sp.apply(project, {"a.sql": None, "c.sql": "C", "gone.sql": None, "b.sql": "B2"})
    assert got == {"b.sql": "B2", "c.sql": "C"}
    assert project == {"a.sql": "A", "b.sql": "B"}


def test_apply_file_limit_is_500_files():
    at_limit = {f"{n}.sql": "" for n in range(500)}
    assert len(sp.apply(at_limit, {})) == 500
    with pytest.raises(sp.Refused) as err:
        sp.apply(at_limit, {"one-more.sql": "x"})
    assert str(err.value) == "The project has more than 500 files."


# ---- objects

def test_objects_keys_each_object_and_says_what_is_wrong():
    files = {"dbo/Tables/A.sql": T_A, "dbo/Tables/Notes.sql": "-- nothing\n",
             "Security/sales.sql": "CREATE SCHEMA sales;\n",
             "dbo/Views/Wrong.sql": V_A,
             "dbo/Tables/a.sql": "CREATE TABLE dbo.a (z INT);\n"}
    found, problems = sp.objects(files)
    assert set(found) == {("table", "dbo", "a"), ("schema", "sales", "sales"), ("view", "dbo", "va")}
    assert found[("table", "dbo", "a")]["path"] == "dbo/Tables/A.sql"
    assert found[("table", "dbo", "a")]["text"] == T_A
    assert found[("schema", "sales", "sales")]["name"] == "sales"
    assert problems == [
        "dbo/Tables/Notes.sql doesn't create a table, view, procedure, function or schema.",
        "table dbo.a is defined twice: dbo/Tables/A.sql and dbo/Tables/a.sql.",
        "dbo/Views/Wrong.sql defines view dbo.vA, which Fabric's Git layout keeps at dbo/Views/vA.sql.",
    ]


def test_objects_layout_is_compared_without_case():
    found, problems = sp.objects({"DBO/tables/a.SQL": T_A})
    assert problems == [] and list(found) == [("table", "dbo", "a")]


def test_objects_a_schema_file_is_labelled_by_its_name():
    _, problems = sp.objects({"dbo/Tables/sales.sql": "CREATE SCHEMA sales;\n"})
    assert problems == ["dbo/Tables/sales.sql defines schema sales, which Fabric's Git layout keeps at Security/sales.sql."]


def test_objects_a_file_with_more_than_its_object():
    _, problems = sp.objects({"dbo/Tables/A.sql": T_A + "DROP TABLE dbo.B;\n"})
    assert problems == [("dbo/Tables/A.sql does more than define table dbo.A: a project file holds one object and "
                         "nothing else.")]


def test_objects_a_name_that_cant_be_a_file_is_a_problem_and_still_found():
    found, problems = sp.objects({"dbo/Tables/X.sql": "CREATE TABLE dbo.[CON] (x INT);\n"})
    assert problems == ["'CON' can't be a file name, so table dbo.CON isn't written."]
    assert list(found) == [("table", "dbo", "con")]


# ---- build

def test_connect_has_no_file_access_and_its_settings_are_locked():
    con = sp._connect()
    try:
        assert con.execute("SELECT current_setting('enable_external_access')").fetchone()[0] is False
        assert con.execute("SELECT current_setting('lock_configuration')").fetchone()[0] is True
        with pytest.raises(Exception, match="(?i)lock"):
            con.execute("SET enable_external_access = true")
    finally:
        con.close()


def test_build_reports_every_file_and_makes_each_schema():
    files = {"dbo/Tables/A.sql": T_A, "Security/sales.sql": "CREATE SCHEMA sales;\n",
             "sales/Tables/B.sql": "CREATE TABLE sales.B (k INT);\n",
             "dbo/Functions/f.sql": "CREATE FUNCTION dbo.f() RETURNS INT AS BEGIN RETURN 1; END\n",
             "dbo/StoredProcedures/p.sql": "CREATE PROCEDURE dbo.p AS SELECT 1;\n",
             "dbo/Views/vA.sql": V_A, "notes.sql": "-- nothing\n"}
    con, report, problems = sp.build(files)
    try:
        schemas = {r[0] for r in con.execute("SELECT schema_name FROM information_schema.schemata "
                                             "WHERE catalog_name = current_database()").fetchall()}
        assert schemas == {"dbo", "main", "sales"}
        assert con.execute("SELECT count(*) FROM sales.B").fetchone()[0] == 0
    finally:
        con.close()
    assert [r["path"] for r in report] == sorted(files)
    by = {r["path"]: r for r in report}
    assert by["dbo/Tables/A.sql"] == {"path": "dbo/Tables/A.sql", "object": "table dbo.A", "build": "built"}
    assert by["Security/sales.sql"]["build"] == "built"
    assert by["dbo/Functions/f.sql"]["build"] == "read, not run on this PC"
    assert by["dbo/StoredProcedures/p.sql"]["build"] == "read, not run on this PC"
    assert by["dbo/Views/vA.sql"] == {"path": "dbo/Views/vA.sql", "object": "view dbo.vA", "build": "built"}
    assert by["notes.sql"] == {"path": "notes.sql", "object": "", "build": "not read"}
    assert problems == ["notes.sql doesn't create a table, view, procedure, function or schema."]


def test_build_quotes_a_schema_name_with_a_double_quote():
    con, report, problems = sp.build({'Security/we"ird.sql': 'CREATE SCHEMA [we"ird];\n'})
    try:
        names = {r[0] for r in con.execute("SELECT schema_name FROM information_schema.schemata "
                                           "WHERE catalog_name = current_database()").fetchall()}
    finally:
        con.close()
    assert 'we"ird' in names and report[0]["build"] == "built"
    assert problems == ["'we\"ird' can't be a file name, so schema we\"ird.we\"ird isn't written."]


def test_build_makes_a_schema_a_table_names_without_a_schema_file():
    con, _, problems = sp.build({"stage/Tables/T.sql": "CREATE TABLE stage.T (k INT);\n"})
    con.close()
    assert problems == []


def test_build_a_table_that_fails_says_why():
    con, report, problems = sp.build({"dbo/Tables/A.sql": "CREATE TABLE dbo.A (x NOSUCHTYPE);\n"})
    con.close()
    assert report[0]["build"].startswith("failed: ")
    assert problems and problems[0].startswith("dbo/Tables/A.sql doesn't build: ")
    assert problems[0][len("dbo/Tables/A.sql doesn't build: "):] == report[0]["build"][len("failed: "):]


def test_build_a_view_that_never_builds_says_why_once():
    con, report, problems = sp.build({"dbo/Tables/A.sql": T_A,
                                      "dbo/Views/vB.sql": "CREATE VIEW dbo.vB AS SELECT nope FROM dbo.A;\n"})
    con.close()
    by = {r["path"]: r["build"] for r in report}
    assert by["dbo/Tables/A.sql"] == "built"
    assert by["dbo/Views/vB.sql"].startswith("failed: ") and "nope" in by["dbo/Views/vB.sql"]
    assert len(problems) == 1 and problems[0].startswith("dbo/Views/vB.sql doesn't build: ")
    assert "\n" not in problems[0]


def test_build_three_views_in_reverse_order():
    files = {"dbo/Tables/A.sql": T_A,
             "dbo/Views/a1.sql": "CREATE VIEW dbo.a1 AS SELECT x FROM dbo.b2;\n",
             "dbo/Views/b2.sql": "CREATE VIEW dbo.b2 AS SELECT x FROM dbo.c3;\n",
             "dbo/Views/c3.sql": "CREATE VIEW dbo.c3 AS SELECT x FROM dbo.A;\n"}
    con, report, problems = sp.build(files)
    try:
        assert con.execute("SELECT count(*) FROM dbo.a1").fetchone()[0] == 0
    finally:
        con.close()
    assert problems == [] and {r["build"] for r in report} == {"built"}


def test_build_loads_the_seed_only_when_the_project_builds():
    con, _, problems = sp.build({"dbo/Tables/A.sql": T_A}, "INSERT INTO dbo.A VALUES (1, 'a'), (2, 'b');")
    try:
        assert problems == [] and con.execute("SELECT count(*) FROM dbo.A").fetchone()[0] == 2
    finally:
        con.close()
    con, _, problems = sp.build({"dbo/Tables/A.sql": T_A, "x.sql": "-- nothing\n"}, "INSERT INTO dbo.A VALUES (1, 'a');")
    try:
        assert con.execute("SELECT count(*) FROM dbo.A").fetchone()[0] == 0
        assert len(problems) == 1
    finally:
        con.close()


def test_build_a_seed_that_fails_is_a_problem():
    con, _, problems = sp.build({"dbo/Tables/A.sql": T_A}, "INSERT INTO dbo.Missing VALUES (1);")
    con.close()
    assert len(problems) == 1
    assert problems[0].startswith("The exercise's rows couldn't be loaded into this project: ")


def test_execute_turns_a_translation_error_into_valueerror(monkeypatch):
    def boom(sql):
        raise ValueError("can't translate this")
    monkeypatch.setattr(sp.tsql, "translate", boom)
    con = sp._connect()
    try:
        with pytest.raises(ValueError, match="^can't translate this$"):
            sp._execute(con, "SELECT 1")
    finally:
        con.close()


def test_execute_says_stopped_when_the_time_limit_fires(monkeypatch):
    monkeypatch.setattr(sp.learn_labs, "TIME_LIMIT", 0.05)
    con = sp._connect()
    try:
        with pytest.raises(ValueError, match="^Stopped after"):
            sp._execute(con, "SELECT count(*) FROM range(1000000) a, range(1000000) b")
    finally:
        con.close()


def test_execute_runs_every_statement():
    con = sp._connect()
    try:
        sp._execute(con, "CREATE TABLE dbo_t (x INT); INSERT INTO dbo_t VALUES (1); INSERT INTO dbo_t VALUES (2);")
        assert con.execute("SELECT count(*) FROM dbo_t").fetchone()[0] == 2
    finally:
        con.close()


# ---- check_problems and spec_problems

@pytest.mark.parametrize("check", [
    {"kind": "objects_present", "objects": []},
    {"kind": "objects_present", "objects": "x"},
    {"kind": "objects_present", "objects": ["x"]},
    {"kind": "objects_present", "objects": [{"kind": "table", "name": "x"}]},
    {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo"}]},
    {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "x", "columns": [1]}]},
    {"kind": "result_matches", "query": "  "},
    {"kind": "result_matches", "query": 3},
    {"kind": "deploy_safe", "allow_drop": [1]},
])
def test_check_problems_more(check):
    assert sp.check_problems(check)


def test_check_problems_objects_not_a_list():
    assert sp.check_problems({"kind": "objects_present", "objects": "ab"}) == ["check objects_present needs objects"]
    assert sp.check_problems({"kind": "objects_present", "objects": []}) == ["check objects_present needs objects"]


def test_check_problems_names_the_object_number():
    c = {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "a"},
                                                {"kind": "view", "schema": "dbo", "name": "b", "columns": "c"},
                                                {"kind": "index", "schema": "dbo", "name": "c"}]}
    assert sp.check_problems(c) == [
        "check objects_present object 2: columns is a list of names",
        "check objects_present object 3 needs kind (schema, table, view, procedure, function), schema and name"]


@pytest.mark.parametrize("check", [
    {"kind": "objects_present", "objects": [{"kind": k, "schema": "dbo", "name": "x"} for k in sp._KINDS]},
    {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "x", "columns": ["a", "b"]}]},
    {"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "x", "columns": []}]},
    {"kind": "result_matches", "query": "SELECT 1"},
    {"kind": "deploy_safe"},
    {"kind": "deploy_safe", "allow_drop": ["dbo.x"]},
    {"kind": "project_builds"},
])
def test_well_formed_checks(check):
    assert sp.check_problems(check) == []


def test_spec_problems_each():
    assert sp.spec_problems("E1", {}) == ["E1: a sqlproj_lab exercise needs project, its files by path in the project"]
    assert sp.spec_problems("E1", {"project": {}}) == sp.spec_problems("E1", {})
    assert sp.spec_problems("E1", {"project": ["a.sql"]}) == sp.spec_problems("E1", {})
    got = sp.spec_problems("E1", {"files": ["a.sql"], "seed": "s.sql",
                                  "project": {"dbo/Tables/A.sql": "a.sql", "x.txt": "a.sql", "dbo/Tables/B.sql": "b.sql"}})
    assert got == ["E1: project 'x.txt' isn't a .sql file path in the project, such as dbo/Tables/DimCustomer.sql.",
                   "E1: project takes 'b.sql', which isn't listed in files",
                   "E1: seed 's.sql' isn't listed in files"]
    assert sp.spec_problems("E1", {"project": {"dbo/Tables/A.sql": "a.sql"}}) == [
        "E1: project takes 'a.sql', which isn't listed in files"]
    assert sp.spec_problems("E1", {"files": ["a.sql", "s.sql"], "seed": "s.sql",
                                   "project": {"dbo/Tables/A.sql": "a.sql"}}) == []


def test_load_project_without_a_seed(tmp_path):
    ex_dir, _, ex = _write(tmp_path, {"dbo\\Tables\\A.sql": T_A})
    project, seed = sp.load_project(ex_dir, ex)
    assert project == {"dbo/Tables/A.sql": T_A} and seed is None


# ---- _rows

def test_rows_needs_one_statement():
    con = sp._connect()
    try:
        with pytest.raises(ValueError, match="^The check's query is one SELECT.$"):
            sp._rows(con, "SELECT 1; SELECT 2")
    finally:
        con.close()


def test_rows_caps_at_max_rows_and_converts_cells(monkeypatch):
    monkeypatch.setattr(sp, "MAX_ROWS", 3)
    con = sp._connect()
    try:
        cols, rows = sp._rows(con, "SELECT CAST(1.5 AS DECIMAL(4, 1)) AS d, CAST('2024-01-02' AS DATE) AS t")
        assert cols == ["d", "t"] and rows == [(1.5, "2024-01-02")]
        _, rows = sp._rows(con, "SELECT * FROM range(10)")
        assert rows == [(0,), (1,), (2,)]
        _, rows = sp._rows(con, "SELECT * FROM range(3)")
        assert rows == [(0,), (1,), (2,)]
    finally:
        con.close()


def test_rows_keeps_10000_rows():
    con = sp._connect()
    try:
        _, rows = sp._rows(con, "SELECT * FROM range(10001)")
        assert len(rows) == 10000 and rows[-1] == (9999,)
    finally:
        con.close()


def test_rows_a_failing_query_says_why_in_one_line():
    con = sp._connect()
    try:
        with pytest.raises(ValueError) as err:
            sp._rows(con, "SELECT nope FROM nowhere_at_all")
        assert "nowhere_at_all" in str(err.value) and "\n" not in str(err.value)
    finally:
        con.close()


def test_rows_says_stopped_when_the_time_limit_fires(monkeypatch):
    monkeypatch.setattr(sp.learn_labs, "TIME_LIMIT", 0.05)
    con = sp._connect()
    try:
        with pytest.raises(ValueError, match="^Stopped after"):
            sp._rows(con, "SELECT count(*) FROM range(1000000) a, range(1000000) b")
    finally:
        con.close()


# ---- checks, one at a time

def _grade(tmp_path, files, validation, text, seed=None, solution=None):
    ex_dir, grading, ex = _write(tmp_path, files, seed, text if solution is None else solution)
    ex["validation"] = validation
    return sp.grade(text, ex_dir, grading, ex)


def test_project_builds_counts_the_files_and_the_other_problems(tmp_path):
    (tmp_path / "a").mkdir()
    got = _grade(tmp_path / "a", {"dbo/Tables/A.sql": T_A}, [{"kind": "project_builds"}], "-- file: dbo/Views/vA.sql\n" + V_A)
    assert got == {"passed": True, "results": [
        {"kind": "project_builds", "passed": True, "detail": "All 2 files define one object each, in place, and build."}]}
    (tmp_path / "b").mkdir()
    bad = "-- file: x.sql\n-- nothing\n-- file: y.sql\n-- nothing\n-- file: z.sql\n-- nothing\n"
    got = _grade(tmp_path / "b", {"dbo/Tables/A.sql": T_A}, [{"kind": "project_builds"}], bad)
    assert got["results"][0] == {"kind": "project_builds", "passed": False,
                                 "detail": "x.sql doesn't create a table, view, procedure, function or schema. (2 more)"}


def test_project_builds_one_problem_has_no_more(tmp_path):
    got = _grade(tmp_path, {"dbo/Tables/A.sql": T_A}, [{"kind": "project_builds"}], "-- file: x.sql\n-- nothing\n")
    assert got["results"][0]["detail"] == "x.sql doesn't create a table, view, procedure, function or schema."
    assert got["passed"] is False


def test_objects_present_columns(tmp_path):
    runs = iter(range(100))

    def one(objs):
        where = tmp_path / str(next(runs))
        where.mkdir()
        return _grade(where, {"dbo/Tables/A.sql": T_A}, [{"kind": "objects_present", "objects": objs}], "")["results"][0]

    assert one([{"kind": "table", "schema": "dbo", "name": "A", "columns": ["x", "y"]},
                {"kind": "table", "schema": "dbo", "name": "A"}]) == {
        "kind": "objects_present", "passed": True, "detail": "The project has all 2 objects."}
    assert one([{"kind": "table", "schema": "dbo", "name": "A", "columns": ["x", "q"]}])["detail"] == (
        "table dbo.A has no column q.")
    assert one([{"kind": "table", "schema": "dbo", "name": "A", "columns": ["p", "x", "q"]}])["detail"] == (
        "table dbo.A has no columns p, q.")
    assert one([{"kind": "table", "schema": "dbo", "name": "A", "columns": ["X"]}])["detail"] == (
        "table dbo.A has no column X.")
    assert one([{"kind": "view", "schema": "dbo", "name": "A"}])["detail"] == "The project has no view dbo.A."
    assert one([{"kind": "table", "schema": "DBO", "name": "A"}])["detail"] == "The project has no table DBO.A."
    assert one([{"kind": "table", "schema": "dbo", "name": "a"}])["detail"] == "The project has no table dbo.a."
    assert one([{"kind": "table", "schema": "dbo", "name": "A"},
                {"kind": "table", "schema": "dbo", "name": "B"}])["detail"] == "The project has no table dbo.B."


def test_objects_present_columns_of_a_view_that_doesnt_build(tmp_path):
    got = _grade(tmp_path, {"dbo/Tables/A.sql": T_A},
                 [{"kind": "objects_present", "objects": [{"kind": "view", "schema": "dbo", "name": "vB",
                                                           "columns": ["x"]}]}],
                 "-- file: dbo/Views/vB.sql\nCREATE VIEW dbo.vB AS SELECT nope FROM dbo.A;\n")
    assert got["results"][0]["detail"] == "view dbo.vB doesn't build, so its columns can't be read."


def test_deploy_safe_drop_allowed_and_not(tmp_path):
    files = {"dbo/Tables/A.sql": T_A, "dbo/Views/vA.sql": V_A}
    delete = "-- delete: dbo/Views/vA.sql\n"
    (tmp_path / "a").mkdir()
    got = _grade(tmp_path / "a", files, [{"kind": "deploy_safe", "allow_drop": ["dbo.vA"]}], delete)
    assert got["results"][0] == {"kind": "deploy_safe", "passed": True,
                                 "detail": "The deploy drops nothing and rebuilds no table."}
    (tmp_path / "b").mkdir()
    got = _grade(tmp_path / "b", files, [{"kind": "deploy_safe", "allow_drop": ["dbo.other"]}], delete)
    assert got["results"][0]["detail"] == ("Deploying would drop view dbo.vA, which the warehouse has. Keep its file, "
                                           "or remove it on purpose in a separate, reviewed change.")


def test_deploy_safe_a_new_column_is_fine_and_a_removed_one_rebuilds(tmp_path):
    files = {"dbo/Tables/A.sql": T_A}
    (tmp_path / "a").mkdir()
    added = "-- file: dbo/Tables/A.sql\nCREATE TABLE dbo.A (x INT NOT NULL, y VARCHAR(10) NULL, z INT NULL);\n"
    got = _grade(tmp_path / "a", files, [{"kind": "deploy_safe"}], added)
    assert got["passed"] is True
    (tmp_path / "b").mkdir()
    removed = "-- file: dbo/Tables/A.sql\nCREATE TABLE dbo.A (x INT NOT NULL);\n"
    got = _grade(tmp_path / "b", files, [{"kind": "deploy_safe"}], removed)
    assert got["results"][0]["detail"] == ("Deploying would rebuild table dbo.A: column y is only in the warehouse. "
                                           "A column that is removed, renamed or retyped breaks what reads it.")


def test_result_matches_paths(tmp_path):
    files = {"dbo/Tables/A.sql": T_A}
    seed = "INSERT INTO dbo.A VALUES (2, 'b'), (1, 'a');"
    view = "-- file: dbo/Views/vA.sql\n" + V_A
    q = {"kind": "result_matches", "query": "SELECT x FROM dbo.vA"}

    def go(name, text, check, solution=None, s=seed):
        (tmp_path / name).mkdir()
        return _grade(tmp_path / name, files, [check], text, s, solution)["results"][0]

    assert go("same", view, q) == {"kind": "result_matches", "passed": True,
                                   "detail": "The query returns the expected 2 rows."}
    assert go("broken", view + "-- file: x.sql\n-- nothing\n", q, solution=view)["detail"] == (
        "The project doesn't build, so the query can't run on it.")
    assert go("noref", view, q, solution="-- file: x.sql\n-- nothing\n")["detail"] == (
        "The reference project doesn't build; this exercise needs fixing.")
    got = go("fails", "", q, solution=view)
    assert got["detail"].startswith("The check's query failed on your project: ")
    other = "-- file: dbo/Views/vA.sql\nCREATE VIEW dbo.vA AS SELECT y AS x2 FROM dbo.A;\n"
    qc = {"kind": "result_matches", "query": "SELECT * FROM dbo.vA"}
    assert go("cols", other, qc, solution=view)["detail"] == "The query returns columns x2; expected x."
    upper = "-- file: dbo/Views/vA.sql\nCREATE VIEW dbo.vA AS SELECT x AS X FROM dbo.A;\n"
    assert go("case", upper, qc, solution=view)["passed"] is True
    fewer = "-- file: dbo/Views/vA.sql\nCREATE VIEW dbo.vA AS SELECT x FROM dbo.A WHERE x = 1;\n"
    assert go("fewer", fewer, q, solution=view)["detail"] == (
        "The query returns 1 rows that differ from the expected 2.")


def test_result_matches_ordered_only_when_asked(tmp_path):
    ex_dir, _, ex = _write(tmp_path, {"dbo/Tables/A.sql": T_A}, "INSERT INTO dbo.A VALUES (1, 'a'), (2, 'b');")
    reference = (["x"], [(1,), (2,)])
    q = "SELECT x FROM dbo.A ORDER BY x DESC"
    for ordered, passed in ((False, True), (True, False)):
        con, found, problems, project, files = _built("", ex_dir, ex)
        try:
            got = sp._check({"kind": "result_matches", "query": q, "ordered": ordered}, con, found, problems, project,
                            files, reference)
        finally:
            con.close()
        assert got["passed"] is passed, (ordered, got)
    con, found, problems, project, files = _built("", ex_dir, ex)
    try:
        got = sp._check({"kind": "result_matches", "query": "SELECT x FROM dbo.A ORDER BY x", "ordered": True}, con,
                        found, problems, project, files, reference)
    finally:
        con.close()
    assert got["passed"] is True


def _built(text, ex_dir, ex):
    project, seed = sp.load_project(ex_dir, ex)
    files = sp.apply(project, sp.parse(text))
    found, _ = sp.objects(files)
    con, _, problems = sp.build(files, seed)
    return con, found, problems, project, files


def test_an_unknown_check_fails_in_words(tmp_path):
    got = _grade(tmp_path, {"dbo/Tables/A.sql": T_A}, [{"kind": "lint"}], "")
    assert got == {"passed": False, "results": [
        {"kind": "lint", "passed": False, "detail": "A database project exercise can't check lint."}]}


def test_grade_with_no_checks_doesnt_pass(tmp_path):
    assert _grade(tmp_path, {"dbo/Tables/A.sql": T_A}, [], "") == {"passed": False, "results": []}


def test_grade_one_failing_check_of_two_fails(tmp_path):
    got = _grade(tmp_path, {"dbo/Tables/A.sql": T_A}, [{"kind": "project_builds"}, {"kind": "lint"}], "")
    assert got["passed"] is False and [r["passed"] for r in got["results"]] == [True, False]


def test_reference_without_a_query_reads_nothing(tmp_path):
    ex_dir, grading, ex = _write(tmp_path, {"dbo/Tables/A.sql": T_A})
    (grading / "solution.sql").unlink()
    ex["validation"] = [{"kind": "project_builds"}]
    assert sp._reference(ex_dir, grading, ex) == {}


# ---- run

def test_run_rows_and_limit(tmp_path):
    ex_dir, _, ex = _write(tmp_path, {"dbo/Tables/A.sql": T_A, "dbo/Views/vA.sql": V_A})
    text = ("-- file: dbo/Tables/B.sql\nCREATE TABLE dbo.B (k INT);\n-- delete: dbo/Views/vA.sql\n"
            "-- file: dbo/Views/Wrong.sql\nCREATE VIEW dbo.vW AS SELECT 1 AS one;\n")
    got = sp.run(ex_dir, ex, text)
    rows = got["rows"]
    by = {r[0]: r for r in rows if not r[0].startswith("(")}
    assert by["dbo/Tables/A.sql"] == ["dbo/Tables/A.sql", "table dbo.A", "built", ""]
    assert by["dbo/Tables/B.sql"][:3] == ["dbo/Tables/B.sql", "table dbo.B", "built"]
    assert by["dbo/Tables/B.sql"][3].startswith("create: ")
    assert [r for r in rows if r[0] == "(deleted)"] == [
        ["(deleted)", "view dbo.vA", "", rows[[r[0] for r in rows].index("(deleted)")][3]]]
    assert [r for r in rows if r[0] == "(deleted)"][0][3].startswith("drop: ")
    assert [r for r in rows if r[0] == "(problem)"] == []
    assert got["more"] is False
    short = sp.run(ex_dir, ex, text, limit=2)
    assert short["rows"] == rows[:2] and short["more"] is True
    exact = sp.run(ex_dir, ex, text, limit=len(rows))
    assert exact["rows"] == rows and exact["more"] is False


def test_run_shows_200_rows_unless_asked(tmp_path):
    ex_dir, _, ex = _write(tmp_path, {"dbo/Tables/A.sql": T_A})
    text = "".join(f"-- file: n{n:03d}.sql\n-- nothing\n" for n in range(199))
    got = sp.run(ex_dir, ex, text)
    assert len(got["rows"]) == 200 and got["more"] is False
    got = sp.run(ex_dir, ex, text + "-- file: n999.sql\n-- nothing\n")
    assert len(got["rows"]) == 200 and got["more"] is True


def test_run_lists_a_problem_that_names_no_file(tmp_path):
    ex_dir, _, ex = _write(tmp_path, {"dbo/Tables/A.sql": T_A}, seed="INSERT INTO dbo.Missing VALUES (1);")
    rows = sp.run(ex_dir, ex, "")["rows"]
    problem = [r for r in rows if r[0] == "(problem)"]
    assert len(problem) == 1 and problem[0][1] == "" and problem[0][3] == ""
    assert problem[0][2].startswith("The exercise's rows couldn't be loaded")
