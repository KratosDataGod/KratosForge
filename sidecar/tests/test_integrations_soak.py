"""Soak tests for the read-only tools: thousands of calls through every way a tool connects, and the
server's resource use (threads, open files and sockets, memory, its own per-client maps) stays flat.

A long-lived server (the local API runs for as long as the app does; an AI tool keeps its
``fabric-desktop mcp`` process for a whole working day) must not grow with the number of calls it
has answered, the number of clients that have come and gone, or the ways they misbehaved: hanging up
mid-response, sending half a body, abandoning an MCP session without closing it.

Each test is bounded (about 30 seconds or less on a 4-core runner). They measure after a warm-up, since the
first calls fill caches, import modules and start pooled worker threads, and then check that later
phases don't add to what the warm-up left. The thresholds are generous but real:

- threads: at most 2 more than after warm-up, once a short wait has let finished calls' threads
  exit. Each call runs on its own ``integration-call`` thread (core.run_call), so a leak there would
  show as hundreds; anyio's pooled worker threads idle out only after 10 seconds, hence measuring
  against the warm-up rather than the empty process.
- open file descriptors (handles on Windows): at most 5 more. A leaked socket or sqlite connection
  per request would show as thousands.
- memory (the stdio process's RSS): under 30 MB of growth across thousands of calls after warm-up.
  The interpreter's allocator keeps freed arenas and sqlite has a page cache, so a few MB of drift is
  normal; a leak of even 10 KB per call would pass 30 MB.

What can't be measured on a platform is skipped with the reason (open handles on Windows without
psutil, another process's RSS without /proc or psutil); the rest of the test still runs.
Stdio is read with a thread per pipe and a queue (test_integrations_framing_fuzz.Stdio), not
select() on the pipe, so this runs on Windows.
"""

import json
import os
import queue
import socket
import threading
import time
import urllib.parse
from types import SimpleNamespace

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import streamable_http_manager

from fabric_desktop.integrations import http_server as local_api
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.core import IntegrationError
from fabric_desktop.integrations.http_server import RateLimiter, build_app, server_config
from fabric_desktop.integrations.registry import Capability, schema

from test_integrations_conformance import env  # noqa: F401 - a fixture
from test_integrations_framing_fuzz import Stdio

try:
    import psutil
except ImportError:  # not a dependency: /proc stands in on Linux, and the rest is skipped
    psutil = None

THREAD_SLACK = 2       # threads more than after warm-up, once settled
FD_SLACK = 5           # open descriptors (handles) more than after warm-up
RSS_SLACK_MB = 30      # the stdio process's memory growth after warm-up
SETTLE_SECONDS = 8.0   # how long finished calls get to let their threads and sockets go
RESULT_CAP = 100_000   # the result limit in these tests (the capability list fits), so a too_large result is quick to make
BIG = RESULT_CAP - 20_000  # a large result that is still allowed: big enough to be cut off mid-send
BODY_SECONDS = 0.2     # a slow body is a 408 this quickly here
SESSION_IDLE = 1.5     # an abandoned MCP session is reaped this quickly here


# ---------------------------------------------------------------------------- measuring

def call_threads():
    """Handler threads (core.run_call) still running."""
    return {t for t in threading.enumerate() if t.name == "integration-call" and t.is_alive()}


def worker_threads():
    return sum(t.name == "AnyIO worker thread" for t in threading.enumerate())


def thread_count():
    return threading.active_count()


def open_fds():
    """This process's open file descriptors (handles on Windows), or None where that can't be counted."""
    if psutil is not None:
        me = psutil.Process()
        return me.num_handles() if os.name == "nt" else me.num_fds()
    for folder in ("/proc/self/fd", "/dev/fd"):  # Linux; macOS
        if os.path.isdir(folder):
            return len(os.listdir(folder))
    return None  # Windows without psutil: there is no portable way to count handles


def proc_rss_mb(pid):
    """Another process's resident memory in MB, or None where that can't be read."""
    if psutil is not None:
        return psutil.Process(pid).memory_info().rss / 2**20
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def proc_threads(pid):
    if psutil is not None:
        return psutil.Process(pid).num_threads()
    try:
        return len(os.listdir(f"/proc/{pid}/task"))
    except OSError:
        return None


