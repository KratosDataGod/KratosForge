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
- The CLI restores a backup over this PC's data itself (no RPC method does): CLI_ACTIONS names it, and the
  scan finds the "started" line it records under that name (test_cli_restore_audit tests it).
- The CLI runs a pipeline on this PC itself, with the same audit lines as the app's pipeline_run_start:
  with --allow-uncontained it writes the "started" line first and doesn't run when it can't. The scan
  also finds an action the CLI records by name (fails_closed("..."), .record("...")).
"""

from __future__ import annotations

import functools
import inspect
import json
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
    ("cli.py", "backup_restore"):
        "restore --yes: it replaces this PC's data, so the CLI writes its started line first, always, and "
        "changes nothing when that line can't be written (test_cli_restore_audit)",
    ("cli.py", "pipeline_run_start"):
        "pipeline run: the CLI runs it in-process and writes pipeline_run_start's lines itself, the started "
        "line first when audit.fails_closed says so (--allow-uncontained); the run is refused without it",
    ("cli.py", "add_pc_schedule"):
        "pipeline schedule: the target is a pipeline folder on this PC, which never names Prod, so the call "
        "never fails closed (a Prod PC schedule's run does: test_failclosed_paths)",
}

# The integration capability that makes each MCP / local API call, and arguments that pass its schema.
CAPABILITY_CALLS = {
    "pipeline_run_start": ("fabric_desktop_start_pipeline_run", {"definition": "{}"}),
    "prove_delete_outputs": ("fabric_desktop_delete_proof_outputs", {"id": "p-1"}),
}

_NAMES = "|".join(sorted(ACTIONS, key=len, reverse=True))
_CALL = re.compile(r"""(?:\.|getattr\(\s*\w+\s*,\s*["'])(%s)\b""" % _NAMES)
# Actions an in-process caller runs itself that are no RPC method, so they aren't in ACTIONS: each always
# fails closed, and the scan finds the line its caller records under its name.
CLI_ACTIONS = {"backup_restore"}  # fabric-desktop restore --yes: replaces this PC's data with a backup's
# An action a caller runs itself and records under the action's own name.
_RECORDED = re.compile(r"""(?:fails_closed|\.record)\(\s*["'](%s)["']"""
                       % "|".join(sorted(set(ACTIONS) | CLI_ACTIONS, key=len, reverse=True)))


def _found() -> set[tuple[str, str]]:
    """Every call (or getattr) of a listed action in the in-process callers' modules."""
    out = set()
    for module in CALLERS:
        for line in (ENGINE / module).read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("#"):
                out |= {(module, m.group(1)) for pattern in (_CALL, _RECORDED) for m in pattern.finditer(line)}
    return out


def test_every_in_process_call_of_a_fail_closed_action_is_listed():
    found = _found()
    assert sorted(found - set(IN_PROCESS)) == [], (
        "a new in-process call of a fail-closed action: list it in IN_PROCESS with why it can't run "
        "unrecorded, and test it here")
    assert sorted(set(IN_PROCESS) - found) == [], "listed in IN_PROCESS but no longer called there"
    assert all(len(why) >= 40 for why in IN_PROCESS.values())


def test_the_clis_own_actions_are_no_rpc_method():
    """An action in CLI_ACTIONS has no RPC method of that name, whose own fail-closed check would cover it."""
    from fabric_desktop import server

    assert not set(CLI_ACTIONS) & (set(server.METHODS) | set(ACTIONS))


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


def _wait_pipeline(path: Path) -> Path:
    path.write_text(json.dumps({"name": "p", "properties": {"activities": [
        {"name": "Pause", "type": "Wait", "dependsOn": [], "typeProperties": {"waitTimeInSeconds": 1}}]}}),
        encoding="utf-8")
    return path


@pytest.fixture
def cli_run(tmp_path, monkeypatch):
    """`fabric-desktop pipeline run` with this PC's data folder in tmp_path (conftest); notes when the run
    itself starts, with the audit lines written by then."""
    from fabric_desktop.orchestration import runner
    from fabric_desktop.service import data_dir

    assert data_dir().is_relative_to(tmp_path)
    log = audit.AuditLog(data_dir() / "audit")
    ran: list[list[tuple[str, str]]] = []
    real = runner.PipelineRun.run

    def run(self):
        if self.depth == 0:
            ran.append(lines())
        return real(self)

    def lines() -> list[tuple[str, str]]:
        return [(e["action"], e["outcome"]) for e in log.entries()] if log.folder.exists() else []
    monkeypatch.setattr(runner.PipelineRun, "run", run)
    path = _wait_pipeline(tmp_path / "p.json")

    def go(*flags: str) -> int:
        with pytest.raises(SystemExit) as done:
            cli.main(["pipeline", "run", str(path), "--wait-scale", "0", "--json", *flags])
        return done.value.code
    go.ran, go.lines, go.log = ran, lines, log  # type: ignore[attr-defined]
    return go


def test_the_clis_uncontained_pipeline_run_writes_its_started_line_before_it_runs(cli_run):
    assert cli_run("--allow-uncontained") == 0
    assert cli_run.ran == [[("pipeline_run_start", "started"), ("pipeline_run_start", "ok")]]
    assert cli_run.lines() == [("pipeline_run_start", "started"), ("pipeline_run_start", "ok"),
                               ("pipeline_run_finished", "ok")]
    entries = list(cli_run.log.entries())
    assert all(e["who"].endswith(" (cli)") for e in entries)
    assert entries[0]["target"]["allow_uncontained"] is True
    assert cli_run.log.verify()["ok"]


def test_the_clis_uncontained_pipeline_run_doesnt_run_when_its_started_line_cant_be_written(
        cli_run, monkeypatch, capsys):
    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit_chain, "write", full)
    assert cli_run("--allow-uncontained") == 2
    assert cli_run.ran == []
    out = capsys.readouterr().out
    assert "The audit log couldn't be written, so this didn't run" in out
    assert "disk full" in out
    assert cli_run.lines() == []


def test_the_clis_pipeline_run_without_the_flag_is_audited_as_the_apps_is(cli_run, monkeypatch, capsys):
    """Without --allow-uncontained the app's call isn't fail-closed: it gets its lines, but runs when they
    can't be written."""
    assert not audit.fails_closed("pipeline_run_start", {"allow_uncontained": False})
    assert cli_run() == 0
    assert cli_run.ran == [[("pipeline_run_start", "ok")]]
    assert cli_run.lines() == [("pipeline_run_start", "ok"), ("pipeline_run_finished", "ok")]

    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit_chain, "write", full)
    assert cli_run() == 0
    assert len(cli_run.ran) == 2
    assert "audit: could not record pipeline_run_start" in capsys.readouterr().err


