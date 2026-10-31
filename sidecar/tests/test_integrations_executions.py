"""External execution (PR-AI-007): read-only sandboxed SQL, Python and notebooks in a process kernel
of their own, execution ids and states, timeouts, cancellation, output caps, no tokens but storage,
no table writes, approvals for every code run, and Prove's actions behind the right permissions."""

import json
import sys
import threading
import time
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, sql
from fabric_desktop.integrations import executions, policy
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.executions import ExecutionManager, shape_outputs
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

BOOTSTRAP = str(Path(__file__).with_name("kernel_bootstrap.py"))
LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "lh1", "workspace_name": ""}


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def write_policy(tmp_path, data):
    folder = tmp_path / "programdata" / "FabricDesktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "policy.json").write_text(json.dumps(data))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    tables = tmp_path / "tables"
    write_deltalake(str(tables / "sales"), pa.table({"Country": ["US", "UK", "DE"], "Amount": [10.0, 3.0, 1.0]}))
    monkeypatch.setattr(lakehouse, "open_table", lambda name: DeltaTable(str(tables / lakehouse.table_ref(name).table)))
    s = Service(client_id="x", root=tmp_path / "data")
    s._auth = type("Auth", (), {"storage_token": lambda self: "storage-tok", "fabric_token": lambda self: "fabric-tok",
                                "signed_in_user": lambda self: None, "signed_in_tenant": lambda self: None})()
    s._schema_cache = {"lh1": False}
    s._integration_executions = ExecutionManager(s, kernel_command=[sys.executable, BOOTSTRAP, str(tmp_path / "files")])
    s.notebooks.save("nb", [{"type": "code", "source": "x = 6"}, {"type": "markdown", "source": "# hi"},
                            {"type": "code", "source": "print(x * 7)"}], LH)
    s.notebooks.save("broken", [{"type": "code", "source": "1 / 0"}, {"type": "code", "source": "print('never')"}], LH)
    yield s
    lakehouse.attach(None)


@pytest.fixture
def store(tmp_path):
    st = IntegrationStore(tmp_path / "i.sqlite")
    st.set_setting("enabled", True)
    return st


def reg(store=None, wait=0.2):
    r = Registry(PermissionAuthorizer(store, wait_seconds=wait, poll=0.02)) if store else Registry()
    register_core(r)
    return r


def ctx(client_id="cl_1", timeout=120):
    return CallContext(Caller(client_id, "test"), timeout=timeout)


def call(svc, cap_id, client_id="cl_1", **args):
    """Call a handler directly (no authorizer), as invoke would after the checks."""
    from fabric_desktop.integrations.core import validate
    r = reg()
    cap = r.get(cap_id)
    args = validate(cap.input_schema, args)
    if cap.bind:
        args |= cap.bind(svc, dict(args))
    return cap.handler(svc, ctx(client_id), **args)


# ---- SQL

def test_sql_reads_lakehouse_tables(svc):
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
               query="SELECT Country, Amount FROM sales ORDER BY Amount DESC", max_rows=2)
    assert out["state"] == "succeeded" and out["id"].startswith("ex_")
    assert out["result"]["rows"] == [["US", 10.0], ["UK", 3.0]] and out["result"]["truncated"] is True
    assert out["result"]["row_count"] == 3


@pytest.mark.parametrize("query", ["DROP TABLE sales", "INSERT INTO sales VALUES ('FR', 1)", "SELECT 1; SELECT 2",
                                   "SELECT * FROM read_csv('/etc/passwd')", "COPY (SELECT 1) TO 'x.csv'",
                                   "SELECT * FROM glob('*')", "INSTALL httpfs", "ATTACH 'x.db'",
                                   "SET enable_external_access = true", "CREATE TABLE x AS SELECT 1"])
def test_sql_refuses_anything_but_reading_tables(svc, query):
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=query)
    assert out["state"] == "failed", out
    assert out["error"]["code"] in ("permission_denied", "invalid_input", "internal", "failed")


def test_app_sql_is_not_sandboxed(svc):
    # The SQL page's own queries are unchanged: several statements, no sandbox.
    assert sql.run("SELECT 1 AS a; SELECT 2 AS b").column("b").to_pylist() == [2]