def settle(condition, seconds=SETTLE_SECONDS):
    """Wait until ``condition`` holds (finished calls' threads exiting, sockets closing), or time is up."""
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.05)
    return condition()


class Usage:
    """Threads and open descriptors after warm-up, and checks that a later phase didn't add to them."""

    def __init__(self):
        assert settle(lambda: not call_threads()), call_threads()
        self.threads, self.workers, self.fds = thread_count(), worker_threads(), open_fds()
        self.log = [("warm-up", self.threads, self.workers, self.fds)]

    def check(self, phase, before_calls=frozenset()):
        assert settle(lambda: not (call_threads() - before_calls)), f"{phase}: handler threads left running"
        assert settle(lambda: thread_count() <= self.threads + THREAD_SLACK), \
            f"{phase}: {thread_count()} threads, {self.threads} after warm-up: {sorted(t.name for t in threading.enumerate())}"
        assert worker_threads() <= self.workers + THREAD_SLACK, (phase, worker_threads(), self.workers)
        fds = open_fds()
        if self.fds is not None:  # None: Windows without psutil, see open_fds
            assert settle(lambda: open_fds() <= self.fds + FD_SLACK), f"{phase}: {open_fds()} open, {self.fds} after warm-up"
            fds = open_fds()
        self.log.append((phase, thread_count(), worker_threads(), fds))


# ---------------------------------------------------------------------------- the server

def big(svc, ctx, size):
    return {"blob": "x" * size}


def slow(svc, ctx):
    deadline = time.monotonic() + 5  # never hang the run if the cancel doesn't arrive
    while not ctx.cancelled and time.monotonic() < deadline:
        time.sleep(0.01)
    return {}


class RecordingManager(streamable_http_manager.StreamableHTTPSessionManager):
    """The MCP session manager build_app makes, kept so a test can see its sessions; abandoned
    sessions are reaped after SESSION_IDLE seconds instead of the production setting (recorded)."""
    made: list = []

    def __init__(self, *args, **kwargs):
        self.production_idle = kwargs.get("session_idle_timeout", streamable_http_manager.DEFAULT_SESSION_IDLE_TIMEOUT)
        kwargs["session_idle_timeout"] = SESSION_IDLE
        super().__init__(*args, **kwargs)
        RecordingManager.made.append(self)


@pytest.fixture
def api(env, monkeypatch):  # noqa: F811 - the conformance fixture
    """The local API as ``serve`` runs it (server_config: its h11 protocol, connection cap and
    deadlines) on a real socket, with four connected tools, a large and a slow read-only tool, and
    the rate limit lifted (these tests send thousands of calls; the limiter's map is checked below)."""
    monkeypatch.setattr(local_api, "BODY_SECONDS", BODY_SECONDS)
    monkeypatch.setattr(local_api, "MAX_RESULT_BYTES", RESULT_CAP)
    monkeypatch.setattr(streamable_http_manager, "StreamableHTTPSessionManager", RecordingManager)
    monkeypatch.setattr(RecordingManager, "made", [])
    reg = env["registry"]
    reg.register(Capability("ext_big", "Big", "A result of the size asked for.",
                            schema({"size": {"type": "integer", "minimum": 0, "maximum": 10_000_000}}, ["size"]),
                            schema(extra=True), "READ_METADATA", big, needs_service=False, timeout=30))
    reg.register(Capability("ext_slow", "Slow", "Waits until cancelled.", schema(), schema(extra=True),
                            "READ_METADATA", slow, needs_service=False, timeout=30))
    tools = [env["store"].add_client(f"Tool {n}", "other-mcp", transport="http", grants=["READ_METADATA"])
             for n in range(4)]
    limiter = RateLimiter(rate=1e6, burst=10**6)
    # IPPROTO_TCP named, as the sockets uvicorn binds in production are: asyncio sets TCP_NODELAY only
    # on those, and without it each kept-alive response waits ~40 ms for a delayed ACK.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    guard = build_app(reg, env["store"], port, lambda: env["service"], limiter=limiter)
    server = uvicorn.Server(server_config(guard, port))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True, name="soak-uvicorn")
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"
    yield {"port": port, "guard": guard, "limiter": limiter, "tools": [{"client": c, "token": t} for c, t in tools],
           "manager": RecordingManager.made[-1], "env": env}
    server.should_exit = True
    thread.join(15)
    sock.close()


