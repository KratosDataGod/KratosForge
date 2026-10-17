"""Permissions and approvals for external tools (integrations/permissions.py, store.py, policy.py,
app_service.py): off by default, per-tool grants, policy limits, single-use approvals bound to the
exact arguments, revocation, and no way to approve from outside the app."""

import json
import threading
import time

import anyio
import pytest
from mcp import Client

from fabric_desktop import server
from fabric_desktop.integrations import policy
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def write_policy(tmp_path, data):
    folder = tmp_path / "programdata" / "FabricDesktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "policy.json").write_text(json.dumps(data))


@pytest.fixture
def store(tmp_path):
    return IntegrationStore(tmp_path / "integrations.sqlite")


def ran(svc, ctx, **args):
    return {"ran": args}


def registry(store, wait=0.3):
    reg = Registry(PermissionAuthorizer(store, wait_seconds=wait, poll=0.02))
    register_core(reg)
    reg.register(Capability("ext_preview", "Preview rows", "Rows of a table.", schema({"table": {"type": "string"}}),
                            schema(extra=True), "READ_DATA", ran, approval="ask", needs_service=False))
    reg.register(Capability("ext_run", "Run Python", "Runs code.", schema({"code": {"type": "string"}}), schema(extra=True),
                            "EXECUTE_LOCAL_CODE", ran, read_only=False, approval="always", needs_service=False))
    reg.register(Capability("ext_write", "Write OneLake", "Writes.", schema(), schema(extra=True), "WRITE_ONELAKE",
                            ran, read_only=False, approval="always", needs_service=False))
    reg.register(Capability("ext_ws", "Workspace thing", "Reads a workspace.", schema({"workspace_id": {"type": "string"}}),
                            schema(extra=True), "READ_METADATA", ran, needs_service=False))
    return reg


def ctx(client_id):
    return CallContext(Caller(client_id, "test"), timeout=10)


def enabled_client(store, **kw):
    store.set_setting("enabled", True)
    client, _ = store.add_client("Claude Code", "claude-code", grants=kw.pop("grants", ["READ_METADATA"]), **kw)
    return client


def code_of(reg, cap_id, client_id, args=None):
    try:
        reg.invoke(None, cap_id, args or {}, ctx(client_id))
    except IntegrationError as err:
        return err.code
    return "ok"


def test_off_by_default_and_capability_list_says_why(store):
    reg = registry(store)
    client, _ = store.add_client("Tool", grants=["READ_METADATA"])
    assert code_of(reg, "ext_ws", client["id"]) == "integrations_disabled"
    caps = reg.invoke(None, "fabric_desktop_list_capabilities", {}, ctx(client["id"]))["capabilities"]
    assert all(c["status"] == "unavailable" and "turned off" in c["reason"] for c in caps)


def test_unknown_and_revoked_tools_get_nothing(store):
    reg = registry(store)
    client = enabled_client(store)
    assert code_of(reg, "ext_ws", "cl_nobody") == "permission_denied"
    assert code_of(reg, "ext_ws", client["id"]) == "ok"
    store.revoke_client(client["id"])
    assert code_of(reg, "ext_ws", client["id"]) == "permission_denied"
    with pytest.raises(IntegrationError, match="disconnected"):
        reg.invoke(None, "ext_ws", {}, ctx(client["id"]))
    assert code_of(reg, "fabric_desktop_list_capabilities", "cl_nobody") == "ok"


def test_categories_must_be_granted_offered_and_allowed(store, tmp_path):
    reg = registry(store)
    client = enabled_client(store)
    assert code_of(reg, "ext_preview", client["id"], {"table": "t"}) == "permission_denied"
    store.update_client(client["id"], grants=["READ_METADATA", "WRITE_ONELAKE"])
    with pytest.raises(IntegrationError, match="isn't offered"):
        reg.invoke(None, "ext_write", {}, ctx(client["id"]))
    store.update_client(client["id"], grants=["READ_METADATA", "READ_DATA"], auto=["READ_DATA"])
    assert code_of(reg, "ext_preview", client["id"], {"table": "t"}) == "ok"
    write_policy(tmp_path, {"integrations_max_permissions": ["READ_METADATA"]})
    with pytest.raises(IntegrationError, match="organization doesn't allow"):
        reg.invoke(None, "ext_preview", {"table": "t"}, ctx(client["id"]))


def test_policy_can_turn_integrations_off_and_bad_values_fail_closed(store, tmp_path):
    reg = registry(store)
    client = enabled_client(store)
    write_policy(tmp_path, {"integrations": False})
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_ws", {}, ctx(client["id"]))
    assert err.value.code == "integrations_disabled" and "organization" in err.value.message
    write_policy(tmp_path, {"integrations": "yes", "integrations_max_permissions": "all"})
    rules = policy.current()
    assert rules["enabled"] is False and rules["max_permissions"] == ["READ_METADATA"] and len(rules["problems"]) == 2
    write_policy(tmp_path, {"integrations": True})
    assert policy.current()["enabled"] is None