def test_sql_needs_read_data_and_approval(svc, store):
    r = reg(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA"])
    args = {"workspace_id": "ws1", "lakehouse_id": "lh1", "query": "SELECT 1"}
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_execute_sql", args, ctx(client["id"]))
    assert e.value.code == "permission_denied"
    store.update_client(client["id"], grants=["READ_METADATA", "READ_DATA"])
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_execute_sql", args, ctx(client["id"]))
    assert e.value.code == "approval_required"
    store.decide(e.value.details["approval_id"], True)
    out = r.invoke(svc, "fabric_desktop_execute_sql", args, ctx(client["id"]))
    assert out["state"] == "succeeded"
    pending = store.approvals("used")[0]
    assert pending["summary"]["content"] == {"kind": "query", "text": "SELECT 1"}


# ---- Python and notebooks in a process kernel

def test_python_runs_in_its_own_process(svc):
    import os
    out = call(svc, "fabric_desktop_execute_python", code="import os\nprint(os.getpid())\n2 * 21")
    assert out["state"] == "succeeded", out
    cell = out["result"]["cells"][0]
    assert cell["status"] == "ok"
    assert int(cell["outputs"][0]["text"]) != os.getpid()
    assert cell["outputs"][-1] == {"type": "text", "text": "42"}


def test_python_gets_only_a_storage_token(svc):
    code = ("print(notebookutils.credentials.getToken('storage'))\n"
            "notebookutils.credentials.getToken('pbi')")
    out = call(svc, "fabric_desktop_execute_python", code=code, workspace_id="ws1", lakehouse_id="lh1")
    outputs = out["result"]["cells"][0]["outputs"]
    assert outputs[0]["text"].strip() == "storage-tok"
    assert outputs[-1]["type"] == "error" and "no pbi token" in outputs[-1]["evalue"]
    assert out["state"] == "failed"


def test_python_gets_only_an_allowlisted_environment(svc, monkeypatch):
    """HD-012 (EN-2): code an external tool runs doesn't see the engine's environment (secrets,
    tokens) beyond what Python needs to start; PATH still gets through."""
    import os
    monkeypatch.setenv("FABRIC_DESKTOP_TEST_SECRET", "hd012-not-for-tools")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") or os.defpath)
    code = ("import os, json\n"
            "print(json.dumps(sorted(os.environ)))\n"
            "print(os.environ.get('FABRIC_DESKTOP_TEST_SECRET', 'absent'))\n"
            "print(os.environ.get('PATH') == %r)" % os.environ["PATH"])
    out = call(svc, "fabric_desktop_execute_python", code=code)
    assert out["state"] == "succeeded", out
    lines = "".join(o.get("text", "") for o in out["result"]["cells"][0]["outputs"]).splitlines()
    names = json.loads(lines[0])
    assert lines[1] == "absent"
    assert lines[2] == "True"
    assert "FABRIC_DESKTOP_TEST_SECRET" not in names and "PATH" in names
    from fabric_desktop.kernel_proc import EXTERNAL_ENV_ALLOW, EXTERNAL_ENV_PREFIXES, EXTERNAL_ENV_SET
    allowed = {n.upper() for n in EXTERNAL_ENV_ALLOW} | set(EXTERNAL_ENV_SET)
    # Set inside the kernel by its own imports (notebook.py's matplotlib backend, polars' allocator,
    # onelake.py's upload limits), in the engine too, so they show up on both sides without being passed down.
    set_by_imports = {"MPLBACKEND", "_RJEM_MALLOC_CONF", "DELTARS_MAX_CONCURRENCY_TASKS", "DELTARS_MAX_IN_FLIGHT_UPLOAD_BYTES"}
    from_parent = [n for n in names if n in os.environ and n not in set_by_imports]
    leaked = [n for n in from_parent if n.upper() not in allowed and not n.upper().startswith(EXTERNAL_ENV_PREFIXES)]
    assert leaked == []


def test_the_external_environment_is_the_allowlist_and_nothing_else():
    from fabric_desktop.kernel_proc import external_environment
    parent = {"PATH": "/bin", "Path": "/x", "SystemRoot": r"C:\Windows", "TEMP": "/t", "LC_TIME": "C", "LANG": "C.UTF-8",
              "HOME": "/h", "USERPROFILE": "/u", "AGENT_SP_SECRET": "s", "AZURE_CLIENT_SECRET": "s",
              "FABRIC_DESKTOP_CLIENT_ID": "c", "PYTHONPATH": "/elsewhere", "PYTHONSTARTUP": "/evil.py",
              "https_proxy": "http://proxy:8080", "SSL_CERT_FILE": "/ca.pem", "LOCALAPPDATA": "/l", "APPDATA": "/a", "USERNAME": "me"}
    env = external_environment(parent)
    assert set(env) == {"PATH", "Path", "SystemRoot", "TEMP", "LC_TIME", "LANG", "HOME", "USERPROFILE",
                        "https_proxy", "SSL_CERT_FILE", "PYTHONPATH", "PYTHONUNBUFFERED"}
    assert env["PYTHONPATH"] != "/elsewhere" and Path(env["PYTHONPATH"], "fabric_desktop", "kernel_host.py").exists()



def test_the_tool_can_tell_which_processor_it_runs_on(monkeypatch):
    """Windows: when platform's WMI query fails, platform.machine() reads PROCESSOR_ARCHITECTURE.
    Without it the answer is "" and Polars won't import ("unknown feature flag: 'sse3'")."""
    import platform
    from fabric_desktop.kernel_proc import external_environment
    parent = {"PATH": "/bin", "PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_IDENTIFIER": "Intel64 Family 6",
              "NUMBER_OF_PROCESSORS": "4"}
    env = external_environment(parent)
    assert {k: env[k] for k in parent} == parent

    def wmi_fails(*args, **kwargs):
        raise OSError("WMI timed out")
    monkeypatch.setattr(platform, "_wmi_query", wmi_fails, raising=False)
    monkeypatch.setattr("os.environ", env)
    assert platform._get_machine_win32() == "AMD64"

@pytest.mark.parametrize("proxy,sent", [
    ("http://user:s3cret@proxy.contoso.com:8080", "http://proxy.contoso.com:8080"),
    ("http://CONTOSO%5Cme:p%40ss@proxy:3128/", "http://proxy:3128/"),
    ("user:p@ss@proxy:3128", "proxy:3128"),  # no scheme, and an "@" left unencoded in the password
    ("socks5h://me@proxy:1080", "socks5h://proxy:1080"),
    ("http://proxy:8080", "http://proxy:8080"),
    ("http://proxy:8080/path@x", "http://proxy:8080/path@x"),  # an "@" after the host isn't a password
    ("http://u:pa/ss@proxy:8080", "http://proxy:8080"),  # an unencoded "/" in the password
    ("  http://u:p@h:1 ", "http://h:1"),  # spaces around the value
    ("http://u:p@[::1]:3128", "http://[::1]:3128"),
    ("socks5h://u:p@@h:1080", "socks5h://h:1080"),
])
def test_a_proxy_password_doesnt_reach_the_tool(proxy, sent):
    from fabric_desktop.kernel_proc import external_environment
    env = external_environment({"HTTPS_PROXY": proxy, "http_proxy": proxy, "ALL_PROXY": proxy, "NO_PROXY": "a@b"})
    assert env["HTTPS_PROXY"] == env["http_proxy"] == env["ALL_PROXY"] == sent
    assert env["NO_PROXY"] == "a@b"  # a host list, not an address


def test_python_cant_write_lakehouse_tables(svc):
    code = "import pyarrow as pa\nfrom fabric_desktop import lakehouse\nlakehouse.write_table(pa.table({'a': [1]}), 'dbo.t')"
    out = call(svc, "fabric_desktop_execute_python", code=code, workspace_id="ws1", lakehouse_id="lh1")
    err = out["result"]["cells"][0]["outputs"][-1]
    assert err["type"] == "error" and "can't write lakehouse tables" in err["evalue"]


def test_python_output_has_secrets_removed_and_is_capped(svc):
    out = call(svc, "fabric_desktop_execute_python",
               code="print('Authorization: Bearer abcdefghijklmnopqrstuv')\nprint('x' * 300000)")
    text = json.dumps(out)
    assert "abcdefghijklmnopqrstuv" not in text
    assert len(text) < executions.MAX_OUTPUT_CHARS + 5000


def test_shape_outputs_drops_images_and_html():
    budget = [executions.MAX_OUTPUT_CHARS]
    out = shape_outputs([{"type": "image", "mime": "image/png", "data": "AAAA"}, {"type": "html", "html": "<b>x</b>"},
                         {"type": "table", "columns": ["a"], "rows": [[i] for i in range(500)], "total_rows": 500}], budget)
    assert [o["type"] for o in out] == ["omitted", "omitted", "table"]
    assert len(out[2]["rows"]) == executions.MAX_TABLE_ROWS and "AAAA" not in json.dumps(out)


def test_notebook_runs_cell_by_cell(svc):
    out = call(svc, "fabric_desktop_run_notebook", name="nb", workspace_id="ws1")
    assert out["state"] == "succeeded", out
    assert out["result"]["cells_run"] == 2
    assert out["result"]["cells"][1]["outputs"][0]["text"].strip() == "42"


def test_notebook_run_skips_the_run_on_fabric_cell_and_says_so(svc):
    svc.notebooks.save("step", [{"type": "code", "source": "print('here')"},
                                {"type": "code", "source": "1 / 0", "meta": {"fabric_step": True}}], LH)
    out = call(svc, "fabric_desktop_run_notebook", name="step", workspace_id="ws1")
    assert out["state"] == "succeeded" and out["result"]["cells_run"] == 1
    assert out["result"]["skipped"].startswith("Cell 2 is marked Run on Fabric")
    # A notebook whose only code is the step has nothing to run here, and says why.
    svc.notebooks.save("only", [{"type": "code", "source": "1 / 0", "meta": {"fabric_step": True}}], LH)
    with pytest.raises(ValueError, match="only has no code to run. Cell 1 is marked Run on Fabric"):
        call(svc, "fabric_desktop_run_notebook", name="only", workspace_id="ws1")


def test_notebook_stops_at_first_error(svc):
    out = call(svc, "fabric_desktop_run_notebook", name="broken", workspace_id="ws1")
    assert out["state"] == "failed" and out["result"]["cells_run"] == 1
    assert out["result"]["cells"][0]["outputs"][-1]["ename"] == "ZeroDivisionError"


def test_notebook_workspace_must_match(svc):
    with pytest.raises(ValueError, match="pass that as workspace_id"):
        call(svc, "fabric_desktop_run_notebook", name="nb", workspace_id="other")


def test_notebook_handler_needs_the_bound_cells(svc):
    # Only invoke's bind supplies the code; the handler won't load and run a notebook on its own.
    cap = reg().get("fabric_desktop_run_notebook")
    with pytest.raises(TypeError):
        cap.handler(svc, ctx(), name="nb", workspace_id="ws1")


def test_notebook_changed_after_binding_runs_nothing(svc):
    cap = reg().get("fabric_desktop_run_notebook")
    bound = cap.bind(svc, {"name": "nb", "workspace_id": "ws1"})
    svc.notebooks.save("nb", [{"type": "code", "source": "x = 6"}, {"type": "code", "source": "print('changed')"}], LH)
    with pytest.raises(IntegrationError) as e:
        cap.handler(svc, ctx(), name="nb", workspace_id="ws1", **bound)
    assert e.value.code == "approval_required" and e.value.message == "The notebook changed after it was approved. Ask again."
    # Bound cells that don't match their own hash are refused the same way.
    fresh = cap.bind(svc, {"name": "nb", "workspace_id": "ws1"})
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        cap.handler(svc, ctx(), name="nb", workspace_id="ws1", **(fresh | {"cells": ["print('other')"]}))
    assert call(svc, "fabric_desktop_list_executions")["items"] == []


def test_proof_lakehouse_changed_during_approval_starts_nothing(svc, monkeypatch):
    # The workspace limit is checked before the approval wait; a lakehouse swapped during it is refused.
    started = []
    monkeypatch.setattr(svc, "prove_start", lambda *a: started.append(a) or {"id": "p1"}, raising=False)
    cap = reg().get("fabric_desktop_start_proof")
    bound = cap.bind(svc, {"name": "nb"})
    svc.notebooks.save("nb", [{"type": "code", "source": "x = 6"}], LH | {"workspace_id": "ws2", "lakehouse_id": "lh2"})
    limited = ctx()
    limited.workspaces = frozenset({"ws1"})
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        cap.handler(svc, limited, name="nb", **bound)
    # Even bound to the swapped notebook, a tool limited to ws1 can't start it.
    with pytest.raises(IntegrationError) as e:
        cap.handler(svc, limited, name="nb", **cap.bind(svc, {"name": "nb"}))
    assert e.value.code == "permission_denied" and started == []
    with pytest.raises(TypeError):
        cap.handler(svc, limited, name="nb")  # only invoke's bind supplies the hash


def test_proof_approval_shows_the_bar_values_and_refuses_changed_ones(svc, monkeypatch):
    # Both of a proof's runs use the Parameters bar's values, so the approval shows them and is bound to them.
    started = []
    monkeypatch.setattr(svc, "prove_start", lambda *a: started.append(a) or {"id": "p1"}, raising=False)
    cap = reg().get("fabric_desktop_start_proof")
    svc.notebooks.set_extra("nb", "inputs", {"parameters": {"x": "5"}})
    bound = cap.bind(svc, {"name": "nb"})
    assert bound["inputs"] == {"parameters": {"x": "5"}}
    svc.notebooks.set_extra("nb", "inputs", {"parameters": {"x": "500"}})
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        cap.handler(svc, ctx(), name="nb", **bound)
    # Bound values that don't match their own hash are refused the same way.
    fresh = cap.bind(svc, {"name": "nb"})
    with pytest.raises(IntegrationError, match="changed after it was approved"):
        cap.handler(svc, ctx(), name="nb", **(fresh | {"inputs": {"parameters": {"x": "5"}}}))
    assert started == []
    assert cap.handler(svc, ctx(), name="nb", **fresh)["id"] == "p1"


def test_external_notebook_cant_run_other_notebooks(svc):
    # %run and notebookutils.notebook.run would run code the person wasn't shown.
    svc.notebooks.save("outer", [{"type": "code", "source": "%run nb"}], LH)
    out = call(svc, "fabric_desktop_run_notebook", name="outer", workspace_id="ws1")
    err = out["result"]["cells"][0]["outputs"][-1]
    assert out["state"] == "failed" and err["type"] == "error" and "can't run other notebooks" in err["evalue"]
    out = call(svc, "fabric_desktop_execute_python", code="notebookutils.notebook.run('nb')")
    err = out["result"]["cells"][0]["outputs"][-1]
    assert out["state"] == "failed" and "can't run other notebooks" in err["evalue"]


def test_app_notebooks_still_run_other_notebooks(svc):
    # The app's own kernel is unchanged: %run loads the saved notebook.
    kernel = svc._new_kernel(process=False)
    out = kernel.run("%run nb")
    assert out["status"] == "ok", out
    assert kernel.namespace["x"] == 6


# ---- ids, status, waiting, timeouts, cancel

def test_long_run_returns_id_then_status_follows(svc):
    out = call(svc, "fabric_desktop_execute_python", code="import time\ntime.sleep(2)\nprint('done')", wait_seconds=0)
    assert out["state"] in ("queued", "running") and "result" not in out
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=30)
    assert status["state"] == "succeeded" and status["result"]["cells"][0]["outputs"][0]["text"].strip() == "done"
    logs = call(svc, "fabric_desktop_get_execution_logs", execution_id=out["id"])
    assert any("succeeded" in line for line in logs["items"])
    listed = call(svc, "fabric_desktop_list_executions")["items"]
    assert listed[0]["id"] == out["id"] and "result" not in listed[0]