# ---------------------------------------------------------------------------- a raw HTTP/1.1 client

class Http:
    """One connection on a socket of our own, so the test decides when it is kept, closed or cut off."""

    def __init__(self, port, rcvbuf=None):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf:  # a small window, so the server is still sending when we hang up
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.settimeout(20)
        self.sock.connect(("127.0.0.1", port))
        self.buf = b""

    def send(self, method, path, token, body=None, close=False, length=None):
        data = b"" if body is None else json.dumps(body).encode()
        lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{self.port}", f"Authorization: Bearer {token}"]
        if close:
            lines.append("Connection: close")
        if body is not None or length is not None:
            lines += ["Content-Type: application/json", f"Content-Length: {len(data) if length is None else length}"]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + data)

    def _fill(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("closed by the server")
        self.buf += chunk

    def response(self):
        while b"\r\n\r\n" not in self.buf:
            self._fill()
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        fields = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        length = int(fields.get("content-length", "0"))
        while len(self.buf) < length:
            self._fill()
        body, self.buf = self.buf[:length], self.buf[length:]
        return int(lines[0].split(" ", 2)[1]), fields, body

    def request(self, method, path, token, body=None, close=False):
        self.send(method, path, token, body, close)
        return self.response()

    def close(self):
        self.sock.close()


def invoke(cap_id):
    return f"/api/v1/capabilities/{cap_id}/invoke"


# (method, path, body, expected status) for each kind of read-only call in the mix
CALLS = {
    "status": ("POST", invoke("fabric_desktop_get_status"), {"arguments": {}}, 200),
    "resource": ("GET", "/api/v1/resources/read?uri=" + urllib.parse.quote("fabric-desktop://notebooks?limit=5", safe=""),
                 None, 200),
    "not_found": ("POST", invoke("fabric_desktop_get_notebook"), {"arguments": {"name": "not-there"}}, 404),
    "invalid": ("POST", invoke("fabric_desktop_get_project"), {"arguments": {"id": "no-such-kind"}}, 400),
    "too_large": ("POST", invoke("ext_big"), {"arguments": {"size": RESULT_CAP + 1000}}, 413),
    "capabilities": ("POST", invoke("fabric_desktop_list_capabilities"), {"arguments": {"include_unavailable": False}}, 200),
}
MIX = ["status", "resource", "not_found", "invalid", "status", "too_large", "status", "resource", "not_found", "invalid"]


def kind_of(i):
    return "capabilities" if i % 100 == 0 else MIX[i % len(MIX)]


def rest_client(port, token, calls, offset, failures):
    """One tool's calls: kept-alive on one connection, with every 7th call on a new connection it
    closes (Connection: close), and now and then a hang-up mid-response, a hang-up while its call
    runs, and a body that never all arrives."""
    conn = None
    used = time.monotonic()
    try:
        for i in range(offset, offset + calls):
            kind = kind_of(i)
            method, path, body, want = CALLS[kind]
            if i % 7 == 3:
                once = Http(port)
                status, _, out = once.request(method, path, token, body, close=True)
                once.close()
            else:
                if conn is not None and time.monotonic() - used >= local_api.IDLE_SECONDS - 0.5:
                    # The side trips below can take longer than the server keeps an idle connection
                    # (on a busy Windows runner, a big result read through a small window): the server
                    # has closed it by design, so start a new one, as any HTTP client would.
                    conn.close()
                    conn = None
                if conn is None:
                    conn = Http(port)
                status, _, out = conn.request(method, path, token, body)
                used = time.monotonic()
            if status != want:
                failures.append((kind, status, out[:300]))
            if i % 40 == 11:
                mid_response(port, token, failures)
            elif i % 40 == 23:
                mid_call(port, token)
            elif i % 40 == 35:
                slow_body(port, token, failures)
    except Exception as exc:  # noqa: BLE001 - reported by the test
        failures.append(("exception", type(exc).__name__, str(exc)))
    finally:
        if conn is not None:
            conn.close()


def mid_response(port, token, failures):
    """Ask for a large result and hang up once its first bytes arrive."""
    conn = Http(port, rcvbuf=2048)
    try:
        conn.send("POST", invoke("ext_big"), token, {"arguments": {"size": BIG}})
        while b"\r\n" not in conn.buf:
            conn._fill()
        if not conn.buf.startswith(b"HTTP/1.1 200"):
            failures.append(("mid_response", conn.buf[:200]))
    finally:
        conn.close()


def mid_call(port, token):
    """Start a call that runs until cancelled, and hang up at once: the server must notice and stop it."""
    conn = Http(port)
    conn.send("POST", invoke("ext_slow"), token, {"arguments": {}})
    time.sleep(0.02)
    conn.close()


def slow_body(port, token, failures):
    """Promise a body and send only part of it: a 408 after BODY_SECONDS, and the connection closed."""
    conn = Http(port)
    try:
        conn.send("POST", invoke("fabric_desktop_get_status"), token, length=40)
        conn.sock.sendall(b'{"argu')
        status, fields, _ = conn.response()
        if (status, fields.get("connection")) != (408, "close"):
            failures.append(("slow_body", status, fields))
    finally:
        conn.close()


def rest_phase(api, calls_per_tool, offset):
    failures: list = []
    threads = [threading.Thread(target=rest_client, args=(api["port"], tool["token"], calls_per_tool, offset, failures),
                                name=f"soak-client-{n}")
               for n, tool in enumerate(api["tools"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads), "a client is stuck"
    assert not failures, failures[:10]
    return calls_per_tool * len(threads)


# ---------------------------------------------------------------------------- REST

def test_rest_soak_keeps_threads_sockets_and_maps_flat(api):
    """A few thousand REST calls to read-only tools from four tools at once, every outcome a
    read-only call can have, over kept-alive and closed connections, with hang-ups and slow bodies
    mixed in. Threads and open sockets return to what warm-up left, phase after phase."""
    warm = rest_phase(api, 100, 0)
    usage = Usage()
    total = warm
    for n in range(3):
        total += rest_phase(api, 200, 100 + n * 200)
        usage.check(f"after {total} calls")
        # One bucket per tool that called; nothing per connection, per request or per refusal.
        assert len(api["limiter"]._buckets) <= len(api["tools"])
        assert len(api["guard"]._refused) <= 3 * len(api["tools"])  # reasons x tools, at most
    assert total >= 2800
    print("rest soak (phase, threads, anyio workers, fds):", usage.log)
    # Still answering, on a fresh connection.
    conn = Http(api["port"])
    assert conn.request(*CALLS["status"][:2], api["tools"][0]["token"], CALLS["status"][2], close=True)[0] == 200
    conn.close()


def test_refusal_and_rate_maps_stay_bounded_as_clients_come_and_go(api, monkeypatch):
    """The Guard keeps a little state per tool: a token bucket (RateLimiter._buckets) and when it last
    audited each kind of refusal (Guard._refused). Tools come and go (added, revoked, rotated), and a
    server runs for as long as the app; state about a tool not heard from lately must be let go, or the
    maps grow with every tool there has ever been."""
    env = api["env"]
    env["registry"].audit = IntegrationAudit(env["store"]).record  # refusals are only tracked when audited
    now = [1000.0]
    limiter = RateLimiter(rate=1.0, burst=1, clock=lambda: now[0])  # the second call in a moment is refused
    api["guard"].limiter = limiter
    monkeypatch.setattr(local_api, "REFUSED_EVERY", 0.2)
    monkeypatch.setattr(local_api, "PRUNE_AT", 32)
    port, waves, per_wave = api["port"], 10, 20
    sizes = []
    for wave in range(waves):
        tokens = [env["store"].add_client(f"Wave {wave} tool {n}", "other-mcp", transport="http",
                                          grants=["READ_METADATA"])[1] for n in range(per_wave)]
        for token in tokens:
            conn = Http(port)
            codes = [conn.request(*CALLS["status"][:2], token, CALLS["status"][2])[0] for _ in range(2)]
            conn.close()
            assert codes == [200, 429], codes
            # A bad token: every one of these is the same unauthenticated caller to the map.
            conn = Http(port)
            assert conn.request(*CALLS["status"][:2], f"bogus-{wave}-{token[:8]}", CALLS["status"][2], close=True)[0] == 401
            conn.close()
        sizes.append((len(limiter._buckets), len(api["guard"]._refused)))
        now[0] += 10.0  # these tools go quiet: their buckets fill up again
        time.sleep(0.25)  # and their refusals are past REFUSED_EVERY
    print("map sizes per wave (buckets, refusals):", sizes)
    bound = 2 * 32 + per_wave + 2  # PRUNE_AT, doubled at most once, plus one wave's tools, plus the bad-token keys
    seen = waves * per_wave
    assert max(b for b, _ in sizes) <= bound, f"RateLimiter._buckets grew with every tool seen ({seen}): {sizes}"
    assert max(r for _, r in sizes) <= bound, f"Guard._refused grew with every tool seen ({seen}): {sizes}"
    # The refusals were still audited, and the trail is whole.
    trail = IntegrationAudit(env["store"])
    assert sum(r["outcome"] == "rate_limited" for r in trail.recent(1000)) == seen
    assert trail.verify()["ok"]


def test_pruning_keeps_a_refusal_count_until_its_line_reports_it(monkeypatch):
    """Letting go of stale refusals never loses one the audit trail hasn't counted yet."""
    monkeypatch.setattr(local_api, "PRUNE_AT", 2)
    monkeypatch.setattr(local_api, "REFUSED_EVERY", 0.05)
    lines = []
    guard = local_api.Guard(None, None, 1, RateLimiter(), SimpleNamespace(audit=lambda *a: lines.append(a[3])))
    err, scope = IntegrationError("rate_limited", "Too many requests."), {"method": "POST", "path": "/x"}
    guard._audit(scope, err, {"id": "a"})
    guard._audit(scope, err, {"id": "a"})  # within the window: counted, not written
    time.sleep(0.1)
    for n in range(6):  # other tools, past PRUNE_AT: a prune runs
        guard._audit(scope, err, {"id": f"other-{n}"})
    assert len(guard._refused) < 8 and ("Too many requests.", "a") in guard._refused
    guard._audit(scope, err, {"id": "a"})
    assert lines[-1]["refused_since_last_line"] == 1, lines
    limiter = RateLimiter(rate=1.0, burst=2, clock=lambda: now[0])
    now = [0.0]
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [True, True, False]
    for n in range(4):
        limiter.allow(f"other-{n}")  # past PRUNE_AT: a prune runs, and "a" is still empty
    assert limiter.allow("a") is False


# ---------------------------------------------------------------------------- MCP over HTTP

def mcp_target(port, token):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return http, streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)


async def mcp_session(port, token, mode, calls, failures):
    http, target = mcp_target(port, token)
    try:
        async with Client(target, mode=mode) as c:
            for i in range(calls):
                kind = i % 4
                if kind == 0:
                    out = await c.call_tool("fabric_desktop_get_status", {})
                    ok = not out.is_error
                elif kind == 1:
                    out = await c.call_tool("fabric_desktop_get_notebook", {"name": "not-there"})
                    ok = out.is_error and out.structured_content["error"]["code"] == "not_found"
                elif kind == 2:
                    out = await c.call_tool("fabric_desktop_get_project", {"id": "no-such-kind"})
                    ok = out.is_error and out.structured_content["error"]["code"] == "invalid_input"
                else:
                    out = await c.read_resource("fabric-desktop://notebooks?limit=5")
                    ok = bool(out.contents)
                if not ok:
                    failures.append((mode, i, out))
    finally:
        await http.aclose()


def abandon_session(port, token, with_stream):
    """A legacy (2025-11-25) session opened, used once and dropped without a DELETE, as when the tool's
    process is killed; with ``with_stream``, it also had its server-to-client stream open."""
    head = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    with httpx2.Client(timeout=10) as http:
        url = f"http://127.0.0.1:{port}/mcp"
        opened = http.post(url, headers=head, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "soak", "version": "1"}}})
        assert opened.status_code == 200, opened.text
        sid = opened.headers["mcp-session-id"]
        head = {**head, "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"}
        assert http.post(url, headers=head, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
        called = http.post(url, headers=head, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                    "params": {"name": "fabric_desktop_get_status", "arguments": {}}})
        assert called.status_code == 200 and "KratosForge" in called.text, called.text
    if with_stream:  # a GET stream on a socket of our own, cut off
        conn = Http(port)
        conn.sock.sendall((f"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n"
                           f"Accept: text/event-stream\r\nmcp-session-id: {sid}\r\nmcp-protocol-version: 2025-11-25\r\n\r\n").encode())
        while b"\r\n\r\n" not in conn.buf:
            conn._fill()
        assert conn.buf.startswith(b"HTTP/1.1 200"), conn.buf[:200]
        conn.close()
    return sid


