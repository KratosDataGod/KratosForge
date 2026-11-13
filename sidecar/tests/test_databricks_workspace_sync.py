"""Databricks workspace sync against a fake Workspace API: compare writes nothing, push never
overwrites a file changed in Databricks since the last sync, never deletes without the second
confirm, refuses Git folders and paths outside the pair, and lists what a .ipynb loses."""

from __future__ import annotations

import base64
import json
import os

import pytest

from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import databricks_workspace_sync as sync

from test_databricks_discovery import HOST, TOKEN, Resp

REMOTE = "/Workspace/Users/dev@contoso.com/sales-etl"


class FakeWorkspace:
    """A workspace folder in memory: {name: {type, modified_at, content}}."""

    def __init__(self, objects=None, repo=False, exists=True, repo_at=None):
        self.objects = objects or {}
        self.calls = []
        self.repo = repo
        self.repo_at = repo_at
        self.exists = exists
        self.clock = 1000

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append((method, path, dict(params or {}), json))
        if path == "/api/2.0/workspace/get-status":
            here = params["path"]
            if here == REMOTE and not self.exists:
                return Resp(404, {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "no such path"})
            repo = (self.repo and here == REMOTE) or here == self.repo_at
            return Resp(body={"object_type": "REPO" if repo else "DIRECTORY", "path": here})
        if path == "/api/2.0/workspace/list":
            return Resp(body={"objects": [{"path": f"{REMOTE}/{n}", "object_type": o["type"], "modified_at": o["modified_at"]}
                                          for n, o in self.objects.items()]})
        if path == "/api/2.0/workspace/export":
            name = params["path"].rsplit("/", 1)[1]
            return Resp(body={"content": base64.b64encode(self.objects[name]["content"].encode()).decode()})
        if path == "/api/2.0/workspace/mkdirs":
            self.exists = True
            return Resp(body={})
        if path == "/api/2.0/workspace/import":
            name = json["path"].rsplit("/", 1)[1]
            if name in self.objects and not json["overwrite"]:
                return Resp(400, {"error_code": "RESOURCE_ALREADY_EXISTS", "message": "exists"})
            self.clock += 1
            self.objects[name] = {"type": "NOTEBOOK", "modified_at": self.clock,
                                  "content": base64.b64decode(json["content"]).decode()}
            return Resp(body={})
        if path == "/api/2.0/workspace/delete":
            self.objects.pop(json["path"].rsplit("/", 1)[1])
            return Resp(body={})
        return Resp(404, {"message": "no such path"})

    def writes(self):
        return [(m, p) for m, p, _, _ in self.calls if m == "POST"]


def client(ws):
    return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), ws)


IPYNB = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
    {"cell_type": "code", "source": "x = notebookutils.lakehouse.get('a')", "metadata": {}, "outputs": [{"output_type": "stream", "name": "stdout", "text": "1"}], "execution_count": 1}]})


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "nb"
    root.mkdir()
    (root / "load_orders.py").write_text("# Databricks notebook source\nprint(1)\n")
    (root / "silver.ipynb").write_text(IPYNB)
    (root / "notes.txt").write_text("not a notebook")
    return root


def pair(folder, files=None):
    return sync.new_pair("sales-etl", "cli:dev", str(folder), REMOTE) | {"files": files or {}}


def rows(out):
    return {r["file"]: r for r in out["rows"]}


def test_compare_reads_only_and_lists_what_push_would_do(folder):
    ws = FakeWorkspace(exists=False)
    out = sync.compare(client(ws), pair(folder))
    assert ws.writes() == [] and not out["remote_exists"]
    r = rows(out)
    assert set(r) == {"load_orders.py", "silver.ipynb"}
    assert r["load_orders.py"] | {"losses": []} == {"file": "load_orders.py", "remote": "load_orders", "here": "New", "there": "Not there",
                                                    "action": "push", "note": "", "losses": []}
    assert any("notebookutils" in x for x in r["silver.ipynb"]["losses"])


def test_first_push_makes_the_folder_writes_without_overwrite_and_remembers_the_sync(folder):
    ws = FakeWorkspace(exists=False)
    out = sync.push(client(ws), pair(folder), ["load_orders.py", "silver.ipynb"])
    assert [d["action"] for d in out["done"]] == ["pushed", "pushed"]
    imports = [c[3] for c in ws.calls if c[1] == "/api/2.0/workspace/import"]
    assert [i["overwrite"] for i in imports] == [False, False] and {i["format"] for i in imports} == {"SOURCE"}
    assert ws.objects["silver"]["content"].startswith("# Databricks notebook source")
    assert set(out["files"]) == {"load_orders.py", "silver.ipynb"} and out["files"]["load_orders.py"]["modified_at"] > 0


