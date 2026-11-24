"""WR-410: the in-process callers of a fail-closed action (test_fail_closed_actions.ACTIONS) refuse it, and
it doesn't run, when its "started" audit line can't be written, as server.handle does for the app.

The MCP server, the local HTTP API, the CLI and the agent call Service methods in-process, not through
server.handle. IN_PROCESS lists each call they make to a listed action and why it can't run unrecorded;
the scan below finds those calls in their modules, so a new one fails here until it is listed and tested.

- MCP and the local API reach the engine only through Registry.invoke (mcp_server, http_server). A
  capability that calls a listed action asks for approval whatever the tool's settings, and an approved
  call writes its "started" line to the integration audit trail before it runs (Registry._audit), so a
  line that can't be written refuses it.
- The agent's own Prod job is in test_failclosed_paths; it calls no listed action.
- The CLI schedules a pipeline on this PC, which never names Prod, so that call never fails closed.
"""

from __future__ import annotations

import functools
import inspect
import re
import threading
import time
from pathlib import Path

import pytest

from fabric_desktop import audit, audit_chain, cli
from fabric_desktop.integrations import permissions
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

from test_fail_closed_actions import ACTIONS

ENGINE = Path(__file__).resolve().parents[1] / "fabric_desktop"

# The modules that call the engine in-process: the CLI, the agent and its runtime, MCP and the local API.
# integrations/app_service.py is left out: it is a Service mixin, its methods are RPC methods (server.METHODS)
# and test_fail_closed_actions checks them through server.handle.
CALLERS = sorted({"cli.py", "agent.py", "agent_config.py", "agent_start.py", "agent_status.py", "runtime.py",
                  *(f"integrations/{p.name}" for p in (ENGINE / "integrations").glob("*.py")
                    if p.name != "app_service.py")})

# Each in-process call of a listed action, by module, and why it can't run unrecorded.
IN_PROCESS: dict[tuple[str, str], str] = {
    ("integrations/services.py", "pipeline_run_start"):
        "fabric_desktop_start_pipeline_run asks for approval every time, so Registry writes its started line "
        "first; it never passes allow_uncontained",
    ("integrations/services.py", "prove_delete_outputs"):
        "fabric_desktop_delete_proof_outputs is destructive and asks for approval every time, so Registry "
        "writes its started line first (WRITE_ONELAKE isn't offered to tools yet)",
    ("cli.py", "add_pc_schedule"):
        "pipeline schedule: the target is a pipeline folder on this PC, which never names Prod, so the call "
        "never fails closed (a Prod PC schedule's run does: test_failclosed_paths)",
}

# The integration capability that makes each MCP / local API call, and arguments that pass its schema.
CAPABILITY_CALLS = {
    "pipeline_run_start": ("fabric_desktop_start_pipeline_run", {"definition": "{}"}),
    "prove_delete_outputs": ("fabric_desktop_delete_proof_outputs", {"id": "p-1"}),
}

_CALL = re.compile(r"""(?:\.|getattr\(\s*\w+\s*,\s*["'])(%s)\b""" % "|".join(sorted(ACTIONS, key=len, reverse=True)))


def _found() -> set[tuple[str, str]]:
    """Every call (or getattr) of a listed action in the in-process callers' modules."""
    out = set()
    for module in CALLERS:
        for line in (ENGINE / module).read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("#"):
                out |= {(module, m.group(1)) for m in _CALL.finditer(line)}
    return out


def test_every_in_process_call_of_a_fail_closed_action_is_listed():
    found = _found()
    assert sorted(found - set(IN_PROCESS)) == [], (
        "a new in-process call of a fail-closed action: list it in IN_PROCESS with why it can't run "
        "unrecorded, and test it here")
    assert sorted(set(IN_PROCESS) - found) == [], "listed in IN_PROCESS but no longer called there"
    assert all(len(why) >= 40 for why in IN_PROCESS.values())


def test_the_transports_reach_the_engine_only_through_the_registry():
    for module in ("integrations/mcp_server.py", "integrations/http_server.py"):
        text = (ENGINE / module).read_text(encoding="utf-8")
        assert not {m for _, m in _found() if _ == module}, module
        assert re.search(r"\b(reg|registry)\.invoke\(", text), module


def _capability_of(registry: Registry, method: str):
    """The core capability whose handler calls ``method`` on the engine."""
    [cap] = [c for c in registry.all() if re.search(rf"\bsvc\.{method}\(", inspect.getsource(c.handler))]
    return cap


def test_every_capability_that_calls_a_fail_closed_action_always_asks_for_approval():
    """Only an approved call gets a started line before it runs (Registry._invoke), so a capability that
    reaches a listed action must ask whatever the tool's automatic approvals are."""
    reg = Registry()
    register_core(reg)
    listed = {m for module, m in IN_PROCESS if module.startswith("integrations/")}
    assert listed == set(CAPABILITY_CALLS)
    every_auto = {"auto": list(permissions.OFFERED) + ["WRITE_ONELAKE", "MODIFY_FABRIC_ARTIFACTS"]}
    for method, (cap_id, _) in CAPABILITY_CALLS.items():
        cap = _capability_of(reg, method)
        assert cap.id == cap_id
        assert PermissionAuthorizer._needs_approval(cap, every_auto), cap_id
        assert not cap.read_only