def test_mcp_over_http_sessions_come_and_go_without_leaking(api):
    """Many MCP sessions over HTTP, opened and closed properly (both protocol eras) and abandoned:
    closed ones are forgotten at once, abandoned ones once idle, and threads return to baseline."""
    port, tools, manager = api["port"], api["tools"], api["manager"]
    # Production reaps abandoned sessions eventually (the SDK's default, 30 minutes): never "forever".
    assert manager.production_idle is not None and 0 < manager.production_idle <= 30 * 60

    def sessions(mode, count, calls):
        failures: list = []

        async def go():
            for start in range(0, count, 4):  # four tools at a time
                async with anyio.create_task_group() as tg:
                    for n in range(start, min(count, start + 4)):
                        tg.start_soon(mcp_session, port, tools[n % len(tools)]["token"], mode, calls, failures)
        anyio.run(go)
        assert not failures, failures[:5]
        return count * calls

    sessions("legacy", 4, 8)  # warm-up
    sessions("auto", 4, 8)
    usage = Usage()
    total = 0
    for _ in range(2):
        total += sessions("legacy", 12, 20)
        assert settle(lambda: not manager._server_instances), f"{len(manager._server_instances)} closed sessions kept"
        total += sessions("auto", 12, 20)  # 2026-07-28: no session to keep
        assert not manager._server_instances
        usage.check(f"after {total} MCP calls")

    # Abandoned sessions: kept while they might come back, then reaped, and their slots free again.
    abandoned = [abandon_session(port, tools[n % len(tools)]["token"], with_stream=n % 2 == 0) for n in range(12)]
    assert set(abandoned) <= set(manager._server_instances)
    assert settle(lambda: not manager._server_instances, SESSION_IDLE + 10), \
        f"{len(manager._server_instances)} abandoned sessions never reaped"
    sessions("legacy", 4, 4)
    usage.check("after abandoned sessions")
    print("mcp soak (phase, threads, anyio workers, fds):", usage.log)


