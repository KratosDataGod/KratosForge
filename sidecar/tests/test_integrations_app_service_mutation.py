"""Mutation batch 361: Settings > Integrations (integrations/app_service.py). Policy refusals, the port's
range, what status and pending report, the activity list's merging, the log roll's size and the
stdio test's success path. The local API process, the audit trail and the MCP client are stand-ins."""

from __future__ import annotations

import os

import pytest

from fabric_desktop.integrations import app_service
from fabric_desktop.integrations import audit as audit_mod
from fabric_desktop.integrations import executions as exec_mod
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    return Service(root=tmp_path / "FabricDesktop")


def set_policy(monkeypatch, **over):
    real = app_service.policy.current

    def current():
        return real() | over
    monkeypatch.setattr(app_service.policy, "current", current)


def test_the_last_line_of_a_short_log_keeps_its_first_character(tmp_path):
    log = tmp_path / "http.log"
    log.write_bytes(b"port busy\n")
    assert app_service._last_line(log) == "port busy"


def test_the_log_rolls_only_past_five_mebibytes(tmp_path):
    log = tmp_path / "http.log"
    log.write_bytes(b"x" * (5 * 1024 * 1024))
    app_service._roll_log(log)
    assert log.exists() and not (tmp_path / "http.prev.log").exists()
    with open(log, "ab") as f:
        f.write(b"y")
    app_service._roll_log(log)
    assert not log.exists() and (tmp_path / "http.prev.log").stat().st_size == 5 * 1024 * 1024 + 1


def test_a_tools_config_has_the_api_url_only_for_http(svc):
    http = svc.integrations_add_client("Script", "other-mcp", transport="http")["client"]
    stdio = svc.integrations_add_client("Code", "other-mcp")["client"]
    port = svc.integration_store.settings()["http_port"]
    assert f"http://127.0.0.1:{port}" in str(http["config"])
    assert "127.0.0.1" not in str(stdio["config"])


def test_the_api_isnt_allowed_when_the_organization_turns_integrations_off(svc, monkeypatch):
    set_policy(monkeypatch, http=True, enabled=False)
    assert svc._http_state()["allowed"] is False
    set_policy(monkeypatch, http=True, enabled=None)
    assert svc._http_state()["allowed"] is True


def test_starting_the_api_when_its_folder_is_there(svc, monkeypatch):
    class Proc:
        returncode = None

        def poll(self):
            return None
    (svc.root / "integrations").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_service.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(app_service, "HTTP_START_SECONDS", 0)
    svc._http_start()
    assert svc._http_state()["running"] is True


def test_stopping_an_api_that_never_started(svc):
    svc._http_stop()
    assert svc._http_proc is None


def test_the_api_switch_is_refused_when_policy_forbids_it(svc, monkeypatch):
    set_policy(monkeypatch, http=False)
    with pytest.raises(PermissionError, match="doesn't allow the local API"):
        svc.integrations_set_http(True)


@pytest.mark.parametrize("port", [1023, 65536, "47600", 80])
def test_a_port_outside_1024_to_65535_is_refused(svc, port):
    with pytest.raises(ValueError, match="port is a number from 1024 to 65535"):
        svc.integrations_set_http(False, port=port)


@pytest.mark.parametrize("port", [1024, 65535])
def test_the_ends_of_the_port_range_are_taken(svc, port):
    out = svc.integrations_set_http(False, port=port)
    assert out["http"]["port"] == port


def test_a_new_token_comes_back_once(svc):
    client = svc.integrations_add_client("Code", "other-mcp")["client"]
    got = svc.integrations_new_token(client["id"])
    assert got["client"]["id"] == client["id"] and got["token"]


def test_status_when_the_organization_turns_integrations_off(svc, monkeypatch):
    svc.integration_store.set_setting("enabled", True)
    set_policy(monkeypatch, enabled=False)
    written = []
    monkeypatch.setattr(app_service, "write_launcher", lambda root: written.append(root))
    out = svc.integrations_status()
    assert out["enabled"] is False and out["switch"] is True and out["set_by_organization"] is True
    assert written == []


def test_status_when_the_person_turns_integrations_on(svc, monkeypatch):
    svc.integration_store.set_setting("enabled", True)
    set_policy(monkeypatch, enabled=None)
    written = []
    monkeypatch.setattr(app_service, "write_launcher", lambda root: written.append(root))
    out = svc.integrations_status()
    assert out["enabled"] is True and out["set_by_organization"] is False
    assert written == [svc.root]


def test_turning_integrations_on_is_refused_when_the_organization_turned_them_off(svc, monkeypatch):
    set_policy(monkeypatch, enabled=False)
    with pytest.raises(PermissionError, match="turned off external AI tools"):
        svc.integrations_set_enabled(True)
    assert svc.integrations_set_enabled(False)["switch"] is False


def test_pending_follows_the_switch_and_the_policy(svc, monkeypatch):
    monkeypatch.setattr(svc.integration_store, "approvals", lambda state: [{"id": "a1"}])
    svc.integration_store.set_setting("enabled", True)
    set_policy(monkeypatch, enabled=None)
    assert svc.integrations_pending() == {"enabled": True, "pending": [{"id": "a1"}]}
    set_policy(monkeypatch, enabled=False)
    assert svc.integrations_pending() == {"enabled": False, "pending": []}


