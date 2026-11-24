"""Settings > Integrations (integrations/app_service.py), pinned so a changed number, a flipped check
or a dropped answer fails a test: how long the local API may take to start, the log's size limit,
the port check, what each call hands back, when the launcher is written, the URL only an HTTP tool
is shown, stopping with no local API, and the pending list while integrations are off. The local
API's process and clock are module-local stand-ins."""

from __future__ import annotations

import inspect
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import app_service
from fabric_desktop.integrations.config import launcher_path
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    return Service(root=tmp_path / "FabricDesktop")


def test_the_local_api_gets_15_seconds_to_say_it_listens(svc, monkeypatch):
    polls = []
    proc = SimpleNamespace(poll=lambda: polls.append(1), returncode=None)
    monkeypatch.setattr(app_service, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: proc,
                                                                   DEVNULL=subprocess.DEVNULL))
    clock = iter([0.0, 14.9, 15.0, 15.5, 99.0])
    monkeypatch.setattr(app_service, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda s: None))
    (svc.root / "integrations").mkdir(parents=True, exist_ok=True)  # there already: started all the same
    svc._http_start()
    assert len(polls) == 1  # polled at 14.9 s, given up at 15 s


def test_the_log_is_rolled_only_once_past_5_mib(tmp_path):
    log = tmp_path / "http.log"
    with open(log, "wb") as f:
        f.truncate(5 * 1024 * 1024)
    app_service._roll_log(log)
    assert log.exists() and not (tmp_path / "http.prev.log").exists()
    with open(log, "ab") as f:
        f.write(b"x")
    app_service._roll_log(log)
    assert not log.exists() and (tmp_path / "http.prev.log").stat().st_size == 5 * 1024 * 1024 + 1


def test_a_port_outside_1024_to_65535_is_refused_and_leaving_it_out_keeps_it(svc):
    for port in (80, 1023, 65536, "5005"):
        with pytest.raises(ValueError, match="1024 to 65535"):
            svc.integrations_set_http(False, port=port)
    for port in (1024, 65535):
        assert svc.integrations_set_http(False, port=port)["http"]["port"] == port
    out = svc.integrations_set_http(False, port=5005)
    assert out["http"]["port"] == 5005 and out["http"]["switch"] is False
    assert svc.integrations_set_http(False)["http"]["port"] == 5005


def test_a_new_token_comes_back_with_its_tool(svc):
    client = svc.integrations_add_client("Tool", "other-mcp", transport="http")["client"]
    out = svc.integrations_new_token(client["id"])
    assert out["client"]["id"] == client["id"] and isinstance(out["token"], str) and out["token"]


def test_the_launcher_is_written_only_while_integrations_are_on(svc):
    svc.integrations_status()
    assert not launcher_path(svc.root).exists()
    svc.integrations_set_enabled(True)
    assert launcher_path(svc.root).exists()


def test_only_an_http_tool_is_shown_the_local_api_address(svc):
    http = svc.integrations_add_client("Web", "other-mcp", transport="http")["client"]
    stdio = svc.integrations_add_client("Pipe", "other-mcp")["client"]
    assert "http" in http["config"] and "http" not in stdio["config"]


def test_stopping_with_no_local_api_is_fine(svc):
    svc._http_stop()
    assert svc._http_proc is None


def test_approvals_are_asked_for_50_by_default(svc, monkeypatch):
    asked = []
    monkeypatch.setattr(svc.integration_store, "approvals", lambda state, limit: asked.append((state, limit)) or [])
    assert svc.integrations_approvals() == []
    assert asked == [("pending", 50)]


def test_nothing_is_pending_while_integrations_are_off(svc):
    assert svc.integrations_pending() == {"enabled": False, "pending": []}
    svc.integrations_set_enabled(True)
    assert svc.integrations_pending() == {"enabled": True, "pending": []}


def test_the_audit_check_names_its_folder(svc):
    out = svc.integrations_audit_check()
    assert out["ok"] is True and out["problems"] == [] and out["folder"] == str(svc.root / "integrations" / "audit")


def test_testing_a_stdio_tool_hands_back_what_the_launcher_answered(svc, monkeypatch):
    client = svc.integrations_add_client("Pipe", "other-mcp")["client"]
    monkeypatch.setattr(app_service, "_test_stdio", lambda launcher, client_id: {"ok": True, "id": client_id})
    assert svc.integrations_test(client["id"]) == {"ok": True, "id": client["id"]}


def test_a_stdio_test_waits_60_seconds_at_most():
    assert inspect.signature(app_service._test_stdio).parameters["timeout"].default == 60


def test_the_last_line_of_a_short_log_is_read_whole(tmp_path):
    (tmp_path / "http.log").write_bytes(b"Port 5005 is in use.")
    assert app_service._last_line(tmp_path / "http.log") == "Port 5005 is in use."
    (tmp_path / "http.log").write_bytes(b"A" + b"B" * 4095)  # its last 4096 bytes are the whole of it
    assert app_service._last_line(tmp_path / "http.log") == "A" + "B" * 499


def test_activity_shows_100_calls_by_default(svc, monkeypatch):
    from fabric_desktop.integrations import audit
    asked = []
    monkeypatch.setattr(audit.IntegrationAudit, "recent", lambda self, limit, client_id: asked.append(limit) or [])
    assert svc.integrations_activity() == [] and asked == [100]