# ---------------------------------------------------------------------------- stdio

STDIO_MIX = [
    ("tools/call", {"name": "fabric_desktop_get_status", "arguments": {}}),
    ("tools/call", {"name": "fabric_desktop_get_notebook", "arguments": {"name": "not-there"}}),
    ("tools/call", {"name": "fabric_desktop_get_project", "arguments": {"id": "no-such-kind"}}),
    ("resources/read", {"uri": "fabric-desktop://notebooks?limit=5"}),
    ("tools/call", {"name": "fabric_desktop_get_status", "arguments": {}}),
    ("resources/list", {}),
]


def stdio_batch(proc, start, count, window=50):
    """Send ``count`` requests, at most ``window`` unanswered at a time, and check each answer."""
    pending, sent, answered = {}, start, 0
    deadline = time.monotonic() + 60
    while answered < count:
        while sent < start + count and len(pending) < window:
            method, params = STDIO_MIX[sent % len(STDIO_MIX)]
            rid = f"soak-{sent}"
            pending[rid] = (method, params)
            proc.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode() + b"\n")
            sent += 1
        try:
            line = proc.lines.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            raise AssertionError(f"stdio stopped answering; alive={proc.alive()}; stderr={proc.err_text()[-2000:]}") from None
        got = json.loads(line)
        if got.get("id") not in pending:
            continue  # a notification (list changed)
        method, params = pending.pop(got["id"])
        assert "result" in got, (method, params, got)
        if method == "tools/call" and params["name"] != "fabric_desktop_get_status":
            code = got["result"]["structuredContent"]["error"]["code"]
            assert code == ("not_found" if params["name"].endswith("notebook") else "invalid_input"), got
        elif method == "tools/call":
            assert not got["result"].get("isError"), got
        answered += 1
    return sent


