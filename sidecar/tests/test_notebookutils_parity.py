"""Fabric notebookutils parity on this PC: the documented calls the other notebookutils tests don't
reach. Each either works as in Fabric or says plainly why not. Nothing leaves this PC: the lakehouse
is the in-memory store from test_notebookutils_fs, and Key Vault is a fake reply."""

from __future__ import annotations

import types

import pytest

import test_notebookutils_fs as t
from fabric_desktop import http_session, lakehouse, notebookutils_local as nu


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = t.FakeStore({"Files/raw/a.csv": b"a,b\n1,2\n", "Files/one.txt": b"hello"}, {"Files/empty"})
    monkeypatch.setattr(nu, "_store", lambda: s)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield s
    lakehouse.attach(None)


def _nu(**host):
    return nu.build(nu.Host(**host))


@pytest.fixture
def fs():
    return _nu().fs


# ---------------------------------------------------------------- fs


@pytest.mark.parametrize("path, words", [
    ("abfss://ws9@onelake.dfs.fabric.microsoft.com/lh9/Files/a", "is in another lakehouse"),
    ("abfss://c@acct.dfs.core.windows.net/a", "only the attached lakehouse and local files"),
    ("wasbs://c@acct.blob.core.windows.net/a", "only the attached lakehouse and local files"),
    ("https://acct.dfs.core.windows.net/c/a", "only the attached lakehouse and local files"),
])
def test_storage_other_than_the_attached_lakehouse_is_refused(store, fs, path, words):
    with pytest.raises(ValueError, match=words):
        fs.head(path)
    assert store.calls == []


def test_the_attached_lakehouse_can_be_named_by_its_onelake_url(store, fs):
    assert fs.head("abfss://ws1@onelake.dfs.fabric.microsoft.com/lh1/Files/one.txt") == "hello"
    assert fs.head("/lakehouse/default/Files/one.txt", 4) == "hell"


def test_head_of_a_missing_lakehouse_file_says_not_found(store, fs):
    with pytest.raises(FileNotFoundError):
        fs.head("Files/nope.txt")


def test_mv_onto_a_file_that_exists_needs_overwrite_and_keeps_both(store, fs):
    with pytest.raises(FileExistsError, match="overwrite=True"):
        fs.mv("Files/one.txt", "Files/raw/a.csv")
    assert store.files["Files/one.txt"] == b"hello" and store.mutations() == []
    assert fs.mv("Files/one.txt", "Files/raw/a.csv", overwrite=True)
    assert store.files["Files/raw/a.csv"] == b"hello" and "Files/one.txt" not in store.files


def test_mv_of_a_missing_source_says_not_found_and_changes_nothing(store, fs):
    with pytest.raises(FileNotFoundError):
        fs.mv("Files/nope.txt", "Files/two.txt")
    assert store.mutations() == []


def test_append_adds_to_a_file_and_creates_one_only_when_asked(store, fs, tmp_path):
    assert fs.append("Files/one.txt", " world")
    assert store.files["Files/one.txt"] == b"hello world"
    with pytest.raises(FileNotFoundError):
        fs.append("Files/new.txt", "x")
    assert fs.append("Files/new.txt", "x", createFileIfNotExists=True) and store.files["Files/new.txt"] == b"x"
    local = tmp_path / "log.txt"
    fs.append(f"file:{local.as_posix()}", "a", True)
    fs.append(f"file:{local.as_posix()}", "b")
    assert local.read_text() == "ab"


def test_append_to_tables_is_refused_before_any_request(store, fs):
    with pytest.raises(PermissionError, match="Tables/"):
        fs.append("Tables/dbo/x", "x", True)
    assert store.calls == []


def test_only_the_default_mount_point_exists(fs):
    assert fs.getMountPath() == fs.getMountPath("/default") == "/lakehouse/default"
    with pytest.raises(ValueError, match="only the attached lakehouse is mounted"):
        fs.getMountPath("/test")


@pytest.mark.parametrize("call", ["mount", "unmount"])
def test_mounting_other_storage_runs_in_fabric(fs, call):
    with pytest.raises(NotImplementedError, match="runs in Fabric"):
        getattr(fs, call)("abfss://c@a.dfs.core.windows.net", "/test")
    assert fs.mounts() == []


def test_help_lists_what_works_on_this_pc(capsys):
    m = _nu()
    m.fs.help()
    m.credentials.help()
    m.help()
    out = capsys.readouterr().out
    assert "append, cp, fastcp" in out and "getToken, getSecret, isValidToken" in out and "variableLibrary" in out


def test_a_file_info_shows_its_path_name_and_size(store, fs):
    info = next(f for f in fs.ls("Files") if f.name == "one.txt")
    assert repr(info) == "FileInfo(path=Files/one.txt, name=one.txt, size=5)"
    assert info.isFile and info.modificationTime == info.modifyTime


# ---------------------------------------------------------------- credentials


def test_get_token_needs_a_sign_in():
    with pytest.raises(RuntimeError, match="Sign in first"):
        _nu().credentials.getToken("storage")


