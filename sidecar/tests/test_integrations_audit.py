"""The audit trail for external AI tools (integrations/audit.py): one line for every call, with the
decision and outcome, arguments without code or secrets, a hash chain that holds across processes,
refused HTTP requests, and Recent activity in Settings."""

import json
import subprocess
import sys
import threading
import time

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from fabric_desktop.integrations.audit import GIVEN, IntegrationAudit, arguments
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.http_server import build_app
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
PORT = 47699


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations" / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


def boom(svc, ctx, **a):
    raise RuntimeError(f"upstream said Authorization: Bearer {JWT}")


def registry(store, wait=0.2):
    reg = Registry(PermissionAuthorizer(store, wait_seconds=wait, poll=0.02))
    register_core(reg)
    reg.register(Capability("ext_sql", "Run SQL", "Runs a query.",
                            schema({"workspace_id": {"type": "string"}, "query": {"type": "string"}}),
                            schema(extra=True), "READ_METADATA", lambda s, c, **a: {"execution_id": "ex_" + "a" * 20},
                            needs_service=False))
    reg.register(Capability("ext_run", "Run Python", "Runs code.", schema({"code": {"type": "string"}}), schema(extra=True),
                            "EXECUTE_LOCAL_CODE", lambda s, c, **a: {"ok": True}, read_only=False, approval="always",
                            needs_service=False))
    reg.register(Capability("ext_boom", "Fails", "Fails.", schema(), schema(extra=True), "READ_METADATA", boom,
                            needs_service=False))
    reg.audit = IntegrationAudit(store).record
    return reg


def ctx(client_id, channel="mcp-stdio"):
    return CallContext(Caller(client_id, channel), timeout=10)


def call(reg, cap_id, client_id, args=None):
    try:
        reg.invoke(None, cap_id, args or {}, ctx(client_id))
    except IntegrationError as err:
        return err.code
    return "ok"


def lines(store):
    return IntegrationAudit(store).recent(100)[::-1]  # oldest first


def test_every_call_is_recorded_with_decision_outcome_and_no_code_or_secrets(store):
    reg = registry(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA"])
    assert call(reg, "ext_sql", client["id"], {"workspace_id": "ws1", "query": "SELECT ssn FROM customers"}) == "ok"
    assert call(reg, "ext_run", client["id"], {"code": "import os"}) == "permission_denied"
    assert call(reg, "nope_nothing", client["id"]) == "not_found"
    assert call(reg, "ext_sql", client["id"], {"workspace_id": 5}) == "invalid_input"
    assert call(reg, "ext_boom", client["id"]) == "failed"
    assert call(reg, "ext_sql", "cl_unknown", {"workspace_id": "ws1"}) == "permission_denied"
    sql, run, missing, bad, failed, stranger = lines(store)
    assert (sql["decision"], sql["outcome"], sql["client"], sql["channel"]) == ("allowed", "ok", "Claude Code", "mcp-stdio")
    assert sql["arguments"] == {"workspace_id": "ws1", "query": GIVEN} and sql["execution_id"] == "ex_" + "a" * 20
    assert sql["permission"] == "READ_METADATA" and sql["seconds"] >= 0 and sql["at"].endswith("+00:00")
    assert (run["decision"], run["outcome"], run["arguments"]) == ("denied", "permission_denied", {"code": GIVEN})
    assert (missing["decision"], missing["capability"], missing["permission"]) == ("invalid", "nope_nothing", "")
    assert bad["decision"] == "invalid"
    assert failed["decision"] == "allowed"  # it was allowed, then failed
    assert failed["outcome"] == "failed" and JWT not in failed["error"] and "[removed]" in failed["error"]
    assert (stranger["client_id"], stranger["client"], stranger["decision"]) == ("cl_unknown", "", "denied")
    everything = json.dumps(lines(store))
    assert "customers" not in everything and "import os" not in everything and JWT not in everything


def test_approvals_are_recorded_as_the_person_decided(store):
    reg = registry(store, wait=5)
    client, _ = store.add_client("Codex", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])

    def answer(approve):
        for _ in range(250):
            pending = store.approvals("pending")
            if pending:
                store.decide(pending[0]["id"], approve)
                return
            time.sleep(0.02)
    threading.Thread(target=answer, args=(True,)).start()
    assert call(reg, "ext_run", client["id"], {"code": "print(1)"}) == "ok"
    threading.Thread(target=answer, args=(False,)).start()
    assert call(reg, "ext_run", client["id"], {"code": "print(2)"}) == "approval_denied"
    reg.authorizer.wait_seconds = 0.1
    assert call(reg, "ext_run", client["id"], {"code": "print(3)"}) == "approval_required"
    started, done, denied, pending = lines(store)
    assert [(r["decision"], r["outcome"]) for r in lines(store)] == [("approved", "started"),
        ("approved", "ok"), ("approval_denied", "approval_denied"), ("approval_pending", "approval_required")]
    assert done["seconds"] > 0 and started["seconds"] == 0  # the wait for the person counts, on the outcome line
    [used] = store.approvals("used")
    assert started["approval_id"] == done["approval_id"] == used["id"] and "approval_id" not in denied
    assert started["arguments"] == {"code": GIVEN} and started["client"] == "Codex"


