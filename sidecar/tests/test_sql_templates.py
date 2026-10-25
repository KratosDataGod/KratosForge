"""Your own SQL templates for the Warehouse page's gallery (sql_templates_service.py)."""

import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop import sql_templates_service as st
from fabric_desktop.service import Service

SQL = "TRUNCATE TABLE [<schema_name, sysname, dbo>].[<table_name, sysname, DimCustomer>];"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return Service(client_id="x", root=tmp_path / "app")


def test_a_template_file_reads_back_as_it_was_saved():
    meta = {"title": "Load a dimension", "group": "Team loads", "description": "Reload from staging", "endpoint": True, "sql": SQL}
    assert st.parse(st.render(meta), "x") == meta
    assert st.parse("SELECT 1", "Quick count") == {"title": "Quick count", "group": "", "description": "", "endpoint": False, "sql": "SELECT 1"}
    assert st.parse("-- title: A\n-- endpoint: no\n-- a comment that is part of the SQL\nSELECT 1")["sql"].startswith("-- a comment")



def test_comments_at_the_top_of_the_sql_stay_the_sqls():
    sql = "-- Description: old notes\n-- group: not a header\nSELECT 1;"
    meta = {"title": "Kept", "group": "Mine", "description": "", "endpoint": False, "sql": sql}
    assert st.parse(st.render(meta), "x") == meta
    assert st.parse(st.render(st.parse(st.render(meta))), "x") == meta, "a second save keeps them too"

def test_problems_are_said_before_anything_is_written():
    assert st.problem({"title": "", "sql": SQL}) == "Give the template a title."
    assert "one line" in st.problem({"title": "a\nb", "sql": SQL})
    assert st.problem({"title": "A", "sql": " "}) == "Write the template's SQL."
    assert "kind number" in st.problem({"title": "A", "sql": "SELECT <n, number, 5>"})
    assert st.problem({"title": "A", "sql": SQL}) == ""


def test_save_list_edit_export_and_delete_in_my_templates(svc, tmp_path):
    saved = svc.sql_template_save("mine", "Load a dimension", SQL, group="Team loads", endpoint=False)
    again = svc.sql_template_save("mine", "Load a dimension", "SELECT 1")
    assert (saved["name"], again["name"]) == ("Load a dimension.sql", "Load a dimension (2).sql")
    listed = svc.sql_templates_list()
    assert [p["id"] for p in listed["places"]] == ["mine"]
    first = next(t for t in listed["templates"] if t["name"] == saved["name"])
    assert (first["title"], first["group"], first["sql"], first["problem"]) == ("Load a dimension", "Team loads", SQL, "")
    svc.sql_template_save("mine", "Load a dimension, v2", "SELECT 2", name=saved["name"])
    text = (tmp_path / "app" / "sql-templates" / saved["name"]).read_text(encoding="utf-8")
    assert text.startswith("-- template: Load a dimension, v2\n") and text.endswith("SELECT 2\n")
    out = svc.sql_template_export("mine", saved["name"])
    out2 = svc.sql_template_export("mine", saved["name"])
    assert Path(out["path"]).read_text(encoding="utf-8") == text and out2["path"].endswith("(2).sql")
    svc.sql_template_delete("mine", saved["name"])
    assert [t["name"] for t in svc.sql_templates_list()["templates"]] == ["Load a dimension (2).sql"]


def test_import_reads_the_header_and_never_replaces_a_template(svc):
    first = svc.sql_template_import("mine", "C:\\Users\\a\\reload.sql", "-- group: Ops\n" + SQL)
    second = svc.sql_template_import("mine", "reload.sql", SQL)
    assert (first["name"], second["name"]) == ("reload.sql", "reload (2).sql")
    t = next(t for t in svc.sql_templates_list()["templates"] if t["name"] == "reload.sql")
    assert (t["title"], t["group"]) == ("reload", "Ops")
    with pytest.raises(ValueError, match="64 KB"):
        svc.sql_template_import("mine", "big.sql", "x" * (65 * 1024))


def test_a_call_names_a_place_and_a_file_name_never_a_path(svc, tmp_path):
    svc.sql_template_save("mine", "A", "SELECT 1")
    for name in ("../secrets.sql", "..\\x.sql", "/etc/x.sql", "a/b.sql", ".hidden.sql", "x.txt", ""):
        with pytest.raises(ValueError, match="template file name"):
            svc.sql_template_delete("mine", name)
    with pytest.raises(ValueError, match="Source control"):
        svc.sql_template_save(str(tmp_path), "A", "SELECT 1")
    with pytest.raises(ValueError, match="isn't there"):
        svc.sql_template_save("mine", "A", "SELECT 1", name="gone.sql")


