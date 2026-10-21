"""Workspace limits on every capability (HD-014, finding EN-3).

A tool the person limited to some workspaces must not reach any other workspace or lakehouse
through any capability: not only the ones that take a ``workspace_id`` argument, but also those
that reach one through a notebook, proof, receipt, side-by-side run, execution or metadata
project, and lists, which leave out what is elsewhere.

``test_every_capability_refuses_an_out_of_limit_workspace`` walks every registered capability, so
a capability added later is covered without anyone remembering to add it here: it is refused for a
limited tool unless it declares how it is confined (integrations/scope.py).
"""

import json

import pytest

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.extensions import BUILT_IN, check as check_extension
from fabric_desktop.integrations.permissions import OFFERED, PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore

IN, OUT = "ws-allowed", "ws-other"
RECEIPT_IN, RECEIPT_OUT = "b" * 64, "a" * 64
EXEC_IN, EXEC_OUT = "ex_" + "b" * 20, "ex_" + "a" * 20

# Reviewed by hand: these reach no workspace or lakehouse (the status, the capability list, and
# pipeline definitions passed in the call, run only on this PC). A new capability that claims the
# same must be added here, so the claim gets a reviewer.
REACHES_NO_WORKSPACE = {
    "fabric_desktop_get_status", "fabric_desktop_list_capabilities",
    "fabric_desktop_validate_pipeline", "fabric_desktop_inspect_pipeline", "fabric_desktop_export_pipeline",
    "fabric_desktop_check_pipeline_compatibility", "fabric_desktop_get_activity_registry",
    "fabric_desktop_check_pipeline_run", "fabric_desktop_start_pipeline_run", "fabric_desktop_get_pipeline_run",
    "fabric_desktop_list_pipeline_runs", "fabric_desktop_cancel_pipeline_run",
}
# Lists: answered for a limited tool, leaving out everything outside its workspaces.
LISTS = {
    "fabric_desktop_list_projects", "fabric_desktop_list_workspaces", "fabric_desktop_list_notebooks",
    "fabric_desktop_list_proofs", "fabric_desktop_list_receipts", "fabric_desktop_list_side_by_side",
    "fabric_desktop_list_executions",
}


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


def ws_of(key: str) -> str:
    return IN if ("allowed" in key or key.startswith("b") or key.startswith("ex_b")) else OUT


def lakehouse(ws: str) -> dict:
    return {"name": "Sales", "workspace_id": ws, "workspace_name": ws.title(), "lakehouse_id": "lh-" + ws}


class Notebooks:
    def list(self):
        return [{"name": "nb_allowed", "modified": 2.0}, {"name": "nb_other", "modified": 1.0}]

    def load(self, name):
        if name not in ("nb_allowed", "nb_other"):
            raise FileNotFoundError(f"No notebook {name}")
        ws = ws_of(name)
        return {"name": name, "cells": [{"type": "code", "source": "x = 1"}], "lakehouse": lakehouse(ws),
                "workspace": {"workspace_id": ws, "workspace_name": ws.title()}, "compute": "local"}


def proof_record(pid: str) -> dict:
    ws = ws_of(pid)
    return {"id": pid, "name": "nb", "state": "done", "started_at": pid, "lakehouse": lakehouse(ws),
            "workspace_id": ws, "workspace_name": ws.title(), "fabric": {"status": "ok", "workspace_id": ws},
            "pc": {"status": "ok"}, "tables": []}


def receipt_body(rid: str) -> dict:
    return {"schema": "fabric-desktop/receipt@1", "kind": "proof", "created_at": rid,
            "notebook": {"name": "nb"}, "lakehouse": lakehouse(ws_of(rid)),
            "runs": {"fabric": {"workspace_id": ws_of(rid)}}}


