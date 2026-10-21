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
    assert _last(out) == "42"
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