def with_policy(monkeypatch, **changes):
    rules = app_service.policy.current() | changes
    monkeypatch.setattr(app_service, "policy", SimpleNamespace(current=lambda: rules))


def test_the_local_api_isnt_allowed_when_policy_turns_it_off(svc, monkeypatch):
    with_policy(monkeypatch, http=False, enabled=True)
    assert svc._http_state()["allowed"] is False
    with_policy(monkeypatch, http=True, enabled=True)
    assert svc._http_state()["allowed"] is True


def test_integrations_turned_on_by_policy_and_by_the_person_are_on(svc, monkeypatch):
    svc.integrations_set_enabled(True)
    with_policy(monkeypatch, enabled=True)
    assert svc.integrations_status()["enabled"] is True
    assert svc.integrations_pending()["enabled"] is True


def fake_trail(monkeypatch, svc, rows, uses=()):
    from fabric_desktop.integrations import audit
    since = []
    monkeypatch.setattr(audit.IntegrationAudit, "recent", lambda self, limit, client_id: [dict(r) for r in rows])
    monkeypatch.setattr(audit.IntegrationAudit, "approvals_recorded", lambda self, ids: set())
    monkeypatch.setattr(svc.integration_store, "approval_uses",
                        lambda oldest, client_id: since.append(oldest) or [dict(u) for u in uses])
    return since


def test_a_started_line_drops_out_only_when_its_approval_has_an_outcome(svc, monkeypatch):
    rows = [{"at": "2026-01-01T00:00:04+00:00", "approval_id": "a", "outcome": "ok"},
            {"at": "2026-01-01T00:00:03+00:00", "approval_id": "a", "outcome": "started"},
            {"at": "2026-01-01T00:00:02+00:00", "approval_id": "b", "outcome": "started"},
            {"at": "2026-01-01T00:00:01+00:00", "approval_id": "", "outcome": "started"}]
    fake_trail(monkeypatch, svc, rows)
    got = svc.integrations_activity()
    assert [(r["approval_id"], r["outcome"]) for r in got] == [("a", "ok"), ("b", "started"), ("", "started")]


def test_a_full_page_looks_for_approvals_back_to_its_oldest_line(svc, monkeypatch):
    rows = [{"at": "2026-01-01T00:00:09+00:00", "outcome": "ok"}, {"at": "2026-01-01T00:00:01+00:00", "outcome": "ok"}]
    since = fake_trail(monkeypatch, svc, rows)
    svc.integrations_activity(limit=2)
    svc.integrations_activity(limit=3)
    assert since == [1767225601.0, 0.0]


def test_an_approval_used_with_no_line_shows_as_unfinished_and_took_no_time(svc, monkeypatch):
    use = {"id": "u1", "used_at": 1767225600.0, "client_id": "c", "client_name": "Tool", "capability": "run",
           "permission": "EXECUTE_LOCAL_CODE", "arguments": None}
    fake_trail(monkeypatch, svc, [], uses=[use])
    (row,) = svc.integrations_activity()
    assert row["outcome"] == "unfinished" and row["seconds"] == 0 and row["approval_id"] == "u1"


def test_a_local_api_that_wont_stop_in_10_seconds_is_killed(svc):
    seen = []

    def wait(timeout):
        seen.append(timeout)
        raise subprocess.TimeoutExpired("api", timeout)

    svc._http_proc = SimpleNamespace(poll=lambda: None, terminate=lambda: seen.append("terminate"), wait=wait,
                                     kill=lambda: seen.append("kill"))
    svc._http_stop()
    assert seen == ["terminate", 10, "kill"] and svc._http_proc is None


def test_testing_an_http_tool_waits_5_seconds_for_the_local_api(svc, monkeypatch):
    import urllib.request
    client = svc.integrations_add_client("Web", "other-mcp", transport="http")["client"]
    monkeypatch.setattr(svc, "_http_state", lambda: {"running": True, "url": "http://127.0.0.1:9"})
    asked = []

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: asked.append((url, timeout)) or Answer())
    assert svc.integrations_test(client["id"])["ok"] is True
    assert asked == [("http://127.0.0.1:9/api/v1/health", 5)]


@pytest.mark.parametrize("version, shown", [("2025-06-18", "2025-06-18"), (None, "")])
def test_a_stdio_test_counts_the_tools_and_names_the_protocol(tmp_path, monkeypatch, version, shown):
    import mcp

    class Client:
        def __init__(self, params):
            self.protocol_version = version

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def list_tools(self):
            return SimpleNamespace(tools=["a", "b"])

    monkeypatch.setattr(mcp, "Client", Client)
    launcher = tmp_path / "launcher"
    launcher.write_text("")
    out = app_service._test_stdio(launcher, "c1")
    assert out == {"ok": True, "tools": 2, "protocol": shown, "message": "Connected: 2 tools available to this tool."}


def test_the_local_api_cant_be_switched_on_when_policy_doesnt_allow_it(svc, monkeypatch):
    with_policy(monkeypatch, http=False)
    with pytest.raises(PermissionError, match="doesn't allow the local API"):
        svc.integrations_set_http(True)
    assert svc.integration_store.settings()["http_enabled"] is False