class Executions:
    def record(self, eid: str, client: str) -> dict:
        return {"id": eid, "kind": "sql", "state": "succeeded", "client_id": client, "created_at": 1.0,
                "target": {"workspace_id": ws_of(eid), "lakehouse_id": "lh"}, "logs": ["done"], "result": {"rows": []}}

    def get(self, eid, client):
        return self.record(eid, client)

    def list(self, client, limit=50):
        return [self.record(EXEC_IN, client) | {"result": None}, self.record(EXEC_OUT, client) | {"result": None}]

    def cancel(self, eid, client):
        return self.record(eid, client) | {"cancelled": False}


class FakeService:
    """Everything an external tool can reach, half of it in the allowed workspace and half not."""

    def __init__(self, meta_ws=OUT):
        self.meta_ws = meta_ws
        self.notebooks = Notebooks()
        self._integration_executions = Executions()

    def list_workspaces(self):
        return [{"id": IN, "displayName": "Allowed"}, {"id": OUT, "displayName": "Other"}]

    def list_items(self, ws):
        return [{"id": "item-1", "displayName": "Sales", "type": "Lakehouse"}]

    def meta_project(self):
        return {"path": "C:/mesh", "project": {"name": "Mesh"}, "entities": [], "layers": [], "errors": [],
                "environments": {"dev": {"workspace_id": self.meta_ws, "workspace_name": self.meta_ws.title()}}}

    def prove_list(self, name=None, limit=20):
        return [proof_record("p-allowed"), proof_record("p-other")]

    def prove_status(self, pid):
        return proof_record(pid)

    def receipts_list(self, name=None, limit=50):
        return [{"id": RECEIPT_IN, "valid": True}, {"id": RECEIPT_OUT, "valid": True}]

    def receipt_get(self, rid):
        return {"id": rid, "receipt": receipt_body(rid)}

    def side_by_side_list(self, name=None, limit=20):
        return [proof_record("s-allowed"), proof_record("s-other")]

    def side_by_side_status(self, sid):
        return proof_record(sid)

    def risk_check(self, workload_id, action, env=None):
        return {"result": "pass", "findings": []}

    def _wl_record(self, wid):
        ws = ws_of(wid)
        return {"id": wid, "name": wid, "kind": "fabric_item", "deployments": [{"env": None, "actions": ["run_item"],
                "location": {"kind": "fabric", "label": "Fabric", "workspace_id": "" if "named" in wid else ws,
                             "workspace_name": ws.title()}}]}  # w-named: the workspace's name, not its id


def registry(store) -> Registry:
    reg = Registry(PermissionAuthorizer(store, wait_seconds=0.2, poll=0.02))
    register_core(reg)
    for ext in BUILT_IN:  # extensions' capabilities are capabilities like any other
        for cap in check_extension(ext):
            reg.register(cap)
    return reg


def limited_client(store, workspaces=(IN,)):
    client, _ = store.add_client("Cursor", "cursor", grants=list(OFFERED), auto=list(OFFERED), workspaces=list(workspaces))
    return client


def ctx(client_id):
    return CallContext(Caller(client_id, "test"), timeout=10)


def arguments(cap: Capability, inside: bool) -> dict:
    """The capability's required arguments (and any workspace it can take), all pointing inside or
    outside the tool's workspaces."""
    ws = IN if inside else OUT
    values = {"workspace_id": ws, "lakehouse_id": "lh-" + ws, "item_id": "item-1", "table": "dbo.orders",
              "name": "nb_allowed" if inside else "nb_other", "id": RECEIPT_IN if inside else RECEIPT_OUT,
              "execution_id": EXEC_IN if inside else EXEC_OUT, "query": "select 1", "code": "x = 1",
              "definition": "{}", "workload_id": "w-allowed" if inside else "w-other", "action": "run"}
    props = cap.input_schema.get("properties", {})
    out = {}
    optional = [k for k in ("workspace_id", "lakehouse_id") if k in props]  # a lakehouse to attach, say
    for key in list(cap.input_schema.get("required", [])) + optional:
        if "enum" in props[key]:
            out[key] = "metadata" if not inside and "metadata" in props[key]["enum"] else props[key]["enum"][0]
        else:
            out[key] = values[key]
    return out


