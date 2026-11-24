"""Snowpark (this PC, simulated) (engines/snowpark_local.py, SF-007 option A), against a fake of Snowpark in
its runtime folder: the real worker process opens a local testing session with telemetry always off and no
network, session.sql and unimplemented calls are refused, and installs come only from the pinned lock file.

The real runtime can't be installed in CI. ``test_the_real_runtime_*`` runs it when KF_SNOWPARK_SITE names a
site-packages folder holding snowflake-snowpark-python[localtest] 1.58 and KF_SNOWPARK_PYTHON the Python 3.12
it was installed for; otherwise it is skipped."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import app_settings, parity
from fabric_desktop.engines import registry, runtime_worker, runtimes
from fabric_desktop.engines import snowpark_local as sl
from fabric_desktop.engines.capabilities import check_cell
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported

SIDECAR = Path(__file__).resolve().parents[1]
ROOT = SIDECAR.parent

# A stand-in for Snowpark 1.58's local testing, shaped as its source is: Session.builder.configs(...).create(),
# a telemetry singleton in snowflake.snowpark.mock._telemetry that the session's options disable, and
# NotImplementedError ("[Local Testing] ...") for what it doesn't implement.
FAKE = '''
import os, socket

class Row(tuple):
    pass

class DataFrame:
    def __init__(self, rows, columns):
        self.rows, self.columns = rows, columns
    schema = "fake"
    def limit(self, n):
        return DataFrame(self.rows[:n], self.columns)
    def collect(self):
        return [Row(r) for r in self.rows]
    def toPandas(self):
        raise AssertionError("the preview must not need pyarrow")
    to_pandas = toPandas

class Session:
    options = None
    def __init__(self, options):
        from snowflake.snowpark.mock._telemetry import LocalTestOOBTelemetryService as T
        Session.options = dict(options)
        if not options.get("local_testing"):
            socket.create_connection(("myaccount.snowflakecomputing.com", 443))
        if options.get("disable_local_testing_telemetry") and not os.environ.get("FAKE_TELEMETRY_STUCK"):
            T.get_instance().disable()
        else:
            T.get_instance().log_session_creation()
    def create_dataframe(self, data, schema):
        return DataFrame([list(r) for r in data], [s.upper() for s in schema])
    def sql(self, query):
        raise NotImplementedError("[Local Testing] Session.sql is not supported.")
    def soundex(self):
        raise NotImplementedError("[Local Testing] Function soundex is not implemented.")
    def close(self):
        pass

    class SessionBuilder:
        def configs(self, options):
            self._options = options
            return self
        def create(self):
            return Session(self._options)
    builder = SessionBuilder()
'''
TELEMETRY = '''
import socket

class LocalTestOOBTelemetryService:
    PROD = "https://client-telemetry.snowflakecomputing.com/enqueue"
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._enabled = True
        self.sent = []

    @property
    def enabled(self):
        return self._enabled

    def disable(self):
        self._enabled = False

    def log_session_creation(self, uuid=None):
        if self._enabled:
            self.sent.append("session")  # queued; Snowpark sends the queue on flush or at exit

    def flush(self):
        """As Snowpark's: a POST to the telemetry host, when enabled."""
        if self._enabled:
            socket.getaddrinfo("client-telemetry.snowflakecomputing.com", 443)
