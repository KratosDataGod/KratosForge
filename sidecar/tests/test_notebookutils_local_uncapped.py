"""Mutation testing batch 178 (docs/quality/mutation-testing.md): notebookutils on this PC, every mutant.

Each test pins a behavior a surviving mutant changed without any other test noticing."""

from __future__ import annotations

import pytest

from fabric_desktop import lakehouse, notebookutils_local as nu, spark_local


class _Writer:
    def __init__(self, calls):
        self.calls = calls

    def mode(self, mode):
        self.calls.append(("mode", mode))
        return self

    def option(self, key, value):
        self.calls.append(("option", key, value))
        return self

    def saveAsTable(self, name):  # noqa: N802 - Spark's name
        self.calls.append(("saveAsTable", name))


class _Frame:
    def __init__(self, calls, path):
        self.calls, self.path = calls, path
        self.write = _Writer(calls)

    def unionByName(self, other, allowMissingColumns=False):  # noqa: N802, N803 - Spark's names
        self.calls.append(("union", other.path, allowMissingColumns))
        return self


class _Reader:
    def __init__(self, calls):
        self.calls = calls

    def csv(self, path, **kw):
        self.calls.append(("csv", path, kw))
        return _Frame(self.calls, path)

    def parquet(self, path):
        self.calls.append(("parquet", path))
        return _Frame(self.calls, path)


