"""notebookutils.fs on the attached lakehouse follows Fabric's rules: rm keeps a folder that isn't
empty unless recurse=True, Tables/ isn't changed through it, the write guard is asked before any
request is sent, and mv keeps its source until the copy is there."""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests

from fabric_desktop import lakehouse, notebookutils_local
from fabric_desktop.onelake import FileStore

MUTATIONS = {"upload", "delete"}


class FakeStore:
    """The lakehouse's files in memory; records every call it gets."""

    def __init__(self, files: dict[str, bytes] | None = None, folders: set[str] | None = None):
        self.files = dict(files or {})
        self.folders = set(folders or ())
        self.calls: list[tuple[str, Any]] = []
        self.lose_uploads = False

    def _dirs(self) -> set[str]:
        dirs = set(self.folders)
        for f in self.files:
            parts = f.split("/")
            dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
        return dirs

    def list(self, ws, lh, directory="Files", recursive=False, max_results=None):
        self.calls.append(("list", directory))
        d = directory.strip("/")
        if d not in self._dirs():
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError("404", response=resp)
        entries = [{"name": p.rsplit("/", 1)[-1], "path": p, "is_dir": False, "size": len(b)}
                   for p, b in self.files.items()
                   if p.startswith(d + "/") and (recursive or "/" not in p[len(d) + 1:])]
        entries += [{"name": p.rsplit("/", 1)[-1], "path": p, "is_dir": True, "size": 0}
                    for p in self._dirs()
                    if p.startswith(d + "/") and (recursive or "/" not in p[len(d) + 1:])]
        return entries[:max_results] if max_results else entries

    def read_bytes(self, ws, lh, path):
        self.calls.append(("read_bytes", path))
        return self.files.get(path)

    def upload(self, ws, lh, path, data):
        self.calls.append(("upload", path))
        if not self.lose_uploads:
            self.files[path] = data

    def delete(self, ws, lh, path, recursive=True):
        self.calls.append(("delete", (path, recursive)))
        self.files = {p: b for p, b in self.files.items() if p != path and not (recursive and p.startswith(path + "/"))}
        self.folders.discard(path)

    def mutations(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if c[0] in MUTATIONS]


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = FakeStore({"Files/raw/a.csv": b"a,b\n1,2\n", "Files/raw/b.csv": b"x\n", "Files/one.txt": b"hello",
                   "Tables/dbo/orders/_delta_log/0.json": b"{}"}, {"Files/empty"})
    monkeypatch.setattr(notebookutils_local, "_store", lambda: s)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield s
    lakehouse.attach(None)


@pytest.fixture
def fs():
    return notebookutils_local.build(notebookutils_local.Host()).fs


def test_rm_keeps_a_folder_that_isnt_empty_without_recurse(store, fs):
    with pytest.raises(IsADirectoryError, match="isn't empty. Pass recurse=True"):
        fs.rm("Files/raw")
    assert store.mutations() == []
    assert "Files/raw/a.csv" in store.files


def test_rm_with_recurse_removes_the_folder(store, fs):
    assert fs.rm("Files/raw", recurse=True)
    assert store.mutations() == [("delete", ("Files/raw", True))]
    assert not any(p.startswith("Files/raw/") for p in store.files)


def test_rm_removes_a_file_and_an_empty_folder_without_recurse(store, fs):
    fs.rm("/lakehouse/default/Files/one.txt")
    fs.rm("Files/empty")
    assert store.mutations() == [("delete", ("Files/one.txt", False)), ("delete", ("Files/empty", False))]


def test_rm_maps_onelakes_refusal_of_a_folder_that_isnt_empty(store, fs, monkeypatch):
    def refuse(ws, lh, path, recursive=True):
        resp = requests.Response()
        resp.status_code = 409
        raise requests.HTTPError("409", response=resp)
    monkeypatch.setattr(store, "delete", refuse)
    with pytest.raises(IsADirectoryError, match="Pass recurse=True"):
        fs.rm("Files/unknown")


def test_exists_is_false_only_when_the_path_is_really_missing(store, fs):
    assert fs.exists("Files/one.txt") and fs.exists("Files/empty")
    assert not fs.exists("Files/nope.txt")
    assert not fs.exists("Files/no_folder/a.csv")  # the folder above it is missing: 404


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_exists_raises_when_onelake_refuses_or_fails(store, fs, monkeypatch, status):
    def fail(*a, **k):
        resp = requests.Response()
        resp.status_code = status
        raise requests.HTTPError(str(status), response=resp)
    monkeypatch.setattr(store, "list", fail)
    with pytest.raises(requests.HTTPError):
        fs.exists("Files/one.txt")
    # put() without overwrite asks exists() first, so a refusal can't let it replace the file.
    with pytest.raises(requests.HTTPError):
        fs.put("Files/one.txt", "new")
    assert store.mutations() == [] and store.files["Files/one.txt"] == b"hello"


