"""A warehouse as files in Git (warehouse_project_service.py): the guards the other warehouse project tests
don't reach. A folder that can't be listed, a folder that isn't a repository, a project outside the
repository, a write outside the project, a file that can't be read, and a file that already holds
another object. Everything runs in temporary folders."""

from __future__ import annotations

from fabric_desktop import warehouse_project_service as wp  # noqa: F401 - the module under test
from tests.test_warehouse_project import TABLE, objs, repo, svc  # noqa: F401 - fixtures


def test_a_folder_that_cant_be_listed_holds_no_projects(svc, tmp_path):  # noqa: F811
    assert svc._wh_find(tmp_path / "gone") == []


def test_a_folder_that_isnt_a_repository_shows_files_without_codes(svc, tmp_path):  # noqa: F811
    plain = tmp_path / "plain"
    (plain / "WH.Warehouse").mkdir(parents=True)
    state = svc._wh_git(plain, plain / "WH.Warehouse")
    assert state == {"codes": {}, "branch": None, "commit": None, "upstream": None, "ahead": None}


def test_a_project_outside_the_repository_has_nothing_committed(svc, repo, tmp_path):  # noqa: F811
    outside = tmp_path / "elsewhere" / "WH.Warehouse"
    outside.mkdir(parents=True)
    assert svc._wh_committed(repo, outside, ["dbo/Tables/A.sql"]) == {"dbo/Tables/A.sql": False}


def test_a_folder_outside_the_project_is_never_written(svc, tmp_path):  # noqa: F811
    project = tmp_path / "WH.Warehouse"
    project.mkdir()
    try:
        svc._wh_mkdirs(project, tmp_path / "other" / "x.sql")
    except ValueError as exc:
        assert "link to somewhere else" in str(exc)
    else:
        raise AssertionError("a folder outside the project was accepted")


def test_a_file_that_cant_be_read_is_left_out_of_the_map(svc, repo):  # noqa: F811
    svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE)), create="WH")
    project = repo / "WH.Warehouse"
    (project / "dbo" / "Tables" / "Broken.sql").write_bytes(b"\xff\xfe\x00 not text")
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE)), folder="WH.Warehouse")
    assert out["counts"]["same"] == 1 and out["counts"]["skipped"] == 0


def test_an_object_whose_file_already_holds_another_object_is_skipped(svc, repo):  # noqa: F811
    svc.wh_project_write(str(repo), objs(("table", "dbo", "DimCustomer", TABLE)), create="WH")
    project = repo / "WH.Warehouse"
    (project / "dbo" / "Tables" / "Foo.sql").write_text("CREATE TABLE [dbo].[Bar] (x int);\nGO\n", encoding="utf-8")
    out = svc.wh_project_write(str(repo), objs(("table", "dbo", "Foo", "CREATE TABLE [dbo].[Foo] (y int);\nGO\n")),
                               folder="WH.Warehouse")
    assert out["skipped"][0]["why"] == "Its file would be dbo/Tables/Foo.sql, which already defines dbo.Bar."
    assert "Bar" in (project / "dbo" / "Tables" / "Foo.sql").read_text(encoding="utf-8")  # not written over