def test_timeout_interrupts_then_ends(svc):
    out = call(svc, "fabric_desktop_execute_python", code="import time\nwhile True: time.sleep(0.1)",
               timeout_seconds=1, wait_seconds=0)
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=30)
    assert status["state"] == "timed_out"


# Runs for days but streams, so it holds no memory. Reads a lakehouse table, so a view is registered too.
ENDLESS_SQL = "SELECT count(*) AS n FROM range(1000000000000000) t(i) WHERE hash(i) = (SELECT count(*) FROM sales)"


def test_sql_timeout_interrupts_the_query_and_frees_the_engine(svc):
    # EN-4: the timeout used to only mark the run timed_out; DuckDB kept going and held the lock.
    started = time.monotonic()
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
               timeout_seconds=1, wait_seconds=0)
    # Generous bounds for slow CI runners: the point is that it ends, not how fast.
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=60)
    assert status["state"] == "timed_out", status
    assert status["finished_at"] is not None, "the query is still running after its timeout"
    assert status["error"]["code"] == "timeout" and "1 s" in status["error"]["message"], status["error"]
    assert time.monotonic() - started < 60
    # The next run starts straight away: the lock was released.
    started = time.monotonic()
    nxt = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
               query="SELECT count(*) AS n FROM sales", wait_seconds=60)
    assert nxt["state"] == "succeeded" and nxt["result"]["rows"] == [[3]], nxt
    assert time.monotonic() - started < 30


