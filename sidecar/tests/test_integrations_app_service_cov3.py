"""Settings > Integrations (integrations/app_service.py): the remaining edges. A local API that doesn't
say it's listening in time, a port set to what it already is, a tool renamed without touching its
permissions, approvals recorded on another page of the trail, Test connection for an HTTP tool, and a log
that can't be rolled. The local API process and its answers are stand-ins."""

from __future__ import annotations

import pytest

from fabric_desktop.integrations import app_service
from fabric_desktop.integrations import audit as audit_mod
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    return Service(root=tmp_path / "FabricDesktop")


class Proc:
    returncode = None

    def poll(self):
        return None


def test_a_local_api_that_doesnt_say_it_listens_in_time_is_left_starting(svc, monkeypatch):
    started = []
    monkeypatch.setattr(app_service.subprocess, "Popen", lambda *a, **k: started.append(a[0]) or Proc())
    monkeypatch.setattr(app_service, "HTTP_START_SECONDS", 0)
    svc._http_start()  # returns without waiting
    assert len(started) == 1 and started[0][1:5] == ["-m", "fabric_desktop.cli", "integrations", "serve"]
    assert svc._http_state()["running"] is True and svc._http_state()["listening"] is False
    svc._http_start()  # already running: nothing more is started
    assert len(started) == 1


def test_setting_the_port_it_already_has_doesnt_stop_the_api(svc, monkeypatch):
    stopped = []
    monkeypatch.setattr(svc, "_http_stop", lambda: stopped.append(1))
    port = svc.integration_store.settings()["http_port"]
    svc.integrations_set_http(False, port=port)
    assert stopped == [] and svc.integration_store.settings()["http_port"] == port


def test_renaming_a_tool_leaves_its_permissions(svc):
    client = svc.integrations_add_client("Claude Code", "claude-code", grants=["READ_DATA"], auto=["READ_DATA"])["client"]
    out = svc.integrations_update_client(client["id"], {"name": "Claude"})
    assert out["name"] == "Claude" and out["auto"] == ["READ_DATA"]
    assert sorted(out["grants"]) == ["READ_DATA", "READ_METADATA"]


def test_an_approval_recorded_on_another_page_of_the_trail_isnt_shown_as_unfinished(svc, monkeypatch):
    use = {"id": "ap1", "used_at": 1_800_000_000.0, "client_id": "c1", "client_name": "Claude", "capability": "run",
           "permission": "EXECUTE_LOCAL_CODE", "arguments": "{}"}
    monkeypatch.setattr(svc.integration_store, "approval_uses", lambda oldest, client_id: [use])
    monkeypatch.setattr(audit_mod.IntegrationAudit, "approvals_recorded", lambda self, ids: set(ids))
    assert svc.integrations_activity() == []
    monkeypatch.setattr(audit_mod.IntegrationAudit, "approvals_recorded", lambda self, ids: set())
    [row] = svc.integrations_activity()
    assert row["outcome"] == "unfinished" and row["approval_id"] == "ap1"


class Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("status, ok, says", [(200, True, "The local API answers at http://127.0.0.1:"),
                                              (500, False, "The local API answered with an error.")])
def test_an_http_tool_s_test_asks_the_local_api_for_its_health(svc, monkeypatch, status, ok, says):
    import urllib.request
    client = svc.integrations_add_client("Cursor", "cursor", transport="http")["client"]
    monkeypatch.setattr(svc, "_http_state", lambda: {"running": True, "url": "http://127.0.0.1:8765"})
    asked = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: asked.append(url) or Answer(status))
    out = svc.integrations_test(client["id"])
    assert out["ok"] is ok and out["message"].startswith(says)
    assert asked == ["http://127.0.0.1:8765/api/v1/health"]


def test_a_log_another_process_holds_isnt_rolled_and_says_so(tmp_path, monkeypatch, capsys):
    log = tmp_path / "http.log"
    log.write_text("x" * 20, encoding="utf-8")

    def held(src, dst):
        raise PermissionError("in use by another process")
    monkeypatch.setattr(app_service.jsonfile, "replace", held)
    app_service._roll_log(log, cap=10)
    assert log.exists() and "integrations: couldn't roll http.log: in use by another process" in capsys.readouterr().err