def test_stdio_long_lived_process_stays_flat(env):  # noqa: F811 - the conformance fixture
    proc = Stdio(env["tmp"], env["client"]["id"])
    try:
        proc.initialize()
        sent = stdio_batch(proc, 0, 600)  # warm-up: imports, the engine's Service, sqlite caches
        pid = proc.proc.pid
        time.sleep(0.5)
        rss0, threads0 = proc_rss_mb(pid), proc_threads(pid)
        samples = [(sent, rss0, threads0)]
        for _ in range(3):
            sent = stdio_batch(proc, sent, 700)
            samples.append((sent, proc_rss_mb(pid), proc_threads(pid)))
        print("stdio soak (requests, RSS MB, threads):", samples)
        assert sent >= 2700
        if threads0 is None:
            pytest.skip("this platform can't count another process's threads without psutil (pip install psutil)")
        assert settle(lambda: proc_threads(pid) <= threads0 + THREAD_SLACK), samples + [proc_threads(pid)]
        if rss0 is None:
            pytest.skip("this platform can't read another process's memory without psutil (pip install psutil)")
        assert max(rss for _, rss, _ in samples) - rss0 < RSS_SLACK_MB, samples
        assert proc.alive()
    finally:
        code = proc.close()
    err = proc.err_text()
    assert "Traceback" not in err and "could not record" not in err, err[-3000:]
    assert code == 0, err[-2000:]