def approve_first(store):
    def answer():
        for _ in range(500):
            pending = store.approvals("pending")
            if pending:
                store.decide(pending[0]["id"], True)
                return
            time.sleep(0.02)
    threading.Thread(target=answer, daemon=True).start()


def test_an_approved_call_is_recorded_before_it_runs(store):
    reg = registry(store, wait=10)
    seen = []
    reg.register(Capability("ext_peek", "Peek", "Looks at the trail.", schema({"code": {"type": "string"}}),
                            schema(extra=True), "EXECUTE_LOCAL_CODE",
                            lambda s, c, **a: seen.extend(lines(store)) or {"ok": True}, read_only=False,
                            approval="always", needs_service=False))
    client, _ = store.add_client("Codex", grants=["EXECUTE_LOCAL_CODE"])
    approve_first(store)
    assert call(reg, "ext_peek", client["id"], {"code": "x"}) == "ok"
    [line] = seen  # while the handler ran, the started line was already there
    assert (line["capability"], line["decision"], line["outcome"]) == ("ext_peek", "approved", "started")
    assert line["approval_id"] == store.approvals("used")[0]["id"]


KILLED = """
import pathlib, sys, threading, time
sys.path.insert(0, sys.argv[3])
from test_integrations_audit import approve_first, registry
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.registry import Capability, schema
from fabric_desktop.integrations.store import IntegrationStore
store = IntegrationStore(sys.argv[1])
reg = registry(store, wait=10)
def hang(s, c, **a):
    pathlib.Path(sys.argv[2]).write_text("running")
    time.sleep(120)
reg.register(Capability("ext_hang", "Hang", "Never ends.", schema({"code": {"type": "string"}}), schema(extra=True),
                        "EXECUTE_LOCAL_CODE", hang, read_only=False, approval="always", needs_service=False))
client = store.client(sys.argv[4])
approve_first(store)
reg.invoke(None, "ext_hang", {"code": "x"}, CallContext(Caller(client["id"], "mcp-stdio"), timeout=300))
"""


def test_a_call_killed_after_approval_still_shows_in_recent_activity(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path)
    svc.integrations_set_enabled(True)
    store = svc.integration_store
    client, _ = store.add_client("Codex", grants=["EXECUTE_LOCAL_CODE"])
    marker = tmp_path / "running"
    proc = subprocess.Popen([sys.executable, "-c", KILLED, str(store.path), str(marker), str(__import__("pathlib").Path(__file__).parent),
                             client["id"]])
    try:
        for _ in range(1500):
            if marker.exists() or proc.poll() is not None:
                break
            time.sleep(0.02)
        assert marker.exists(), "the handler never started"
    finally:
        proc.kill()  # SIGKILL / TerminateProcess: no finally, no handler
        proc.wait(30)
    [row] = svc.integrations_activity(10)
    assert (row["capability"], row["decision"], row["outcome"]) == ("ext_hang", "approved", "started")
    assert row["approval_id"] == store.approvals("used")[0]["id"]
    assert IntegrationAudit(store).verify()["ok"]