def outcome(reg, svc, cap_id, client_id, args):
    try:
        return reg.invoke(svc, cap_id, dict(args), ctx(client_id))
    except IntegrationError as err:
        return err


def test_every_capability_refuses_an_out_of_limit_workspace(store):
    reg = registry(store)
    svc = FakeService()
    client = limited_client(store)
    leaks = []
    for cap in reg.all():
        if cap.id in REACHES_NO_WORKSPACE:
            continue
        if cap.id in LISTS:
            got = outcome(reg, svc, cap.id, client["id"], {})
            text = json.dumps(got, default=str) if not isinstance(got, IntegrationError) else ""
            if isinstance(got, IntegrationError) or any(x in text for x in (OUT, "-other", "nb_other", RECEIPT_OUT, EXEC_OUT)):
                leaks.append(f"{cap.id}: {getattr(got, 'code', 'listed things outside the limit')}")
            continue
        got = outcome(reg, svc, cap.id, client["id"], arguments(cap, inside=False))
        if not (isinstance(got, IntegrationError) and got.code == "permission_denied"):
            leaks.append(f"{cap.id}: {getattr(got, 'code', 'allowed')}")
    assert leaks == [], "capabilities that reach a workspace outside the tool's limit:\n" + "\n".join(leaks)


def test_every_capability_declares_how_it_is_confined(store):
    """The shared helper's view of each capability: what it reaches inside and outside the limit.
    Covers categories a limited tool can't be given today (code, OneLake writes), so the limit
    still holds the day they are offered."""
    from fabric_desktop.integrations import scope
    reg = registry(store)
    svc = FakeService()
    client = limited_client(store)
    for cap in reg.all():
        c = ctx(client["id"])
        if cap.id in REACHES_NO_WORKSPACE:
            assert cap.workspaces == scope.NONE, cap.id
            continue
        if cap.id in LISTS:
            assert cap.workspaces == scope.FILTERED, cap.id
            continue
        outside = scope.targets(cap, svc, arguments(cap, inside=False), c)
        assert outside is None or OUT in outside, (cap.id, outside)
        inside = scope.targets(cap, FakeService(meta_ws=IN), arguments(cap, inside=True), c)
        if cap.workspaces == scope.UNKNOWN:
            assert inside is None, cap.id
        else:
            assert inside is not None and inside <= {IN}, (cap.id, inside)


def test_in_limit_calls_still_work(store):
    reg = registry(store)
    svc = FakeService()
    client = limited_client(store)
    nb = reg.invoke(svc, "fabric_desktop_get_notebook", {"name": "nb_allowed"}, ctx(client["id"]))
    assert nb["lakehouse"]["workspace_id"] == IN
    assert reg.invoke(svc, "fabric_desktop_get_proof", {"id": "p-allowed"}, ctx(client["id"]))["id"] == "p-allowed"
    assert reg.invoke(svc, "fabric_desktop_get_receipt", {"id": RECEIPT_IN}, ctx(client["id"]))["id"] == RECEIPT_IN
    assert reg.invoke(svc, "fabric_desktop_get_side_by_side", {"id": "s-allowed"}, ctx(client["id"]))["id"] == "s-allowed"
    assert reg.invoke(svc, "notebook_outline_get", {"name": "nb_allowed"}, ctx(client["id"]))["name"] == "nb_allowed"
    out = reg.invoke(svc, "fabric_desktop_get_execution_status", {"execution_id": EXEC_IN}, ctx(client["id"]))
    assert out["target"]["workspace_id"] == IN


