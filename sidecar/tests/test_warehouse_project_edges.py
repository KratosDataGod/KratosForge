"""A warehouse as files in Fabric's Git layout (warehouse_project_service.py), the paths the main tests
don't reach: projects nested in folders, files that are too large, not UTF-8 or define nothing, a project
folder gone since it was chosen, dry runs, and asks with too many objects. All in a local repository."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import warehouse_project_service as wp
from tests.test_warehouse_project import TABLE, VIEW, objs, repo, svc  # noqa: F401 - fixtures


@pytest.fixture
def project(svc, repo):  # noqa: F811
    svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE)), create="WH")
    return repo / "WH.Warehouse"


# ---- finding projects


def test_projects_nested_in_folders_are_found_and_a_bad_platform_file_keeps_the_folder_name(svc, repo):  # noqa: F811
    nested = repo / "warehouses" / "gold" / "Sales.Warehouse"
    nested.mkdir(parents=True)
    (nested / ".platform").write_text("{not json")
    found = svc.wh_projects_list()["projects"]
    assert {"folder": "warehouses/gold/Sales.Warehouse", "name": "Sales"}.items() <= found[0].items()


def test_a_repository_whose_folder_is_gone_lists_no_projects(svc, repo, tmp_path):  # noqa: F811
    other = tmp_path / "other"
    other.mkdir()
    svc.git_init(str(other))
    other.rename(tmp_path / "moved")
    assert [r["repo"] for r in svc.wh_projects_list()["repos"]] == [str(repo)]


def test_a_project_folder_gone_since_it_was_chosen_says_so(svc, repo):  # noqa: F811
    with pytest.raises(ValueError, match="isn't there any more"):
        svc.wh_project_files(str(repo), "Gone.Warehouse")


# ---- files in a project


def test_each_file_that_cant_be_used_says_why(svc, repo, project, monkeypatch):  # noqa: F811
    (project / "dbo" / "Tables" / "notes.sql").write_text("-- just a note\n")
    (project / "dbo" / "Tables" / "latin1.sql").write_bytes(b"CREATE TABLE dbo.T (a int) -- caf\xe9")
    (project / "dbo" / "Tables" / "big.sql").write_text("CREATE TABLE dbo.Big (a int)\n" + "--\n" * 50)
    monkeypatch.setattr(wp, "MAX_FILE", 100)
    files = {f["path"]: f["problem"] for f in svc.wh_project_files(str(repo), "WH.Warehouse")["files"]}
    assert files["dbo/Tables/notes.sql"] == "It doesn't create a table, view, procedure, function or schema."
    assert files["dbo/Tables/latin1.sql"] == "It couldn't be read as UTF-8 text."
    assert files["dbo/Tables/big.sql"] == "It is larger than 0 KB."
    with pytest.raises(ValueError, match="That file is larger than"):
        svc.wh_project_read(str(repo), "WH.Warehouse", "dbo/Tables/big.sql")
    with pytest.raises(ValueError, match="A project file can be up to"):
        svc.wh_project_save(str(repo), "WH.Warehouse", "dbo/Tables/big.sql", "x" * 200, "v")


def test_a_long_project_is_cut_and_says_so(svc, repo, project, monkeypatch):  # noqa: F811
    (project / "dbo" / "Views").mkdir(parents=True)
    (project / "dbo" / "Views" / "v.sql").write_text(VIEW)
    monkeypatch.setattr(wp, "MAX_FILES", 1)
    out = svc.wh_project_files(str(repo), "WH.Warehouse")
    assert len(out["files"]) == 1 and "more than 1 .sql files" in out["note"]


def test_reading_or_saving_a_file_that_isnt_there_any_more(svc, repo, project):  # noqa: F811
    with pytest.raises(ValueError, match="isn't in the project any more"):
        svc.wh_project_read(str(repo), "WH.Warehouse", "dbo/Tables/Nope.sql")
    with pytest.raises(ValueError, match="Open the file again before saving it"):
        svc.wh_project_save(str(repo), "WH.Warehouse", "dbo/Tables/Nope.sql", "x", "")
    with pytest.raises(ValueError, match="Import or New object makes it again"):
        svc.wh_project_save(str(repo), "WH.Warehouse", "dbo/Tables/Nope.sql", "x", "v")


# ---- import


@pytest.mark.parametrize("kwargs, says", [
    ({"objects": "not a list", "create": "WH"}, "Send up to"),
    ({"objects": []}, "Choose a project, or the warehouse to make one for"),
    ({"objects": [], "create": "a/b"}, "can't be a folder name"),
    ({"objects": [], "create": "a/b", "dry_run": True}, "can't be a folder name"),
])
def test_an_import_that_cant_start_is_refused(svc, repo, kwargs, says):  # noqa: F811
    with pytest.raises(ValueError, match=says):
        svc.wh_project_write(str(repo), **kwargs)


def test_a_dry_run_for_a_new_project_writes_nothing(svc, repo):  # noqa: F811
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE)), create="Fresh", dry_run=True)
    assert out["counts"]["added"] == 1 and out["created"] is False and not (repo / "Fresh.Warehouse").exists()


def test_a_dry_run_into_an_existing_project_says_what_would_change(svc, repo, project):  # noqa: F811
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE + "-- edited\n")),
                               create="WH", dry_run=True)
    assert out["counts"]["changed"] == 1 and (project / "dbo" / "Tables" / "DimCustomer.sql").read_text() == TABLE


def test_an_object_too_large_is_skipped_and_one_already_defined_elsewhere_is_refused(svc, repo, project, monkeypatch):  # noqa: F811
    (project / "dbo" / "Tables" / "Customer.sql").write_text("CREATE TABLE dbo.Customer (a int)\n")
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "customer", "CREATE TABLE dbo.customer (a int)")),
                               folder="WH.Warehouse")
    assert "which is dbo.Customer's file apart from letter case" in out["skipped"][0]["why"]
    monkeypatch.setattr(wp, "MAX_FILE", 10)
    out = svc.wh_project_write(str(repo), objs(("view", "dbo", "v", VIEW)), folder="WH.Warehouse")
    assert out["skipped"][0]["why"] == "Its script is larger than 0 KB."


# ---- compare and diff


def test_compare_lists_files_it_couldnt_use(svc, repo, project, monkeypatch):  # noqa: F811
    tables = project / "dbo" / "Tables"
    (tables / "again.sql").write_text(TABLE)
    (tables / "sys.sql").write_text("CREATE VIEW [sys].[mine] AS SELECT 1 AS x;\n")
    (tables / "latin1.sql").write_bytes(b"CREATE TABLE dbo.T (a int) -- caf\xe9")
    unread = {u["path"]: u["why"] for u in svc.wh_project_compare(str(repo), "WH.Warehouse", [])["unread"]}
    assert unread["dbo/Tables/again.sql"] == "dbo.DimCustomer is also defined in dbo/Tables/DimCustomer.sql."
    assert unread["dbo/Tables/sys.sql"] == wp.FABRIC_OWN
    assert unread["dbo/Tables/latin1.sql"] == "It couldn't be read as UTF-8 text."
    monkeypatch.setattr(wp, "MAX_FILE", 10)
    unread = {u["path"]: u["why"] for u in svc.wh_project_compare(str(repo), "WH.Warehouse", [])["unread"]}
    assert unread["dbo/Tables/DimCustomer.sql"] == "It is larger than 0 KB."
    with pytest.raises(ValueError, match="Send up to"):
        svc.wh_project_compare(str(repo), "WH.Warehouse", "nope")


def test_a_diff_with_sides_too_large_is_refused(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wp, "MAX_FILE", 1)
    with pytest.raises(ValueError, match="Each side can be up to"):
        svc.wh_project_diff("abc", "def")


# ---- deploy environment


def test_a_metadata_project_that_cant_be_read_gates_the_deploy(svc, monkeypatch):  # noqa: F811
    def broken():
        raise json.JSONDecodeError("bad", "", 0)

    monkeypatch.setattr(svc, "_project", broken)
    assert svc._wh_workspace_env("ws-1") == "none"
