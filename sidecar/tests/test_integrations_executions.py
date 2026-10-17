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
    return cap.handler(svc, ctx(client_id), **validate(cap.input_schema, args))


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


def test_notebook_stops_at_first_error(svc):
    out = call(svc, "fabric_desktop_run_notebook", name="broken", workspace_id="ws1")
    assert out["state"] == "failed" and out["result"]["cells_run"] == 1
    assert out["result"]["cells"][0]["outputs"][-1]["ename"] == "ZeroDivisionError"


def test_notebook_workspace_must_match(svc):
    with pytest.raises(ValueError, match="pass that as workspace_id"):
        call(svc, "fabric_desktop_run_notebook", name="nb", workspace_id="other")


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