def test_workspace_scope(store):
    reg = registry(store)
    client = enabled_client(store, workspaces=["ws-allowed"])
    assert code_of(reg, "ext_ws", client["id"], {"workspace_id": "ws-allowed"}) == "ok"
    assert code_of(reg, "ext_ws", client["id"], {"workspace_id": "ws-other"}) == "permission_denied"


def test_approval_waits_for_the_person_and_is_single_use(store):
    reg = registry(store, wait=5)
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])

    def approve_when_asked():
        for _ in range(200):
            pending = store.approvals("pending")
            if pending:
                store.decide(pending[0]["id"], True)
                return
            time.sleep(0.02)
    threading.Thread(target=approve_when_asked).start()
    out = reg.invoke(None, "ext_run", {"code": "print(1)"}, ctx(client["id"]))
    assert out == {"ran": {"code": "print(1)"}}
    [used] = store.approvals("used")
    assert used["summary"]["content"] == {"kind": "code", "text": "print(1)"}
    assert used["summary"]["client"] == "Claude Code"
    # the same call again needs a new approval
    reg.authorizer.wait_seconds = 0.2
    assert code_of(reg, "ext_run", client["id"], {"code": "print(1)"}) == "approval_required"


def test_no_answer_returns_approval_required_and_a_later_identical_call_uses_the_approval(store):
    reg = registry(store, wait=0.2)
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_run", {"code": "x = 1"}, ctx(client["id"]))
    assert err.value.code == "approval_required" and err.value.retryable
    approval_id = err.value.details["approval_id"]
    # asking again doesn't create a second request
    assert code_of(reg, "ext_run", client["id"], {"code": "x = 1"}) == "approval_required"
    assert len(store.approvals("pending")) == 1
    store.decide(approval_id, True)
    # an approval is for those exact arguments only
    assert code_of(reg, "ext_run", client["id"], {"code": "import os; os.remove('x')"}) == "approval_required"
    assert reg.invoke(None, "ext_run", {"code": "x = 1"}, ctx(client["id"])) == {"ran": {"code": "x = 1"}}


def test_denied_requests_fail_and_say_so(store):
    reg = registry(store, wait=0.2)
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    code_of(reg, "ext_run", client["id"], {"code": "x"})
    store.decide(store.approvals("pending")[0]["id"], False, "not now")
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_run", {"code": "x"}, ctx(client["id"]))
    assert err.value.code == "approval_denied" and err.value.details["note"] == "not now"


def test_ask_categories_can_be_pre_approved_but_code_always_asks(store):
    reg = registry(store, wait=0.1)
    client = enabled_client(store, grants=["READ_METADATA", "READ_DATA", "EXECUTE_LOCAL_CODE"],
                            auto=["READ_DATA", "EXECUTE_LOCAL_CODE"])
    assert code_of(reg, "ext_preview", client["id"], {"table": "t"}) == "ok"
    assert code_of(reg, "ext_run", client["id"], {"code": "x"}) == "approval_required"


def test_bound_arguments_are_shown_hashed_and_bound_for_pre_approved_calls_too(store):
    reg = registry(store, wait=0.1)
    version = {"cells": ["a = 1", "print(a)"]}
    for cap_id, approval in (("ext_bound", "always"), ("ext_bound_auto", "ask")):
        reg.register(Capability(cap_id, "Run a notebook", "Runs it.", schema({"name": {"type": "string"}}), schema(extra=True),
                                "EXECUTE_LOCAL_CODE", ran, read_only=False, approval=approval,
                                bind=lambda svc, args: {"cells": list(version["cells"])}))
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"], auto=["EXECUTE_LOCAL_CODE"])
    # Pre-approved: no question, and the handler still gets what bind read.
    assert reg.invoke(object(), "ext_bound_auto", {"name": "n"}, ctx(client["id"])) == {"ran": {"name": "n", "cells": ["a = 1", "print(a)"]}}
    # Asked: every cell is shown, and an approval for one version doesn't run another.
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), "ext_bound", {"name": "n"}, ctx(client["id"]))
    first = store.approval(err.value.details["approval_id"])
    assert first["summary"]["content"]["text"] == "# ---- cell 1 of 2\na = 1\n\n# ---- cell 2 of 2\nprint(a)"
    store.decide(first["id"], True)
    version["cells"] = ["import os"]
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), "ext_bound", {"name": "n"}, ctx(client["id"]))
    assert err.value.code == "approval_required" and err.value.details["approval_id"] != first["id"]
    # A caller can't supply a bound argument itself.
    assert code_of(reg, "ext_bound", client["id"], {"name": "n", "cells": ["x"]}) == "invalid_input"


def test_long_code_says_it_was_cut(store):
    reg = registry(store, wait=0.05)
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_run", {"code": "x" * 25_000}, ctx(client["id"]))
    content = store.approval(err.value.details["approval_id"])["summary"]["content"]
    assert content["truncated"] is True and content["text"].endswith("[5,000 more characters not shown]")


