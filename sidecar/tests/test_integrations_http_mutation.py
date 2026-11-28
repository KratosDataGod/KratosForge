"""Mutation-testing follow-up for the local HTTP API (integrations/http_server.py): the exact size,
rate and concurrency limits, the status each error code gets, serve() refusing when either the
organization or the person has the API off, and the Windows parent check. Nothing here listens on
a socket: requests go in-process through the guard, and serve() runs with a stand-in server."""

import ctypes
import json
import os
import subprocess
import sys

import anyio
import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, peer
from fabric_desktop.integrations.core import IntegrationError
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_http import IN_PROCESS, this_account

PORT = 47615


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


# ------------------------------------------------------------------ errors


@pytest.mark.parametrize("code,status", [
    ("invalid_input", 400), ("not_found", 404), ("permission_denied", 403), ("approval_denied", 403),
    ("approval_required", 428), ("conflict", 409), ("timeout", 504), ("cancelled", 409), ("unavailable", 503),
    ("integrations_disabled", 503), ("too_large", 413), ("rate_limited", 429), ("upstream", 502), ("failed", 422),
    ("internal", 500),
])
def test_each_error_code_gets_its_status_and_a_not_ok_body(code, status):
    r = http_server.error_response(IntegrationError(code, "Nope."))
    assert r.status_code == status
    assert json.loads(r.body) == {"ok": False, "error": {"code": code, "message": "Nope.", "retryable": False}}


def test_a_status_given_outright_wins():
    assert http_server.error_response(IntegrationError("timeout", "x"), status=401).status_code == 401


# ------------------------------------------------------------------ limits


def test_the_default_rate_limit_allows_a_burst_of_thirty():
    now = [0.0]
    lim = http_server.RateLimiter(clock=lambda: now[0])
    assert [lim.allow("a") for _ in range(31)] == [True] * 30 + [False]
    now[0] += 0.2  # five a second: one more after a fifth of a second
    assert lim.allow("a") and not lim.allow("a")


class Inner:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await JSONResponse({"ok": True})(scope, receive, send)


