"""A local data project's app calls (localdata_service.py) that test_local_data.py leaves out: listing,
settings, closing, forgetting, a folder taken off and refreshed, column types, and cancelling a run that
isn't there. Everything runs on this PC; the data files are never changed."""

from __future__ import annotations

import pytest

from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    yield s
    s.local_data.close_all()


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "orders.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    return d


def test_a_project_is_listed_set_up_closed_and_forgotten_with_its_files_left(svc, data):
    pid = svc.local_create_project("Sales")["id"]
    assert [p["id"] for p in svc.list_local_projects()] == [pid]
    out = svc.local_project_settings(pid, {"threads": 2, "auto_refresh": False})
    assert out["settings"]["threads"] == 2 and out["settings"]["auto_refresh"] is False
    assert svc.local_close_project(pid) == {"closed": pid}
    folder = svc.local_data.project(pid).folder
    assert svc.local_forget_project(pid) == {"forgotten": pid, "folder": folder}
    assert svc.list_local_projects() == []
    assert (data / "orders.csv").is_file()


def test_a_folder_taken_off_drops_its_tables_and_leaves_its_files(svc, data):
    pid = svc.local_create_project("Sales")["id"]
    svc.local_add_source(pid, str(data))
    tables = svc.list_local_tables(pid)["tables"]
    assert [t["table"] for t in tables] == ["orders"]
    refreshed = svc.local_refresh(pid)
    assert [s["path"] for s in refreshed["sources"]] == [str(data.resolve())]
    after = svc.local_remove_source(pid, str(data.resolve()))
    assert after["tables"] == [] and after["sources"] == []
    assert (data / "orders.csv").read_text(encoding="utf-8").startswith("id,amount")


def test_a_files_columns_are_read_as_the_types_given(svc, data):
    pid = svc.local_create_project("Sales")["id"]
    svc.local_add_source(pid, str(data))
    t = svc.list_local_tables(pid)["tables"][0]
    table = f"{t['schema']}.{t['table']}"
    svc.local_set_types(pid, table, {"amount": "VARCHAR"})
    assert svc.local_query(pid, f"SELECT typeof(amount) AS t FROM {table} LIMIT 1")["rows"] == [["VARCHAR"]]
    with pytest.raises(ValueError, match="is not a column type"):
        svc.local_set_types(pid, table, {"amount": "INT); DROP TABLE x; --"})


def test_cancelling_a_run_that_isnt_there_cancels_nothing(svc):
    assert svc.local_query_cancel("no-such-run") == {"cancelled": False}