def test_lists_leave_out_other_workspaces_with_right_totals(store):
    reg = registry(store)
    svc = FakeService()
    client = limited_client(store)
    call = lambda cap_id, args=None: reg.invoke(svc, cap_id, args or {}, ctx(client["id"]))  # noqa: E731
    ws = call("fabric_desktop_list_workspaces")
    assert [w["id"] for w in ws["items"]] == [IN] and ws["total"] == 1
    assert [n["name"] for n in call("fabric_desktop_list_notebooks")["items"]] == ["nb_allowed"]
    assert [p["id"] for p in call("fabric_desktop_list_proofs")["items"]] == ["p-allowed"]
    assert [r["id"] for r in call("fabric_desktop_list_receipts")["items"]] == [RECEIPT_IN]
    assert [s["id"] for s in call("fabric_desktop_list_side_by_side")["items"]] == ["s-allowed"]
    assert [e["id"] for e in call("fabric_desktop_list_executions")["items"]] == [EXEC_IN]
    projects = call("fabric_desktop_list_projects")["projects"]  # the metadata project is in ws-other
    assert [p["id"] for p in projects] == ["notebooks"] and projects[0]["items"] == 1
    assert [n["name"] for n in call("fabric_desktop_get_project", {"id": "notebooks"})["notebooks"]] == ["nb_allowed"]


def test_a_tool_with_no_limit_sees_everything(store):
    reg = registry(store)
    svc = FakeService()
    client, _ = store.add_client("Claude", "claude-code", grants=list(OFFERED), auto=list(OFFERED))
    c = lambda: ctx(client["id"])  # noqa: E731
    assert reg.invoke(svc, "fabric_desktop_list_workspaces", {}, c())["total"] == 2
    assert reg.invoke(svc, "fabric_desktop_list_proofs", {}, c())["total"] == 2
    assert reg.invoke(svc, "fabric_desktop_get_notebook", {"name": "nb_other"}, c())["name"] == "nb_other"
    assert reg.invoke(svc, "fabric_desktop_check_run_risk", {"workload_id": "w1", "action": "run"}, c())["result"] == "pass"


def test_refusals_say_why_and_what_to_do(store):
    reg = registry(store)
    svc = FakeService()
    client = limited_client(store)
    err = outcome(reg, svc, "fabric_desktop_get_notebook", client["id"], {"name": "nb_other"})
    assert err.code == "permission_denied" and "limited to other workspaces" in err.message
    assert "Settings > Integrations" in err.hint
    err = outcome(reg, svc, "fabric_desktop_check_run_risk", client["id"], {"workload_id": "w-named", "action": "run"})
    assert err.code == "permission_denied" and "can't tell which workspace" in err.message
    # a missing notebook is reported as missing, not as another workspace
    err = outcome(reg, svc, "fabric_desktop_get_notebook", client["id"], {"name": "nope"})
    assert err.code == "not_found"


def test_an_undeclared_capability_fails_closed_for_a_limited_tool(store):
    """An extension that doesn't say how it is confined still works for a tool with no limit, and
    is refused (and listed as unavailable) for a limited one: it can't be checked."""
    reg = registry(store)
    reg.register(Capability("ext_peek", "Peek", "Reads something.", schema({"name": {"type": "string"}}),
                            schema(extra=True), "READ_METADATA", lambda svc, ctx, **a: {"ok": True},
                            needs_service=False, source="ext"))
    limited = limited_client(store)
    err = outcome(reg, None, "ext_peek", limited["id"], {"name": "x"})
    assert err.code == "permission_denied" and "can't tell which workspace" in err.message
    status, reason = reg.availability(reg.get("ext_peek"), ctx(limited["id"]))
    assert status == "unavailable" and "workspace" in reason
    free, _ = store.add_client("Claude", "claude-code", grants=["READ_METADATA"])
    assert reg.invoke(None, "ext_peek", {"name": "x"}, ctx(free["id"])) == {"ok": True}