def test_sql_timeout_before_the_query_starts_still_stops_it(svc, monkeypatch):
    # The interrupt can land while the tables are still being registered, before DuckDB starts the
    # statement; one interrupt then is lost, so the run keeps interrupting until the query stops.
    real = sql.bind_tables

    def slow_bind(*args, **kwargs):
        time.sleep(1.5)  # past the 1 second timeout
        return real(*args, **kwargs)
    monkeypatch.setattr(sql, "bind_tables", slow_bind)
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
               timeout_seconds=1, wait_seconds=0)
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=60)
    assert status["state"] == "timed_out" and status["finished_at"] is not None, status
    assert status["error"]["code"] == "timeout"


def test_sql_interrupt_just_before_the_statement_starts_is_repeated(svc, monkeypatch):
    # DuckDB drops an interrupt sent before it starts a statement: the timeout lands in that gap here.
    real = sql.execute_sandboxed

    def late_start(con, statement):
        time.sleep(1.5)  # the 1 second timeout fires (and interrupts) before the statement starts
        return real(con, statement)
    monkeypatch.setattr(sql, "execute_sandboxed", late_start)
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
               timeout_seconds=1, wait_seconds=0)
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=60)
    assert status["state"] == "timed_out" and status["finished_at"] is not None, status
    assert status["error"]["code"] == "timeout"