def test_requests_expire_and_revoking_drops_them(store):
    now = [1000.0]
    store.clock = lambda: now[0]
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    a = store.request_approval(client, "ext_run", "Run", "EXECUTE_LOCAL_CODE", {}, {"code": "1"})
    now[0] += 301
    assert store.approval(a["id"])["state"] == "expired"
    b = store.request_approval(client, "ext_run", "Run", "EXECUTE_LOCAL_CODE", {}, {"code": "2"})
    store.revoke_client(client["id"])
    assert store.approval(b["id"])["state"] == "expired"
    with pytest.raises(ValueError):
        store.decide(b["id"], True)


def test_cancel_while_waiting_for_approval(store):
    reg = registry(store, wait=10)
    client = enabled_client(store, grants=["READ_METADATA", "EXECUTE_LOCAL_CODE"])
    c = ctx(client["id"])
    threading.Timer(0.2, c.cancel).start()
    started = time.monotonic()
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_run", {"code": "x"}, c)
    assert err.value.code == "cancelled" and time.monotonic() - started < 3


def test_mcp_lists_only_what_this_tool_may_use(store):
    reg = registry(store)
    client = enabled_client(store)

    async def go():
        async with Client(build_server(reg, client_id=client["id"])) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert "ext_ws" in names and "ext_run" not in names and "ext_preview" not in names
            out = await c.call_tool("ext_run", {"code": "x"})
            assert out.structured_content["error"]["code"] == "permission_denied"
            assert "Settings > Integrations" in out.structured_content["error"]["message"]
    anyio.run(go)


def test_no_capability_can_approve_or_change_permissions():
    reg = Registry()
    register_core(reg)
    for cap in reg.all():
        assert "approv" not in cap.id and "permission" not in cap.id and "grant" not in cap.id


# ---- the app's side (engine RPC)

@pytest.fixture
def svc(tmp_path):
    return Service(root=tmp_path / "data")


def rpc(svc, method, **params):
    return json.loads(json.dumps(server.handle(svc, json.dumps({"id": 1, "method": method, "params": params})), default=str))


def test_app_switches_on_connects_a_tool_and_gets_its_configuration(svc):
    status = rpc(svc, "integrations_status")["result"]
    assert status["enabled"] is False and status["clients"] == []
    assert rpc(svc, "integrations_set_enabled", enabled=True)["result"]["enabled"] is True
    added = rpc(svc, "integrations_add_client", name="Claude Code on this PC", kind="claude-code", grants=["READ_DATA"])["result"]
    client = added["client"]
    assert client["grants"] == ["READ_DATA", "READ_METADATA"] and added["token"] is None
    assert client["id"] in client["config"]["claude-code"]["text"]
    assert json.loads(client["config"]["cursor"]["text"])["mcpServers"]["fabric-desktop"]["args"] == ["--client", client["id"]]
    assert '"type": "stdio"' in client["config"]["vscode"]["text"]
    launcher = svc.root / "bin"
    assert any(launcher.iterdir())


def test_app_refuses_what_is_not_offered_or_allowed(svc, tmp_path):
    rpc(svc, "integrations_set_enabled", enabled=True)
    assert "can't be given" in rpc(svc, "integrations_add_client", name="x", grants=["DEPLOY_TO_FABRIC"])["error"]
    assert "always asks" in rpc(svc, "integrations_add_client", name="x", grants=["EXECUTE_LOCAL_CODE"],
                                auto=["EXECUTE_LOCAL_CODE"])["error"]
    write_policy(tmp_path, {"integrations_max_permissions": ["READ_METADATA"]})
    assert "organization" in rpc(svc, "integrations_add_client", name="x", grants=["READ_DATA"])["error"]
    write_policy(tmp_path, {"integrations": False})
    assert "organization" in rpc(svc, "integrations_set_enabled", enabled=True)["error"]
    assert rpc(svc, "integrations_status")["result"]["set_by_organization"] is True


def test_app_decides_approvals_and_revokes(svc):
    rpc(svc, "integrations_set_enabled", enabled=True)
    client = rpc(svc, "integrations_add_client", name="Cursor", kind="cursor", grants=["EXECUTE_LOCAL_CODE"])["result"]["client"]
    store = svc.integration_store
    request = store.request_approval(store.client(client["id"]), "ext_run", "Run Python", "EXECUTE_LOCAL_CODE",
                                     {"client": "Cursor"}, {"code": "1"})
    assert [a["id"] for a in rpc(svc, "integrations_approvals")["result"]] == [request["id"]]
    assert rpc(svc, "integrations_decide", approval_id=request["id"], approve=False)["result"]["state"] == "denied"
    assert rpc(svc, "integrations_revoke_client", client_id=client["id"])["result"]["revoked_at"]
    assert rpc(svc, "integrations_status")["result"]["clients"] == []


def test_launcher_runs_this_engine(tmp_path):
    from fabric_desktop.integrations.config import write_launcher
    path = write_launcher(tmp_path)
    import sys
    assert sys.executable in path.read_text()
    assert write_launcher(tmp_path) == path
