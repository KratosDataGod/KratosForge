"""A warehouse as files in Fabric's Git layout (warehouse_project_service.py)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop import warehouse_project_service as wp
from fabric_desktop.service import Service

TABLE = "CREATE TABLE [dbo].[DimCustomer]\n(\n    [CustomerKey] int NOT NULL,\n    [Segment] varchar(20) NULL\n);\nGO\n"
VIEW = "-- CREATE TABLE dbo.NotThis\nCREATE VIEW [dbo].[vw_Sales] AS SELECT 1 AS x;\nGO\n"

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


@pytest.fixture
def repo(svc, tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    r = tmp_path / "sales-platform"
    r.mkdir()
    svc.git_init(str(r))
    return r


def objs(*items):
    return [dict(zip(("kind", "schema", "name", "text"), i)) for i in items]


def test_a_file_says_which_object_it_defines():
    assert wp.defines(TABLE) == {"kind": "table", "schema": "dbo", "name": "DimCustomer"}
    assert wp.defines(VIEW) == {"kind": "view", "schema": "dbo", "name": "vw_Sales"}, "a CREATE in a comment doesn't count"
    assert wp.defines("CREATE OR ALTER PROC usp_Load AS SELECT 1") == {"kind": "procedure", "schema": "dbo", "name": "usp_Load"}
    assert wp.defines('CREATE FUNCTION "fin"."fn ]x" () RETURNS int AS BEGIN RETURN 1 END') == \
        {"kind": "function", "schema": "fin", "name": "fn ]x"}
    assert wp.defines("CREATE SCHEMA [staging];") == {"kind": "schema", "schema": "staging", "name": "staging"}
    assert wp.defines("SELECT 'CREATE TABLE t (a int)'") is None
    assert wp.default_path("procedure", "dbo", "usp_Load") == "dbo/StoredProcedures/usp_Load.sql"
    assert wp.default_path("schema", "staging", "staging") == "Security/staging.sql"
    with pytest.raises(ValueError, match="can't be a file name"):
        wp.default_path("table", "dbo", "a/b")


def test_import_makes_the_project_in_fabrics_layout_and_lists_it(svc, repo):
    out = svc.wh_project_write(str(repo), objs(("schema", "staging", "staging", "CREATE SCHEMA [staging];"),
                                               ("table", "dbo", "DimCustomer", TABLE), ("view", "dbo", "vw_Sales", VIEW)),
                               create="WH_Gold")
    assert out["created"] and out["folder"] == "WH_Gold.Warehouse"
    assert out["counts"] == {"added": 3, "changed": 0, "same": 0, "skipped": 0}
    project = repo / "WH_Gold.Warehouse"
    platform = json.loads((project / ".platform").read_text(encoding="utf-8"))
    assert platform["metadata"] == {"type": "Warehouse", "displayName": "WH_Gold"}
    assert "SqlDwUnifiedDatabaseSchemaProvider" in (project / "WH_Gold.sqlproj").read_text(encoding="utf-8")
    assert (project / "dbo" / "Tables" / "DimCustomer.sql").read_text(encoding="utf-8") == TABLE
    assert (project / "Security" / "staging.sql").is_file()

    assert svc.wh_projects_list()["projects"] == [{"repo": str(repo), "repo_label": "sales-platform",
                                                    "folder": "WH_Gold.Warehouse", "name": "WH_Gold"}]
    files = svc.wh_project_files(str(repo), "WH_Gold.Warehouse")
    assert files["sqlproj"] and files["platform"]
    assert [(f["path"], f["kind"], f["name"], f["git"]) for f in files["files"]] == [
        ("Security/staging.sql", "schema", "staging", "A"),
        ("dbo/Tables/DimCustomer.sql", "table", "DimCustomer", "A"),
        ("dbo/Views/vw_Sales.sql", "view", "vw_Sales", "A")]


def test_import_again_keeps_your_edits_unless_asked_and_never_removes(svc, repo):
    svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE), ("view", "dbo", "vw_Old", VIEW)), create="WH_Gold")
    f = repo / "WH_Gold.Warehouse" / "dbo" / "Tables" / "DimCustomer.sql"
    f.write_text(TABLE.replace("varchar(20)", "varchar(40)"), encoding="utf-8")
    again = objs(("table", "dbo", "DimCustomer", TABLE), ("table", "dbo", "FactSales", "CREATE TABLE dbo.FactSales (a int);"))
    plan = svc.wh_project_write(str(repo), again, folder="WH_Gold.Warehouse", dry_run=True)
    assert plan["counts"] == {"added": 1, "changed": 1, "same": 0, "skipped": 0}
    assert not (repo / "WH_Gold.Warehouse" / "dbo" / "Tables" / "FactSales.sql").exists(), "a dry run writes nothing"
    kept = svc.wh_project_write(str(repo), again, folder="WH_Gold.Warehouse")
    assert kept["changed"][0]["replaced"] == "no" and "varchar(40)" in f.read_text(encoding="utf-8")
    svc.wh_project_write(str(repo), again, folder="WH_Gold.Warehouse", replace=True)
    assert "varchar(20)" in f.read_text(encoding="utf-8")
    assert (repo / "WH_Gold.Warehouse" / "dbo" / "Views" / "vw_Old.sql").is_file(), "only in the project stays"
    same = svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE.replace("\n", "\r\n"))), folder="WH_Gold.Warehouse")
    assert same["counts"]["same"] == 1, "line endings don't make a change"


def test_an_object_goes_to_the_file_that_already_defines_it(svc, repo):
    project = repo / "WH_Gold.Warehouse"
    (project / "dbo" / "Stored Procedures").mkdir(parents=True)
    (project / "dbo" / "Stored Procedures" / "Load.sql").write_text("CREATE PROCEDURE dbo.usp_Load AS SELECT 1;", encoding="utf-8")
    out = svc.wh_project_write(str(repo), objs(("procedure", "dbo", "usp_Load", "CREATE PROCEDURE dbo.usp_Load AS SELECT 2;"),
                                               ("procedure", "dbo", "usp_New", "CREATE PROCEDURE dbo.usp_New AS SELECT 3;")),
                               folder="WH_Gold.Warehouse", replace=True)
    assert [r["path"] for r in out["changed"]] == ["dbo/Stored Procedures/Load.sql"]
    assert [r["path"] for r in out["added"]] == ["dbo/Stored Procedures/usp_New.sql"], "the project's own folder name"


def test_objects_whose_names_differ_only_in_case_never_share_a_file(svc, repo):
    # Fabric's BIN2 collation keeps dbo.Sales and dbo.sales apart; Windows file names don't.
    sales = "CREATE TABLE dbo.Sales (a int NULL);"
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "Sales", sales), ("table", "dbo", "sales", "CREATE TABLE dbo.sales (b int NULL);")),
                               create="WH_Gold")
    assert [r["path"] for r in out["added"]] == ["dbo/Tables/Sales.sql"]
    assert [r["name"] for r in out["skipped"]] == ["dbo.sales"] and "apart from letter case" in out["skipped"][0]["why"]
    again = svc.wh_project_write(str(repo), objs(("table", "dbo", "sales", "CREATE TABLE dbo.sales (b int NULL);")),
                                 folder="WH_Gold.Warehouse", replace=True)
    assert again["counts"]["skipped"] == 1, "a later import doesn't write over dbo.Sales's file either"
    assert (repo / "WH_Gold.Warehouse" / "dbo" / "Tables" / "Sales.sql").read_text(encoding="utf-8").startswith(sales)
    assert svc.wh_project_write(str(repo), objs(("table", "dbo", "Sales", sales)), folder="WH_Gold.Warehouse")["counts"]["same"] == 1


def test_save_reads_back_and_refuses_a_file_changed_since_it_was_opened(svc, repo):
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    opened = svc.wh_project_read(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql")
    assert opened["text"] == VIEW
    saved = svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql", VIEW + "-- edited\n", expect=opened["version"])
    f = repo / "WH_Gold.Warehouse" / "dbo" / "Views" / "vw_Sales.sql"
    f.write_text("CREATE VIEW dbo.vw_Sales AS SELECT 2 AS x;", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since you opened it"):
        svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql", "x", expect=saved["version"])
    with pytest.raises(ValueError, match="isn't in the project"):
        svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/gone.sql", "x", expect="0123456789abcdef")
    with pytest.raises(ValueError, match="Open the file again"):
        svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql", "x", expect="")


def test_a_write_that_fails_leaves_no_temp_file(svc, repo, monkeypatch):
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    opened = svc.wh_project_read(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql")

    def locked(src, dst):
        raise PermissionError("The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(wp.os, "replace", locked)
    with pytest.raises(PermissionError):
        svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/vw_Sales.sql", "x", expect=opened["version"])
    assert sorted(p.name for p in (repo / "WH_Gold.Warehouse" / "dbo" / "Views").iterdir()) == ["vw_Sales.sql"]


def test_paths_stay_inside_the_project(svc, repo, tmp_path):
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    for bad in ("../outside.sql", "/etc/passwd", "dbo/../../x.sql", "dbo/Views/vw_Sales.txt", "dbo\\Views\\vw_Sales.sql", "CON.sql"):
        with pytest.raises(ValueError):
            svc.wh_project_read(str(repo), "WH_Gold.Warehouse", bad)
    for bad in ("..", "sub/../WH_Gold.Warehouse", "dbo", ""):
        with pytest.raises(ValueError):
            svc.wh_project_files(str(repo), bad)
    with pytest.raises(ValueError, match="Source control"):
        svc.wh_project_files(str(tmp_path / "elsewhere"), "WH_Gold.Warehouse")
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "a:b", "CREATE TABLE dbo.[a:b] (x int);"), ("index", "dbo", "i", "x")),
                               folder="WH_Gold.Warehouse")
    assert out["counts"]["skipped"] == 2


@pytest.mark.skipif(os.name == "nt", reason="links need privileges on Windows")
def test_links_are_refused(svc, repo, tmp_path):
    outside = tmp_path / "outside"
    (outside / "Views").mkdir(parents=True)
    (outside / "Views" / "v.sql").write_text(VIEW, encoding="utf-8")
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    os.symlink(outside, repo / "WH_Gold.Warehouse" / "linked")
    os.symlink(outside, repo / "Linked.Warehouse")
    assert [f["path"] for f in svc.wh_project_files(str(repo), "WH_Gold.Warehouse")["files"]] == ["dbo/Views/vw_Sales.sql"]
    assert [p["folder"] for p in svc.wh_projects_list()["projects"]] == ["WH_Gold.Warehouse"]
    with pytest.raises(ValueError, match="link"):
        svc.wh_project_read(str(repo), "WH_Gold.Warehouse", "linked/Views/v.sql")
    with pytest.raises(ValueError, match="link"):
        svc.wh_project_files(str(repo), "Linked.Warehouse")


def test_only_reads_are_read_only_and_the_sql_is_never_audited():
    assert wp.METHODS <= server.METHODS
    assert not audit.audited("wh_project_files") and not audit.audited("wh_project_read") and not audit.audited("wh_projects_list")
    assert audit.audited("wh_project_save") and audit.audited("wh_project_write")
    assert audit.target({"repo": "r", "text": TABLE})["text"] == "(given)"
    assert audit.target({"repo": "r", "objects": objs(("table", "dbo", "t", TABLE))})["objects"] == "(given)"


# ---- compare (WH-12)

from fabric_desktop import warehouse_compare as wc  # noqa: E402


def test_formatting_case_and_brackets_dont_make_a_difference():
    fabric = "CREATE TABLE [dbo].[DimCustomer] (\n    [CustomerKey] INT NOT NULL,\n    [Segment] VARCHAR (20) NULL\n);\n\n\nGO\n"
    assert wc.same(TABLE, fabric)
    assert wc.same("CREATE VIEW v AS SELECT 'A' AS x -- note", "create view [v]\nAS Select 'A' as [x];")
    assert not wc.same("SELECT 'A'", "SELECT 'a'"), "strings are compared as written"
    assert not wc.same("CREATE VIEW v AS SELECT [a b] FROM t", "CREATE VIEW v AS SELECT [a c] FROM t")


def test_names_keep_their_case_as_the_warehouse_collation_does():
    # Fabric's BIN2 collation: [Region] and [region] are different columns, dbo.Sales and dbo.sales different tables.
    assert not wc.same("CREATE VIEW v AS SELECT [Region] FROM t", "CREATE VIEW v AS SELECT [region] FROM t")
    assert not wc.same("CREATE VIEW v AS SELECT Region FROM dbo.Sales", "CREATE VIEW v AS SELECT Region FROM dbo.sales")
    assert wc.same("CREATE VIEW v AS SELECT CAST(Region AS VARCHAR(5)) FROM t", "create view v as select cast(Region as varchar(5)) from t")
    project = TABLE.replace("[Segment] varchar(20) NULL", "[segment] varchar(20) NULL")
    assert wc.table_detail(TABLE, project) == ["column segment is only in the project", "column Segment is only in the warehouse"]


def test_a_column_named_like_a_type_keeps_its_case():
    assert not wc.same("CREATE VIEW v AS SELECT Date, Count FROM t", "CREATE VIEW v AS SELECT date, count FROM t")
    assert not wc.same("CREATE VIEW v AS SELECT a AS Date FROM t", "CREATE VIEW v AS SELECT a AS date FROM t")
    assert wc.same("CREATE VIEW v AS SELECT CAST(Date AS DATE), MAX(Count) FROM t",
                   "create view v as select cast(Date as date), max(Count) from t")
    assert wc.same("CREATE TABLE t ([Date] DATE NOT NULL, Count INT NULL)", "CREATE TABLE t ([Date] date NOT NULL, Count int NULL)")
    assert not wc.same("CREATE TABLE t ([Date] date NULL)", "CREATE TABLE t ([date] date NULL)")
    assert wc.same("CREATE FUNCTION f(@d DATE) RETURNS INT AS BEGIN RETURN CONVERT(INT, @d) END",
                   "create function f(@d date) returns int as begin return convert(int, @d) end")


def test_a_go_line_inside_a_string_or_comment_is_kept():
    view = "CREATE VIEW v AS SELECT 'a\nGO\nb' AS x\nGO\n"
    assert not wc.same(view, "CREATE VIEW v AS SELECT 'a\nb' AS x\nGO\n"), "the string's own GO line counts"
    assert wc.same(view, "CREATE VIEW v AS SELECT 'a\nGO\nb' AS x;")
    assert wc.tokens("/* one\nGO\ntwo */ SELECT 1\n go ;\n") == ["select", "1"]


def test_a_changed_table_says_which_columns_differ():
    project = TABLE.replace("[Segment] varchar(20) NULL", "[Segment] varchar(40) NULL,\n    [Region] nvarchar(50) NULL")
    assert wc.table_detail(TABLE, project) == ["column Region is only in the project",
                                               "column Segment: varchar(20) NULL in the warehouse, varchar(40) NULL in the project"]
    with_key = TABLE.replace("NULL\n);", "NULL,\n    CONSTRAINT pk PRIMARY KEY NONCLUSTERED ([CustomerKey]) NOT ENFORCED\n);")
    assert wc.table_detail(TABLE, with_key) == ["the table's options differ"], "a constraint isn't a column"


def test_side_by_side_rows_pair_changed_lines():
    rows = wc.side_by_side("a\nb\nc\n", "a\nB\nc\nd")["rows"]
    assert [r["op"] for r in rows] == ["same", "changed", "same", "added"]
    assert rows[3] == {"op": "added", "left": None, "right": "d"}


def test_compare_lists_every_object_on_either_side(svc, repo):
    svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE.replace("varchar(20)", "varchar(40)")),
                                         ("view", "dbo", "vw_Sales", VIEW),
                                         ("procedure", "dbo", "usp_New", "CREATE PROCEDURE dbo.usp_New AS SELECT 1;")),
                         create="WH_Gold")
    (repo / "WH_Gold.Warehouse" / "notes.sql").write_text("-- just notes\n", encoding="utf-8")
    warehouse = objs(("table", "dbo", "DimCustomer", TABLE), ("view", "dbo", "vw_Sales", VIEW.replace("SELECT", "select")),
                     ("view", "dbo", "vw_Old", "CREATE VIEW dbo.vw_Old AS SELECT 2 AS y;"))
    out = svc.wh_project_compare(str(repo), "WH_Gold.Warehouse", warehouse)
    states = {(r["kind"], r["name"]): r["state"] for r in out["objects"]}
    assert states == {("table", "DimCustomer"): "changed", ("view", "vw_Sales"): "same",
                      ("view", "vw_Old"): "only_warehouse", ("procedure", "usp_New"): "only_project"}
    assert out["counts"] == {"changed": 1, "only_project": 1, "only_warehouse": 1, "same": 1}
    changed = next(r for r in out["objects"] if r["state"] == "changed")
    assert changed["path"] == "dbo/Tables/DimCustomer.sql" and changed["detail"] == [
        "column Segment: varchar(20) NULL in the warehouse, varchar(40) NULL in the project"]
    assert out["unread"] == [{"path": "notes.sql", "why": "It doesn't create a table, view, procedure, function or schema."}]
    rows = svc.wh_project_diff(changed["warehouse_text"], changed["project_text"])["rows"]
    assert [r["op"] for r in rows].count("changed") == 1
    assert not audit.audited("wh_project_compare") and not audit.audited("wh_project_diff")


def test_fabrics_own_schemas_are_never_imported_compared_or_deployed(svc, repo):
    insights = "CREATE VIEW [queryinsights].[exec_requests_history] AS SELECT 1 AS x;\nGO\n"
    out = svc.wh_project_write(str(repo), objs(("schema", "queryinsights", "queryinsights", "CREATE SCHEMA [queryinsights];"),
                                               ("schema", "_rsc", "_rsc", "CREATE SCHEMA [_rsc];"),
                                               ("view", "queryinsights", "exec_requests_history", insights),
                                               ("table", "dbo", "DimCustomer", TABLE)), create="WH_Gold")
    assert out["counts"] == {"added": 1, "changed": 0, "same": 0, "skipped": 3}
    assert {r["why"] for r in out["skipped"]} == {wp.FABRIC_OWN}
    project = repo / "WH_Gold.Warehouse"
    assert not (project / "queryinsights").exists() and not (project / "Security").exists()
    # A project imported before this fix keeps its file, but Compare and Deploy leave it out.
    (project / "queryinsights" / "Views").mkdir(parents=True)
    (project / "queryinsights" / "Views" / "exec_requests_history.sql").write_text(insights, encoding="utf-8")
    warehouse = objs(("table", "dbo", "DimCustomer", TABLE), ("view", "QueryInsights", "exec_requests_history", insights),
                     ("schema", "_rsc", "_rsc", "CREATE SCHEMA [_rsc];"))
    cmp = svc.wh_project_compare(str(repo), "WH_Gold.Warehouse", warehouse)
    assert [(r["name"], r["state"]) for r in cmp["objects"]] == [("DimCustomer", "same")]
    assert cmp["unread"] == [{"path": "queryinsights/Views/exec_requests_history.sql", "why": wp.FABRIC_OWN}]
    assert svc.wh_project_deploy_plan(str(repo), "WH_Gold.Warehouse", warehouse, environment="dev")["changes"] == []


# ---- deploy (WH-13)


def _row(state, kind, name, project_text="", warehouse_text="", schema="dbo", path=""):
    return {"state": state, "kind": kind, "schema": schema, "name": name, "path": path,
            "project_text": project_text, "warehouse_text": warehouse_text}


def test_a_deploy_creates_alters_adds_columns_and_never_ticks_a_drop():
    added = TABLE.replace("[Segment] varchar(20) NULL", "[Segment] varchar(20) NULL,\n    [Region] nvarchar(50) NULL")
    plan = wc.deploy_plan([
        _row("changed", "procedure", "usp_Load", "-- loads\nCREATE PROCEDURE dbo.usp_Load AS SELECT 2;", "x"),
        _row("only_warehouse", "view", "vw_Old"),
        _row("changed", "table", "DimCustomer", added, TABLE),
        _row("only_project", "schema", "staging", "CREATE SCHEMA [staging];", schema="staging"),
        _row("only_project", "table", "FactTargets", "CREATE TABLE dbo.FactTargets (a int NULL);"),
        _row("changed", "view", "vw_Sales", "CREATE OR ALTER VIEW dbo.vw_Sales AS SELECT 2 AS x;", VIEW),
    ])
    assert [(c["action"], c["name"], c["ticked"], c["offered"]) for c in plan] == [
        ("create", "staging", True, True), ("add_columns", "DimCustomer", True, True), ("create", "FactTargets", True, True),
        ("alter", "vw_Sales", True, True), ("alter", "usp_Load", True, True), ("drop", "vw_Old", False, True)]
    assert plan[1]["sql"] == "ALTER TABLE [dbo].[DimCustomer] ADD [Region] nvarchar(50) NULL;"
    assert plan[4]["sql"] == "-- loads\nCREATE OR ALTER PROCEDURE dbo.usp_Load AS SELECT 2;"
    assert plan[3]["sql"].startswith("CREATE OR ALTER VIEW dbo.vw_Sales")
    assert plan[5]["sql"] == "DROP VIEW [dbo].[vw_Old];"


@pytest.mark.parametrize("project", [
    TABLE.replace("varchar(20)", "varchar(40)"),                                        # a type changes
    TABLE.replace(",\n    [Segment] varchar(20) NULL", ""),                              # a column goes
    TABLE.replace("[Segment] varchar(20) NULL", "[Segment] varchar(20) NULL,\n    [Region] int NOT NULL"),  # NOT NULL
    TABLE.replace("    [Segment]", "    [Region] int NULL,\n    [Segment]"),           # not last
])
def test_a_table_change_that_would_rebuild_it_isnt_offered(project):
    (c,) = wc.deploy_plan([_row("changed", "table", "DimCustomer", project, TABLE)])
    assert not c["offered"] and not c["ticked"] and c["why"] == wc.REBUILD


@pytest.mark.parametrize("state,kind,text", [
    ("only_project", "view", "CREATE VIEW dbo.vw_Sales AS SELECT 1 AS x\nGO\nDROP TABLE dbo.FactSales"),
    ("changed", "view", "CREATE VIEW dbo.vw_Sales AS SELECT 2 AS x\nGO\nDROP TABLE dbo.FactSales\nGO\n"),
    ("changed", "procedure", "CREATE PROCEDURE dbo.usp_Load AS SELECT 1;\nGO\nEXEC dbo.usp_Load;"),
    ("only_project", "table", "CREATE TABLE dbo.FactSales (a int NULL);\nTRUNCATE TABLE dbo.FactSales;"),
    ("only_project", "table", "CREATE TABLE dbo.FactSales (a int NULL);\nGO\nTRUNCATE TABLE dbo.FactSales;"),
    ("only_project", "table", "CREATE TABLE dbo.FactSales AS SELECT * FROM dbo.Staging;"),
    ("only_project", "schema", "CREATE SCHEMA staging CREATE TABLE t (a int);"),
    ("changed", "view", "CREATE VIEW dbo.x AS SELECT 1 AS x; DROP TABLE dbo.FactSales;"),
    ("only_project", "function", "CREATE FUNCTION dbo.x() RETURNS TABLE AS RETURN SELECT 1 AS a; TRUNCATE TABLE dbo.t"),
])
def test_a_file_with_more_than_its_object_isnt_offered(state, kind, text):
    (c,) = wc.deploy_plan([_row(state, kind, "x", text, "CREATE VIEW dbo.x AS SELECT 0 AS x" if state == "changed" else "")])
    assert not c["offered"] and not c["ticked"] and c["sql"] == "" and c["why"] in (wc.ONE_DEFINITION, wc.ONE_TABLE)


def test_a_file_that_only_defines_its_object_runs_without_its_go_line():
    (c,) = wc.deploy_plan([_row("only_project", "view", "v", "-- note\nCREATE VIEW dbo.v AS SELECT 'a\nGO\nb' AS x; -- done\nGO\n\n")])
    assert c["offered"] and c["sql"] == "-- note\nCREATE VIEW dbo.v AS SELECT 'a\nGO\nb' AS x; -- done"
    (t,) = wc.deploy_plan([_row("only_project", "table", "t", "CREATE TABLE [dbo].[t] (a decimal(18,2) NULL);\nGO\n")])
    assert t["offered"] and t["sql"] == "CREATE TABLE [dbo].[t] (a decimal(18,2) NULL);"


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo, check=True, capture_output=True)


def test_the_deploy_plan_says_which_files_arent_committed(svc, repo):
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    out = svc.wh_project_deploy_plan(str(repo), "WH_Gold.Warehouse", objs(("view", "dbo", "vw_Old", "CREATE VIEW dbo.vw_Old AS SELECT 1 AS y;")),
                                     environment="dev")
    assert [(c["action"], c["name"], c["git"], c["offered"]) for c in out["changes"]] == [
        ("create", "vw_Sales", "A", True), ("drop", "vw_Old", "", True)], "Dev deploys files as they are"
    assert out["uncommitted"] == ["dbo/Views/vw_Sales.sql"] and out["branch"] == "main" and not out["gated"]
    assert not audit.audited("wh_project_deploy_plan")


@pytest.mark.parametrize("environment", ["prod", "test", "none", ""])
def test_outside_dev_only_committed_and_pushed_files_deploy(svc, repo, tmp_path, environment):
    svc.wh_project_write(str(repo), objs(("view", "dbo", "vw_Sales", VIEW)), create="WH_Gold")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "project")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")
    plan = lambda: {c["name"]: c for c in svc.wh_project_deploy_plan(  # noqa: E731
        str(repo), "WH_Gold.Warehouse", objs(("view", "dbo", "vw_Old", "CREATE VIEW dbo.vw_Old AS SELECT 1 AS y;")),
        environment=environment)["changes"]}
    assert plan()["vw_Sales"]["offered"] and plan()["vw_Sales"]["committed"]

    # Ignored by Git: git status doesn't list it, but it isn't committed, so it's held.
    (repo / ".gitignore").write_text("dbo/Tables/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore")
    _git(repo, "push", "-q")
    svc.wh_project_write(str(repo), objs(("table", "dbo", "FactSales", "CREATE TABLE dbo.FactSales (a int NULL);")), folder="WH_Gold.Warehouse")
    c = plan()["FactSales"]
    assert not c["committed"] and c["held"] and not c["offered"] and not c["ticked"] and c["git"]
    assert plan()["vw_Sales"]["offered"], "a committed file still deploys"

    # Edited since the commit: held. Committed but not pushed: everything is held.
    view = repo / "WH_Gold.Warehouse" / "dbo" / "Views" / "vw_Sales.sql"
    view.write_text(VIEW.replace("1 AS x", "2 AS x"), encoding="utf-8")
    assert plan()["vw_Sales"]["held"]
    _git(repo, "commit", "-qam", "edit")
    assert all(not c["offered"] for c in plan().values())
    _git(repo, "push", "-q")
    assert plan()["vw_Sales"]["offered"] and plan()["vw_Old"]["offered"]


@pytest.mark.parametrize("sql", [
    "CREATE SCHEMA [staging];",
    "CREATE TABLE dbo.FactTargets (a int NULL);",
    "ALTER TABLE [dbo].[DimCustomer] ADD [Region] nvarchar(50) NULL;",
    "CREATE OR ALTER VIEW dbo.vw_Sales AS SELECT 2 AS x;\nGO\n",
    "-- loads\nCREATE OR ALTER PROCEDURE dbo.usp_Load AS BEGIN UPDATE dbo.t SET a = 1; END;\nGO\n",
    "DROP VIEW [dbo].[vw_Old];",
])
def test_each_kind_of_deploy_change_is_a_warehouse_change_the_engine_runs(sql):
    from fabric_desktop import warehouse
    verdicts = warehouse.verdicts(sql, writes=True)
    assert {v.kind for v in verdicts} <= {"warehouse_write", "read_only"} and any(v.kind == "warehouse_write" for v in verdicts), verdicts
