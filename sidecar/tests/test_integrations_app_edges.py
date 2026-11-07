"""Settings > Integrations (integrations/app_service.py): the edges test_integrations_app.py leaves out.
What a tool may be given, changing a connected tool, the local API's process starting, failing and
stopping, and Test connection when something isn't there. The local API is a stand-in process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop.integrations import app_service
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    return Service(root=tmp_path / "FabricDesktop")


# --------------------------------------------------------------------------- what a tool may be given

@pytest.mark.parametrize("kw, message", [
    ({"name": "  "}, "Give the tool a name"),
    ({"kind": "toaster"}, "kind is one of"),
    ({"transport": "carrier-pigeon"}, "transport is stdio or http"),
    ({"grants": ["READ_EVERYTHING"]}, "grants: unknown permission READ_EVERYTHING"),
    ({"grants": ["EXECUTE_LOCAL_CODE"], "auto": ["EXECUTE_LOCAL_CODE"]}, "Running code always asks first"),
])
def test_a_tool_cant_be_connected_with_a_bad_name_kind_transport_or_permission(svc, kw, message):
    args = {"name": "Claude Code", "kind": "claude-code"} | kw
    with pytest.raises(ValueError, match=message):
        svc.integrations_add_client(**args)
    assert svc.integration_store.clients() == []


def test_a_connected_tools_permissions_change_and_read_metadata_always_stays(svc):
    client = svc.integrations_add_client("Claude Code", "claude-code", grants=["READ_DATA"], auto=["READ_DATA"])["client"]
    out = svc.integrations_update_client(client["id"], {"grants": ["WRITE_LOCAL_FILES"]})
    assert sorted(out["grants"]) == ["READ_METADATA", "WRITE_LOCAL_FILES"]
    assert out["auto"] == []  # READ_DATA was taken away, so it no longer runs without asking
    out = svc.integrations_update_client(client["id"], {"auto": ["WRITE_LOCAL_FILES", "READ_DATA"]})
    assert out["auto"] == ["WRITE_LOCAL_FILES"]  # only what it was given
    with pytest.raises(ValueError, match="Running code always asks first"):
        svc.integrations_update_client(client["id"], {"grants": ["EXECUTE_LOCAL_CODE"], "auto": ["EXECUTE_LOCAL_CODE"]})


def test_a_tool_that_was_disconnected_cant_be_changed_or_tested(svc):
    client = svc.integrations_add_client("Claude Code", "claude-code")["client"]
    svc.integrations_revoke_client(client["id"])
    with pytest.raises(LookupError, match="isn't connected"):
        svc.integrations_update_client(client["id"], {"grants": []})
    with pytest.raises(LookupError, match="isn't connected"):
        svc.integrations_test(client["id"])
    with pytest.raises(LookupError, match="isn't connected"):
        svc.integrations_update_client("nope", {})


# --------------------------------------------------------------------------- the local API's process

def stand_in(monkeypatch, script):
    """The local API's process runs ``script`` instead."""
    real = subprocess.Popen

    def popen(args, **kw):
        return real([sys.executable, "-c", script], **kw)
    monkeypatch.setattr(app_service.subprocess, "Popen", popen)


def test_a_local_api_that_stops_at_once_says_why_from_its_log(svc, monkeypatch):
    stand_in(monkeypatch, "import sys; sys.stderr.write('starting\\nport 47600 is in use\\n'); sys.exit(2)")
    with pytest.raises(RuntimeError, match="The local API didn't start: port 47600 is in use"):
        svc._http_start()


def test_a_running_local_api_isnt_started_twice_and_one_that_wont_stop_is_killed(svc, monkeypatch):
    stand_in(monkeypatch, "import json, sys, time, pathlib; "
                          "pathlib.Path('integrations/http.json').write_text('{}'); "
                          "time.sleep(60)")
    svc._http_start()
    proc = svc._http_proc
    assert (svc.root / "integrations" / "http.json").exists()
    svc._http_start()  # already running: the same process
    assert svc._http_proc is proc

    real_wait = proc.wait

    def stubborn(timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired("api", timeout)
        return real_wait()
    monkeypatch.setattr(proc, "wait", stubborn)
    killed = []
    real_kill = proc.kill
    monkeypatch.setattr(proc, "kill", lambda: killed.append(1) or real_kill())
    svc._http_stop()
    assert killed == [1] and svc._http_proc is None
    assert not (svc.root / "integrations" / "http.json").exists()
    real_wait()


# --------------------------------------------------------------------------- Test connection

def test_testing_an_http_tool_while_the_local_api_is_off_says_to_turn_it_on(svc):
    client = svc.integrations_add_client("Script", "other-mcp", transport="http")["client"]
    assert svc.integrations_test(client["id"]) == {"ok": False, "message": "The local HTTP API is off. Turn it on first."}


def test_testing_an_http_tool_whose_local_api_doesnt_answer_says_so(svc, monkeypatch):
    client = svc.integrations_add_client("Script", "other-mcp", transport="http")["client"]
    monkeypatch.setattr(type(svc), "_http_state", lambda self: {"running": True, "url": "http://127.0.0.1:1"})
    out = svc.integrations_test(client["id"])
    assert out["ok"] is False and out["message"].startswith("The local API didn't answer")


def test_testing_a_stdio_tool_without_its_launcher_or_with_one_that_fails(tmp_path):
    assert app_service._test_stdio(tmp_path / "missing.cmd", "cl_1") == {
        "ok": False, "message": "The launcher isn't there yet. Turn integrations on first."}
    if sys.platform == "win32":
        broken = tmp_path / "broken.cmd"
        broken.write_text("@exit /b 3\r\n")
    else:
        broken = tmp_path / "broken"
        broken.write_text("#!/bin/sh\nexit 3\n")
        broken.chmod(0o755)
    out = app_service._test_stdio(Path(broken), "cl_1", timeout=20)
    assert out["ok"] is False and out["message"].startswith("The MCP server didn't start:")