@pytest.mark.parametrize("call", [
    lambda fs: fs.rm("Tables/dbo/orders", recurse=True),
    lambda fs: fs.rm("abfss://ws1@onelake.dfs.fabric.microsoft.com/lh1/Tables/dbo/orders"),
    lambda fs: fs.put("Tables/dbo/orders/x.parquet", "x", True),
    lambda fs: fs.append("Tables/dbo/orders/x.json", "x", True),
    lambda fs: fs.cp("Files/one.txt", "Tables/dbo/orders/one.txt"),
    lambda fs: fs.mv("Tables/dbo/orders", "Files/orders"),
    lambda fs: fs.mv("Files/one.txt", "Tables/one.txt"),
])
def test_tables_are_not_changed_through_fs(store, fs, call):
    with pytest.raises(PermissionError, match="Tables/ holds managed Delta tables"):
        call(fs)
    assert store.calls == []


def test_tables_can_still_be_read(store, fs):
    assert [e.name for e in fs.ls("Tables/dbo/orders")] == ["_delta_log"]
    assert fs.exists("Tables/dbo/orders")


@pytest.mark.parametrize("call", [
    lambda fs: fs.rm("Files/one.txt"),
    lambda fs: fs.rm("Files/raw", recurse=True),
    lambda fs: fs.put("Files/new.txt", "x"),
    lambda fs: fs.append("Files/one.txt", " world"),
    lambda fs: fs.cp("Files/raw", "Files/copy", recurse=True),
    lambda fs: fs.mv("Files/one.txt", "Files/two.txt"),
])
def test_a_refusing_write_guard_stops_every_change_before_a_request(store, fs, monkeypatch, call):
    asked = []
    monkeypatch.setattr(lakehouse, "write_guard", lambda name, schema, table: asked.append((name, schema, table))
                        or "External AI tools can't write")
    with pytest.raises(PermissionError, match="External AI tools can't write"):
        call(fs)
    assert store.calls == []
    assert asked and asked[0][0] == "Sales"


def test_a_guard_that_allows_files_lets_changes_through(store, fs, monkeypatch):
    monkeypatch.setattr(lakehouse, "write_guard", lambda name, schema, table: None)
    fs.put("Files/new.txt", "x")
    assert store.files["Files/new.txt"] == b"x"


def test_a_running_proof_doesnt_change_lakehouse_files(store, fs, monkeypatch):
    monkeypatch.setattr(lakehouse, "proof", object())
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)
    with pytest.raises(PermissionError, match="proof"):
        fs.put("Files/new.txt", "x")
    assert store.calls == []


def test_mv_moves_a_file_and_a_folder(store, fs):
    fs.mv("Files/one.txt", "Files/two.txt")
    assert store.files["Files/two.txt"] == b"hello" and "Files/one.txt" not in store.files
    fs.mv("Files/raw", "Files/moved")
    assert store.files["Files/moved/a.csv"] == b"a,b\n1,2\n" and store.files["Files/moved/b.csv"] == b"x\n"
    assert not any(p.startswith("Files/raw/") for p in store.files)


def test_mv_keeps_the_source_when_the_copy_didnt_land(store, fs):
    store.lose_uploads = True
    with pytest.raises(OSError, match="was kept"):
        fs.mv("Files/one.txt", "Files/two.txt")
    with pytest.raises(OSError, match="was kept"):
        fs.mv("Files/raw", "Files/moved")
    assert [c for c in store.mutations() if c[0] == "delete"] == []
    assert store.files["Files/one.txt"] == b"hello" and "Files/raw/a.csv" in store.files


def test_mv_keeps_the_source_when_the_copy_is_short(store, fs, monkeypatch):
    real = store.upload
    monkeypatch.setattr(store, "upload", lambda ws, lh, path, data: real(ws, lh, path, data[:2]))
    with pytest.raises(OSError, match="was kept"):
        fs.mv("Files/one.txt", "Files/two.txt")
    assert store.files["Files/one.txt"] == b"hello"


def test_local_paths_are_unchanged(tmp_path, fs):
    folder = tmp_path / "local"
    fs.put(f"file:{folder}/a.txt", "hi")
    with pytest.raises(IsADirectoryError):
        fs.rm(f"file:{folder}")
    fs.mv(f"file:{folder}", f"file:{tmp_path}/other")
    assert (tmp_path / "other" / "a.txt").read_text() == "hi" and not folder.exists()
    fs.rm(f"file:{tmp_path}/other", recurse=True)
    assert not (tmp_path / "other").exists()