def test_approvals_and_decisions_pass_through(svc, monkeypatch):
    monkeypatch.setattr(svc.integration_store, "approvals", lambda state, limit: [state, limit])
    monkeypatch.setattr(svc.integration_store, "decide", lambda a, ok, note: {"id": a, "ok": ok, "note": note})
    assert svc.integrations_approvals() == ["pending", 50]
    assert svc.integrations_decide("a1", 1, "fine") == {"id": "a1", "ok": True, "note": "fine"}


class Trail:
    rows: list = []
    limits: list = []

    def __init__(self, store):
        self.folder = "audit"

    def recent(self, limit, client_id):
        Trail.limits.append(limit)
        return [dict(r) for r in Trail.rows]

    def approvals_recorded(self, ids):
        return set()

    def verify(self):
        return {"ok": True}


def activity(svc, monkeypatch, rows, uses=(), states=(), **kw):
    Trail.rows, Trail.limits = rows, []
    monkeypatch.setattr(audit_mod, "IntegrationAudit", Trail)
    seen_since = []
    monkeypatch.setattr(svc.integration_store, "approval_uses",
                        lambda since, client_id: seen_since.append(since) or list(uses))

    class Executions:
        def __init__(self, service):
            pass

        def list(self, client_id, limit):
            return list(states)
    monkeypatch.setattr(exec_mod, "ExecutionManager", Executions)
    return svc.integrations_activity(**kw), seen_since


def test_activity_reads_a_hundred_lines_by_default(svc, monkeypatch):
    activity(svc, monkeypatch, [])
    assert Trail.limits == [100]


def test_activity_shows_a_finished_call_once(svc, monkeypatch):
    rows = [{"at": "2026-11-20T10:00:02+00:00", "approval_id": "ap1", "outcome": "ok"},
            {"at": "2026-11-20T10:00:01+00:00", "approval_id": "ap1", "outcome": "started"},
            {"at": "2026-11-20T10:00:00+00:00", "approval_id": "ap2", "outcome": "started"},
            {"at": "2026-11-20T09:00:00+00:00", "approval_id": "", "outcome": "ok"}]
    got, _ = activity(svc, monkeypatch, rows)
    assert [(r["approval_id"], r["outcome"]) for r in got] == [("ap1", "ok"), ("ap2", "started"), ("", "ok")]


def test_activity_looks_for_unrecorded_approvals_from_its_oldest_line_when_the_page_is_full(svc, monkeypatch):
    rows = [{"at": "2026-11-20T10:00:00+00:00"}, {"at": "2026-11-20T09:00:00+00:00"}]
    _, since = activity(svc, monkeypatch, rows, limit=2)
    assert since == [1795165200.0]


def test_an_approval_with_no_line_shows_as_unfinished(svc, monkeypatch):
    use = {"id": "ap9", "used_at": 1795165200.0, "client_id": "c1", "client_name": "Code", "capability": "run",
           "permission": "EXECUTE_LOCAL_CODE", "arguments": "{}"}
    got, _ = activity(svc, monkeypatch, [], uses=[use])
    assert got[0]["outcome"] == "unfinished" and got[0]["seconds"] == 0
    assert got[0]["at"] == "2026-11-20T09:00:00.000+00:00"


def test_activity_shows_an_executions_state_where_it_has_one(svc, monkeypatch):
    rows = [{"at": "2026-11-20T10:00:01+00:00", "execution_id": "e1"},
            {"at": "2026-11-20T10:00:00+00:00", "execution_id": "e2"}]
    got, _ = activity(svc, monkeypatch, rows, states=[{"id": "e1", "state": "done"}])
    assert got[0]["execution_state"] == "done" and "execution_state" not in got[1]


def test_the_audit_check_names_its_folder(svc, monkeypatch):
    monkeypatch.setattr(audit_mod, "IntegrationAudit", Trail)
    assert svc.integrations_audit_check() == {"ok": True, "folder": "audit"}


def test_testing_a_stdio_tool_runs_the_stdio_test(svc, monkeypatch):
    client = svc.integrations_add_client("Code", "other-mcp")["client"]
    monkeypatch.setattr(app_service, "_test_stdio", lambda launcher, cid: {"ok": True, "client": cid})
    assert svc.integrations_test(client["id"]) == {"ok": True, "client": client["id"]}


def test_the_stdio_test_lists_the_tools_with_a_stand_in_client(tmp_path, monkeypatch):
    import mcp
    launcher = tmp_path / "launcher"
    launcher.write_text("")
    seen = []

    class Params:
        def __init__(self, command, args, env):
            seen.append((command, args))

    class Tools:
        tools = ["a", "b"]

    class Client:
        protocol_version = "2025-06-18"

        def __init__(self, params):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def list_tools(self):
            return Tools()
    monkeypatch.setattr(mcp, "StdioServerParameters", Params)
    monkeypatch.setattr(mcp, "Client", Client)
    got = app_service._test_stdio(launcher, "cl_1")
    assert got == {"ok": True, "tools": 2, "protocol": "2025-06-18",
                   "message": "Connected: 2 tools available to this tool."}
    if os.name == "nt":
        assert seen[0][1][-3:] == [str(launcher), "--client", "cl_1"]
    else:
        assert seen == [(str(launcher), ["--client", "cl_1"])]
