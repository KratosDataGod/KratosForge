"""Databricks Connect: version checks, installs, the environment the process gets, and real cells in
the worker process against a fake databricks-connect package. Nothing reaches a workspace."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import app_settings, connprofiles
from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import dbconnect as dbc
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import CAPABILITIES, check_cell
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported
from fabric_desktop.engines.databricks_auth import DatabricksAuthError

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
PY = "%d.%d" % sys.version_info[:2]
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "cli-profile", "profile": "dev"},
        "compute": {"cluster_id": "0123-456789-abcdefgh"}}

FAKE = '''
import time
__version__ = "16.4.2"

class _Frame:
    schema = "fake"
    def __init__(self, rows):
        self.rows = rows
    def limit(self, n):
        return _Frame(self.rows[:n])
    def toPandas(self):
        import pandas as pd
        return pd.DataFrame(self.rows, columns=["id", "name"])

class _Spark:
    interrupted = False
    def range(self, n):
        return _Frame([[i, None if i % 2 else f"n{i}"] for i in range(n)])
    def sql(self, q):
        if "slow" in q:
            for _ in range(200):
                if _Spark.interrupted:
                    raise RuntimeError("[OPERATION_CANCELED] interrupted")
                time.sleep(0.05)
        return self.range(3)
    def interruptAll(self):
        _Spark.interrupted = True

class _Builder:
    def getOrCreate(self):
        import os
        if os.environ.get("DATABRICKS_CLUSTER_ID") == "fail":
            raise RuntimeError("cluster not found")
        return _Spark()

class DatabricksSession:
    builder = _Builder()
'''


@pytest.fixture(autouse=True)
def signed_in(tmp_path, monkeypatch):
    """A ~/.databrickscfg with the "dev" profile signed in through the CLI, and the CLI on PATH."""
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[dev]\nhost = {HOST}\nauth_type = databricks-cli\n")
    monkeypatch.setattr(connprofiles, "databricks_config_path", lambda: cfg)
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: "/opt/databricks/databricks")
    return cfg


@pytest.fixture
def runtimes(tmp_path):
    r = dbc.Runtimes(tmp_path)
    folder = r.folder("16.4")
    (folder / "databricks" / "connect").mkdir(parents=True)
    (folder / "databricks" / "__init__.py").write_text("")
    (folder / "databricks" / "connect" / "__init__.py").write_text(FAKE)
    meta = folder / "databricks_connect-16.4.2.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text("Metadata-Version: 2.1\nName: databricks-connect\nVersion: 16.4.2\n")
    return r


def adapter(runtimes, version="16.4.x-scala2.12", cell_seconds=60):
    # A cell limit, so a worker that stops answering fails the test instead of hanging the run.
    return dbc.DatabricksConnectAdapter(runtimes, cluster_of=lambda conn, cid: {"spark_version": version,
                                                                                 "state": "RUNNING"},
                                        cell_seconds=cell_seconds)


def ok(cluster="0123-456789-abcdefgh"):
    return auth.confirmed("databricks-connect", cluster, "chris")


# ---------------------------------------------------------------- descriptor and checks


def test_registered_as_preview_remote_in_dbu_with_pyspark_native():
    d = registry.get("databricks-connect")
    assert d.adapter and d.remote and d.preview and d.billing == "databricks_dbu"
    assert set(d.as_dict()["capabilities"]) == set(CAPABILITIES)
    assert d.language("pyspark").level == "native" and d.language("python").level == "partial"
    assert not check_cell(d, "sql")["ok"]


def test_versions_are_checked_before_anything_starts():
    assert dbc.runtime_line("16.4.x-cpu-ml-scala2.12") == (16, 4)
    assert dbc.check("16.4.x-scala2.12", "16.4.2", "3.12") == []
    assert dbc.check("17.3.x-scala2.13", "17.3.0", "3.12") == []
    assert "Python 3.11" in dbc.check("15.4.x-scala2.12", "15.4.1", "3.12")[0]
    assert dbc.check("16.3.x-scala2.12", None, "3.12") == [
        ("Databricks Connect for Databricks Runtime 16.3 isn't supported in this version of KratosForge. "
         "Supported: 16.4, 17.3.")]
    assert "isn't installed" in dbc.check("16.4.x-scala2.12", None, "3.12")[0]
    assert "needs 16.4.x" in dbc.check("16.4.x-scala2.12", "16.3.0", "3.12")[0]
    assert "13.3 and later" in dbc.check("12.2.x-scala2.12", "12.2.0", "3.12")[0]
    assert "isn't supported in this version" in dbc.check("18.0.x-scala2.13", "18.0.1", "3.12")[0]
    assert "Update KratosForge" in dbc.check("42.0.x-scala2.13", "42.0.0", "3.12")[0]
    with pytest.raises(Unsupported):
        dbc.runtime_line("custom:latest")


def test_installs_are_hash_pinned_wheels_from_the_lock_file_in_their_own_folder(tmp_path, monkeypatch):
    seen = []
    r = dbc.Runtimes(tmp_path, python="py", run=lambda cmd, **kw: seen.append((cmd, kw)) or SimpleNamespace(
        returncode=0, stdout="ok", stderr=""))
    base = {"PATH": "/bin", "TEMP": "/tmp", "PIP_INDEX_URL": "https://evil.example.com", "PIP_CONFIG_FILE": "/x",
            "PYTHONPATH": "/evil", "DATABRICKS_TOKEN": "x", "HTTPS_PROXY": "http://proxy:8080"}
    out = r.install("16.4", base)
    cmd, kw = seen[0]
    assert cmd[:6] == ["py", "-I", "-m", "pip", "install", "--isolated"]
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps"):
        assert flag in cmd
    assert cmd[cmd.index("-r") + 1] == str(dbc.LOCKS / "16.4.txt") and "--index-url" not in cmd
    folder = str(tmp_path / "runtimes" / "dbconnect-16.4" / "site-packages")
    assert cmd[cmd.index("--target") + 1] == folder and kw["cwd"] == folder
    assert kw["env"] == {"PATH": "/bin", "TEMP": "/tmp", "HTTPS_PROXY": "http://proxy:8080"}
    assert kw["timeout"] and out["ok"] and out["version"] is None
    with pytest.raises(ValueError):
        r.folder("../16.4")
    with pytest.raises(Unsupported, match="Supported: 16.4, 17.3"):
        r.install("16.3", base)
    slow = dbc.Runtimes(tmp_path, run=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("pip", 1)))
    assert not slow.install("16.4")["ok"]


def test_policy_can_point_installs_at_a_mirror_or_turn_them_off(tmp_path, monkeypatch):
    seen = []
    r = dbc.Runtimes(tmp_path, python="py", run=lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    values = {"allow_dbconnect_install": True, "dbconnect_index_url": "https://pkgs.contoso.com/pypi/simple"}
    monkeypatch.setattr(app_settings, "value", lambda key: values[key])
    r.install("17.3")
    assert seen[0][seen[0].index("--index-url") + 1] == values["dbconnect_index_url"]
    assert "--require-hashes" in seen[0]
    for bad in ("http://pkgs.contoso.com/simple", "https://user:pw@pkgs.contoso.com/simple"):
        values["dbconnect_index_url"] = bad
        with pytest.raises(ValueError):
            r.install("17.3")
    values["allow_dbconnect_install"] = False
    with pytest.raises(PermissionError):
        r.install("17.3")
    assert len(seen) == 1
    assert app_settings.RESTRICTED["allow_dbconnect_install"] is False


@pytest.mark.parametrize("line", dbc.supported_lines())
def test_every_lock_file_pins_each_package_to_one_version_with_a_hash(line):
    text = (dbc.LOCKS / f"{line}.txt").read_text()
    reqs = [r for r in text.replace("\\\n", " ").splitlines() if r.strip() and not r.lstrip().startswith("#")]
    assert reqs and any(r.startswith(f"databricks-connect=={line}.") for r in reqs)
    for r in reqs:
        name = r.split()[0]
        assert "==" in name and "--hash=sha256:" in r, name


def test_the_process_gets_the_connections_sign_in_and_nothing_inherited():
    base = {"PATH": "/bin", "DATABRICKS_TOKEN": "x", "databricks_host": "y", "PYTHONPATH": "/evil",
            "SPARK_REMOTE": "sc://evil:443", "SPARK_CONNECT_USER_AGENT": "x"}
    env = dbc.worker_env(CONN, "c1", base)
    assert "SPARK_REMOTE" not in env and "SPARK_CONNECT_USER_AGENT" not in env
    assert env["DATABRICKS_CONFIG_PROFILE"] == "dev" and env["DATABRICKS_CLUSTER_ID"] == "c1"
    assert "DATABRICKS_TOKEN" not in env and "databricks_host" not in env and "PYTHONPATH" not in env
    env = dbc.worker_env({**CONN, "credential": {"method": "oauth-u2m"}}, "c1", base)
    assert env["DATABRICKS_AUTH_TYPE"] == "databricks-cli" and env["DATABRICKS_HOST"] == HOST
    m2m = {**CONN, "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP"}}
    env = dbc.worker_env(m2m, "c1", base, environ={"SP": "s3cret"})
    assert env["DATABRICKS_CLIENT_SECRET"] == "s3cret" and env["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    with pytest.raises(DatabricksAuthError):
        dbc.worker_env({**CONN, "host": "https://evil.example.com"}, "c1", base)


# ---------------------------------------------------------------- opening


def test_nothing_is_read_or_started_without_an_authorization_for_that_cluster(runtimes):
    read = []
    a = dbc.DatabricksConnectAdapter(runtimes, cluster_of=lambda c, i: read.append(i) or {})
    with pytest.raises(auth.RemoteNotAuthorized):
        a.open_session(SessionConfig("nb", options={"connection": CONN}))
    with pytest.raises(auth.RemoteNotAuthorized):
        a.open_session(SessionConfig("nb", options={"connection": CONN}), ok("other-cluster"))
    assert read == []


def test_serverless_and_missing_clusters_are_refused(runtimes):
    with pytest.raises(Unsupported, match="Serverless"):
        adapter(runtimes).open_session(SessionConfig("nb", options={"connection": CONN, "serverless": True}), ok())
    with pytest.raises(Unsupported, match="Pick a cluster"):
        adapter(runtimes).open_session(SessionConfig("nb", options={"connection": {**CONN, "compute": {}}}), ok())
    with pytest.raises(Unsupported, match="Pick a cluster"):
        adapter(runtimes).open_session(SessionConfig("nb", options={"connection": CONN, "cluster_id": "../x"}), ok())


def test_an_incompatible_cluster_says_why_and_starts_no_process(runtimes):
    with pytest.raises(Unsupported, match="runs Python 3.11"):
        adapter(runtimes, "15.4.x-scala2.12").open_session(SessionConfig("nb", options={"connection": CONN}), ok())
    with pytest.raises(Unsupported, match="17.3 isn't installed"):
        adapter(runtimes, "17.3.x-scala2.13").open_session(SessionConfig("nb", options={"connection": CONN}), ok())


# ---------------------------------------------------------------- the worker, for real


@pytest.fixture
def session(runtimes, monkeypatch):
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    s = adapter(runtimes).open_session(SessionConfig("nb", options={"connection": CONN}), ok())
    yield s
    s.close()


def test_cells_run_with_spark_and_show_the_first_rows(session):
    assert session.client == "16.4.2"
    seen = []
    r = session.execute(CellRequest("print('hi')\nx = 41\nspark.range(5)", "pyspark", preview_rows=2), seen.append)
    assert r.status == "ok" and r.rows == 2 and r.engine == "databricks-connect"
    assert r.outputs[0] == {"type": "stream", "text": "hi\n"}
    table = r.outputs[1]
    assert table["preview_only"] and table["total_rows"] is None and len(table["rows"]) == 2 and seen == r.outputs
    r = session.execute(CellRequest("x + 1", "python"))
    assert r.outputs == [{"type": "text", "text": "42"}] and r.rows is None


def test_display_and_errors(session):
    r = session.execute(CellRequest("display(spark.sql('select 1'))", "pyspark"))
    assert r.status == "ok" and r.rows == 3
    r = session.execute(CellRequest("1/0", "python"))
    assert r.status == "error" and r.outputs[-1]["ename"] == "ZeroDivisionError"
    with pytest.raises(Unsupported, match="spark.sql"):
        session.execute(CellRequest("select 1", "sql"))


def test_cancel_interrupts_the_spark_job(session):
    import threading
    result = {}
    t = threading.Thread(target=lambda: result.update(r=session.execute(CellRequest("spark.sql('slow')", "pyspark"))))
    t.start()
    for _ in range(100):
        if session._running:
            break
        time.sleep(0.02)
    time.sleep(0.2)
    assert session.cancel()
    t.join(10)
    assert result["r"].status == "error" and "OPERATION_CANCELED" in result["r"].outputs[-1]["evalue"]


def test_close_ends_the_process(session):
    proc = session.proc
    session.close()
    assert proc.poll() is not None
    with pytest.raises(SessionClosed):
        session.execute(CellRequest("1", "python"))
    assert session.cancel() is False


def test_a_cluster_that_fails_to_connect_reports_and_leaves_no_process(runtimes, monkeypatch):
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    conn = {**CONN, "compute": {"cluster_id": "fail"}}
    with pytest.raises(RuntimeError, match="cluster not found"):
        adapter(runtimes).open_session(SessionConfig("nb", options={"connection": conn}), ok("fail"))


def test_the_sdk_gets_the_cli_find_cli_trusts_never_one_in_the_current_folder(tmp_path, monkeypatch, signed_in):
    import os
    monkeypatch.setattr(dbc, "find_cli", dba.find_cli)
    name = "databricks.exe" if sys.platform == "win32" else "databricks"
    real = tmp_path / "bin"
    real.mkdir()
    (real / name).write_text("")
    (real / name).chmod(0o755)
    env = dbc.worker_env({**CONN, "credential": {"method": "oauth-u2m"}}, "c1",
                         {"PATH": os.pathsep.join([".", str(real)]), "PATHEXT": ".EXE"})
    assert env["DATABRICKS_CLI_PATH"] == str(real / name)
    # A sign-in through the CLI doesn't start without one find_cli trusts.
    for cred in ({"method": "oauth-u2m"}, CONN["credential"]):
        with pytest.raises(DatabricksAuthError, match="Databricks CLI"):
            dbc.worker_env({**CONN, "credential": cred}, "c1", {"PATH": "."})
    # A profile with its own token or service principal doesn't need the CLI.
    signed_in.write_text(f"[dev]\nhost = {HOST}\nclient_id = abc\nclient_secret = s\n")
    assert "DATABRICKS_CLI_PATH" not in dbc.worker_env(CONN, "c1", {"PATH": "."})
    m2m = {**CONN, "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP"}}
    assert "DATABRICKS_CLI_PATH" not in dbc.worker_env(m2m, "c1", {"PATH": "."}, environ={"SP": "s"})


def test_the_folders_pth_files_run_and_its_packages_come_first(runtimes, monkeypatch, tmp_path):
    folder = runtimes.folder("16.4")
    extra = tmp_path / "shim"
    extra.mkdir()
    (extra / "distutils_shim_probe.py").write_text("FOUND = True\n")
    (folder / "shim.pth").write_text(str(extra) + "\n")
    (folder / "json_probe_marker.py").write_text("WHERE = 'folder'\n")
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    s = adapter(runtimes).open_session(SessionConfig("nb", options={"connection": CONN}), ok())
    try:
        r = s.execute(CellRequest("import sys, os, distutils_shim_probe\n"
                                  "(distutils_shim_probe.FOUND, sys.path[0] == %r, os.getcwd() == %r)"
                                  % (str(folder), str(folder)), "python"))
        assert r.outputs == [{"type": "text", "text": "(True, True, True)"}]
    finally:
        s.close()


def test_the_worker_never_loads_the_engines_modules(runtimes, monkeypatch):
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    s = adapter(runtimes).open_session(SessionConfig("nb", options={"connection": CONN}), ok())
    try:
        r = s.execute(CellRequest(textwrap.dedent("""
            import sys
            sorted(m for m in sys.modules if m.startswith(('fabric_desktop', 'pyspark')) or m in ('contract', 'registry'))
            """), "python"))
        assert r.outputs == [{"type": "text", "text": "[]"}]
    finally:
        s.close()


def test_a_service_principals_secret_is_gone_from_the_process_once_signed_in(runtimes, monkeypatch):
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    monkeypatch.setenv("SP_SECRET_FOR_TEST", "s3cret")
    m2m = {**CONN, "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP_SECRET_FOR_TEST"}}
    s = adapter(runtimes).open_session(SessionConfig("nb", options={"connection": m2m}), ok())
    try:
        r = s.execute(CellRequest("import os\n[k for k, v in os.environ.items() if 's3cret' in v]", "python"))
        assert r.outputs == [{"type": "text", "text": "[]"}]
    finally:
        s.close()


def test_a_cell_past_the_limit_is_cancelled_and_the_session_ends(runtimes, monkeypatch):
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: PY})
    monkeypatch.setattr(dbc, "STOP_SECONDS", 1)
    s = adapter(runtimes, cell_seconds=1).open_session(SessionConfig("nb", options={"connection": CONN}), ok())
    try:
        # Plain Python ignores the cancel, so the process is ended.
        with pytest.raises(SessionClosed, match="still running"):
            s.execute(CellRequest("import time\ntime.sleep(30)", "python"))
        assert s.closed and s.proc.poll() is not None
    finally:
        s.close()


def test_cells_that_import_pandas_or_check_stdin_run_while_the_worker_waits(session):
    # On Windows a read left waiting on stdin blocks these calls; the worker never leaves one waiting.
    r = session.execute(CellRequest("import os, sys, pandas\n(os.isatty(0), sys.stdin.isatty())", "python"))
    assert r.status == "ok" and r.outputs == [{"type": "text", "text": "(False, False)"}]


def test_a_cells_input_gets_end_of_file_not_the_engines_messages(session):
    r = session.execute(CellRequest("input()", "python"))
    assert r.status == "error" and r.outputs[-1]["ename"] == "EOFError"
    assert session.execute(CellRequest("1 + 1", "python")).outputs == [{"type": "text", "text": "2"}]