def test_a_repository_opened_in_source_control_is_a_place_for_team_templates(svc, tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    repo = tmp_path / "team-repo"
    repo.mkdir()
    svc.git_init(str(repo))
    places = svc.sql_templates_list()["places"]
    assert [p["label"] for p in places] == ["My templates", "team-repo"]
    saved = svc.sql_template_save(places[1]["id"], "Team reload", SQL, group="Team")
    assert (repo / "sql-templates" / "Team reload.sql").is_file() and saved["place"] == places[1]["id"]
    team = [t for t in svc.sql_templates_list()["templates"] if t["place"] != "mine"]
    assert [(t["title"], t["place_label"]) for t in team] == [("Team reload", "team-repo")]


def test_an_unreadable_or_oversized_file_is_listed_with_why(svc, tmp_path):
    folder = tmp_path / "app" / "sql-templates"
    folder.mkdir(parents=True)
    (folder / "big.sql").write_text("x" * (65 * 1024), encoding="utf-8")
    (folder / "latin.sql").write_bytes("SELECT 'caf\xe9'".encode("latin-1"))
    (folder / "bad kind.sql").write_text("SELECT <n, number, 1>", encoding="utf-8")
    got = {t["name"]: t["problem"] for t in svc.sql_templates_list()["templates"]}
    assert "64 KB" in got["big.sql"] and "UTF-8" in got["latin.sql"] and "kind number" in got["bad kind.sql"]


def test_only_the_list_is_read_only_and_template_text_is_never_audited(svc):
    assert not audit.audited("sql_templates_list")
    assert all(audit.audited(m) for m in st.METHODS - {"sql_templates_list"})
    assert st.METHODS <= server.METHODS
    target = audit.target({"place": "mine", "title": "A", "sql": SQL, "text": SQL, "description": "d"})
    assert target["sql"] == target["text"] == "(given)"
    reply = server.handle(svc, json.dumps({"id": 1, "method": "sql_templates_list", "params": {}}))
    assert reply["result"]["templates"] == []


def test_links_never_lead_a_template_call_outside_its_place(svc, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.sql").write_text("SELECT 1", encoding="utf-8")
    folder = tmp_path / "app" / "sql-templates"
    folder.parent.mkdir(parents=True)
    try:
        folder.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make links")
    listed = svc.sql_templates_list()
    assert listed["templates"] == [] and "points somewhere else" in listed["notes"][0]
    for call in (lambda: svc.sql_template_delete("mine", "victim.sql"), lambda: svc.sql_template_export("mine", "victim.sql"),
                 lambda: svc.sql_template_save("mine", "A", "SELECT 2"),
                 lambda: svc.sql_template_save("mine", "A", "SELECT 2", name="victim.sql")):
        with pytest.raises(ValueError, match="somewhere else"):
            call()
    assert (outside / "victim.sql").read_text(encoding="utf-8") == "SELECT 1"
    folder.unlink()
    folder.mkdir()
    (folder / "link.sql").symlink_to(outside / "victim.sql")
    (folder / "ok.sql").write_text("SELECT 3", encoding="utf-8")
    assert [t["name"] for t in svc.sql_templates_list()["templates"]] == ["ok.sql"]
    with pytest.raises(ValueError, match="link"):
        svc.sql_template_delete("mine", "link.sql")
    with pytest.raises(ValueError, match="link"):
        svc.sql_template_save("mine", "B", "SELECT 4", name="link.sql")
    assert (outside / "victim.sql").read_text(encoding="utf-8") == "SELECT 1"


def test_windows_device_names_are_never_template_files(svc):
    assert svc.sql_template_save("mine", "CON", "SELECT 1")["name"] == "CON template.sql"
    for name in ("CON.sql", "nul.sql", "com1.sql", "LPT9.sql"):
        with pytest.raises(ValueError, match="template file name"):
            svc.sql_template_delete("mine", name)


def test_a_place_with_more_templates_than_are_listed_says_so(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(st, "MAX_PER_PLACE", 2)
    for title in ("a", "b", "c"):
        svc.sql_template_save("mine", title, "SELECT 1")
    listed = svc.sql_templates_list()
    assert [t["name"] for t in listed["templates"]] == ["a.sql", "b.sql"]
    assert listed["notes"] == ["My templates has 3 templates; the first 2 by name are listed."]
