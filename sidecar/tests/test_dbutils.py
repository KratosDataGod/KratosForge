"""Databricks dbutils on this PC: fs, notebook, widgets, secrets, task values, restartPython."""

import pytest

from fabric_desktop import dbutils_local
from fabric_desktop.notebook import Kernel, NotebookStore


def _last(out):
    assert out["status"] == "ok", out
    return out["outputs"][-1]["text"]


def test_dbutils_is_a_global_and_importable_like_databricks(tmp_path):
    k = Kernel()
    folder = (tmp_path / "out").as_posix()
    out = k.run(f"dbutils.fs.mkdirs('file:{folder}')\n"
                f"dbutils.fs.put('file:{folder}/a.txt', 'hello')\n"
                f"print(dbutils.fs.head('file:{folder}/a.txt'))\n"
                "from databricks.sdk.runtime import dbutils as d\n"
                f"[f.name for f in d.fs.ls('file:{folder}')]")
    assert out["outputs"][0] == {"type": "stream", "text": "hello\n"}
    assert _last(out) == "['a.txt']"
    assert "dbutils" not in {v["name"] for v in k.variables()}


@pytest.mark.parametrize("path", ["dbfs:/tmp/x", "/dbfs/tmp/x", "/Volumes/main/raw/x"])
def test_databricks_storage_says_it_isnt_reachable(path):
    out = Kernel().run(f"dbutils.fs.ls({path!r})")
    assert out["status"] == "error"
    assert "Databricks storage" in out["outputs"][-1]["evalue"]


def test_widgets_defaults_values_and_errors():
    k = Kernel()
    out = k.run("dbutils.widgets.text('region', 'West', 'Region')\n"
                "dbutils.widgets.dropdown('env', 'dev', ['dev', 'prod'])\n"
                "dbutils.widgets.multiselect('days', 'Mon,Tue', ['Mon', 'Tue', 'Wed'])\n"
                "(dbutils.widgets.get('region'), dbutils.widgets.getAll()['days'], "
                "dbutils.widgets.getArgument('nope', 'fallback'))")
    assert _last(out) == "('West', 'Mon,Tue', 'fallback')"
    widgets = k.namespace["dbutils"].widgets
    assert [w["name"] for w in widgets.describe()] == ["region", "env", "days"]
    k.widget_values = {"region": "East"}  # set in the Widgets bar
    assert _last(k.run("dbutils.widgets.get('region')")) == "'East'"
    out = k.run("dbutils.widgets.dropdown('bad', 'qa', ['dev', 'prod'])")
    assert out["status"] == "error" and "isn't one of the choices" in out["outputs"][-1]["evalue"]
    out = k.run("dbutils.widgets.remove('region')\ndbutils.widgets.get('region')")
    assert out["outputs"][-1]["ename"] == "InputWidgetNotDefined"