def test_a_file_changed_in_databricks_since_the_last_sync_is_a_conflict_and_never_overwritten(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"]["modified_at"] += 50          # someone changed it in Databricks
    (folder / "load_orders.py").write_text("# Databricks notebook source\nprint(2)\n")
    r = rows(sync.compare(client(ws), p))["load_orders.py"]
    assert (r["here"], r["there"], r["action"]) == ("Changed", "Changed there", "conflict")
    before = len(ws.writes())
    out = sync.push(client(ws), p, ["load_orders.py"])
    assert out["done"] == [] and "Changed in Databricks too" in out["skipped"][0]["why"] and len(ws.writes()) == before


def test_an_unchanged_workspace_file_is_overwritten_on_push(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    (folder / "load_orders.py").write_text("# Databricks notebook source\nprint(3)\n")
    out = sync.push(client(ws), p, ["load_orders.py"])
    assert out["done"][0]["action"] == "pushed" and "print(3)" in ws.objects["load_orders"]["content"]
    assert [c[3]["overwrite"] for c in ws.calls if c[1] == "/api/2.0/workspace/import"][-1] is True


def test_a_file_already_in_databricks_but_never_synced_is_a_conflict(folder):
    ws = FakeWorkspace({"load_orders": {"type": "NOTEBOOK", "modified_at": 5, "content": "x"}})
    assert rows(sync.compare(client(ws), pair(folder)))["load_orders.py"]["action"] == "conflict"


def test_delete_is_off_by_default_and_needs_the_second_confirm(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    (folder / "load_orders.py").unlink()
    r = rows(sync.compare(client(ws), p))["load_orders.py"]
    assert (r["here"], r["there"], r["action"]) == ("Deleted", "Still there", "delete")
    with pytest.raises(sync.SyncError, match="second confirm"):
        sync.push(client(ws), p, [], deletes=["load_orders.py"])
    assert "load_orders" in ws.objects
    out = sync.push(client(ws), p, [], deletes=["load_orders.py"], confirm_deletes=True)
    assert out["done"] == [{"file": "load_orders.py", "action": "deleted", "path": f"{REMOTE}/load_orders"}]
    assert "load_orders" not in ws.objects and "load_orders.py" not in out["files"]
    delete = [c[3] for c in ws.calls if c[1] == "/api/2.0/workspace/delete"][0]
    assert delete == {"path": f"{REMOTE}/load_orders", "recursive": False}


def test_git_folders_and_odd_workspace_paths_are_refused(folder):
    with pytest.raises(sync.SyncError, match="Git folder"):
        sync.compare(client(FakeWorkspace(repo=True)), pair(folder))
    for bad in ("/Repos/dev/x", "Users/x", "/Workspace/Users/a/../../Shared/x", "/Workspace/Shared/a\nb", "https://x/y"):
        with pytest.raises(sync.SyncError):
            sync.new_pair("x", "cli:dev", str(folder), bad)


def test_a_folder_inside_a_git_folder_is_refused_and_only_read(folder):
    ws = FakeWorkspace(repo_at="/Workspace/Users/dev@contoso.com")
    sync.compare(client(ws), pair(folder))                 # the user's home itself isn't read
    ws = FakeWorkspace(repo_at=REMOTE)
    with pytest.raises(sync.SyncError, match="inside one"):
        sync.compare(client(ws), pair(folder))
    deep = "/Workspace/Users/dev@contoso.com/team/repo/nb"
    ws = FakeWorkspace(repo_at="/Workspace/Users/dev@contoso.com/team/repo")
    with pytest.raises(sync.SyncError, match="inside one"):
        sync.remote_files(client(ws), deep)
    with pytest.raises(sync.SyncError, match="inside one"):
        sync.push(client(ws), pair(folder) | {"remote": deep}, ["load_orders.py"])
    assert ws.writes() == []
    paths = [c[2]["path"] for c in ws.calls if c[1] == "/api/2.0/workspace/get-status"]
    assert "/Workspace/Users/dev@contoso.com" not in paths


@pytest.mark.parametrize("repo", ["/Shared/repo", "/Workspace/Shared/repo", "/Users/dev@contoso.com/repo"])
def test_a_short_path_git_folder_is_itself_checked(folder, repo):
    ws = FakeWorkspace(repo_at=repo)
    for remote in (repo, repo + "/nb"):
        with pytest.raises(sync.SyncError, match="Git folder"):
            sync.push(client(ws), pair(folder) | {"remote": remote}, ["load_orders.py"])
    assert ws.writes() == []


def test_a_pair_at_the_home_folder_sees_what_is_already_there(folder):
    home = "/Workspace/Users/dev@contoso.com"
    ws = FakeWorkspace(objects={"load_orders": {"type": "NOTEBOOK", "modified_at": 5, "content": "x"}})
    out = sync.compare(client(ws), pair(folder) | {"remote": home})
    assert out["remote_exists"] and rows(out)["load_orders.py"]["action"] == "conflict"
    assert [c[2]["path"] for c in ws.calls if c[1] == "/api/2.0/workspace/get-status"] == [home]
    with pytest.raises(sync.SyncError, match="Git folder"):
        sync.compare(client(FakeWorkspace(repo_at=home)), pair(folder) | {"remote": home})


def test_a_file_deleted_here_but_changed_in_databricks_is_a_conflict_not_a_delete(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    (folder / "load_orders.py").unlink()
    ws.objects["load_orders"]["modified_at"] += 50
    r = rows(sync.compare(client(ws), p))["load_orders.py"]
    assert (r["here"], r["there"], r["action"]) == ("Deleted", "Changed there", "conflict")
    out = sync.push(client(ws), p, [], deletes=["load_orders.py"], confirm_deletes=True)
    assert out["done"] == [] and "load_orders" in ws.objects


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_pull_never_writes_through_a_planted_link(folder, tmp_path):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"] |= {"modified_at": 9999, "content": "# Databricks notebook source\nprint('from dbx')\n"}
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    for planted in (folder / "load_orders.py.kf-pull", folder / ".kf-pull-x"):
        os.symlink(victim, planted)
    out = sync.pull(client(ws), p, ["load_orders.py"])
    assert out["done"] == [{"file": "load_orders.py", "action": "pulled"}]
    assert victim.read_text() == "keep me" and "from dbx" in (folder / "load_orders.py").read_text()
    assert not [x for x in folder.iterdir() if x.name.endswith(".tmp") and not x.is_symlink()]
    # The notebook itself swapped for a link: left alone, nothing written through it.
    (folder / "load_orders.py").unlink()
    os.symlink(victim, folder / "load_orders.py")
    ws.objects["load_orders"]["modified_at"] = 10000
    sync.pull(client(ws), p | {"files": out["files"]}, ["load_orders.py"])
    assert victim.read_text() == "keep me"


def test_two_files_that_would_be_one_notebook_are_blocked(folder):
    (folder / "silver.py").write_text("print(1)")
    r = rows(sync.compare(client(FakeWorkspace()), pair(folder)))
    assert r["silver.py"]["action"] == r["silver.ipynb"]["action"] == "blocked"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_out_of_the_folder_is_left_out(folder, tmp_path):
    outside = tmp_path / "secret.py"
    outside.write_text("token")
    os.symlink(outside, folder / "linked.py")
    out = sync.compare(client(FakeWorkspace()), pair(folder))
    assert "linked.py" not in rows(out)


def test_pull_brings_a_workspace_change_only_when_unchanged_here(folder):
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    ws.objects["load_orders"] |= {"modified_at": 9999, "content": "# Databricks notebook source\nprint('from dbx')\n"}
    out = sync.pull(client(ws), p, ["load_orders.py"])
    assert out["done"] == [{"file": "load_orders.py", "action": "pulled"}]
    assert "from dbx" in (folder / "load_orders.py").read_text() and out["files"]["load_orders.py"]["modified_at"] == 9999
    (folder / "load_orders.py").write_text("changed here too")
    ws.objects["load_orders"]["modified_at"] = 10000
    assert sync.pull(client(ws), p | {"files": out["files"]}, ["load_orders.py"])["done"] == []


def test_a_pull_the_swap_refuses_keeps_the_file_and_leaves_no_temporary_file(folder, monkeypatch):
    from fabric_desktop import jsonfile
    ws = FakeWorkspace(exists=False)
    p = pair(folder)
    p["files"] = sync.push(client(ws), p, ["load_orders.py"])["files"]
    before = (folder / "load_orders.py").read_bytes()
    names = sorted(x.name for x in folder.iterdir())
    ws.objects["load_orders"] |= {"modified_at": 9999, "content": "# Databricks notebook source\nprint('from dbx')\n"}

    def refused(src, dst):
        raise PermissionError("the notebook is open in another program")
    monkeypatch.setattr(jsonfile, "replace", refused)
    with pytest.raises(PermissionError):
        sync.pull(client(ws), p, ["load_orders.py"])
    assert (folder / "load_orders.py").read_bytes() == before
    assert sorted(x.name for x in folder.iterdir()) == names