# ---- behaviour: MCP and the local API, with the integration audit trail unwritable


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A Service whose listed actions only note that they ran, keeping their real signatures."""
    ran: list[str] = []
    for method in CAPABILITY_CALLS:
        real = getattr(Service, method)

        @functools.wraps(real)
        def stand_in(self, *a, _m=method, _real=real, **k):
            inspect.signature(_real).bind(self, *a, **k)  # called as the real one would be
            ran.append(_m)
            return {"ok": True, "deleted": "x"}
        monkeypatch.setattr(Service, method, stand_in)
    svc = Service(client_id="x", root=tmp_path)
    svc.ran = ran  # type: ignore[attr-defined]
    return svc


@pytest.fixture
def store(tmp_path, monkeypatch):
    # WRITE_ONELAKE isn't offered to tools yet; offered here so delete_proof_outputs is reachable at all
    monkeypatch.setattr(permissions, "OFFERED", (*permissions.OFFERED, "WRITE_ONELAKE"))
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    store.set_setting("enabled", True)
    return store


def _registry(store: IntegrationStore) -> Registry:
    """The registry the MCP server and the local API build (run_stdio, http_server.serve)."""
    reg = Registry(PermissionAuthorizer(store, wait_seconds=5, poll=0.02))
    register_core(reg)
    reg.audit = IntegrationAudit(store).record
    return reg


def _client(store: IntegrationStore, cap) -> str:
    """A tool given the capability's permission, with automatic approval for it too."""
    client, _ = store.add_client("Tool", "claude-code", grants=[cap.permission], auto=[cap.permission])
    return client["id"]


def _approve_when_asked(store: IntegrationStore) -> threading.Thread:
    def approve() -> None:
        for _ in range(250):
            pending = store.approvals("pending")
            if pending:
                store.decide(pending[0]["id"], True)
                return
            time.sleep(0.02)
    thread = threading.Thread(target=approve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("method", sorted(CAPABILITY_CALLS))
def test_an_approved_tool_call_doesnt_run_when_its_started_line_cant_be_written(method, service, store, monkeypatch):
    reg = _registry(store)
    cap_id, args = CAPABILITY_CALLS[method]
    client_id = _client(store, reg.get(cap_id))

    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit_chain, "write", full)
    thread = _approve_when_asked(store)
    with pytest.raises(IntegrationError, match="couldn't record the call, so it didn't run"):
        reg.invoke(service, cap_id, args, CallContext(Caller(client_id, "mcp-stdio"), timeout=30))
    thread.join(5)
    assert store.approvals("used"), "the call was approved, so only the audit line stopped it"
    assert service.ran == []


@pytest.mark.parametrize("method", sorted(CAPABILITY_CALLS))
def test_an_approved_tool_call_writes_its_started_line_before_it_runs(method, service, store, monkeypatch):
    reg = _registry(store)
    cap_id, args = CAPABILITY_CALLS[method]
    client_id = _client(store, reg.get(cap_id))
    real = reg.audit

    def noted(ctx, capability, *a, stage="", **k):
        service.ran.append(f"{capability}:{stage or 'done'}")
        return real(ctx, capability, *a, stage=stage, **k)
    reg.audit = noted
    thread = _approve_when_asked(store)
    reg.invoke(service, cap_id, args, CallContext(Caller(client_id, "mcp-stdio"), timeout=30))
    thread.join(5)
    assert service.ran == [f"{cap_id}:started", method, f"{cap_id}:done"]
    assert [line["outcome"] for line in reversed(IntegrationAudit(store).recent())] == ["started", "ok"]


def test_deleting_a_proofs_outputs_isnt_offered_to_tools_today(service, tmp_path):
    store = IntegrationStore(tmp_path / "today.sqlite")
    store.set_setting("enabled", True)
    reg = Registry(PermissionAuthorizer(store, wait_seconds=1, poll=0.02))
    register_core(reg)
    client, _ = store.add_client("Tool", "claude-code", grants=["READ_METADATA"])
    with pytest.raises(IntegrationError, match="isn't offered"):
        reg.invoke(service, "fabric_desktop_delete_proof_outputs", {"id": "p-1"},
                   CallContext(Caller(client["id"], "mcp-stdio"), timeout=10))
    assert service.ran == []


# ---- the CLI


def test_the_clis_pipeline_schedule_never_names_prod(tmp_path, monkeypatch):
    """Whatever is passed, the target is a pipeline on this PC: none of its keys is an env or a target,
    so add_pc_schedule from the CLI is never a fail-closed call (an `env` added to it must be audited)."""
    calls = []

    class Svc:
        def add_pc_schedule(self, target, schedule, signed_out=False):
            inspect.signature(Service.add_pc_schedule).bind(None, target, schedule, signed_out)
            calls.append({"target": target, "schedule": schedule})
            return {"schedule": {"id": "s1", "target": {"name": "p"}}, "windows": True}

    monkeypatch.setattr(cli, "Service", Svc)
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "schedule", str(tmp_path / "prod.DataPipeline"), "--at", "06:00", "--weekday", "Monday",
                  "--param", "env=prod", "--param", "target=prod", "--notebook", "prod=prod", "--workspace", "prod",
                  "--sql-project", "prod=prod", "--read-lakehouse"])
    assert done.value.code in (0, None)
    [params] = calls
    assert not set(params["target"]) & set(audit._PROD_PARAMS)
    assert not audit.fails_closed("add_pc_schedule", params)
