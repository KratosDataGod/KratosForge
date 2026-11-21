"""Databricks workspace sync (engines/databricks_workspace_sync.py), the paths the main tests don't
reach: pairs that can't be made, notebooks left out of a folder, a workspace that refuses a read, a file
deleted in Databricks since the last sync, a notebook that can't be read, and pushes or pulls of a file
that changed under the folder between the compare and the write. All against a fake Workspace API."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import databricks_workspace_sync as sync

from test_databricks_discovery import Resp
from test_databricks_workspace_sync import REMOTE, FakeWorkspace, client, folder, pair, rows  # noqa: F401


class Refusing(FakeWorkspace):
    """A workspace that answers ``status`` to one API path."""

    def __init__(self, api, status, **kw):
        super().__init__(**kw)
        self.api, self.status = api, status

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        if url.endswith(self.api):
            self.calls.append((method, self.api, dict(params or {}), json))
            return Resp(self.status, {"error_code": "RESOURCE_DOES_NOT_EXIST" if self.status == 404 else "DENIED",
                                      "message": "refused"})
        return super().request(method, url, headers, timeout, json, params)


# ---- pairs


def test_a_pair_needs_a_plain_name_and_an_absolute_local_folder(folder):
    with pytest.raises(sync.SyncError, match="Name the pair with letters"):
        sync.new_pair("sales/etl", "cli:dev", str(folder), REMOTE)
    for local in ("", "nb", str(folder / "load_orders.py")):
        with pytest.raises(sync.SyncError, match="Pick a folder on this PC"):
            sync.check_local(local)


def test_a_pairs_file_without_the_pair_says_so_and_removing_one_keeps_the_rest(tmp_path, folder):
    pairs = sync.Pairs(tmp_path / "sync" / "pairs.json")
    with pytest.raises(sync.SyncError, match="no such synced folder"):
        pairs.get("a")
    pairs.put("a", pair(folder))
    pairs.put("b", pair(folder))
    pairs.remove("a")
    assert list(pairs.all()["pairs"]) == ["b"]


# ---- reading this PC's folder


def test_odd_names_and_files_over_the_import_limit_are_left_out_and_listed(folder, monkeypatch):
    (folder / "a&b.py").write_text("print(1)\n")
    (folder / "big.sql").write_text("select 1 -- " + "x" * 2000)
    monkeypatch.setattr(sync, "MAX_BYTES", 1024)
    here = sync.local_files(folder.resolve())
    assert here["\x00skipped"] == {"names": ["a&b.py", "big.sql"]}
    out = sync.compare(client(FakeWorkspace(exists=False)), pair(folder))
    assert out["skipped"] == ["a&b.py", "big.sql"] and "big.sql" not in rows(out)


# ---- reading the workspace


def test_a_workspace_that_refuses_a_status_read_stops_the_compare(folder):
    ws = Refusing("/api/2.0/workspace/get-status", 403)
    with pytest.raises(dbsql.DatabricksError, match="refused"):
        sync.compare(client(ws), pair(folder))
    assert ws.writes() == []


def test_only_notebooks_and_files_in_the_workspace_folder_are_compared(folder):
    ws = FakeWorkspace({"archive": {"type": "DIRECTORY", "modified_at": 1},
                        "load_orders": {"type": "NOTEBOOK", "modified_at": 5, "content": "x"}})
    there, exists = sync.remote_files(client(ws), REMOTE)
    assert exists and there == {"load_orders": {"type": "NOTEBOOK", "modified_at": 5}}


def test_a_folder_that_goes_missing_before_its_list_reads_as_not_there(folder):
    out = sync.compare(client(Refusing("/api/2.0/workspace/list", 404)), pair(folder))
    assert out["remote_exists"] is False and {r["there"] for r in out["rows"]} == {"Not there"}


def test_a_list_the_workspace_refuses_stops_the_compare(folder):
    with pytest.raises(dbsql.DatabricksError, match="refused"):
        sync.compare(client(Refusing("/api/2.0/workspace/list", 403)), pair(folder))


# ---- what would change


def test_a_file_deleted_in_databricks_since_the_last_sync_is_pushed_back(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    del ws.objects["load_orders"]
    r = rows(sync.compare(client(ws), p))["load_orders.py"]
    assert (r["there"], r["action"]) == ("Deleted there", "push")
    assert r["note"] == "Deleted in Databricks since the last sync: pushing puts it back."
    out = sync.push(client(ws), p, ["load_orders.py"])
    assert out["done"][0]["action"] == "pushed" and "load_orders" in ws.objects
    assert [c[3]["overwrite"] for c in ws.calls if c[1] == "/api/2.0/workspace/import"][-1] is False


def test_a_file_deleted_on_both_sides_isnt_listed(folder):
    p = pair(folder, {"gone.py": {"hash": "h", "modified_at": 3}})
    assert "gone.py" not in rows(sync.compare(client(FakeWorkspace(exists=False)), p))


def test_a_notebook_that_cant_be_read_is_listed_and_skipped_on_push(folder):
    (folder / "silver.ipynb").write_text("{not json")
    ws = FakeWorkspace(exists=False)
    r = rows(sync.compare(client(ws), pair(folder)))["silver.ipynb"]
    assert r["losses"] == ["This notebook can't be read, so it isn't pushed."]
    out = sync.push(client(ws), pair(folder), ["silver.ipynb"])
    assert out["done"] == [] and out["skipped"][0]["file"] == "silver.ipynb"
    assert [p for m, p in ws.writes() if p == "/api/2.0/workspace/import"] == []


# ---- a file that changed between the compare and the write


def _swap_for_a_link(folder, tmp_path, monkeypatch, name):
    """compare() sees the file; then it becomes a link to a file outside the folder."""
    real = sync.compare

    def then_link(c, p):
        out = real(c, p)
        outside = tmp_path / "outside.py"
        outside.write_text("# Databricks notebook source\nprint('outside')\n")
        (folder / name).unlink()
        (folder / name).symlink_to(outside)
        return out

    monkeypatch.setattr(sync, "compare", then_link)
    return tmp_path / "outside.py"


def test_a_push_of_a_file_that_became_a_link_out_of_the_folder_is_skipped(folder, tmp_path, monkeypatch):
    ws = FakeWorkspace(exists=False)
    _swap_for_a_link(folder, tmp_path, monkeypatch, "load_orders.py")
    out = sync.push(client(ws), pair(folder), ["load_orders.py"])
    assert out["skipped"] == [{"file": "load_orders.py", "why": "Not a file in this folder."}]
    assert "load_orders" not in ws.objects


def test_a_pull_into_a_file_that_became_a_link_is_skipped(folder, tmp_path, monkeypatch):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"] |= {"modified_at": 9999, "content": "print('from dbx')\n"}
    outside = _swap_for_a_link(folder, tmp_path, monkeypatch, "load_orders.py")
    out = sync.pull(client(ws), p, ["load_orders.py"])
    assert out["skipped"] == [{"file": "load_orders.py", "why": "Not a file in this folder."}]
    assert "outside" in outside.read_text()


def test_a_pull_over_the_import_limit_is_skipped(folder, monkeypatch):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"] |= {"modified_at": 9999, "content": "print('from dbx')\n" * 20}
    monkeypatch.setattr(sync, "MAX_BYTES", 64)
    out = sync.pull(client(ws), p, ["load_orders.py"])
    assert out["skipped"] == [{"file": "load_orders.py", "why": "It's over 10 MB in Databricks, so it isn't pulled."}]
    assert "from dbx" not in (folder / "load_orders.py").read_text()
