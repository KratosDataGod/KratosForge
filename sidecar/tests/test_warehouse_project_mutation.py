"""warehouse_project_service.py paths that mutation testing (batch 21, docs/quality/mutation-testing.md)
showed no test checked: the size and count limits and the messages that name them, how deep projects
and files may sit, which folders are looked in, a name with a quote in brackets, a project made twice,
and which environment a deploy counts as. Everything runs in temporary folders."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fabric_desktop import warehouse_project_service as wp
from fabric_desktop.service import Service

TABLE = "CREATE TABLE [dbo].[A] (x int);\nGO\n"


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def svc(tmp_path):
    return Service(client_id="x", root=tmp_path / "app")


@pytest.fixture
def root(svc, tmp_path, monkeypatch):
    """A repository folder Source control has open (no Git needed for these paths)."""
    r = (tmp_path / "sales").resolve()
    r.mkdir()
    monkeypatch.setattr(svc, "git_repos", lambda: {"repos": [{"exists": True, "path": str(r), "name": "Sales repo"}]})
    return r


def _project(root: Path, *parts: str) -> Path:
    p = root.joinpath(*parts)
    p.mkdir(parents=True)
    return p


# ---- limits


def test_up_to_5000_objects_are_taken_and_5001_are_refused(svc, root):
    _project(root, "WH.Warehouse")
    ok = svc.wh_project_write(str(root), [{}] * 5000, folder="WH.Warehouse", dry_run=True)
    assert ok["counts"]["skipped"] == 5000
    for call in (lambda o: svc.wh_project_write(str(root), o, folder="WH.Warehouse", dry_run=True),
                 lambda o: svc.wh_project_compare(str(root), "WH.Warehouse", o)):
        with pytest.raises(ValueError, match="^Send up to 5000 objects at a time.$"):
            call([{}] * 5001)


def test_a_file_of_exactly_one_megabyte_is_read_and_one_byte_more_is_not(svc, root):
    project = _project(root, "WH.Warehouse")
    body = "CREATE TABLE dbo.A (x int);\n"
    (project / "a.sql").write_bytes((body + "-" * (wp.MAX_FILE - len(body))).encode())
    assert svc.wh_project_read(str(root), "WH.Warehouse", "a.sql")["text"].startswith(body)
    (project / "b.sql").write_bytes(b"-" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match=r"^That file is larger than 1024 KB\.$"):
        svc.wh_project_read(str(root), "WH.Warehouse", "b.sql")


def test_a_save_too_large_names_the_limit_in_kb(svc, root):
    _project(root, "WH.Warehouse")
    with pytest.raises(ValueError, match=r"^A project file can be up to 1024 KB\.$"):
        svc.wh_project_save(str(root), "WH.Warehouse", "a.sql", "x" * (1024 * 1024 + 1), "v")


def test_a_diff_takes_two_megabytes_in_all_and_names_the_limit(svc):
    half = "x" * (1024 * 1024)
    assert isinstance(svc.wh_project_diff(half, half), dict)  # exactly 2 MB in all is taken
    with pytest.raises(ValueError, match=r"^Each side can be up to 1024 KB\.$"):
        svc.wh_project_diff(half, half + "x")


def test_a_small_project_has_no_note_about_being_cut(svc, root):
    project = _project(root, "WH.Warehouse")
    (project / "a.sql").write_text(TABLE, encoding="utf-8")
    out = svc.wh_project_files(str(root), "WH.Warehouse")
    assert out["note"] == "" and [f["path"] for f in out["files"]] == ["a.sql"]


# ---- where projects and files may be


def test_projects_are_found_four_folders_down_and_no_deeper(svc, root):
    _project(root, "a", "b", "c", "d", "Deep.Warehouse")
    _project(root, "a", "b", "c", "d", "e", "TooDeep.Warehouse")
    folders = [p["folder"] for p in svc.wh_projects_list()["projects"]]
    assert folders == ["a/b/c/d/Deep.Warehouse"]
    assert svc._wh_project_dir(str(root), "a/b/c/d/Deep.Warehouse") == root / "a/b/c/d/Deep.Warehouse"
    with pytest.raises(ValueError, match="isn't a warehouse project folder"):
        svc._wh_project_dir(str(root), "a/b/c/d/e/TooDeep.Warehouse")


def test_hidden_build_and_file_entries_hold_no_projects(svc, root):
    _project(root, ".hidden", "H.Warehouse")
    _project(root, "bin", "B.Warehouse")
    _project(root, "Node_Modules", "N.Warehouse")
    (root / "F.Warehouse").write_text("a file, not a folder", encoding="utf-8")
    _project(root, "Real.Warehouse")
    assert [p["folder"] for p in svc.wh_projects_list()["projects"]] == ["Real.Warehouse"]


def test_at_most_50_projects_are_listed_from_one_repository(svc, root):
    for i in range(51):
        _project(root, f"P{i:02}.Warehouse")
    projects = svc.wh_projects_list()["projects"]
    assert len(projects) == 50 and projects[-1]["folder"] == "P49.Warehouse"


def test_a_project_is_named_from_its_platform_file_and_the_repo_by_its_name(svc, root, tmp_path, monkeypatch):
    project = _project(root, "Folder.Warehouse")
    (project / ".platform").write_text(json.dumps({"metadata": {"displayName": "Sales WH"}}), encoding="utf-8")
    out = svc.wh_projects_list()
    assert out["repos"] == [{"repo": str(root), "label": "Sales repo"}]
    assert out["projects"][0]["name"] == "Sales WH" and out["projects"][0]["repo_label"] == "Sales repo"
    monkeypatch.setattr(svc, "git_repos", lambda: {"repos": [{"exists": True, "path": str(root)}]})
    assert svc.wh_projects_list()["repos"] == [{"repo": str(root), "label": "sales"}]


def test_a_file_seven_folders_deep_is_allowed_and_eight_are_not(svc, tmp_path):
    project = tmp_path / "WH.Warehouse"
    assert svc._wh_file(project, "a/b/c/d/e/f/x.sql") == project / "a/b/c/d/e/f/x.sql"
    with pytest.raises(ValueError, match="isn't a .sql file in the project"):
        svc._wh_file(project, "a/b/c/d/e/f/g/x.sql")


def test_a_dangling_link_in_a_file_path_is_refused(svc, tmp_path):
    project = tmp_path / "WH.Warehouse"
    project.mkdir()
    try:
        os.symlink(tmp_path / "nowhere.sql", project / "x.sql")
    except (OSError, NotImplementedError):
        pytest.skip("this account can't make links")
    with pytest.raises(ValueError, match="link to somewhere else"):
        svc._wh_file(project, "x.sql")


# ---- names and objects


def test_quotes_inside_a_bracketed_name_are_part_of_the_name():
    # The name is read from the text, not from the copy with strings blanked, so 'b' stays in it
    assert wp.defines("CREATE TABLE dbo.[a'b'] (x int)") == {"kind": "table", "schema": "dbo", "name": "a'b'"}
    assert wp.defines("CREATE TABLE dbo.[it's] (x int)") == {"kind": "table", "schema": "dbo", "name": "it's"}


def test_a_table_without_a_schema_is_skipped_for_that_reason(svc, root):
    _project(root, "WH.Warehouse")
    out = svc.wh_project_write(str(root), [
        {"kind": "table", "schema": "", "name": "T", "text": "CREATE TABLE T (x int)"},
        {"kind": "schema", "schema": "staging", "name": "staging", "text": "CREATE SCHEMA staging"},
    ], folder="WH.Warehouse", dry_run=True)
    assert out["skipped"] == [{"kind": "table", "name": ".T",
                               "why": "It isn't a table, view, procedure, function or schema with a name."}]
    assert out["added"] == [{"kind": "schema", "name": "staging", "path": "Security/staging.sql"}]


def test_compare_leaves_out_a_warehouse_table_without_a_schema(svc, root):
    _project(root, "WH.Warehouse")
    out = svc.wh_project_compare(str(root), "WH.Warehouse", [
        {"kind": "table", "schema": "", "name": "T", "text": "CREATE TABLE T (x int)"},
        {"kind": "schema", "schema": "", "name": "staging", "text": "CREATE SCHEMA staging"},
    ])
    assert [(o["kind"], o["name"]) for o in out["objects"]] == [("schema", "staging")]


def test_a_project_made_twice_keeps_its_first_platform_file(svc, root):
    first = svc.wh_project_write(str(root), [{"kind": "table", "schema": "dbo", "name": "A", "text": TABLE}], create="WH")
    platform = (root / "WH.Warehouse" / ".platform").read_text(encoding="utf-8")
    again = svc.wh_project_write(str(root), [{"kind": "table", "schema": "dbo", "name": "A", "text": TABLE}], create="WH")
    assert first["created"] and again["created"] and again["counts"]["same"] == 1
    assert (root / "WH.Warehouse" / ".platform").read_text(encoding="utf-8") == platform


# ---- deploy environment


def test_prod_sent_by_the_page_without_a_workspace_counts_as_no_environment(svc, root, monkeypatch):
    _project(root, "WH.Warehouse")
    monkeypatch.setattr(svc, "_wh_git", lambda r, p: {"codes": {}, "branch": "main", "commit": "c",
                                                       "upstream": "origin/main", "ahead": 0})
    plan = svc.wh_project_deploy_plan(str(root), "WH.Warehouse", [], environment="prod")
    assert plan["environment"] == "none" and plan["gated"] is True