def test_an_old_record_with_no_workspace_is_refused_for_a_limited_tool(store):
    """Side-by-side runs saved before HD-014 named their lakehouse's workspace only by name. A
    limited tool can't be shown them: the workspace can't be checked."""
    reg = registry(store)
    svc = FakeService()
    old = proof_record("s-allowed")
    old["lakehouse"] = {"name": "Sales", "workspace_name": "Allowed"}
    svc.side_by_side_status = lambda sid: old
    svc.side_by_side_list = lambda name=None, limit=20: [old]
    client = limited_client(store)
    err = outcome(reg, svc, "fabric_desktop_get_side_by_side", client["id"], {"id": "s-allowed"})
    assert err.code == "permission_denied"
    assert reg.invoke(svc, "fabric_desktop_list_side_by_side", {}, ctx(client["id"]))["total"] == 0


def test_a_verification_receipt_takes_the_workspace_of_the_proof_it_checks(store):
    reg = registry(store)
    svc = FakeService()
    child = "c" * 64

    def receipt_get(rid):
        if rid == child:
            return {"id": rid, "receipt": {"schema": "fabric-desktop/receipt@1", "kind": "verification",
                                           "proof": {"id": "p-other", "receipt": RECEIPT_OUT}, "outputs": []}}
        return {"id": rid, "receipt": receipt_body(rid)}
    svc.receipt_get = receipt_get
    client = limited_client(store)
    assert outcome(reg, svc, "fabric_desktop_export_receipt", client["id"], {"id": child}).code == "permission_denied"
    assert outcome(reg, svc, "fabric_desktop_get_receipt", client["id"], {"id": child}).code == "permission_denied"


def test_registration_checks_the_declaration(store):
    from fabric_desktop.integrations import scope
    reg = Registry()
    base = dict(title="T", description="D", input_schema=schema(), output_schema=schema(extra=True),
                permission="READ_METADATA", handler=lambda svc, ctx: {})
    with pytest.raises(ValueError, match="workspace_id"):
        reg.register(Capability("ext_a", workspaces=scope.ARGUMENT, **base))
    with pytest.raises(ValueError, match="workspaces"):
        reg.register(Capability("ext_b", workspaces="everything", **base))
    reg.register(Capability("ext_c", workspaces=scope.NONE, **base))


def test_the_risk_check_reaches_where_the_workload_runs():
    """FINOPS-004 for a limited tool: the deployment's workspace (its capacity is read), and for a
    Data mesh entity's run, the environment's workspace (its inputs' Delta log)."""
    from fabric_desktop.integrations import scope
    svc = FakeService(meta_ws=IN)
    pc = {"kind": "this_pc", "label": "This PC"}
    fabric = {"kind": "fabric", "label": "Fabric", "workspace_id": OUT, "workspace_name": "Other"}
    notebook = {"kind": "notebook", "deployments": [{"env": None, "location": pc},
                                                    {"env": None, "location": fabric}]}
    assert scope.workload_of(svc, notebook, "run_in_fabric", "") == {OUT}
    entity = {"kind": "meta_entity", "deployments": [{"env": "dev", "location": pc}, {"env": "prod", "location": fabric}]}
    assert scope.workload_of(svc, entity, "run", "dev") == {IN}  # dev's inputs, not prod's capacity
    assert scope.workload_of(svc, entity, "run_schedule", "dev") == set()  # a schedule on this PC reads no inputs
    assert scope.workload_of(svc, entity, "run", "prod") == {OUT}  # prod has no environment entry here
    named_only = {"kind": "fabric_item", "deployments": [{"env": None, "location": dict(fabric, workspace_id="")}]}
    assert scope.workload_of(svc, named_only, "run_item", "") is None  # a name without its id can't be checked


def test_a_limited_tool_checks_risk_only_in_its_workspaces(store):
    reg = registry(store)
    client = limited_client(store)
    args = {"workload_id": "w-allowed", "action": "run_item"}
    assert reg.invoke(FakeService(), "fabric_desktop_check_run_risk", args, ctx(client["id"]))["result"] == "pass"
    got = outcome(reg, FakeService(), "fabric_desktop_check_run_risk", client["id"], args | {"workload_id": "w-other"})
    assert isinstance(got, IntegrationError) and got.code == "permission_denied"