def test_recent_activity_shows_an_approved_call_once_and_one_with_no_line_as_unfinished(tmp_path):
    from fabric_desktop.integrations.permissions import summary
    from fabric_desktop.integrations.store import args_hash
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path)
    svc.integrations_set_enabled(True)
    store = svc.integration_store
    reg = registry(store, wait=10)
    client, _ = store.add_client("Codex", grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    approve_first(store)
    assert call(reg, "ext_run", client["id"], {"code": "print(1)"}) == "ok"
    [row] = svc.integrations_activity(10)  # the started line drops out once the outcome is there
    assert (row["outcome"], row["approval_id"]) == ("ok", store.approvals("used")[0]["id"])
    # An approval used with no line at all: the process was killed between using it and writing.
    cap = reg.get("ext_run")
    args = {"code": "import os"}
    asked = store.request_approval(store.client(client["id"]), cap.id, cap.title, cap.permission,
                                   summary(cap, store.client(client["id"]), args), args)
    store.decide(asked["id"], True)
    assert store.consume(asked["id"], client["id"], args_hash(cap.id, args))
    lost, done = svc.integrations_activity(10)
    assert (lost["outcome"], lost["decision"], lost["capability"], lost["approval_id"]) == ("unfinished", "approved", "ext_run", asked["id"])
    assert lost["arguments"] == {"code": GIVEN} and lost["client"] == "Codex" and "no record of how it ended" in lost["error"]
    assert done["outcome"] == "ok"
    assert svc.integrations_activity(10, client_id="cl_other") == []
    # A shorter page leaves out approvals used before its oldest line.
    for i in range(3):
        call(reg, "ext_sql", client["id"], {"workspace_id": f"ws{i}"})
    assert [r["outcome"] for r in svc.integrations_activity(3)] == ["ok", "ok", "ok"]
    assert [r["outcome"] for r in svc.integrations_activity(5)] == ["ok", "ok", "ok", "unfinished", "ok"]


def test_an_audit_problem_never_fails_the_call(store, capsys):
    reg = registry(store)
    client, _ = store.add_client("Tool", grants=["READ_METADATA"])

    def broken(*a, **k):
        raise OSError("disk full")
    reg.audit = broken
    assert call(reg, "ext_sql", client["id"], {"workspace_id": "ws1"}) == "ok"
    assert "could not record ext_sql" in capsys.readouterr().err


def test_an_approved_call_that_cant_be_recorded_does_not_run(store, capsys):
    reg = registry(store, wait=10)
    ran = []
    reg.register(Capability("ext_mark", "Mark", "Runs code.", schema({"code": {"type": "string"}}), schema(extra=True),
                            "EXECUTE_LOCAL_CODE", lambda s, c, **a: ran.append(1) or {"ok": True}, read_only=False,
                            approval="always", needs_service=False))
    client, _ = store.add_client("Codex", grants=["EXECUTE_LOCAL_CODE"])

    def broken(*a, **k):
        raise OSError("disk full")
    reg.audit = broken
    approve_first(store)
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_mark", {"code": "x"}, ctx(client["id"]))
    assert err.value.code == "internal" and "didn't run" in err.value.message and ran == []
    assert "could not record ext_mark" in capsys.readouterr().err


def test_arguments_keep_ids_and_drop_content():
    out = arguments({"name": "nb_sales", "max_rows": 20, "rules": {"tolerance": 0.1, "keys": ["id"]},
                     "note": f"token={JWT}", "long": "x" * 500, "client_secret": "s", "cells": ["a"], "blob": object()})
    assert out["name"] == "nb_sales" and out["max_rows"] == 20 and out["rules"] == {"tolerance": 0.1, "keys": ["id"]}
    assert JWT not in out["note"] and out["long"] == GIVEN and out["client_secret"] == GIVEN
    assert out["cells"] == GIVEN and out["blob"] == GIVEN
    assert arguments("not a dict") == {"arguments": GIVEN}


def test_the_chain_holds_and_shows_a_changed_or_removed_line(store):
    audit = IntegrationAudit(store)
    reg = registry(store)
    client, _ = store.add_client("Tool", grants=["READ_METADATA"])
    for i in range(4):
        call(reg, "ext_sql", client["id"], {"workspace_id": f"ws{i}"})
    assert audit.verify() == {"lines": 4, "ok": True, "problems": []}
    [path] = list(audit.folder.glob("audit-*.jsonl"))
    text = path.read_text().splitlines()
    path.write_text("\n".join([text[0], text[2], text[3]]) + "\n")
    assert not audit.verify()["ok"]
    edited = json.loads(text[1])
    edited["decision"] = "approved"
    path.write_text("\n".join([text[0], json.dumps(edited), text[2], text[3]]) + "\n")
    result = audit.verify()
    assert not result["ok"] and "changed" in result["problems"][0]


WRITER = """
import sys
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.store import IntegrationStore
audit = IntegrationAudit(IntegrationStore(sys.argv[1]))
for i in range(25):
    audit.record(CallContext(Caller(sys.argv[2], "mcp-stdio")), "fabric_desktop_get_status", "READ_METADATA",
                 {"i": i}, 0.0, 0.0)
"""


def test_several_processes_write_one_unbroken_chain(store):
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(store.path), f"cl_{n}"]) for n in range(3)]
    assert [p.wait(60) for p in procs] == [0, 0, 0]
    result = IntegrationAudit(store).verify()
    assert result == {"lines": 75, "ok": True, "problems": []}
    assert len(IntegrationAudit(store).recent(1000)) == 75