def _through(guard, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/health", "client": ("127.0.0.1", 50000),
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    anyio.run(guard, scope, receive, send)
    return sent[0]["status"]


def test_a_content_length_of_exactly_one_mib_passes_and_one_byte_more_doesnt(store):
    inner = Inner()
    guard = http_server.Guard(inner, store, PORT, same_user=lambda client_port, port: True)
    host = {"host": f"127.0.0.1:{PORT}"}
    assert _through(guard, host | {"content-length": str(1024 * 1024)}) == 200
    assert _through(guard, host | {"content-length": str(1024 * 1024 + 1)}) == 413
    assert inner.calls == 1


def test_refusals_say_why(store):
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    guard = http_server.Guard(Inner(), store, PORT, limiter=http_server.RateLimiter(rate=0.0001, burst=1),
                              same_user=lambda client_port, port: True)
    c = TestClient(guard, base_url=f"http://127.0.0.1:{PORT}", client=IN_PROCESS)  # no lifespan: the stand-in app has none
    big = c.post("/api/v1/health", content=b"x" * (1024 * 1024 + 1))
    assert big.status_code == 413 and big.json()["error"]["message"] == "Requests are limited to 1024 KB."
    none = c.get("/api/v1/capabilities")
    assert none.json()["error"]["message"] == "Send Authorization: Bearer <token>."
    wrong = c.get("/api/v1/capabilities", headers={"Authorization": "Bearer fdt_wrong"})
    assert wrong.json()["error"]["message"] == "A valid token is required."
    ok = c.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    limited = c.get("/api/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "1"
    assert limited.json()["error"] == {"code": "rate_limited", "message": "Too many requests.", "retryable": True,
                                       "hint": "Wait a second and try again."}


def test_a_streamed_body_of_exactly_one_mib_is_read_and_one_byte_more_is_refused():
    def feeding(chunks):
        it = iter(chunks)

        async def receive():
            return {"type": "http.request", "body": next(it), "more_body": True}
        return http_server._limit_body(receive)

    half = b"x" * (http_server.MAX_BODY_BYTES // 2)

    async def read(limited, n):
        for _ in range(n):
            await limited()

    anyio.run(read, feeding([half, half]), 2)
    with pytest.raises(IntegrationError, match="limited to 1024 KB"):
        anyio.run(read, feeding([half, half, b"x"]), 3)


def _app(store, monkeypatch, seconds=0.5, value=None):
    reg = Registry()
    reg.register(Capability("ext_echo", "Echo", "Echoes.", schema(), schema(extra=True), "READ_METADATA",
                            lambda s, c: {}, needs_service=False))

    def invoke(service, cap_id, arguments, ctx, finish=None):
        ctx.seconds = seconds
        return finish(value) if finish is not None else value

    monkeypatch.setattr(reg, "invoke", invoke)
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    app = http_server.build_app(reg, store, PORT, same_user=this_account)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=IN_PROCESS), {"Authorization": f"Bearer {token}"}


def test_a_result_of_exactly_the_limit_is_returned_and_one_byte_more_isnt(store, monkeypatch):
    frame = len(json.dumps({"ok": True, "capability": "ext_echo", "seconds": 0.5, "result": {"t": ""}}))
    fits = "x" * (http_server.MAX_RESULT_BYTES - frame)
    client, auth = _app(store, monkeypatch, value={"t": fits})
    with client as c:
        r = c.post("/api/v1/capabilities/ext_echo/invoke", headers=auth)
    assert r.status_code == 200 and len(r.content) == 1_000_000
    client, auth = _app(store, monkeypatch, value={"t": fits + "x"})
    with client as c:
        r = c.post("/api/v1/capabilities/ext_echo/invoke", headers=auth)
    assert r.status_code == 413 and r.json()["error"]["message"] == "The result is bigger than the API returns."


def test_four_calls_may_run_at_once(store, monkeypatch):
    sizes = []
    real = anyio.Semaphore

    def counting(value, *a, **kw):
        sizes.append(value)
        return real(value, *a, **kw)

    monkeypatch.setattr(http_server.anyio, "Semaphore", counting)
    http_server.build_app(Registry(), store, PORT)
    assert sizes[0] == 4


def test_the_server_settings_keep_it_on_loopback_with_its_deadlines():
    cfg = http_server.server_config(Inner(), PORT)
    assert cfg.host == "127.0.0.1" and cfg.port == PORT
    assert cfg.timeout_keep_alive == 5 and cfg.limit_concurrency == 33 and cfg.server_header is False
    assert cfg.access_log is False and cfg.timeout_graceful_shutdown == 10 and cfg.log_level == "warning"


def test_the_openapi_document_describes_errors_bodies_and_resource_reads():
    reg = Registry()
    reg.register(Capability("ext_echo", "Echo", "Echoes.", schema(), schema(extra=True), "READ_METADATA",
                            lambda s, c: {}, needs_service=False))
    doc = http_server.openapi(reg, PORT)
    responses = doc["paths"]["/api/v1/capabilities"]["get"]["responses"]
    assert responses["503"]["description"] == "Unavailable or turned off"
    assert responses["404"]["content"]["application/json"]["schema"]["properties"]["ok"] == {"const": False}
    uri = doc["paths"]["/api/v1/resources/read"]["get"]["parameters"][0]
    assert uri["required"] is True and uri["schema"] == {"type": "string", "maxLength": 2000}
    body = doc["paths"]["/api/v1/capabilities/ext_echo/invoke"]["post"]["requestBody"]
    assert body["required"] is False
    assert body["content"]["application/json"]["schema"]["additionalProperties"] is False


def test_resources_the_caller_cant_use_are_not_listed(store, monkeypatch):
    reg = Registry()
    reg.register(Capability("ext_open", "Open", "Readable.", schema(), schema(extra=True), "READ_METADATA",
                            lambda s, c: {}, needs_service=False, resource="fabric-desktop://open"))
    reg.register(Capability("ext_shut", "Shut", "Switched off.", schema(), schema(extra=True), "READ_METADATA",
                            lambda s, c: {}, needs_service=False, status="unavailable", reason="Off here.",
                            resource="fabric-desktop://shut"))
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    with TestClient(http_server.build_app(reg, store, PORT, same_user=this_account),
                    base_url=f"http://127.0.0.1:{PORT}", client=IN_PROCESS) as c:
        rows = c.get("/api/v1/resources", headers={"Authorization": f"Bearer {token}"}).json()["resources"]
    assert [r["uri"] for r in rows] == ["fabric-desktop://open"]


# ------------------------------------------------------------------ serve


class Started(Exception):
    pass


@pytest.fixture
def serving(tmp_path, monkeypatch):
    root = tmp_path / "data"
    s = IntegrationStore.at(root)
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: root)
    monkeypatch.setattr(peer, "checked", lambda: False)

    def must_not_start():
        raise Started("serve went past its switches")
    monkeypatch.setattr("fabric_desktop.integrations.registry.default_registry", must_not_start)
    return s


def _policy(tmp_path, data):
    folder = tmp_path / "programdata" / "FabricDesktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "policy.json").write_text(json.dumps(data))


@pytest.mark.parametrize("enabled,http_enabled", [(True, False), (False, True)])
def test_serve_stays_off_unless_the_person_turned_on_both_switches(serving, capsys, enabled, http_enabled):
    serving.set_setting("enabled", enabled)
    serving.set_setting("http_enabled", http_enabled)
    assert http_server.serve(PORT) == 2
    assert "turned off in Settings > Integrations" in capsys.readouterr().err


@pytest.mark.parametrize("policy", [{"integrations": False}, {"integrations_http": False}])
def test_serve_stays_off_when_the_organization_turns_off_either(serving, tmp_path, capsys, policy):
    serving.set_setting("enabled", True)
    serving.set_setting("http_enabled", True)
    _policy(tmp_path, policy)
    assert http_server.serve(PORT) == 2
    assert "Your organization doesn't allow the local API" in capsys.readouterr().err


class QuickServer:
    """uvicorn.Server that stops before it ever listens."""

    def __init__(self, config):
        self.started, self.should_exit = False, False

    def run(self):
        self.should_exit = True


def test_a_server_that_never_listened_stops_cleanly_with_its_watcher_in_the_background(serving, monkeypatch):
    import threading

    import uvicorn
    from fabric_desktop.integrations.capabilities import register_core
    serving.set_setting("enabled", True)
    serving.set_setting("http_enabled", True)

    def fresh():
        reg = Registry()
        register_core(reg)
        return reg
    monkeypatch.setattr("fabric_desktop.integrations.registry.default_registry", fresh)
    monkeypatch.setattr(uvicorn, "Server", QuickServer)
    threads = []

    class Recorded(threading.Thread):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            threads.append(self)

    monkeypatch.setattr(http_server.threading, "Thread", Recorded)
    assert http_server.serve(PORT) == 0
    assert [t.daemon for t in threads] == [True] and threads[0].name == "parent-watch"
    threads[0].join(timeout=30)


class WatchedServer:
    """uvicorn.Server that 'listens' at once and runs until the parent watcher has looked twice."""
    asked_twice = None

    def __init__(self, config):
        self.started, self.should_exit = False, False

    def run(self):
        self.started = True
        WatchedServer.kept_going = WatchedServer.asked_twice.wait(timeout=30)
        self.should_exit = True


def test_a_live_parent_keeps_the_server_running_and_no_parent_pid_means_follow_nobody(serving, monkeypatch):
    import threading

    import uvicorn
    from fabric_desktop.integrations.capabilities import register_core
    serving.set_setting("enabled", True)
    serving.set_setting("http_enabled", True)

    def fresh():
        reg = Registry()
        register_core(reg)
        return reg
    monkeypatch.setattr("fabric_desktop.integrations.registry.default_registry", fresh)
    monkeypatch.setattr(uvicorn, "Server", WatchedServer)
    WatchedServer.asked_twice = threading.Event()
    asked = []

    def alive(pid):
        asked.append(pid)
        if len(asked) == 2:
            WatchedServer.asked_twice.set()
        return True
    monkeypatch.setattr(http_server, "_parent_alive", alive)
    monkeypatch.setattr(http_server.time, "sleep", lambda s: None)
    assert http_server.serve(PORT) == 0
    assert WatchedServer.kept_going is True and asked[:2] == [0, 0]


def test_with_everything_on_serve_goes_on_to_start(serving):
    serving.set_setting("enabled", True)
    serving.set_setting("http_enabled", True)
    with pytest.raises(Started):
        http_server.serve(PORT)


# ------------------------------------------------------------------ the parent


def test_a_finished_parent_is_reported_as_not_alive():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert http_server._parent_alive(child.pid) is False
    assert http_server._parent_alive(os.getpid()) is True


class Kernel32:
    def __init__(self, handle, code):
        self.handle, self.code, self.closed = handle, code, []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - the Windows name
        self.opened = (access, inherit, pid)
        return self.handle

    def GetExitCodeProcess(self, handle, ref):  # noqa: N802
        ref._obj.value = self.code
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)


@pytest.mark.parametrize("handle,code,alive", [(0, 259, False), (7, 259, True), (7, 0, False)])
def test_on_windows_the_parent_is_alive_only_while_it_is_still_active(monkeypatch, handle, code, alive):
    k = Kernel32(handle, code)
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": k})(), raising=False)
    monkeypatch.setattr(http_server.os, "name", "nt")
    result = http_server._parent_alive(4242)
    monkeypatch.undo()
    assert result is alive
    assert k.closed == ([7] if handle else [])
    assert k.opened == (0x1000, False, 4242)