def test_sandboxed_sql_has_memory_and_thread_limits_it_cant_change(svc):
    con, statement = sql.prepare_sandboxed("SELECT 1 AS a", memory_mb=512, threads=1)
    try:
        assert con.execute("SELECT current_setting('threads')").fetchall()[0][0] == 1
        memory = con.execute("SELECT current_setting('memory_limit')").fetchall()[0][0]
        assert memory.endswith("MiB") and float(memory.split()[0]) < 512, memory  # 512 MB is 488.2 MiB
        with pytest.raises(Exception, match="(?i)lock"):
            con.execute("SET memory_limit = '64GB'")
        assert sql.execute_sandboxed(con, statement).column("a").to_pylist() == [1]
    finally:
        con.close()


def test_external_sql_runs_one_query_at_a_time(svc):
    # Each query gets the SQL page's memory share, so a second waits for the first to finish.
    first = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
                 timeout_seconds=600, wait_seconds=0)
    try:
        second = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
                      query="SELECT count(*) AS n FROM sales", timeout_seconds=600, wait_seconds=2)
        assert second["state"] == "running" and second["finished_at"] is None, second
    finally:
        assert call(svc, "fabric_desktop_cancel_execution", execution_id=first["id"])["cancelled"] is True
    done = call(svc, "fabric_desktop_get_execution_status", execution_id=second["id"], wait_seconds=60)
    assert done["state"] == "succeeded" and done["result"]["rows"] == [[3]], done


