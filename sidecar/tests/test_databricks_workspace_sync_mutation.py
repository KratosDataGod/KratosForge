"""Databricks workspace sync (engines/databricks_workspace_sync.py), pinned where mutation testing
found the tests quiet (docs/quality/mutation-testing.md, batch 91): how many folder entries are
looked at, the 10 MB limit each way, the parents read for a Git folder and the missing ones skipped,
a time the workspace doesn't give, a .py push listing no losses, and the state a push or pull keeps."""

from __future__ import annotations

import hashlib

import pytest

from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import databricks_workspace_sync as sync

REMOTE = "/Workspace/Users/dev@contoso.com/sales-etl"
LIMIT = 10 * 1024 * 1024


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


class Fake:
    """Just the client calls the sync makes. ``listings`` are the folder's lists, one per list call
    (the last repeats); ``missing`` maps a path to the error get-status gives for it."""

    def __init__(self, listings=(), missing=None, content=""):
        self.listings = [list(x) for x in listings] or [[]]
        self.missing = missing or {}
        self.content = content
        self.status_paths: list[str] = []
        self.lists = 0
        self.imports: list[dict] = []

    def get(self, path, params):
        if path == "/api/2.0/workspace/get-status":
            self.status_paths.append(params["path"])
            if params["path"] in self.missing:
                raise dbsql.DatabricksError(self.missing[params["path"]])
            return {"object_type": "DIRECTORY", "path": params["path"]}
        assert path == "/api/2.0/workspace/export"
        return {"content": self.content}

    def list(self, path, key, params):
        listing = self.listings[min(self.lists, len(self.listings) - 1)]
        self.lists += 1
        return listing

    def workspace_import(self, body):
        self.imports.append(body)

    def workspace_mkdirs(self, path):
        pass

    def workspace_delete(self, path):
        pass


def _obj(name, modified_at=None, kind="NOTEBOOK"):
    out = {"path": f"{REMOTE}/{name}", "object_type": kind}
    if modified_at is not None:
        out["modified_at"] = modified_at
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- this PC's side


def test_the_first_thousand_entries_of_the_folder_are_looked_at(tmp_path):
    root = tmp_path / "nb"
    root.mkdir()
    for i in range(999):
        (root / f"a{i:04d}.txt").write_text("", encoding="utf-8")
    (root / "b.py").write_text("print(1)\n", encoding="utf-8")  # the 1000th entry: looked at
    (root / "c.py").write_text("print(2)\n", encoding="utf-8")  # the 1001st: not
    assert list(sync.local_files(root.resolve())) == ["b.py"]


def test_a_file_of_exactly_ten_megabytes_is_synced_and_one_byte_more_is_skipped(tmp_path):
    root = tmp_path / "nb"
    root.mkdir()
    with open(root / "at.py", "wb") as f:
        f.truncate(LIMIT)
    with open(root / "over.py", "wb") as f:
        f.truncate(LIMIT + 1)
    found = sync.local_files(root.resolve())
    assert found["at.py"]["bytes"] == LIMIT
    assert "over.py" not in found and found["\x00skipped"] == {"names": ["over.py"]}


def test_saving_a_pair_makes_its_folder(tmp_path):
    pairs = sync.Pairs(tmp_path / "a" / "b" / "pairs.json")
    pairs.put("p1", {"name": "x"})
    assert pairs.get("p1") == {"name": "x"}


# ---- the workspace's side


@pytest.mark.parametrize("remote, read", [
    (f"{REMOTE}/daily", [f"{REMOTE}/daily", REMOTE]),  # down to, not including, the user's home
    ("/Workspace/Users/dev@contoso.com", ["/Workspace/Users/dev@contoso.com"]),  # the home itself only
    ("/Workspace/Shared/team/etl", ["/Workspace/Shared/team/etl", "/Workspace/Shared/team"]),
    ("/Users/dev@contoso.com/etl", ["/Users/dev@contoso.com/etl"]),
    ("/Shared/team/etl", ["/Shared/team/etl", "/Shared/team"]),
])
def test_the_folder_and_each_parent_below_the_home_are_checked_for_a_git_folder(remote, read):
    client = Fake()
    assert sync._not_in_git_folder(client, remote) is True
    assert client.status_paths == read


