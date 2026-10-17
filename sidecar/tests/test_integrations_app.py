"""What the Settings > Integrations page calls that isn't covered elsewhere: the cheap poll for
approvals, recent activity (approvals and executions) and Test connection, which starts the MCP
server the way the tool will."""

import json
import os
import sys

import pytest

from fabric_desktop import server
from fabric_desktop.integrations.executions import Execution, ExecutionManager
from fabric_desktop.integrations.permissions import summary
from fabric_desktop.integrations.registry import default_registry
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    return Service(root=tmp_path / "FabricDesktop")


def ask(svc, client):
    cap = default_registry().get("fabric_desktop_execute_python")
    args = {"code": "print(1)"}
    return svc.integration_store.request_approval(client, cap.id, cap.title, cap.permission, summary(cap, client, args), args)


def test_the_page_methods_are_rpc_methods():
    assert {"integrations_pending", "integrations_activity", "integrations_test"} <= server.METHODS


def test_pending_is_empty_while_off(svc):
    client = svc.integrations_add_client("Claude Code", "claude-code")["client"]
    ask(svc, svc.integration_store.client(client["id"]))
    assert svc.integrations_pending() == {"enabled": False, "pending": []}
    svc.integrations_set_enabled(True)
    pending = svc.integrations_pending()
    assert pending["enabled"] and pending["pending"][0]["summary"]["content"]["text"] == "print(1)"


def test_activity_comes_from_the_audit_trail_with_execution_state(svc):
    from fabric_desktop.integrations.audit import IntegrationAudit
    from fabric_desktop.integrations.core import CallContext, Caller
    svc.integrations_set_enabled(True)
    client = svc.integrations_add_client("Claude Code", "claude-code")["client"]
    run = Execution("sql", client["id"], "SELECT 1", {"workspace_id": "ws1"}, 60)
    run.state, run.started_at, run.finished_at = "succeeded", 1.0, 2.5
    ExecutionManager(svc)._save(run)
    IntegrationAudit(svc.integration_store).record(
        CallContext(Caller(client["id"], "mcp-stdio")), "fabric_desktop_execute_sql", "EXECUTE_LOCAL_CODE",
        {"workspace_id": "ws1", "query": "SELECT 1"}, 1.0, 0.2, execution_id=run.id)
    [row] = svc.integrations_activity()
    assert (row["client"], row["decision"], row["outcome"], row["execution_state"]) == ("Claude Code", "allowed", "ok", "succeeded")
    assert row["arguments"]["query"] == "(given)" and "result" not in json.dumps(row)


def test_test_connection_starts_the_real_server(svc):
    svc.integrations_set_enabled(True)
    client = svc.integrations_add_client("Claude Code", "claude-code")["client"]
    res = svc.integrations_test(client["id"])
    assert res["ok"], res
    assert res["tools"] >= 10


def test_test_connection_for_http_needs_the_api_on(svc):
    svc.integrations_set_enabled(True)
    client = svc.integrations_add_client("Script", "http", transport="http")["client"]
    res = svc.integrations_test(client["id"])
    assert res["ok"] is False and "off" in res["message"]


def test_test_connection_refuses_revoked(svc):
    svc.integrations_set_enabled(True)
    client = svc.integrations_add_client("Claude Code", "claude-code")["client"]
    svc.integrations_revoke_client(client["id"])
    with pytest.raises(LookupError):
        svc.integrations_test(client["id"])