class _Session:
    def __init__(self):
        self.requests: list[tuple[str, str, dict]] = []

    def request(self, method, url, params=None, **_kw):
        self.requests.append((method, url, params))
        resp = requests.Response()
        resp.status_code = 200
        return resp

    def get(self, url, params=None, **_kw):
        self.requests.append(("GET", url, dict(params)))
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'{"paths": [{"name": "lh/Files/a", "contentLength": "1"}]}'
        resp.headers["x-ms-continuation"] = "more"
        return resp


def test_filestore_delete_is_recursive_only_when_asked():
    http = _Session()
    store = FileStore(lambda: "tok", session=http)
    store.delete("ws", "lh", "Files/x")
    store.delete("ws", "lh", "Files/y", recursive=False)
    assert [r[2] for r in http.requests] == [{"recursive": "true"}, {"recursive": "false"}]


def test_filestore_list_with_max_results_reads_one_page():
    http = _Session()
    assert len(FileStore(lambda: "tok", session=http).list("ws", "lh", "Files", max_results=1)) == 1
    assert len(http.requests) == 1 and http.requests[0][2]["maxResults"] == "1"


def test_ls_gives_modify_time_in_milliseconds(tmp_path):
    import os
    f = tmp_path / "a.txt"
    f.write_text("x")
    os.utime(f, (1_700_000_000, 1_700_000_000))
    info = notebookutils_local._make_fs().ls(f"file:{tmp_path.as_posix()}")[0]
    assert info.modifyTime == 1_700_000_000_000 and info.modificationTime == info.modifyTime
    assert notebookutils_local._millis("2026-10-20T12:00:00+00:00") == 1_792_497_600_000
    assert notebookutils_local._millis("") == 0 and notebookutils_local._millis("junk") == 0


@pytest.mark.parametrize("dest", ["Files/one.txt", "/lakehouse/default/Files/one.txt", "Files/raw/sub", "Files/raw/"])
def test_mv_and_cp_refuse_a_path_onto_or_into_itself(store, fs, dest):
    src = "Files/raw" if dest.startswith("Files/raw") else "Files/one.txt"
    before = dict(store.files)
    with pytest.raises(ValueError, match="inside it"):
        fs.mv(src, dest, overwrite=True)
    with pytest.raises(ValueError, match="inside it"):
        fs.cp(src, dest, recurse=True)
    assert store.files == before and store.mutations() == []


def test_local_mv_into_itself_keeps_the_folder(tmp_path, fs):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("hi")
    with pytest.raises(ValueError, match="inside it"):
        fs.mv(f"file:{tmp_path}/data", f"file:{tmp_path}/data/../data/sub")
    assert (tmp_path / "data" / "a.txt").read_text() == "hi"
    fs.cp(f"file:{tmp_path}/data", f"file:{tmp_path}/data2", recurse=True)  # a sibling with the same prefix is fine
    assert (tmp_path / "data2" / "a.txt").read_text() == "hi"


def test_a_windows_file_uri_names_the_drive(monkeypatch):
    monkeypatch.setattr(notebookutils_local.os, "name", "nt")
    assert notebookutils_local._split("file:///C:/data/a.csv") == ("local", "C:/data/a.csv")
    assert notebookutils_local._split("file:/D:\\x") == ("local", "D:\\x")
    assert notebookutils_local._split("file:C:/data") == ("local", "C:/data")
    monkeypatch.setattr(notebookutils_local.os, "name", "posix")
    assert notebookutils_local._split("file:///C:/data") == ("local", "/C:/data")


def test_a_link_to_the_source_counts_as_the_source(tmp_path, fs):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("only copy")
    try:
        (tmp_path / "alias.txt").symlink_to(tmp_path / "data" / "a.txt")
        (tmp_path / "linked").symlink_to(tmp_path / "data", target_is_directory=True)
    except OSError:
        pytest.skip("this account can't create symlinks")
    with pytest.raises(ValueError, match="inside it"):
        fs.mv(f"file:{tmp_path}/data/a.txt", f"file:{tmp_path}/alias.txt", overwrite=True)
    with pytest.raises(ValueError, match="inside it"):
        fs.mv(f"file:{tmp_path}/data", f"file:{tmp_path}/linked/sub")
    assert (tmp_path / "data" / "a.txt").read_text() == "only copy"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows folders links")
def test_a_junction_to_the_source_counts_as_the_source(tmp_path, fs):
    import subprocess
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("only copy")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "junction"), str(tmp_path / "data")],
                   check=True, capture_output=True)
    with pytest.raises(ValueError, match="inside it"):
        fs.mv(f"file:{tmp_path}/data/a.txt", f"file:{tmp_path}/junction/a.txt", overwrite=True)
    assert (tmp_path / "data" / "a.txt").read_text() == "only copy"


def test_doubled_slashes_dont_hide_the_source(store, fs):
    with pytest.raises(ValueError, match="inside it"):
        fs.mv("Files/raw", "Files//raw/./sub")
    assert store.mutations() == []