def test_run_passes_arguments_as_widgets_and_returns_a_string(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("child", [
        {"type": "code", "source": "dbutils.widgets.text('region', 'all')\ndbutils.widgets.text('n', '1')"},
        {"type": "code", "source": "dbutils.notebook.exit(dbutils.widgets.get('region') * int(dbutils.widgets.get('n')))"},
    ])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    assert _last(k.run("dbutils.notebook.run('./child', 60, {'region': 'W', 'n': 3})")) == "'WWW'"
    assert _last(k.run("dbutils.notebook.run('child', 60)")) == "'all'"


def test_task_values_flow_between_run_multiple_activities(tmp_path):
    store = NotebookStore(tmp_path)
    store.save("load", [{"type": "code", "source": "dbutils.jobs.taskValues.set('rows', 42)"}])
    store.save("report", [{"type": "code", "source":
                           "dbutils.notebook.exit(dbutils.jobs.taskValues.get('load', 'rows', default=0))"}])
    k = Kernel(load_notebook=lambda name: store.load(name)["cells"])
    out = k.run("r = notebookutils.notebook.runMultiple({'activities': ["
                "{'name': 'load', 'path': 'load'}, {'name': 'report', 'path': 'report', 'dependencies': ['load']}]})\n"
                "r['report']['exitVal']")
    assert _last(out) == "'42'"
    # Run by hand, Databricks answers with debugValue.
    assert _last(k.run("dbutils.jobs.taskValues.get('load', 'rows', debugValue=7)")) == "7"
    out = k.run("dbutils.jobs.taskValues.get('load', 'rows')")
    assert out["status"] == "error" and "debugValue" in out["outputs"][-1]["evalue"]


def test_restart_python_stops_the_cell_and_clears_variables():
    k = Kernel()
    k.run("x = 1")
    out = k.run("y = 2\ndbutils.library.restartPython()\nz = 3")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"] == "Python restarted. Variables were cleared."
    assert not {"x", "y", "z"} & set(k.namespace)
    assert "dbutils" in k.namespace


def test_secrets_read_key_vault_as_you(monkeypatch):
    calls = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append((url, headers["Authorization"]))
            if url.endswith("/secrets/db-password"):
                return Resp({"value": "s3cret"})
            if url.endswith("/secrets"):
                return Resp({"value": [{"id": "https://kv.vault.azure.net/secrets/a"}], "nextLink": "https://kv.vault.azure.net/secrets?page=2"})
            return Resp({"value": [{"id": "https://kv.vault.azure.net/secrets/b"}]})

    from fabric_desktop import http_session
    monkeypatch.setattr(http_session, "session", lambda: Session())
    k = Kernel(token=lambda audience: f"token-{audience}")
    out = k.run("pw = dbutils.secrets.get('kv', 'db-password')\n"
                "([s.key for s in dbutils.secrets.list('kv')], dbutils.secrets.getBytes('kv', 'db-password'))")
    assert _last(out) == "(['a', 'b'], b's3cret')"
    assert calls[0] == ("https://kv.vault.azure.net/secrets/db-password", "Bearer token-keyvault")
    # A secret read through dbutils is hidden in Variables, like notebookutils' getSecret.
    assert next(v for v in k.variables() if v["name"] == "pw")["value"] == "hidden"


def test_entry_point_context_and_unsupported_calls():
    k = Kernel()
    k.notebook_name = "Sales"
    ctx = "dbutils.notebook.entry_point.getDbutils().notebook().getContext()"
    assert _last(k.run(f"{ctx}.notebookPath().get()")) == "'/Sales'"
    assert _last(k.run(f"{ctx}.currentRunId().isDefined()")) == "False"
    out = k.run("dbutils.credentials.assumeRole('arn')")
    assert out["outputs"][-1]["ename"] == "NotImplementedError"


def test_shim_leaves_a_real_databricks_package_alone(monkeypatch):
    import sys
    import types
    real = types.ModuleType("databricks")
    monkeypatch.setitem(sys.modules, "databricks", real)
    Kernel()
    assert sys.modules["databricks"] is real
    assert isinstance(dbutils_local.RestartPython(), dbutils_local.NotebookExit)


def test_checks_know_dbutils():
    from fabric_desktop import proof
    from fabric_desktop.fabric_run import compute_check
    cells = [{"type": "code", "source": "dbutils.widgets.text('a', '')"}]
    check = compute_check(cells, {"id": "lh"})
    assert check["local"]["ok"]
    assert not check["fabric"]["ok"] and "dbutils" in check["fabric"]["reasons"][0]["text"]
    found = proof.check([{"type": "code", "source": "dbutils.fs.put('Files/x', 'y')"}], {"id": "lh"})
    assert "notebookutils.fs" in str(found) or "changes files" in str(found)


def test_notebookutils_session_restarts_python_too():
    from fabric_desktop.fabric_run import compute_check
    k = Kernel()
    k.run("x = 1")
    out = k.run("notebookutils.session.restartPython()")
    assert out["outputs"][-1]["text"] == "Python restarted. Variables were cleared."
    assert "x" not in k.namespace
    assert compute_check([{"type": "code", "source": "mssparkutils.session.stop()"}], {"id": "lh"})["local"]["ok"]


@pytest.mark.parametrize("call", ["rm({p!r}, True)", "rm(dir={p!r}, recurse=True)", "put({p!r}, 'a', True)", "ls({p!r})", "mkdirs({p!r})",
                                  "cp('file:/tmp/a', {p!r})"])
def test_bare_absolute_paths_mean_dbfs_so_nothing_on_this_pc_changes(tmp_path, call):
    victim = tmp_path / "mnt" / "raw"
    victim.mkdir(parents=True)
    (victim / "keep.csv").write_text("precious")
    out = Kernel().run("dbutils.fs." + call.format(p=victim.as_posix()))
    assert out["status"] == "error" and "DBFS path" in out["outputs"][-1]["evalue"]
    assert (victim / "keep.csv").read_text() == "precious"
    assert Kernel().run(r"dbutils.fs.ls('C:\\data')")["status"] == "error"


def test_dbfs_scheme_is_caught_in_any_case():
    out = Kernel().run("dbutils.fs.ls('DBFS:/tmp')")
    assert out["status"] == "error" and "Databricks storage" in out["outputs"][-1]["evalue"]


def test_secrets_list_stays_on_the_vault_host(monkeypatch):
    sent = []

    class Resp:
        status_code = 200

        def json(self):
            return {"value": [{"id": "https://kv.vault.azure.net/secrets/a"}], "nextLink": "https://evil.example/steal"}

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            sent.append(url)
            return Resp()

    from fabric_desktop import http_session
    monkeypatch.setattr(http_session, "session", lambda: Session())
    out = Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")
    assert out["status"] == "error" and "another host" in out["outputs"][-1]["evalue"]
    assert sent == ["https://kv.vault.azure.net/secrets"]


@pytest.mark.parametrize("vault", ["https://evil.example", "evil.example/x#", "https://kv.vault.azure.net.evil.example",
                                   "https://kv.vault.azure.net@evil.example", "http://kv.vault.azure.net",
                                   "https://kv.vault.azure.net:8443", "https://kv.vault.azure.net/other", "kv?x=1"])
def test_the_key_vault_token_only_goes_to_a_key_vault(monkeypatch, vault):
    import types

    from fabric_desktop import http_session
    sent = []
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(
        get=lambda url, **kw: sent.append(url)))
    k = Kernel(token=lambda audience: "tok")
    k.namespace["vault"] = vault
    for code in ("notebookutils.credentials.getSecret(vault, 'pw')", "dbutils.secrets.get(vault, 'pw')",
                 "dbutils.secrets.list(vault)"):
        out = k.run(code)
        assert out["outputs"][-1]["ename"] == "ValueError" and "isn't a Key Vault" in out["outputs"][-1]["evalue"], out
    assert sent == []