'''


def _install_fake(r: sl.Runtimes, version: str = "1.58.0") -> None:
    folder = r.folder(sl.LINE)
    (folder / "snowflake" / "snowpark" / "mock").mkdir(parents=True)
    (folder / "snowflake" / "__init__.py").write_text("")
    (folder / "snowflake" / "snowpark" / "__init__.py").write_text(FAKE)
    (folder / "snowflake" / "snowpark" / "mock" / "__init__.py").write_text("")
    (folder / "snowflake" / "snowpark" / "mock" / "_telemetry.py").write_text(TELEMETRY)
    meta = folder / f"snowflake_snowpark_python-{version}.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: snowflake-snowpark-python\nVersion: {version}\n")


@pytest.fixture
def fake_runtimes(tmp_path):
    r = sl.Runtimes(tmp_path)
    _install_fake(r)
    return r


@pytest.fixture
def session(fake_runtimes):
    s = sl.SnowparkLocalAdapter(fake_runtimes, cell_seconds=60).open_session(SessionConfig("nb"))
    yield s
    s.close()


def run(s, code):
    return s.execute(CellRequest(code, "python"))


# ---- the descriptor and the plan


def test_registered_as_a_preview_local_engine_simulated_by_local_testing_and_never_billed():
    d = registry.get("snowpark-local")
    assert d is sl.SNOWPARK_LOCAL and d.adapter and d.preview and not d.remote and d.billing == "none"
    assert d.label == "Snowpark (this PC, simulated)" and d.family == "snowpark"
    assert d.language("python").level == "simulated"
    assert "simulated by Snowflake's local testing framework" in d.language("python").note
    assert d.capability("execute").level == "simulated"
    assert not d.language("pyspark").available and not d.language("sql").available
    assert any("never compared with Snowflake" in x for x in d.limitations)
    v = check_cell(d, "python")
    assert v["ok"] and v["level"] == "simulated"
    assert isinstance(registry.adapter("snowpark-local"), sl.SnowparkLocalAdapter)


def test_the_parity_cell_is_partial_at_most_and_never_verified():
    area = next(a for a in parity.AREAS if (a.platform, a.id) == ("snowflake", "snowpark"))
    local = area.cell("local")
    assert local.status == "partial" and "test_snowpark_local.py" in local.tests
    assert "simulated by Snowflake's local testing framework" in local.note and "Not a Snowflake runtime" in local.note
    assert area.cell("equivalence").status == "not_assessed"
    assert parity.PLANS[("snowflake", "snowpark")].mode == "simulated"


# ---- installing


def test_installs_are_hash_pinned_wheels_from_the_lock_file_into_their_own_folder(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))
        return type("Done", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(app_settings, "value", lambda key: {"allow_snowpark_install": True}.get(key, ""))
    r = sl.Runtimes(tmp_path, python="py", run=fake_run)
    out = r.install(sl.LINE)
    cmd, kw = seen[0]
    assert out["ok"] is True
    for flag in ("-I", "--isolated", "--require-hashes", "--only-binary=:all:", "--no-deps"):
        assert flag in cmd
    assert cmd[cmd.index("--target") + 1] == str(tmp_path / "runtimes" / "snowpark-1.58" / "site-packages")
    assert cmd[cmd.index("-r") + 1] == str(sl.LOCKS / "1.58.txt")
    assert {k.upper() for k in kw["env"]} <= set(runtimes.PIP_ENV)


def test_policy_can_point_installs_at_a_mirror_or_turn_them_off(tmp_path, monkeypatch):
    values = {"allow_snowpark_install": True, "snowpark_index_url": "https://pkgs.contoso.com/pypi/simple"}
    seen = []
    monkeypatch.setattr(app_settings, "value", lambda key: values.get(key))
    r = sl.Runtimes(tmp_path, python="py", run=lambda cmd, **kw: seen.append(cmd) or type(
        "D", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    r.install(sl.LINE)
    assert seen[0][seen[0].index("--index-url") + 1] == values["snowpark_index_url"]
    values["snowpark_index_url"] = "http://pkgs.contoso.com/simple"
    with pytest.raises(ValueError, match="snowpark_index_url must be an https address"):
        r.install(sl.LINE)
    values["allow_snowpark_install"] = False
    with pytest.raises(PermissionError, match="doesn't allow installing Snowpark Python"):
        r.install(sl.LINE)
    assert app_settings.RESTRICTED["allow_snowpark_install"] is False
    assert "snowpark_index_url" in app_settings.LOOSENING


def test_the_lock_file_pins_every_package_to_one_version_with_hashes():
    assert sl.supported_lines() == ["1.58"]
    text = (sl.LOCKS / "1.58.txt").read_text(encoding="utf-8")
    pins = re.findall(r"^([a-z0-9][a-z0-9._-]*)==(\S+) \\$", text, re.M)
    names = [n for n, _ in pins]
    assert ("snowflake-snowpark-python", "1.58.0") in pins and ("snowflake-connector-python", "4.8.1") in pins
    assert len(names) == len(set(names)) == 35
    blocks = re.split(r"^(?=[a-z0-9])", text, flags=re.M)[1:]
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", b) for b in blocks)
    assert "pyarrow" not in names  # the approved set is [localtest]; to_pandas needs pyarrow (docs/engines.md)


def test_the_lock_ships_and_is_audited_like_databricks_connects():
    data = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))
    assert "snowpark_locks/*.txt" in data["tool"]["setuptools"]["package-data"]["fabric_desktop.engines"]
    audit = (ROOT / ".github" / "actions" / "dependency-audit" / "action.yml").read_text(encoding="utf-8")
    sbom = (ROOT / ".github" / "scripts" / "sbom_check.py").read_text(encoding="utf-8")
    for text in (audit, sbom):
        assert "sidecar/fabric_desktop/engines/snowpark_locks/*.txt" in text


def test_the_app_itself_never_depends_on_snowpark():
    for name in ("pyproject.toml", "constraints.txt", "engine-requirements.txt", "ci-constraints.txt"):
        text = (SIDECAR / name).read_text(encoding="utf-8").lower()
        assert "snowflake-snowpark-python" not in text and "snowflake-connector-python" not in text, name


# ---- opening a session


def test_a_missing_or_other_version_runtime_says_why_and_starts_nothing(tmp_path):
    with pytest.raises(Unsupported, match="isn't installed yet"):
        sl.SnowparkLocalAdapter(sl.Runtimes(tmp_path)).open_session(SessionConfig("nb"))
    r = sl.Runtimes(tmp_path / "other")
    _install_fake(r, version="1.57.0")
    with pytest.raises(Unsupported, match="pins 1.58.x"):
        sl.SnowparkLocalAdapter(r).open_session(SessionConfig("nb"))


def test_the_process_gets_no_snowflake_snowpark_or_proxy_variables():
    base = {"PATH": "/bin", "SNOWFLAKE_PASSWORD": "x", "snowflake_account": "a", "SNOWSQL_PWD": "p",
            "SNOWPARK_LOCAL_TESTING_INTERNAL_TELEMETRY": "1", "HTTPS_PROXY": "http://127.0.0.1:3128",
            "http_proxy": "h", "ALL_PROXY": "a", "NO_PROXY": "n", "PYTHONPATH": "/evil", "SF_OCSP": "1"}
    assert sl.worker_env(base) == {"PATH": "/bin"}


def test_a_session_opens_local_testing_with_telemetry_off(session):
    r = run(session, "from snowflake.snowpark import Session\nSession.options")
    assert r.status == "ok"
    assert r.outputs[-1]["text"] == "{'local_testing': True, 'disable_local_testing_telemetry': True}"
    r = run(session, "from snowflake.snowpark.mock._telemetry import LocalTestOOBTelemetryService as T\n"
                     "T.get_instance().enabled")
    assert r.outputs[-1]["text"] == "False"
    assert session.client == "1.58.0"


def test_the_worker_always_asks_for_telemetry_off_and_refuses_to_start_if_it_stays_on(fake_runtimes, monkeypatch):
    src = Path(runtime_worker.__file__).read_text(encoding="utf-8")
    assert '"disable_local_testing_telemetry": True' in src
    monkeypatch.setenv("FAKE_TELEMETRY_STUCK", "1")
    with pytest.raises(RuntimeError, match="telemetry is still on"):
        sl.SnowparkLocalAdapter(fake_runtimes, cell_seconds=60).open_session(SessionConfig("nb"))


def test_the_process_has_no_route_to_snowflakes_telemetry_host_or_anywhere(session):
    r = run(session, "from snowflake.snowpark.mock._telemetry import LocalTestOOBTelemetryService as T\n"
                     "t = T.get_instance(); t._enabled = True\nt.flush()")
    assert r.status == "error" and r.outputs[-1]["ename"] == "PermissionError"
    assert "client-telemetry.snowflakecomputing.com" in r.outputs[-1]["evalue"]
    for code in ("import socket\nsocket.create_connection(('127.0.0.1', 9))",
                 "import socket\nsocket.getaddrinfo('localhost', 80)",
                 "import socket\nsocket.getnameinfo(('127.0.0.1', 80), 0)",
                 "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.sendto(b'x', ('10.0.0.1', 53))",
                 "import urllib.request\nurllib.request.urlopen('https://example.com', timeout=1)"):
        r = run(session, code)
        assert r.status == "error", code
        assert "no network" in r.outputs[-1]["evalue"], (code, r.outputs[-1])


def test_socketpair_over_loopback_as_on_windows_is_refused_so_asyncio_fails_closed(session):
    # On Windows socket.socketpair() is socket._fallback_socketpair: a TCP connection over AF_INET
    # loopback. Linux and macOS have a native AF_UNIX socketpair, so the Windows one is called directly.
    r = run(session, "import socket\nsocket._fallback_socketpair(socket.AF_INET)")
    assert r.status == "error" and r.outputs[-1]["ename"] == "PermissionError", r.outputs[-1]
    assert "no network" in r.outputs[-1]["evalue"]
    r = run(session, "import asyncio, socket\nsocket.socketpair = socket._fallback_socketpair\n"
                     "asyncio.SelectorEventLoop()")
    assert r.status == "error" and "no network" in r.outputs[-1]["evalue"], r.outputs[-1]


def test_a_session_builder_for_a_real_account_cant_reach_it(session):
    r = run(session, "from snowflake.snowpark import Session\nSession.builder.configs({'account': 'x'}).create()")
    assert r.status == "error" and "no network" in r.outputs[-1]["evalue"]


# ---- cells


def test_frames_preview_from_collect_without_pandas_and_say_simulated(session):
    r = run(session, "df = session.create_dataframe([[1, 'a'], [2, None], [3, 'c']], schema=['id', 'name'])\ndf")
    assert r.status == "ok" and r.engine == "snowpark-local"
    table = r.outputs[-1]
    assert table["type"] == "table" and table["columns"] == ["ID", "NAME"]
    assert table["rows"] == [[1, "a"], [2, None], [3, "c"]] and table["preview_only"] is True
    assert table["note"] == "Preview rows, simulated by Snowflake's local testing framework."


def test_session_sql_is_refused_with_why(session):
    r = run(session, "session.sql('select 1').collect()")
    assert r.status == "error"
    out = r.outputs[-1]
    assert out["ename"] == "Refused" and out["refused"] is True and out["evalue"] == sl.SQL_REFUSED
    assert runtime_worker.SNOWPARK_SQL_REFUSED == sl.SQL_REFUSED  # the worker imports nothing from KratosForge


def test_an_unimplemented_call_is_a_clear_refusal_not_a_failure_of_the_cell(session):
    r = run(session, "session.soundex()")
    out = r.outputs[-1]
    assert r.status == "error" and out["ename"] == "Refused" and out["refused"] is True
    assert out["evalue"].startswith("Refused: Snowflake's local testing framework doesn't implement this call")
    assert "[Local Testing] Function soundex is not implemented." in out["evalue"]
    assert "nothing was sent there" in out["evalue"]
    # Other errors stay the cell's own.
    r = run(session, "1/0")
    assert r.outputs[-1]["ename"] == "ZeroDivisionError" and "refused" not in r.outputs[-1]


def test_refusal_leaves_other_outputs_alone():
    assert sl.refusal({"type": "text", "text": "x"}) == {"type": "text", "text": "x"}
    err = {"type": "error", "ename": "ValueError", "evalue": "v"}
    assert sl.refusal(err) is err
    out = sl.refusal({"type": "error", "ename": "NotImplementedError", "evalue": ""})
    assert out["refused"] and "(no detail)" in out["evalue"]


def test_only_python_cells(session):
    for request in (CellRequest("SELECT 1", "sql"), CellRequest("spark.range(1)", "pyspark")):
        with pytest.raises(Unsupported, match="takes Python cells"):
            session.execute(request)


def test_variables_plan_and_catalog_say_why_not(session):
    for call in (session.variables, lambda: session.plan("x"), session.catalog):
        with pytest.raises(Unsupported):
            call()


def test_cancel_ends_the_process_and_close_ends_it_too(fake_runtimes):
    import threading
    s = sl.SnowparkLocalAdapter(fake_runtimes, cell_seconds=60).open_session(SessionConfig("nb"))
    assert s.cancel() is False  # nothing running
    errors = []

    def slow():
        try:
            run(s, "import time\ntime.sleep(30)")
        except SessionClosed as exc:
            errors.append(exc)

    t = threading.Thread(target=slow)
    t.start()
    for _ in range(200):
        if s._running:
            break
        threading.Event().wait(0.02)
    assert s.cancel() is True
    t.join(15)
    assert errors and s.closed and s.proc.poll() is not None
    assert s.cancel() is False


def test_the_worker_never_loads_the_engines_modules(session):
    r = run(session, "import sys\nsorted(m for m in sys.modules if m.startswith('fabric_desktop'))")
    assert r.outputs[-1]["text"] == "[]"


# ---- the real runtime, when one is installed here


@pytest.mark.skipif(not (os.environ.get("KF_SNOWPARK_SITE") and os.environ.get("KF_SNOWPARK_PYTHON")),
                    reason="no Snowpark runtime on this PC (set KF_SNOWPARK_SITE and KF_SNOWPARK_PYTHON)")
def test_the_real_runtime_simulates_a_dataframe_and_refuses_sql_and_unimplemented_calls():
    site, python = Path(os.environ["KF_SNOWPARK_SITE"]), os.environ["KF_SNOWPARK_PYTHON"]

    class Installed:
        def installed(self, line):
            import importlib.metadata
            return next(d.version for d in importlib.metadata.distributions(path=[str(site)])
                        if d.metadata["Name"] == "snowflake-snowpark-python")

        def folder(self, line):
            return site

    s = sl.SnowparkLocalAdapter(Installed(), python=python, cell_seconds=120).open_session(SessionConfig("nb"))  # type: ignore[arg-type]
    try:
        assert s.client.startswith("1.58.")
        r = run(s, "from snowflake.snowpark.functions import col\n"
                   "df = session.create_dataframe([[1, 'a'], [2, 'b']], schema=['x', 'y'])\ndf.filter(col('x') > 1)")
        assert r.status == "ok" and r.outputs[-1]["rows"] == [[2, "b"]], r.outputs
        r = run(s, "from snowflake.snowpark.mock._telemetry import LocalTestOOBTelemetryService as T\n"
                   "(T.get_instance().enabled, session._conn._disable_local_testing_telemetry)")
        assert r.outputs[-1]["text"] == "(False, True)"
        assert run(s, "session.sql('select 1')").outputs[-1]["evalue"] == sl.SQL_REFUSED
        r = run(s, "from snowflake.snowpark import functions as F\ndf.select(F.soundex('y')).collect()")
        assert r.outputs[-1]["ename"] == "Refused" and "soundex" in r.outputs[-1]["evalue"]
        r = run(s, "import socket\nsocket.getaddrinfo('client-telemetry.snowflakecomputing.com', 443)")
        assert "no network" in r.outputs[-1]["evalue"]
    finally:
        s.close()


def test_skips_cleanly_without_a_runtime_and_uses_this_pythons_interpreter_by_default(fake_runtimes):
    adapter = sl.SnowparkLocalAdapter(fake_runtimes)
    assert adapter._python == sys.executable and adapter.line == "1.58"