def test_sql_timeout_while_waiting_for_the_slot_finishes_cleanly(svc):
    first = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
                 timeout_seconds=600, wait_seconds=0)
    try:
        started = time.monotonic()
        waiting = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
                       query="SELECT count(*) AS n FROM sales", timeout_seconds=1, wait_seconds=0)
        status = call(svc, "fabric_desktop_get_execution_status", execution_id=waiting["id"], wait_seconds=60)
        assert status["state"] == "timed_out" and status["finished_at"] is not None, status
        assert status["error"]["code"] == "timeout" and "1 s" in status["error"]["message"], status["error"]
        assert time.monotonic() - started < 30
        # The waiter gave up without touching the running query.
        assert call(svc, "fabric_desktop_get_execution_status", execution_id=first["id"])["state"] == "running"
    finally:
        assert call(svc, "fabric_desktop_cancel_execution", execution_id=first["id"])["cancelled"] is True
    # The slot is free again: the next query runs straight away.
    nxt = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
               query="SELECT count(*) AS n FROM sales", wait_seconds=60)
    assert nxt["state"] == "succeeded" and nxt["result"]["rows"] == [[3]], nxt


def test_sql_cancel_interrupts_the_query(svc):
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1", query=ENDLESS_SQL,
               timeout_seconds=600, wait_seconds=0)
    time.sleep(0.5)
    cancelled = call(svc, "fabric_desktop_cancel_execution", execution_id=out["id"])
    assert cancelled["cancelled"] is True and cancelled["state"] == "cancelled", cancelled
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=60)
    assert status["state"] == "cancelled" and status["finished_at"] is not None, status
    assert status["error"]["code"] == "cancelled"