def test_refused_http_requests_are_recorded_but_a_flood_is_not(store):
    reg = registry(store)
    client, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    with TestClient(build_app(reg, store, PORT), base_url=f"http://127.0.0.1:{PORT}") as c:
        for _ in range(5):
            assert c.get("/api/v1/capabilities").status_code == 401
        assert c.get("/api/v1/capabilities", headers={"Origin": "https://evil.example"}).status_code == 403
        ok = c.post("/api/v1/capabilities/ext_sql/invoke", json={"arguments": {"workspace_id": "ws1"}},
                    headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
    rows = lines(store)
    assert [(r["capability"], r["outcome"]) for r in rows] == [
        ("http_request", "permission_denied"), ("http_request", "permission_denied"), ("ext_sql", "ok")]
    assert rows[0]["arguments"] == {"method": "GET", "path": "/api/v1/capabilities"} and rows[0]["channel"] == "api"
    assert (rows[2]["channel"], rows[2]["client_id"]) == ("api", client["id"])
    assert token not in json.dumps(rows)


def test_mcp_calls_are_recorded_on_their_channel(store, tmp_path):
    reg = registry(store)
    client, _ = store.add_client("Cursor", "cursor", grants=["READ_METADATA"])

    async def go():
        async with Client(build_server(reg, lambda: None, client["id"])) as c:
            await c.call_tool("ext_sql", {"workspace_id": "ws1", "query": "SELECT 1"})
    anyio.run(go)
    [row] = lines(store)
    assert (row["capability"], row["client"], row["channel"], row["arguments"]["query"]) == ("ext_sql", "Cursor", "mcp-stdio", GIVEN)


def test_recent_activity_and_the_check_in_settings(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path)
    svc.integrations_set_enabled(True)
    store = svc.integration_store
    reg = registry(store)
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA"])
    folder = tmp_path / "integrations" / "executions"
    folder.mkdir(parents=True)
    (folder / f"ex_{'a' * 20}.json").write_text(json.dumps({"id": f"ex_{'a' * 20}", "client_id": client["id"],
                                                            "state": "succeeded", "created_at": 1}))
    call(reg, "ext_sql", client["id"], {"workspace_id": "ws1"})
    call(reg, "ext_run", client["id"], {"code": "x"})
    rows = svc.integrations_activity(10)
    assert [r["capability"] for r in rows] == ["ext_run", "ext_sql"]
    assert rows[1]["execution_state"] == "succeeded"
    assert svc.integrations_activity(10, client_id="cl_other") == []
    check = svc.integrations_audit_check()
    assert check["ok"] and check["lines"] == 2 and check["folder"] == str(tmp_path / "integrations" / "audit")


def test_audit_methods_are_on_the_rpc_allowlist():
    from fabric_desktop import server
    assert {"integrations_activity", "integrations_audit_check"} <= server.METHODS