@pytest.fixture
def spark(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(spark_local, "session", lambda: type("S", (), {"read": _Reader(calls)})())
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield calls
    lakehouse.attach(None)


def test_load_table_returns_true_and_an_append_keeps_the_tables_schema(spark):
    assert nu._load_table({"relativePath": "Files/a.csv", "mode": "Append"}, " sales ", "", "") is True
    assert ("mode", "append") in spark and ("saveAsTable", "sales") in spark
    assert not [c for c in spark if c[0] == "option"]


def test_load_table_overwrite_replaces_the_schema(spark):
    assert nu._load_table({"relativePath": "Files/a.csv"}, "sales", "sales", "ws1") is True
    assert ("mode", "overwrite") in spark and ("option", "overwriteSchema", "true") in spark


def test_load_table_needs_an_attached_lakehouse(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", None)
    with pytest.raises(ValueError, match="Attach a lakehouse first"):
        nu._load_table({"relativePath": "Files/a.csv"}, "sales", "", "")


def test_file_info_has_no_change_time_unless_given():
    assert nu.FileInfo("a.csv", "Files/a.csv", 3, False).modifyTime == 0


def test_a_local_and_a_onelake_path_are_never_the_same_path(tmp_path):
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    try:
        assert nu._same_or_inside(f"file:{tmp_path}", "Files/x") is False
    finally:
        lakehouse.attach(None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from tests.test_notebookutils_fs import FakeStore

    s = FakeStore({"Files/raw/a.csv": b"a,b\n1,2\n", "Files/one.txt": b"hello"}, {"Files/empty"})
    monkeypatch.setattr(nu, "_store", lambda: s)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield s
    lakehouse.attach(None)


@pytest.mark.parametrize("path", ["Files/", "Files//"])
def test_files_itself_cant_be_changed(store, path):
    fs = nu.build(nu.Host()).fs
    with pytest.raises(ValueError, match="not Files/ itself"):
        fs.mkdirs(path)
    assert store.mutations() == []


@pytest.mark.parametrize("table", ["", "   "])
def test_load_table_needs_a_table_name(spark, table):
    with pytest.raises(ValueError, match="Give loadTable the table's name"):
        nu._load_table({"relativePath": "Files/a.csv"}, table, "", "")
    assert spark == []


def test_load_table_refuses_a_path_type_it_doesnt_know(spark):
    with pytest.raises(ValueError, match="pathType is File or Folder"):
        nu._load_table({"relativePath": "Files/a.csv", "pathType": "Link"}, "sales", "", "")
    assert spark == []


def test_head_of_a_local_file_reads_only_its_first_bytes(tmp_path, monkeypatch):
    f = tmp_path / "big.txt"
    f.write_bytes(b"abcdef")
    monkeypatch.setattr(nu.Path, "read_bytes", lambda self: pytest.fail("head read the whole file"))
    assert nu.build(nu.Host()).fs.head(f"file:{f}", 3) == "abc"


def test_a_folder_named_files_deeper_down_is_looked_up(store):
    fs = nu.build(nu.Host()).fs
    assert fs.exists("Files/raw/Files") is False
    assert fs.exists("Files") is True and fs.exists("Tables") is True


def test_put_without_overwrite_keeps_what_is_there(store, tmp_path):
    fs = nu.build(nu.Host()).fs
    with pytest.raises(FileExistsError, match="Pass overwrite=True"):
        fs.put("Files/one.txt", "new")
    assert store.files["Files/one.txt"] == b"hello"
    local = tmp_path / "here.txt"
    local.write_text("old")
    with pytest.raises(FileExistsError):
        fs.put(f"file:{local}", "new")
    assert local.read_text() == "old"


def test_cp_of_a_folder_needs_recurse(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    with pytest.raises(IsADirectoryError, match="Pass recurse=True"):
        nu.build(nu.Host()).fs.cp(f"file:{src}", f"file:{tmp_path / 'dest'}")
    assert not (tmp_path / "dest").exists()


def test_mv_onto_something_there_needs_overwrite(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    with pytest.raises(FileExistsError, match="Pass overwrite=True"):
        nu.build(nu.Host()).fs.mv(f"file:{a}", f"file:{b}")
    assert a.read_text() == "a" and b.read_text() == "b"


def test_an_unknown_notebookutils_name_is_an_attribute_error():
    mod = nu.build(nu.Host())
    with pytest.raises(AttributeError, match="notebookutils on this PC has no nosuch"):
        mod.nosuch  # noqa: B018
    assert not hasattr(mod, "nosuch")


def test_mv_into_itself_is_refused_as_a_move(tmp_path):
    src = tmp_path / "data"
    src.mkdir()
    (src / "a.txt").write_text("a")
    with pytest.raises(ValueError, match="can't be moved there"):
        nu.build(nu.Host()).fs.mv(f"file:{src}", f"file:{src / 'sub'}")
    assert sorted(p.name for p in src.iterdir()) == ["a.txt"]


def test_a_link_with_a_port_that_isnt_a_number_is_not_on_the_vault():
    vault = nu.vault_url("myvault")
    assert nu.on_vault("https://myvault.vault.azure.net:99999/secrets?x=1", vault) is False
    assert nu.on_vault("https://myvault.vault.azure.net:443/secrets?x=1", vault) is True


def test_notebook_run_gives_90_seconds_and_passes_a_cell_timeout_only_when_set():
    calls = []

    def run_notebook(path, timeout, args, **kw):
        calls.append((path, timeout, args, kw))
        return 7

    nb = nu.build(nu.Host(run_notebook=run_notebook)).notebook
    assert nb.run("child") == "7"
    assert nb.run("child", 10, {"a": 1}, cell_timeout=5) == "7"
    assert calls == [("child", 90, {}, {}), ("child", 10, {"a": 1}, {"cell_timeout": 5})]


class _Client:
    def list_notebooks(self, ws):
        return [{"displayName": f"n{i}", "workspaceId": ws} for i in range(1001)]

    def list_lakehouses(self, ws):
        return [{"displayName": f"l{i}"} for i in range(1001)]

    def find_lakehouse(self, ws, name):
        return {"id": "lh1", "workspaceId": ws, "displayName": name}

    def list_tables(self, ws, lh):
        return [{"name": f"t{i}"} for i in range(1001)]


def test_item_lists_give_1000_items_by_default(monkeypatch):
    monkeypatch.setattr(nu, "_client", lambda host: _Client())
    mod = nu.build(nu.Host(token=lambda aud: "tok"))
    assert len(mod.notebook.list("ws1")) == 1000
    assert len(mod.lakehouse.list("ws1")) == 1000
    assert len(mod.lakehouse.listTables("Sales", "ws1")) == 1000


def test_the_runtime_context_equals_its_copy():
    ctx = nu.build(nu.Host(notebook_name=lambda: "nb")).runtime.context
    assert (ctx == ctx.copy()) is True
    assert (ctx != ctx.copy()) is False


def test_a_local_folder_is_listed_with_size_0(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.txt").write_text("abc")
    listed = {i.name: (i.size, i.isDir) for i in nu.build(nu.Host()).fs.ls(f"file:{tmp_path}")}
    assert listed == {"sub": (0, True), "f.txt": (3, False)}


def test_head_of_one_byte_is_one_byte(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("abc")
    fs = nu.build(nu.Host()).fs
    assert fs.head(f"file:{f}", 1) == "a" and fs.head(f"file:{f}", 0) == ""


def test_a_key_vault_400_is_an_error_not_a_value(monkeypatch):
    import requests

    resp = requests.Response()
    resp.status_code = 400
    resp._content = b'{"error": {"code": "BadParameter"}}'
    monkeypatch.setattr(nu, "send_with_retry", lambda send, method: resp)
    creds = nu.build(nu.Host(token=lambda aud: "tok")).credentials
    with pytest.raises(requests.HTTPError, match=r"couldn't read 'db-pass' \(400: BadParameter\)"):
        creds.getSecret("myvault", "db-pass")


def test_load_table_of_an_empty_folder_says_so(spark, monkeypatch):
    from fabric_desktop import onelake

    monkeypatch.setattr(onelake.FileStore, "list", lambda self, ws, lh, rel, recursive=False: [])
    with pytest.raises(FileNotFoundError, match="Files/in has no files to load"):
        nu._load_table({"relativePath": "Files/in", "pathType": "Folder"}, "sales", "", "")
    assert spark == []


def test_a_listing_onelake_refuses_is_raised_without_looking_further(store, monkeypatch):
    import requests

    calls = []

    def refuse(ws, lh, directory="Files", recursive=False, max_results=None):
        calls.append(directory)
        resp = requests.Response()
        resp.status_code = 403
        raise requests.HTTPError("403", response=resp)

    monkeypatch.setattr(store, "list", refuse)
    with pytest.raises(requests.HTTPError, match="403"):
        nu.build(nu.Host()).fs.ls("Files/raw")
    assert calls == ["Files/raw"]


def test_rm_asks_onelake_for_one_entry_to_see_a_folder_isnt_empty(store, monkeypatch):
    asked = []
    real = store.list

    def listing(ws, lh, directory="Files", recursive=False, max_results=None):
        asked.append((directory, max_results))
        return real(ws, lh, directory, recursive, max_results)

    monkeypatch.setattr(store, "list", listing)
    with pytest.raises(IsADirectoryError):
        nu.build(nu.Host()).fs.rm("Files/raw")
    assert ("Files/raw", 1) in asked


def test_the_runtime_context_is_an_interactive_run_of_its_own(monkeypatch):
    monkeypatch.setenv("USERNAME", "winuser")
    monkeypatch.delenv("USER", raising=False)
    ctx = nu.build(nu.Host()).runtime.context
    assert ctx["isForPipeline"] is False and ctx["isReferenceRun"] is False and ctx["isForInteractive"] is True
    assert ctx["userName"] == "winuser"


def test_the_default_lakehouse_is_looked_up_in_the_workspace_given(monkeypatch, tmp_path):
    asked = []

    class Client(_Client):
        def find_lakehouse(self, ws, name):
            asked.append((ws, name))
            return {"id": "lh1", "workspaceId": ws, "displayName": name, "properties": {}}

    monkeypatch.setattr(nu, "_client", lambda host: Client())
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    try:
        got = nu.build(nu.Host(token=lambda aud: "tok")).lakehouse.get(workspaceId="ws9")
    finally:
        lakehouse.attach(None)
    assert asked == [("ws9", "Sales")] and "properties" not in got


def test_load_table_of_a_folder_unions_its_files_by_name_with_missing_columns(spark, monkeypatch):
    from fabric_desktop import onelake

    monkeypatch.setattr(onelake.FileStore, "list", lambda self, ws, lh, rel, recursive=False: [
        {"name": "b.csv", "path": "Files/in/b.csv", "is_dir": False},
        {"name": "a.csv", "path": "Files/in/a.csv", "is_dir": False}])
    assert nu._load_table({"relativePath": "Files/in", "pathType": "Folder"}, "sales", "", "") is True
    assert ("union", "Files/in/b.csv", True) in spark


def test_a_variable_reference_reads_the_library_it_names():
    asked = []

    def variables(name):
        asked.append(name)
        return {"CRM_server_host": "crm.local"}

    vl = nu.build(nu.Host(variables=variables)).variableLibrary
    assert vl.get("$(/**/VL_Sources/CRM_server_host)") == "crm.local"
    assert asked == ["VL_Sources"]


def test_get_secret_asks_key_vault_once_with_a_30_second_limit(monkeypatch):
    import requests

    sent = []
    resp = requests.Response()
    resp.status_code = 200
    resp._content = b'{"value": "s3"}'

    class Session:
        def get(self, url, **kw):
            sent.append((url, kw))
            return resp

    monkeypatch.setattr(nu.http_session, "session", lambda: Session())
    monkeypatch.setattr(nu, "send_with_retry", lambda send, method: send())
    creds = nu.build(nu.Host(token=lambda aud: "tok")).credentials
    assert creds.getSecret("myvault", "db-pass") == "s3"
    [(url, kw)] = sent
    assert url == "https://myvault.vault.azure.net/secrets/db-pass"
    assert kw["timeout"] == 30 and kw["params"] == {"api-version": "7.4"}


def _token(claims: dict) -> str:
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def test_a_token_is_valid_until_the_moment_it_expires(monkeypatch):
    monkeypatch.setattr(nu.time, "time", lambda: 1000.0)
    creds = nu.build(nu.Host()).credentials
    assert creds.isValidToken(_token({"exp": 1001})) is True
    assert creds.isValidToken(_token({"exp": 1000})) is False
    # Payloads of every length mod 4 have their padding put back.
    for pad in ("", "x", "xx", "xxx", "xxxx", "xxxxx"):
        assert creds.isValidToken(_token({"exp": 2000, "p": pad})) is True, pad


@pytest.mark.parametrize("count, listed", [(5, "f0.txt, f1.txt, f2.txt, f3.txt, f4.txt)"),
                                           (6, "f0.txt, f1.txt, f2.txt, f3.txt, f4.txt and more)")])
def test_load_table_names_at_most_five_files_of_another_format(spark, monkeypatch, count, listed):
    from fabric_desktop import onelake

    monkeypatch.setattr(onelake.FileStore, "list", lambda self, ws, lh, rel, recursive=False: [
        {"name": f"f{i}.txt", "path": f"Files/in/f{i}.txt", "is_dir": False} for i in range(count)])
    with pytest.raises(ValueError) as err:
        nu._load_table({"relativePath": "Files/in", "pathType": "Folder"}, "sales", "", "")
    assert f"aren't .csv ({listed}." in str(err.value)


def test_head_of_0_bytes_reads_nothing(store):
    assert nu.build(nu.Host()).fs.head("Files/one.txt", 0) == ""
    assert store.calls == []
