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
        svc.wh_project_save(str(repo), "WH_Gold.Warehouse", "dbo/Views/gone.sql", "x")


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
