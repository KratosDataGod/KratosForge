"""Saved and shared queries for the Warehouse page (sql_queries_service.py)."""

import shutil
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop import sql_queries_service as sq
from fabric_desktop.service import Service

SQL = "SELECT Region, SUM(Amount) AS Revenue\nFROM dbo.FactSales\nGROUP BY Region;"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


def test_a_saved_query_reads_back_and_any_sql_file_is_one():
    meta = {"title": "Revenue by region", "warehouse": "WH_Gold", "description": "Monthly review", "sql": SQL}
    assert sq.parse(sq.render(meta), "x") == meta
    assert sq.parse("SELECT 1", "Quick count") == {"title": "Quick count", "warehouse": "", "description": "", "sql": "SELECT 1"}
    assert sq.problem({"title": "A", "sql": ""}) == "There's no SQL to save."


def test_save_list_save_again_and_delete_in_my_queries(svc, tmp_path):
    first = svc.sql_query_save("mine", "Revenue by region", SQL, warehouse="WH_Gold")
    second = svc.sql_query_save("mine", "Revenue by region", "SELECT 1")
    assert (first["name"], second["name"]) == ("Revenue by region.sql", "Revenue by region (2).sql")
    listed = svc.sql_queries_list()
    assert [p["label"] for p in listed["places"]] == ["My queries"]
    q = next(q for q in listed["queries"] if q["name"] == first["name"])
    assert (q["title"], q["warehouse"], q["sql"], q["problem"]) == ("Revenue by region", "WH_Gold", SQL, "")
    svc.sql_query_save("mine", "Revenue by region", SQL + "\n-- changed", warehouse="WH_Gold", name=first["name"])
    assert (tmp_path / "app" / "sql-queries" / first["name"]).read_text(encoding="utf-8").endswith("-- changed\n")
    svc.sql_query_delete("mine", first["name"])
    assert [q["name"] for q in svc.sql_queries_list()["queries"]] == ["Revenue by region (2).sql"]
    assert not (tmp_path / "app" / "sql-templates").exists(), "queries and templates keep their own folders"


def test_queries_keep_to_their_place(svc, tmp_path):
    for name in ("../x.sql", "a/b.sql", "CON.sql", ".x.sql"):
        with pytest.raises(ValueError, match="query file name"):
            svc.sql_query_delete("mine", name)
    with pytest.raises(ValueError, match="My queries or a repository"):
        svc.sql_query_save(str(tmp_path), "A", "SELECT 1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.sql").write_text("SELECT 1", encoding="utf-8")
    folder = tmp_path / "app" / "sql-queries"
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make links")
    listed = svc.sql_queries_list()
    assert listed["queries"] == [] and "sql-queries folder in My queries points somewhere else" in listed["notes"][0]
    with pytest.raises(ValueError, match="somewhere else"):
        svc.sql_query_delete("mine", "victim.sql")
    assert (outside / "victim.sql").exists()


def test_a_repository_is_where_a_team_shares_queries(svc, tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    repo = tmp_path / "team-repo"
    repo.mkdir()
    svc.git_init(str(repo))
    (repo / "sql-queries").mkdir()
    (repo / "sql-queries" / "Open orders.sql").write_text("SELECT * FROM dbo.Orders WHERE Open = 1", encoding="utf-8")
    listed = svc.sql_queries_list()
    assert [(q["title"], q["place_label"], q["warehouse"]) for q in listed["queries"]] == [("Open orders", "team-repo", "")]
    saved = svc.sql_query_save(listed["places"][1]["id"], "Late shipments", "SELECT 2", warehouse="WH_Gold")
    assert (repo / "sql-queries" / saved["name"]).read_text(encoding="utf-8").startswith("-- query: Late shipments\n-- warehouse: WH_Gold\n")


def test_only_the_list_is_read_only_and_the_sql_is_never_audited():
    assert sq.METHODS <= server.METHODS
    assert not audit.audited("sql_queries_list")
    assert audit.audited("sql_query_save") and audit.audited("sql_query_delete")
    assert audit.target({"place": "mine", "title": "A", "sql": SQL})["sql"] == "(given)"