def test_a_secret_name_cant_reach_another_path(monkeypatch):
    import types

    from fabric_desktop import http_session
    sent = []
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda url, **kw: sent.append(url)))
    out = Kernel(token=lambda audience: "tok").run("notebookutils.credentials.getSecret('kv', '../keys/k')")
    assert out["outputs"][-1]["ename"] == "ValueError" and sent == []
    from fabric_desktop.notebookutils_local import vault_url
    assert vault_url("https://KV.vault.azure.net/") == "https://kv.vault.azure.net" == vault_url(" kv ")


def test_secrets_list_follows_key_vaults_own_next_page_link(monkeypatch):
    import types

    from fabric_desktop import http_session
    sent = []
    pages = {"https://kv.vault.azure.net/secrets": {"value": [{"id": "https://kv.vault.azure.net/secrets/a"}],
                                                    "nextLink": "https://kv.vault.azure.net:443/secrets?api-version=7.4&$skiptoken=x"},
             "https://kv.vault.azure.net:443/secrets?api-version=7.4&$skiptoken=x":
                 {"value": [{"id": "https://kv.vault.azure.net:443/secrets/b"}]}}
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda url, **kw: (
        sent.append(url), types.SimpleNamespace(status_code=200, json=lambda: pages[url], raise_for_status=lambda: None))[1]))
    out = Kernel(token=lambda audience: "tok").run("[s.key for s in dbutils.secrets.list('KV')]")
    assert _last(out) == "['a', 'b']", out
    assert len(sent) == 2


@pytest.mark.parametrize("link", ["https://kv.vault.azure.net:8443/secrets", "http://kv.vault.azure.net/secrets",
                                  "https://kv.vault.azure.net@evil.example/secrets", "https://kv.vault.azure.net:x/secrets",
                                  "https://other.vault.azure.net/secrets"])
def test_secrets_list_refuses_a_next_page_off_the_vault(link):
    from fabric_desktop.notebookutils_local import on_vault
    assert not on_vault(link, "https://kv.vault.azure.net")


def test_a_secret_name_with_a_trailing_newline_is_refused():
    out = Kernel(token=lambda audience: "tok").run("notebookutils.credentials.getSecret('kv', 'pw\\n')")
    assert out["outputs"][-1]["ename"] == "ValueError", out
    from fabric_desktop.notebookutils_local import vault_url
    assert vault_url("MyVault") == "https://myvault.vault.azure.net"


class _KvResp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.headers = status, body, {"Retry-After": "0"}

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def close(self):
        pass


def _kv(monkeypatch, *answers):
    import types

    from fabric_desktop import http_session, retrying
    sent = []
    queue = list(answers)

    def get(url, **kw):
        sent.append(url)
        return queue.pop(0) if len(queue) > 1 else queue[0]
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=get))
    monkeypatch.setattr(retrying.time, "sleep", lambda s: None)
    return sent


def test_a_missing_secret_says_which_one_without_the_url(monkeypatch):
    _kv(monkeypatch, _KvResp(404, {"error": {"code": "SecretNotFound", "message": "A secret with (name/id) pw was not found"}}))
    out = Kernel(token=lambda audience: "tok").run("notebookutils.credentials.getSecret('kv', 'pw')")
    err = out["outputs"][-1]
    assert err["ename"] == "HTTPError"
    assert err["evalue"].startswith("Key Vault https://kv.vault.azure.net has no secret named 'pw'")
    assert "/secrets/" not in err["evalue"] and "api-version" not in err["evalue"]


def test_a_vault_that_keeps_throttling_says_so(monkeypatch):
    sent = _kv(monkeypatch, _KvResp(429))
    out = Kernel(token=lambda audience: "tok").run("notebookutils.credentials.getSecret('kv', 'pw')")
    assert "is throttling requests" in out["outputs"][-1]["evalue"] and len(sent) > 1  # waited out first


def test_other_key_vault_refusals_give_the_status_and_code(monkeypatch):
    _kv(monkeypatch, _KvResp(400, {"error": {"code": "BadParameter", "message": "secret value: hunter2"}}))
    out = Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")
    evalue = out["outputs"][-1]["evalue"]
    assert evalue == "Key Vault https://kv.vault.azure.net couldn't list its secrets (400: BadParameter)."
    assert "hunter2" not in evalue  # Key Vault's message is never shown


def test_an_odd_key_vault_error_code_isnt_shown(monkeypatch):
    _kv(monkeypatch, _KvResp(400, {"error": {"code": "X" * 500 + "\n injected"}}))
    out = Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")
    assert out["outputs"][-1]["evalue"] == "Key Vault https://kv.vault.azure.net couldn't list its secrets (400)."
