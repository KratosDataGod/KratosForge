"""The app's local stores and projects in folders Windows users really have: a path past the old
260-character limit, a user name with a space (C:\\Users\\John Smith), a non-ASCII one with an
apostrophe (C:\\Users\\Jöhn O'Brien\\OneDrive - Contoso), and a folder with a percent sign. Each store is
made, written, closed and opened again there.

A local data project's Delta tables couldn't be read under a folder with a space, a non-ASCII
letter or a percent sign: the engine took their files from delta-rs's file_uris(), which
percent-encodes the table's folder, so DuckDB was given files that don't exist.

It runs on both CI systems. On windows-latest it checks that these work beyond MAX_PATH (the runner
has long paths enabled) and through Windows' wide-character file APIs. UNC shares and OneDrive files
that live only in the cloud aren't covered here (UNVERIFIED): they need a share or a OneDrive client.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

NAMES = {
    "long": lambda base: base.joinpath(*(["a-folder-name-of-forty-characters-long-x"] * 7)),
    "non-ascii": lambda base: base / "Users" / "Jöhn O'Brien 日本" / "OneDrive - Contoso Ünited" / "Données",
    "space": lambda base: base / "Users" / "John Smith" / "AppData" / "Local",
    "percent": lambda base: base / "Users" / "100% sure",
}


@pytest.fixture(params=sorted(NAMES))
def where(request, tmp_path) -> Path:
    path = NAMES[request.param](tmp_path)
    path.mkdir(parents=True)
    if request.param == "long":
        assert len(str(path)) > 260
    return path


def test_a_local_store_writes_upserts_and_reads_back(where):
    from fabric_desktop.meta.store import LocalStore
    s = LocalStore(where / "store")
    s.write("lh", "dbo", "t", pl.DataFrame({"id": [1, 2], "v": ["a", "ö"]}))
    s.upsert("lh", "dbo", "t", pl.DataFrame({"id": [2, 3], "v": ["b", "日"]}), on=["id"])
    s.meta_append("run_log", [{"run_id": "r1", "entity": "e", "status": "ok"}])
    again = LocalStore(where / "store").read("lh", "dbo", "t")
    assert again is not None and sorted(again.rows()) == [(1, "a"), (2, "b"), (3, "日")]


def test_a_local_data_project_with_a_database_a_delta_table_and_a_spill(where):
    from fabric_desktop.localdata import LocalData
    data = LocalData(where / "local-data")
    pid = data.create("Prøject ö")["id"]
    engine = data.engine(pid)
    try:
        data.set_settings(pid, {"memory_limit": "64MB"})  # small, so the GROUP BY below spills to tmp/
        engine.execute("CREATE TABLE t AS SELECT range AS id, md5(range::VARCHAR) AS s FROM range(400000)")
        engine.execute("CREATE TABLE d USING DELTA AS SELECT * FROM t WHERE id < 1000")
        engine.execute("UPDATE d SET s = 'ö' WHERE id = 1")
        assert engine.execute("SELECT count(*) FROM d")["rows"] == [[1000]]
        assert engine.execute("SELECT s FROM d VERSION AS OF 0 WHERE id = 1", dialect="spark")["rows"] != [["ö"]]
        engine.write_delta("p", "SELECT 1 AS id, 'x y%ö' AS r UNION ALL SELECT 2, 'z'", partition_by=["r"])
        assert sorted(engine.execute("SELECT id, r FROM p")["rows"]) == [[1, "x y%ö"], [2, "z"]]
        assert engine.execute("SELECT count(*) FROM (SELECT s, count(*) FROM t GROUP BY s)")["rows"] == [[400000]]
    finally:
        data.close_all()
    data = LocalData(where / "local-data")
    try:
        again = data.engine(pid)
        assert again.execute("SELECT count(*) FROM t")["rows"] == [[400000]]
        assert again.execute("SELECT s FROM d WHERE id = 1")["rows"] == [["ö"]]
        assert sorted(again.execute("SELECT id, r FROM p")["rows"]) == [[1, "x y%ö"], [2, "z"]]
    finally:
        data.close_all()


def test_the_sqlite_stores_open_write_and_open_again(where):
    from fabric_desktop.central.client import LocalPlatform
    from fabric_desktop.integrations.store import IntegrationStore
    from fabric_desktop.jobqueue import JobQueue
    from fabric_desktop.optimize.outbox import ANALYSIS, Outbox
    from fabric_desktop.workloads.store import ManagementStore

    ManagementStore(where / "workloads.sqlite").audit_queue("k1", "wl", "run", "ok", {"n": 1})
    IntegrationStore(where / "integrations" / "integrations.sqlite").add_client("tool ö")
    JobQueue(where / "queue.sqlite3").enqueue("run", {"n": 1})
    Outbox(where / "optimize" / "outbox.sqlite").add("lh-1", ANALYSIS, {"n": 1})
    platform = LocalPlatform(where / "platform" / "keys.sqlite", lambda: "jöhn@contoso.com")
    platform.call("ensure_key_table", name="customer")
    key = platform.call("issue_keys", name="customer", business_keys=["c1"])["keys"]["c1"]
    for f in ("workloads.sqlite", "integrations/integrations.sqlite", "queue.sqlite3", "optimize/outbox.sqlite",
              "platform/keys.sqlite"):
        assert (where / f).is_file()
    again = LocalPlatform(where / "platform" / "keys.sqlite", lambda: "jöhn@contoso.com")
    assert again.call("issue_keys", name="customer", business_keys=["c1"])["keys"]["c1"] == key


def test_the_engine_answers_with_its_data_folder_there(where, monkeypatch):
    from fabric_desktop import server
    from fabric_desktop.service import Service
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(where / "home"))
    svc = Service(client_id="x", root=where / "app")
    reply = server.handle(svc, '{"id": 1, "method": "app_settings", "params": {}}')
    assert "result" in reply, reply