# ---------------------------------------------------------------------------- the audit store

def test_thousands_of_audited_calls_keep_the_store_usable_and_the_chain_whole(api, capsys):
    """Every call writes a line to the integration store (sqlite) and the chained files, under a lock
    shared across processes. The local API's four tools at once, and a stdio process for another tool
    writing to the same store meanwhile: no lock errors, a line for every call, and the chain verifies."""
    env = api["env"]
    store = env["store"]
    env["registry"].audit = IntegrationAudit(store).record
    rest_counts: list = []

    def rest():
        try:
            rest_counts.append(rest_phase(api, 250, 0))
        except BaseException as exc:  # noqa: BLE001 - reported by the test below
            rest_counts.append(exc)

    proc = Stdio(env["tmp"], env["client"]["id"])
    try:
        proc.initialize()
        worker = threading.Thread(target=rest, name="soak-rest")
        worker.start()
        stdio_sent = stdio_batch(proc, 0, 600)
        worker.join(90)
        assert not worker.is_alive()
    finally:
        proc.close()
    err = capsys.readouterr().err + proc.err_text()
    assert "could not record" not in err and "locked" not in err and "Traceback" not in err, err[-3000:]
    assert len(rest_counts) == 1 and isinstance(rest_counts[0], int), rest_counts  # the REST side's own failure

    # Calls that reach Registry.invoke each have a line: every REST call (hang-ups mid-response
    # included; a hang-up while the call runs is recorded as cancelled) and stdio's tools/call and
    # resources/read; slow-body 408s are refused before that, by the Guard, and audited by it.
    audit = IntegrationAudit(store)
    rows = store._rows("select count(*) as n from audit")[0]["n"]
    stdio_audited = sum(STDIO_MIX[i % len(STDIO_MIX)][0] != "resources/list" for i in range(stdio_sent))
    assert rows >= rest_counts[0] + stdio_audited, (rows, rest_counts, stdio_audited)
    result = audit.verify()
    assert result["ok"] and result["lines"] == rows, result
    # The store is still usable: reads, and a write.
    assert len(audit.recent(1000)) == 1000
    assert store.client(env["client"]["id"])["name"] == "Conformance"
    assert store.add_client("After the soak", "other-mcp")[0]["id"]