def test_get_token_takes_the_documented_audiences_or_a_resource_url():
    asked = []
    creds = _nu(token=lambda audience: asked.append(audience) or "tok").credentials
    for audience in ("storage", "PBI", "keyvault", "kusto", "https://database.windows.net/"):
        assert creds.getToken(audience) == "tok"
    assert asked == ["storage", "pbi", "keyvault", "kusto", "https://database.windows.net/"]
    with pytest.raises(ValueError, match="Unknown audience 'graph'"):
        creds.getToken("graph")


class _Reply:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


@pytest.mark.parametrize("status", [401, 403])
def test_a_key_vault_that_refuses_a_secret_names_the_role_and_never_the_token(monkeypatch, status):
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: _Reply(status)))
    creds = _nu(token=lambda audience: "secret-token").credentials
    with pytest.raises(PermissionError, match="Key Vault Secrets User") as exc:
        creds.getSecret("kv1", "db-password")
    assert "secret-token" not in str(exc.value)


@pytest.mark.parametrize("vault, secret", [("evil.example.com", "s"), ("https://kv1.vault.azure.net.evil.com", "s"),
                                           ("kv1", "../keys/k"), ("kv1", "a" * 128)])
def test_a_secret_from_anywhere_but_a_key_vault_name_is_refused_before_a_request(monkeypatch, vault, secret):
    sent = []
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: sent.append(a)))
    with pytest.raises(ValueError):
        _nu(token=lambda audience: "tok").credentials.getSecret(vault, secret)
    assert sent == []


# ---------------------------------------------------------------- runMultiple


def _runner(ran):
    return _nu(run_notebook=lambda path, timeout, args, **kw: ran.append(path) or "done").notebook


@pytest.mark.parametrize("dag, error, words", [
    (["a", "a"], ValueError, "listed twice"),
    ("a", TypeError, "list of notebook names or a DAG"),
    ({"activities": [], "retries": 1}, NotImplementedError, "doesn't support retries"),
    ({"activities": [{"path": "a", "workspace": "w"}]}, NotImplementedError, "workspace in an activity"),
    ({"activities": [{"name": "a"}]}, ValueError, "needs a path"),
    ({"activities": [{"path": "a"}, {"path": "a"}]}, ValueError, "unique name"),
    ({"activities": [{"path": "a", "dependencies": ["z"]}]}, ValueError, "unknown dependency z"),
    ({"activities": [{"name": "a", "path": "a", "dependencies": ["b"]},
                     {"name": "b", "path": "b", "dependencies": ["a"]}]}, ValueError, "form a cycle"),
])
def test_a_dag_the_pc_cant_honor_is_refused_before_any_run(dag, error, words):
    ran: list[str] = []
    nb = _runner(ran)
    with pytest.raises(error, match=words):
        nb.runMultiple(dag)
    with pytest.raises(error, match=words):
        nb.validateDAG(dag)
    assert ran == []


def test_a_list_of_notebooks_runs_each_once():
    ran: list[str] = []
    out = _runner(ran).runMultiple(["a", "b"])
    assert ran == ["a", "b"] and out["b"] == {"exitVal": "done", "exception": None}


def test_running_another_notebook_needs_the_app():
    with pytest.raises(RuntimeError, match="isn't available here"):
        _nu().notebook.run("a")


def test_running_a_notebook_in_another_workspace_runs_in_fabric():
    with pytest.raises(NotImplementedError, match="leave out workspace"):
        _runner([]).run("a", 60, {}, "other-ws")


# ---------------------------------------------------------------- variable libraries, nbResPath


def test_a_variable_library_reads_its_values_and_names_a_missing_one():
    lib = _nu(variables=lambda name: {"host": "crm.local", "port": 1433}).variableLibrary.getLibrary("VL_Sources")
    assert (lib.host, lib.port) == ("crm.local", 1433) and dir(lib) == ["host", "port"]
    assert repr(lib) == "VariableLibrary(VL_Sources, 2 variables)"
    with pytest.raises(AttributeError, match="VL_Sources has no variable user"):
        lib.user  # noqa: B018


def test_a_variable_reference_must_have_the_documented_shape():
    vl = _nu(variables=lambda name: {"host": "crm.local"}).variableLibrary
    assert vl.get("$(/**/VL_Sources/host)") == "crm.local"
    with pytest.raises(ValueError, match="Use a reference like"):
        vl.get("VL_Sources.host")


def test_a_variable_library_needs_an_attached_lakehouse():
    with pytest.raises(LookupError, match="Attach a lakehouse"):
        _nu().variableLibrary.getLibrary("VL_Sources")


def test_nb_res_path_is_set_only_while_a_saved_notebook_runs(tmp_path):
    assert _nu(resources=lambda: str(tmp_path)).nbResPath == str(tmp_path)
    with pytest.raises(AttributeError, match="set while a saved notebook runs"):
        _nu().nbResPath  # noqa: B018
