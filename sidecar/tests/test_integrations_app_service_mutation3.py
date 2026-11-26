"""integrations/app_service.py: the mutants that test_integrations_app_service_mutation.py and the module's other
tests leave alive, which are its waits. The local API gets 15 seconds to say it listens and 10 to stop before
it's killed, testing an HTTP tool waits 5 seconds for it, and a stdio test waits 60 seconds at most. The local
API's process and clock are stand-ins."""

from __future__ import annotations

import inspect
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import app_service
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


def test_a_stdio_test_waits_60_seconds_at_most():
    assert inspect.signature(app_service._test_stdio).parameters["timeout"].default == 60


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