def test_cancel(svc):
    out = call(svc, "fabric_desktop_execute_python", code="import time\ntime.sleep(60)", wait_seconds=0)
    time.sleep(1)
    cancelled = call(svc, "fabric_desktop_cancel_execution", execution_id=out["id"])
    assert cancelled["cancelled"] is True and cancelled["state"] == "cancelled"
    assert call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"])["state"] == "cancelled"


def test_client_cancel_stops_the_execution(svc):
    c = ctx()
    r = reg()
    cap = r.get("fabric_desktop_execute_python")
    threading.Timer(1.0, c.cancel).start()
    out = cap.handler(svc, c, code="import time\ntime.sleep(60)", workspace_id="", lakehouse_id="", wait_seconds=30,
                      timeout_seconds=300)
    status = call(svc, "fabric_desktop_get_execution_status", execution_id=out["id"], wait_seconds=20)
    assert status["state"] == "cancelled"


def test_executions_are_private_to_their_tool(svc):
    out = call(svc, "fabric_desktop_execute_python", code="1")
    with pytest.raises(LookupError):
        call(svc, "fabric_desktop_get_execution_status", client_id="cl_other", execution_id=out["id"])
    assert call(svc, "fabric_desktop_list_executions", client_id="cl_other")["items"] == []


def test_records_kept_on_disk(svc):
    out = call(svc, "fabric_desktop_execute_python", code="print('kept')")
    path = svc.root / "integrations" / "executions" / f"{out['id']}.json"
    record = json.loads(path.read_text())
    assert record["state"] == "succeeded" and record["client_id"] == "cl_1" and record["logs"]


# ---- permissions

