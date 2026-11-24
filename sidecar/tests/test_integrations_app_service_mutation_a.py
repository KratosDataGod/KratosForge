"""Settings > Integrations (integrations/app_service.py), mutation testing batch 378.

The other app tests cover each call's main path. These pin the limits and defaults (the local API's start-up
wait, its log's size cap, the ports allowed, how long a stop or a test waits), the organization's switch in
every place it is read, the activity list's filtering and order, and the stand-in MCP connection's answer.
The local API process, the audit trail and the MCP client are stand-ins.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import app_service, config, policy
from fabric_desktop.integrations import audit as audit_mod
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    return Service(root=tmp_path / "FabricDesktop")


def org_off(monkeypatch):
    real = policy.current
    monkeypatch.setattr(app_service.policy, "current", lambda: {**real(), "enabled": False})


class Running:
    returncode = None

    def __init__(self):
        self.waited = []

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        self.waited.append(timeout)


class Exited(Running):
    returncode = 1

    def poll(self):
        return 1


# ---- the local API's start-up, stop and log


def test_the_local_api_is_waited_for_fifteen_seconds(svc, monkeypatch):
    clock = iter(range(1000))
    slept = []
    monkeypatch.setattr(app_service, "time", SimpleNamespace(monotonic=lambda: float(next(clock)),
                                                             sleep=lambda s: slept.append(s)))
    monkeypatch.setattr(app_service.subprocess, "Popen", lambda *a, **k: Running())
    svc._http_start()
    assert slept == [0.1] * 14  # checked at 1 s to 14 s, then the deadline at 15 s


def test_no_wait_at_all_doesnt_look_at_the_process(svc, monkeypatch):
    monkeypatch.setattr(app_service, "time", SimpleNamespace(monotonic=lambda: 100.0, sleep=lambda s: None))
    monkeypatch.setattr(app_service, "HTTP_START_SECONDS", 0)
    monkeypatch.setattr(app_service.subprocess, "Popen", lambda *a, **k: Exited())
    svc._http_start()  # the deadline is now: it returns without asking whether the process failed


def test_a_stop_waits_ten_seconds(svc):
    proc = Running()
    svc._http_proc = proc
    svc._http_stop()
    assert proc.waited == [10] and svc._http_proc is None


def test_the_log_is_rolled_past_five_mebibytes_not_at_them(tmp_path):
    assert app_service.HTTP_LOG_CAP == 5 * 1024 * 1024
    log = tmp_path / "http.log"
    log.write_bytes(b"x" * app_service.HTTP_LOG_CAP)
    app_service._roll_log(log)
    assert log.exists() and not (tmp_path / "http.prev.log").exists()
    log.write_bytes(b"x" * (app_service.HTTP_LOG_CAP + 1))
    app_service._roll_log(log)
    assert not log.exists() and (tmp_path / "http.prev.log").stat().st_size == app_service.HTTP_LOG_CAP + 1


def test_a_log_at_its_cap_is_kept(tmp_path):
    log = tmp_path / "http.log"
    log.write_bytes(b"x" * 10)
    app_service._roll_log(log, cap=10)
    assert log.exists()


def test_the_last_line_is_read_whole_from_the_start_of_a_short_log(tmp_path):
    log = tmp_path / "http.log"
    log.write_bytes(b"boom")
    assert app_service._last_line(log) == "boom"


def test_the_last_line_is_cut_to_500_characters_from_the_last_4096_bytes(tmp_path):
    log = tmp_path / "http.log"
    text = "".join(chr(ord("a") + i % 26) + str(i % 10) for i in range(2500))  # 5000 characters, no newline
    log.write_bytes(("first\n" + text).encode())
    got = app_service._last_line(log)
    assert got == text[-4096:][:500] and len(got) == 500


# ---- the organization's switch


def test_the_organization_switch_turns_off_the_status_the_api_and_the_prompt(svc, monkeypatch):
    svc.integration_store.set_setting("enabled", True)
    svc.integration_store.set_setting("http_enabled", True)
    assert svc.integrations_status()["enabled"] is True and svc._http_state()["allowed"] is True
    assert svc.integrations_pending()["enabled"] is True
    org_off(monkeypatch)
    status = svc.integrations_status()
    assert status["enabled"] is False and status["switch"] is True and status["set_by_organization"] is True
    assert svc._http_state()["allowed"] is False
    assert svc.integrations_pending() == {"enabled": False, "pending": []}


def test_the_launcher_is_written_only_while_integrations_are_on(svc):
    launcher = config.launcher_path(svc.root)
    svc.integrations_status()
    assert not launcher.exists()
    svc.integration_store.set_setting("enabled", True)
    svc.integrations_status()
    assert launcher.exists()


@pytest.mark.parametrize("port", [1024, 65535])
def test_the_ports_allowed_include_both_ends(svc, monkeypatch, port):
    monkeypatch.setattr(svc, "_http_stop", lambda: None)
    svc.integrations_set_http(False, port=port)
    assert svc.integration_store.settings()["http_port"] == port


@pytest.mark.parametrize("port", [1023, 65536])
def test_a_port_past_either_end_is_refused(svc, port):
    with pytest.raises(ValueError, match="port is a number from 1024 to 65535"):
        svc.integrations_set_http(False, port=port)


# ---- tools and approvals


def test_a_tools_workspaces_are_kept(svc):
    client = svc.integrations_add_client("Claude", "claude-code", workspaces=["ws-1"])["client"]
    assert svc.integration_store.client(client["id"])["workspaces"] == ["ws-1"]


def test_approvals_and_activity_default_limits(svc, monkeypatch):
    asked = []
    monkeypatch.setattr(svc.integration_store, "approvals", lambda state, limit: asked.append((state, limit)) or [])
    svc.integrations_approvals()
    monkeypatch.setattr(audit_mod.IntegrationAudit, "recent", lambda self, limit, client_id: asked.append(limit) or [])
    svc.integrations_activity()
    assert asked == [("pending", 50), 100]


def test_the_audit_check_names_its_folder(svc):
    got = svc.integrations_audit_check()
    assert got["folder"] == str(audit_mod.IntegrationAudit(svc.integration_store).folder)


def at(n: int) -> str:
    return datetime.fromtimestamp(1_800_000_000 + n, timezone.utc).isoformat(timespec="milliseconds")


def test_activity_drops_a_started_line_only_when_its_outcome_is_there(svc, monkeypatch):
    rows = [{"at": at(4), "outcome": "started"}, {"at": at(3), "approval_id": "a", "outcome": "ok"},
            {"at": at(2), "approval_id": "a", "outcome": "started"},
            {"at": at(1), "approval_id": "b", "outcome": "started"}]
    monkeypatch.setattr(audit_mod.IntegrationAudit, "recent", lambda self, limit, client_id: [dict(r) for r in rows])
    monkeypatch.setattr(svc.integration_store, "approval_uses", lambda oldest, client_id: [])
    got = svc.integrations_activity()
    assert [(r["at"], r.get("approval_id"), r["outcome"]) for r in got] == [
        (at(4), None, "started"), (at(3), "a", "ok"), (at(1), "b", "started")]


def test_activity_looks_for_unfinished_approvals_back_to_the_oldest_line_of_a_full_page(svc, monkeypatch):
    rows = [{"at": at(9), "outcome": "ok"}, {"at": at(5), "outcome": "ok"}]
    monkeypatch.setattr(audit_mod.IntegrationAudit, "recent", lambda self, limit, client_id: [dict(r) for r in rows])
    asked = []
    monkeypatch.setattr(svc.integration_store, "approval_uses", lambda oldest, client_id: asked.append(oldest) or [])
    svc.integrations_activity(limit=2)
    svc.integrations_activity(limit=3)
    assert asked == [1_800_000_005.0, 0.0]


def test_unfinished_approvals_take_no_time_and_sort_newest_first(svc, monkeypatch):
    def use(i, n):
        return {"id": i, "used_at": 1_800_000_000.0 + n, "client_id": "c1", "client_name": "Claude",
                "capability": "run", "permission": "EXECUTE_LOCAL_CODE", "arguments": "{}"}
    monkeypatch.setattr(svc.integration_store, "approval_uses", lambda oldest, client_id: [use("old", 1), use("new", 2)])
    monkeypatch.setattr(audit_mod.IntegrationAudit, "approvals_recorded", lambda self, ids: set())
    got = svc.integrations_activity()
    assert [r["approval_id"] for r in got] == ["new", "old"]
    assert {r["seconds"] for r in got} == {0}


# ---- Test connection


def test_an_http_tools_test_waits_five_seconds(svc, monkeypatch):
    import urllib.request
    client = svc.integrations_add_client("Cursor", "cursor", transport="http")["client"]
    monkeypatch.setattr(svc, "_http_state", lambda: {"running": True, "url": "http://127.0.0.1:8765"})
    waits = []

    def urlopen(url, timeout):
        waits.append(timeout)
        raise OSError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    svc.integrations_test(client["id"])
    assert waits == [5]


class FakeClient:
    def __init__(self, params):
        self.params = params

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=["a", "b"])


def test_a_stdio_tools_test_waits_a_minute_and_counts_the_tools(tmp_path, monkeypatch):
    import anyio
    import mcp
    launcher = tmp_path / "launcher.cmd"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(mcp, "Client", FakeClient)
    waits = []
    real = anyio.fail_after
    monkeypatch.setattr(anyio, "fail_after", lambda t: waits.append(t) or real(t))
    got = app_service._test_stdio(launcher, "cl_1")
    assert waits == [60]
    assert got == {"ok": True, "tools": 2, "protocol": "", "message": "Connected: 2 tools available to this tool."}
    FakeClient.protocol_version = "2025-06-18"
    try:
        assert app_service._test_stdio(launcher, "cl_1")["protocol"] == "2025-06-18"
    finally:
        del FakeClient.protocol_version