@pytest.mark.parametrize("message", ["RESOURCE_DOES_NOT_EXIST: no such path", "HTTP 404"])
def test_a_missing_parent_is_skipped_and_a_missing_folder_says_it_isnt_there(message):
    parent = Fake(missing={REMOTE: message})
    assert sync._not_in_git_folder(parent, f"{REMOTE}/daily") is True
    folder = Fake(missing={f"{REMOTE}/daily": message})
    assert sync._not_in_git_folder(folder, f"{REMOTE}/daily") is False


def test_any_other_failure_reading_a_parent_is_raised():
    with pytest.raises(dbsql.DatabricksError, match="denied"):
        sync._not_in_git_folder(Fake(missing={REMOTE: "PERMISSION_DENIED: denied"}), f"{REMOTE}/daily")


def test_an_object_without_a_time_is_listed_at_zero():
    found, exists = sync.remote_files(Fake([[_obj("daily")]]), REMOTE)
    assert exists is True and found == {"daily": {"type": "NOTEBOOK", "modified_at": 0}}


# ---- compare, push and pull


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "nb"
    root.mkdir()
    return root.resolve()


def _pair(root, files=None):
    return {"name": "p", "connection": "c", "local": str(root), "remote": REMOTE, "files": files or {}}


def test_a_py_file_to_push_lists_no_losses(folder):
    (folder / "daily.py").write_text("print(1)\n", encoding="utf-8")
    [row] = sync.compare(Fake(), _pair(folder))["rows"]
    assert row["action"] == "push" and row["losses"] == []


def test_a_pushed_file_the_workspace_doesnt_list_yet_is_remembered_at_zero(folder):
    (folder / "daily.py").write_text("print(1)\n", encoding="utf-8")
    client = Fake([[], []])  # listed neither before the push nor after it
    out = sync.push(client, _pair(folder), ["daily.py"])
    assert [d["action"] for d in out["done"]] == ["pushed"]
    assert client.imports[0]["overwrite"] is False
    assert out["files"]["daily.py"]["modified_at"] == 0


def _pullable(folder, name="daily.py", text="print(1)\n"):
    (folder / name).write_text(text, encoding="utf-8", newline="")
    return {name: {"hash": _sha(text.encode()), "modified_at": 5}}


def test_a_pull_keeps_the_state_of_the_files_it_doesnt_pull(folder):
    last = _pullable(folder) | {"other.py": {"hash": "h", "modified_at": 3}}
    client = Fake([[_obj("daily", 9)], [_obj("daily", 9)], [_obj("daily", 9)]], content="cHJpbnQoMik=")
    out = sync.pull(client, _pair(folder, last), ["daily.py"])
    assert out["done"] == [{"file": "daily.py", "action": "pulled"}]
    assert out["files"]["other.py"] == {"hash": "h", "modified_at": 3}
    assert out["files"]["daily.py"] == {"hash": _sha(b"print(2)"), "modified_at": 9}


def test_a_pulled_file_the_workspace_no_longer_lists_is_remembered_at_zero(folder):
    last = _pullable(folder)
    client = Fake([[_obj("daily", 9)], []], content="cHJpbnQoMik=")  # gone by the second list
    out = sync.pull(client, _pair(folder, last), ["daily.py"])
    assert out["files"]["daily.py"]["modified_at"] == 0


def test_a_pull_of_up_to_ten_megabytes_is_written_and_more_is_skipped(folder):
    biggest = LIMIT * 4 // 3 + 4  # characters of base64 that can still be 10 MB
    ok = "A" * (biggest - biggest % 4)
    last = _pullable(folder)
    out = sync.pull(Fake([[_obj("daily", 9)]], content=ok), _pair(folder, last), ["daily.py"])
    assert out["done"] == [{"file": "daily.py", "action": "pulled"}]
    last = _pullable(folder)
    out = sync.pull(Fake([[_obj("daily", 9)]], content="A" * (biggest + 1)), _pair(folder, last), ["daily.py"])
    assert out["done"] == [] and out["skipped"] == [{"file": "daily.py", "why": "It's over 10 MB in Databricks, so it isn't pulled."}]