def test_code_always_asks_even_with_the_grant(svc, store):
    r = reg(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_execute_python", {"code": "print(1)"}, ctx(client["id"]))
    assert e.value.code == "approval_required"
    approval = store.approval(e.value.details["approval_id"])
    assert approval["summary"]["content"] == {"kind": "code", "text": "print(1)"}


def test_notebook_approval_shows_every_code_cell_and_is_bound_to_them(svc, store):
    r = reg(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    args = {"name": "nb", "workspace_id": "ws1"}
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_run_notebook", args, ctx(client["id"]))
    assert e.value.code == "approval_required"
    first = store.approval(e.value.details["approval_id"])
    content = first["summary"]["content"]
    assert content["kind"] == "cells" and "x = 6" in content["text"] and "print(x * 7)" in content["text"]
    assert "# hi" not in content["text"] and "cell 2 of 2" in content["text"]
    assert first["summary"]["target"]["lakehouse_id"] == "lh1"
    store.decide(first["id"], True)

    # The file changes after the approval: the same call asks again, showing the new code, and
    # the approval given for the old code isn't used.
    svc.notebooks.save("nb", [{"type": "code", "source": "print('something else')"}], LH)
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_run_notebook", args, ctx(client["id"]))
    assert e.value.code == "approval_required" and e.value.details["approval_id"] != first["id"]
    second = store.approval(e.value.details["approval_id"])
    assert "something else" in second["summary"]["content"]["text"]
    assert store.approval(first["id"])["state"] == "approved"
    assert svc._integration_executions.list() == []

    store.decide(second["id"], True)
    out = r.invoke(svc, "fabric_desktop_run_notebook", args, ctx(client["id"]))
    assert out["state"] == "succeeded" and out["result"]["cells"][0]["outputs"][0]["text"].strip() == "something else"


def test_notebook_too_long_to_show_is_refused_before_asking(svc, store):
    r = reg(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    svc.notebooks.save("long", [{"type": "code", "source": "x = 1\n" + "# filler\n" * 2_500},
                                {"type": "code", "source": "print('hidden at the end')"}], LH)
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_run_notebook", {"name": "long", "workspace_id": "ws1"}, ctx(client["id"]))
    assert e.value.code == "too_large"
    assert e.value.message.startswith("This notebook is too long to show for approval (")
    assert e.value.message.endswith("characters, limit 20,000). Run it in KratosForge instead.")
    assert store.approvals("") == [] and svc._integration_executions.list() == []


def test_notebook_changed_while_waiting_for_approval_is_refused(svc, store):
    r = reg(store, wait=10)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    got: dict = {}

    def run():
        try:
            got["out"] = r.invoke(svc, "fabric_desktop_run_notebook", {"name": "nb", "workspace_id": "ws1"}, ctx(client["id"]))
        except IntegrationError as exc:
            got["error"] = exc
    t = threading.Thread(target=run)
    t.start()
    deadline = time.monotonic() + 5
    while not store.approvals("pending") and time.monotonic() < deadline:
        time.sleep(0.02)
    pending = store.approvals("pending")[0]
    svc.notebooks.save("nb", [{"type": "code", "source": "print('swapped')"}], LH)
    store.decide(pending["id"], True)
    t.join(10)
    assert "out" not in got and got["error"].message == "The notebook changed after it was approved. Ask again."
    assert svc._integration_executions.list() == []


def test_workspace_limited_tools_cant_run_code(svc, store):
    r = reg(store)
    client, _ = store.add_client("Cursor", "cursor", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"], workspaces=["ws1"])
    with pytest.raises(IntegrationError) as e:
        r.invoke(svc, "fabric_desktop_execute_python", {"code": "print(1)"}, ctx(client["id"]))
    assert e.value.code == "permission_denied" and "limited to some workspaces" in e.value.message


def test_prove_actions_behind_the_right_permissions(svc, store):
    r = reg(store)
    client, _ = store.add_client("Claude Code", "claude-code",
                                 grants=["READ_METADATA", "READ_DATA", "WRITE_LOCAL_FILES", "EXECUTE_LOCAL_CODE"])
    status = {c["id"]: c for c in r.describe(ctx(client["id"]))}
    assert status["fabric_desktop_start_proof"]["status"] == "unavailable"  # publishes to Fabric: not offered
    assert status["fabric_desktop_delete_proof_outputs"]["status"] == "unavailable"  # deletes in OneLake: not offered
    assert status["fabric_desktop_export_receipt"]["status"] != "unavailable"
    assert r.get("fabric_desktop_cancel_proof").approval == "always"


def test_receipt_export_honours_policy(tmp_path, svc, monkeypatch):
    seen = {}
    monkeypatch.setattr(svc, "receipt_export", lambda id, format, hide: seen.setdefault("hide", hide) and {"format": format, "text": ""}
                        or {"format": format, "text": ""})
    write_policy(tmp_path, {"receipt_redactions": ["identity", "machine"]})
    out = call(svc, "fabric_desktop_export_receipt", id="a" * 64, hide=["values"])
    assert out["hidden"] == ["identity", "machine", "values"] and seen["hide"] == out["hidden"]
    write_policy(tmp_path, {"receipt_redactions": "everything"})
    assert set(policy.receipt_redactions()) == {"identity", "locations", "values", "machine"}  # unreadable hides all
