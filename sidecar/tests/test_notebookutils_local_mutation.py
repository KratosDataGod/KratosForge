"""Mutation testing batch 7 (docs/quality/mutation-testing.md): notebookutils on this PC.

Each test pins a behavior a surviving mutant changed without any other test noticing: plain local
paths, the default display, OneLake's 400 for a listed file, the lakehouse's own Files/ and Tables/,
mv's check of every nested file before the source goes, head's default size, the True each change
returns, runMultiple's task values, the default workspace, user data functions read from code, and
the variable library's one-time note."""

from __future__ import annotations

import requests
import pytest

from fabric_desktop import lakehouse, notebookutils_local as nu
from tests.test_notebookutils_fs import FakeStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = FakeStore({"Files/raw/a.csv": b"a,b\n1,2\n", "Files/raw/sub/c.csv": b"cc", "Files/one.txt": b"hello"},
                  {"Files/empty"})
    monkeypatch.setattr(nu, "_store", lambda: s)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield s
    lakehouse.attach(None)


@pytest.fixture
def fs():
    return nu.build(nu.Host()).fs


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(str(status), response=resp)


# Local paths


def test_a_plain_local_path_is_written_read_and_found(tmp_path, fs):
    path = str(tmp_path / "plain.txt")
    assert fs.put(path, "hi") is True
    assert fs.exists(path) and fs.head(path) == "hi"
    assert not fs.exists(str(tmp_path / "missing.txt"))


def test_each_local_change_returns_true(tmp_path, fs):
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "deep" / "f.txt").write_text("x")
    assert fs.cp(f"file:{src}", f"file:{tmp_path / 'copy'}", recurse=True) is True
    assert (tmp_path / "copy" / "deep" / "f.txt").read_text() == "x"
    assert fs.mkdirs(f"file:{tmp_path / 'a' / 'b' / 'c'}") is True  # parents too
    assert fs.mkdirs(f"file:{tmp_path / 'a' / 'b' / 'c'}") is True  # and again: it's already there
    assert (tmp_path / "a" / "b" / "c").is_dir()
    assert fs.rm(f"file:{tmp_path / 'copy' / 'deep' / 'f.txt'}") is True
    assert fs.rm(f"file:{tmp_path / 'never-there.txt'}") is True  # a missing file is no error
    assert fs.rm(f"file:{tmp_path / 'copy'}", recurse=True) is True
    assert not (tmp_path / "copy").exists()


def test_head_reads_64_kib_by_default(tmp_path, fs):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 70_000)
    assert len(fs.head(f"file:{big}")) == 65_536


def test_the_default_display_prints(capsys):
    nu.Host().display("shown", 2)
    assert capsys.readouterr().out == "shown 2\n"


# The attached lakehouse


def test_listing_a_file_onelake_answers_400_for_lists_just_that_file(store, fs, monkeypatch):
    real = store.list

    def listing(ws, lh, directory="Files", recursive=False, max_results=None):
        if directory == "Files/one.txt":
            raise _http_error(400)  # a file can't be listed as a folder
        return real(ws, lh, directory, recursive, max_results)
    monkeypatch.setattr(store, "list", listing)
    [info] = fs.ls("Files/one.txt")
    assert (info.name, info.size, info.isDir) == ("one.txt", 5, False)


def test_the_lakehouses_files_folder_counts_as_a_folder_when_copied(store, fs, tmp_path):
    assert fs.cp("Files", f"file:{tmp_path / 'all'}", recurse=True) is True
    assert (tmp_path / "all" / "one.txt").read_bytes() == b"hello"
    assert (tmp_path / "all" / "raw" / "sub" / "c.csv").read_bytes() == b"cc"


def test_mv_keeps_the_source_when_a_nested_file_didnt_land(store, fs, monkeypatch):
    real = store.upload

    def lossy(ws, lh, path, data):
        if "/sub/" not in path:  # every file but the nested one arrives
            real(ws, lh, path, data)
    monkeypatch.setattr(store, "upload", lossy)
    with pytest.raises(OSError, match="doesn't match Files/raw, so Files/raw was kept"):
        fs.mv("Files/raw", "Files/moved")
    assert store.files["Files/raw/sub/c.csv"] == b"cc"
    assert not any(c[0] == "delete" for c in store.calls)


def test_the_default_workspace_is_the_attached_lakehouses(store):
    assert nu._workspace("") == "ws1"
    assert nu._workspace("other") == "other"


# notebook


def test_a_top_level_run_multiple_starts_with_no_task_values_and_a_nested_one_keeps_them():
    host = nu.Host(run_notebook=lambda path, timeout, args, **_kw: f"done {path}", task_values={"old": 1})
    nb = nu.build(host).notebook
    out = nb.runMultiple(["a"])
    assert out["a"]["exitVal"] == "done a"
    assert host.task_values == {}
    host.task_values["kept"] = 2
    host.tasks.append("outer")  # runMultiple called from inside a running activity
    nb.runMultiple(["b"])
    assert host.task_values == {"kept": 2}


def test_validate_dag_returns_true():
    assert nu.build(nu.Host()).notebook.validateDAG(["a", "b"]) is True


# udf


def test_user_data_functions_are_read_from_their_decorators_and_annotations():
    code = (
        "import fabric.functions as fn\n"
        "udf = fn.UserDataFunctions()\n"
        "@udf.function()\n"
        "def typed(x: int) -> str:\n"
        "    '''Says x.'''\n"
        "    return str(x)\n"
        "@udf.function()\n"
        "def untyped(y):\n"
        "    return y\n"
        "@staticmethod\n"
        "def plain(z):\n"
        "    return z\n"
        "@udf.other()\n"
        "def other(w):\n"
        "    return w\n"
    )
    found = nu._udf_functions({"function_app.py": code})
    assert found == [
        {"name": "typed", "description": "Says x.", "parameters": [{"name": "x", "type": "int"}], "returnType": "str"},
        {"name": "untyped", "description": "", "parameters": [{"name": "y", "type": ""}], "returnType": ""},
    ]


# variableLibrary


def test_the_variable_librarys_workspace_note_is_printed_once(capsys):
    host = nu.Host(variables=lambda name: {nu.FROM_KEY: "ws-a", "host": "db1"})
    vl = nu.build(host).variableLibrary
    assert vl.getLibrary("VL_Sources").host == "db1"
    assert vl.getLibrary("VL_Sources").host == "db1"
    assert capsys.readouterr().out.count("Read VL_Sources from workspace ws-a") == 1


def test_a_local_mv_keeps_the_source_when_the_copy_is_short(tmp_path, fs, monkeypatch):
    src, dest = tmp_path / "in.txt", tmp_path / "out.txt"
    src.write_bytes(b"12345")
    real = nu.Path.write_bytes

    def short(self, data):
        return real(self, data[:2])  # the copy stops partway
    monkeypatch.setattr(nu.Path, "write_bytes", short)
    with pytest.raises(OSError, match="was kept"):
        fs.mv(f"file:{src}", f"file:{dest}")
    assert src.read_bytes() == b"12345"