def test_the_clis_refused_pipeline_run_records_why(cli_run, tmp_path):
    """A run its checks refuse ends as the app's refused start does: an error line with its reason."""
    assert cli_run("--allow-uncontained", "--param", "nope=1") == 2
    assert cli_run.ran == []
    [started, ended] = list(cli_run.log.entries())
    assert (started["outcome"], ended["action"], ended["outcome"]) == ("started", "pipeline_run_start", "error")
    assert ended["reason"] in audit.REASONS


def test_a_cli_pipeline_run_in_the_tests_writes_only_under_tmp_path(tmp_path, monkeypatch, capsys):
    """conftest points the data folder at tmp_path in a module that runs `pipeline run`, so the audit lines
    of a test's run never reach the audit trail of whoever runs the tests."""
    from fabric_desktop.service import data_dir

    sentinel = tmp_path / "home-sentinel"
    sentinel.mkdir()
    monkeypatch.setenv("HOME", str(sentinel))
    monkeypatch.setenv("USERPROFILE", str(sentinel))
    assert data_dir().is_relative_to(tmp_path)
    mocks = tmp_path / "mocks.json"
    mocks.write_text(json.dumps({"Pause": {"status": "Succeeded"}}), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(_wait_pipeline(tmp_path / "p.json")), "--mocks", str(mocks),
                  "--wait-scale", "0", "--json"])
    assert done.value.code == 0, capsys.readouterr().out
    written = audit.AuditLog(data_dir() / "audit")
    assert [e["action"] for e in written.entries()] == ["pipeline_run_start", "pipeline_run_finished"]
    assert list(sentinel.iterdir()) == []


def test_every_test_module_that_runs_cli_pipelines_gets_a_tmp_data_folder():
    from conftest import runs_cli_pipelines

    tests = Path(__file__).parent
    found = {p.name for p in tests.glob("test_*.py") if runs_cli_pipelines(p)}
    assert {"test_cli_cov2.py", "test_cli_tenant_commands.py", "test_fail_closed_in_process.py",
            "test_pipeline_data.py", "test_pipeline_gotchas.py", "test_pipeline_notebooks.py",
            "test_pipeline_runs.py", "test_pipeline_script.py"} <= found
