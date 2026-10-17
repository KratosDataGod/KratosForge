"""The local HTTP API: REST with OpenAPI at /api/v1 and MCP over Streamable HTTP at /mcp.

For tools that can't start a process (so can't use ``fabric-desktop mcp``) or don't speak MCP.
It is off until the person switches it on in Settings > Integrations; the app's engine then runs

    fabric-desktop integrations serve --port 47615 --parent-pid <engine pid>

as a child process, which stops when the engine does.

Every request passes ``Guard`` before anything else:

- it must arrive on 127.0.0.1, with a ``Host`` naming a loopback address and this port, and with
  no browser ``Origin`` (DNS rebinding and web pages can't reach it);
- it must carry ``Authorization: Bearer <token>`` for a connected tool (only the OpenAPI document
  and the health check are open); tokens are stored hashed and stop working when revoked;
- bodies over 1 MiB are refused, each tool is rate limited, and only a few calls run at once.

Behind the guard, REST and MCP both call ``Registry.invoke``: the same permissions, approvals and
results as MCP over stdio. No endpoint can change settings, permissions or approvals.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Callable

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from .core import CallContext, Caller, IntegrationError, error_from
from .registry import Registry
from .store import IntegrationStore

MAX_BODY_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 1_000_000
RATE_PER_SECOND = 5.0      # sustained requests per tool
RATE_BURST = 30
MAX_CONCURRENT = 4         # calls running at once in this server
QUEUE_WAIT_SECONDS = 10.0
OPEN_PATHS = {"/api/v1/health", "/api/v1/openapi.json"}

STATUS = {"invalid_input": 400, "not_found": 404, "permission_denied": 403, "approval_denied": 403,
          "approval_required": 428, "conflict": 409, "timeout": 504, "cancelled": 409, "unavailable": 503,
          "integrations_disabled": 503, "too_large": 413, "rate_limited": 429, "upstream": 502, "failed": 422,
          "internal": 500}


def error_response(err: IntegrationError, headers: dict[str, str] | None = None, status: int | None = None) -> JSONResponse:
    return JSONResponse({"ok": False, "error": err.to_dict()}, status_code=status or STATUS.get(err.code, 500), headers=headers)


class RateLimiter:
    """A token bucket per tool."""

    def __init__(self, rate: float = RATE_PER_SECOND, burst: int = RATE_BURST, clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True


class Guard:
    """Loopback, Host, Origin, size, token and rate checks for every request."""

    def __init__(self, app: ASGIApp, store: IntegrationStore, port: int, limiter: RateLimiter | None = None):
        self.app, self.store, self.port = app, store, port
        self.limiter = limiter or RateLimiter()
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, err: IntegrationError,
                      headers: dict[str, str] | None = None, status: int | None = None) -> None:
        await error_response(err, headers, status)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = (scope.get("client") or ("", 0))[0]
        if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
            await self._refuse(scope, receive, send, IntegrationError("permission_denied", "Only this PC can use the local API."))
            return
        if headers.get("host") not in self.hosts:
            await self._refuse(scope, receive, send, IntegrationError(
                "permission_denied", "The Host header must be this PC's loopback address and port.",
                hint=f"Use http://127.0.0.1:{self.port}."))
            return
        if "origin" in headers:
            await self._refuse(scope, receive, send, IntegrationError(
                "permission_denied", "Requests from web pages aren't accepted.",
                hint="Call the API from a program, not from a browser."))
            return
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            await self._refuse(scope, receive, send, IntegrationError("too_large", f"Requests are limited to {MAX_BODY_BYTES // 1024} KB."))
            return
        if scope["path"] not in OPEN_PATHS:
            auth = headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            found = self.store.client_by_token(token) if token else None
            if found is None:
                await self._refuse(scope, receive, send, IntegrationError(
                    "permission_denied", "A valid token is required." if token else "Send Authorization: Bearer <token>.",
                    hint="Fabric Desktop by KratosBI > Settings > Integrations shows the token when you connect a tool."),
                    {"WWW-Authenticate": "Bearer"}, 401)
                return
            if not self.limiter.allow(found["id"]):
                await self._refuse(scope, receive, send, IntegrationError(
                    "rate_limited", "Too many requests.", retryable=True, hint="Wait a second and try again."),
                    {"Retry-After": "1"})
                return
            scope.setdefault("state", {})["fd_client"] = found
        await self.app(scope, _limit_body(receive), send)


def _limit_body(receive: Receive) -> Receive:
    """Stop reading a body without Content-Length once it passes the limit."""
    seen = 0

    async def limited() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise IntegrationError("too_large", f"Requests are limited to {MAX_BODY_BYTES // 1024} KB.")
        return message
    return limited


def openapi(registry: Registry, port: int) -> dict[str, Any]:
    """The OpenAPI 3.1 document, generated from the registry: one path per capability."""
    error = {"type": "object", "properties": {"ok": {"const": False}, "error": {"$ref": "#/components/schemas/Error"}},
             "required": ["ok", "error"]}
    errors = {str(code): {"description": text, "content": {"application/json": {"schema": error}}}
              for code, text in [(400, "Invalid input"), (401, "No or bad token"), (403, "Not allowed"),
                                 (404, "Not found"), (428, "Waiting for the person's approval; call again later"),
                                 (429, "Rate limited"), (503, "Unavailable or turned off")]}
    paths: dict[str, Any] = {
        "/api/v1/health": {"get": {"summary": "Is the API up", "security": [], "operationId": "health",
                                   "responses": {"200": {"description": "Up"}}}},
        "/api/v1/capabilities": {"get": {"summary": "List capabilities with schemas, permissions and availability for this token",
                                         "operationId": "listCapabilities", "responses": {"200": {"description": "Capabilities"}, **errors}}},
    }
    for cap in registry.all():
        paths[f"/api/v1/capabilities/{cap.id}/invoke"] = {"post": {
            "operationId": cap.id, "summary": cap.title, "description": cap.description,
            "x-fabric-desktop": {"permission": cap.permission, "read_only": cap.read_only, "destructive": cap.destructive,
                                 "approval": cap.approval, "locations": list(cap.locations), "version": cap.version},
            "requestBody": {"required": False, "content": {"application/json": {"schema": {
                "type": "object", "properties": {"arguments": cap.input_schema}, "additionalProperties": False}}}},
            "responses": {"200": {"description": "Done", "content": {"application/json": {"schema": {
                "type": "object", "properties": {"ok": {"const": True}, "capability": {"type": "string"},
                                                 "seconds": {"type": "number"}, "result": cap.output_schema},
                "required": ["ok", "result"]}}}}, **errors}}}
    return {"openapi": "3.1.0",
            "info": {"title": "Fabric Desktop by KratosBI local API", "version": __version__,
                     "description": "Fabric Desktop by KratosBI's capabilities for external tools, on this PC only. "
                                    "The same capabilities as the MCP server, with the same permissions and approvals."},
            "servers": [{"url": f"http://127.0.0.1:{port}"}],
            "security": [{"bearer": []}],
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
                           "schemas": {"Error": {"type": "object", "required": ["code", "message", "retryable"], "properties": {
                               "code": {"type": "string"}, "message": {"type": "string"}, "hint": {"type": "string"},
                               "retryable": {"type": "boolean"}, "details": {"type": "object"}}}}},
            "paths": paths}


def build_app(registry: Registry, store: IntegrationStore, port: int, service_factory: Callable[[], Any] | None = None,
              limiter: RateLimiter | None = None) -> ASGIApp:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings

    from .mcp_server import ServiceHolder, build_server

    holder = ServiceHolder(service_factory)
    slots = anyio.Semaphore(MAX_CONCURRENT)

    def caller_of(request: Request) -> Caller:
        client = request.scope["state"]["fd_client"]
        return Caller(client["id"], "api", client["name"])

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "product": "Fabric Desktop by KratosBI", "version": __version__})

    async def spec(request: Request) -> Response:
        return JSONResponse(openapi(registry, port))

    async def capabilities(request: Request) -> Response:
        ctx = CallContext(caller_of(request))
        return JSONResponse({"ok": True, "capabilities": registry.describe(ctx)})

    async def invoke(request: Request) -> Response:
        cap_id = request.path_params["cap_id"]
        try:
            raw = await request.body()
            body = json.loads(raw) if raw.strip() else {}
        except IntegrationError as err:
            return error_response(err)
        except ValueError:
            return error_response(IntegrationError("invalid_input", "The body must be JSON: {\"arguments\": {...}}."))
        if not isinstance(body, dict) or not isinstance(body.get("arguments", {}), dict) or set(body) - {"arguments"}:
            return error_response(IntegrationError("invalid_input", "The body must be {\"arguments\": {...}}."))
        cap = registry.get(cap_id)
        ctx = CallContext(caller_of(request), timeout=cap.timeout if cap else 60.0)

        def run() -> Any:
            service = holder.get() if cap is not None and cap.needs_service else None
            return registry.invoke(service, cap_id, body.get("arguments") or {}, ctx)

        with anyio.move_on_after(QUEUE_WAIT_SECONDS) as waiting:
            await slots.acquire()
        if waiting.cancelled_caught:
            return error_response(IntegrationError("rate_limited", "Fabric Desktop by KratosBI is busy with other calls.",
                                                   retryable=True, hint="Try again shortly."), {"Retry-After": "5"})
        result: dict[str, Any] = {}
        try:
            async with anyio.create_task_group() as tg:
                async def call() -> None:
                    try:
                        result["value"] = await anyio.to_thread.run_sync(run, abandon_on_cancel=True)
                    except Exception as exc:  # noqa: BLE001 - shaped below
                        result.setdefault("error", exc)
                    tg.cancel_scope.cancel()

                async def watch() -> None:
                    while True:  # a client that hangs up cancels its call
                        await anyio.sleep(0.25)
                        if await request.is_disconnected():
                            ctx.cancel()
                            result["error"] = IntegrationError("cancelled", "The client disconnected.")
                            tg.cancel_scope.cancel()
                            return
                tg.start_soon(call)
                tg.start_soon(watch)
        finally:
            slots.release()
        if "error" in result:
            return error_response(error_from(result["error"]))
        payload = {"ok": True, "capability": cap_id, "seconds": ctx.seconds, "result": result.get("value")}
        text = json.dumps(payload, default=str)
        if len(text.encode()) > MAX_RESULT_BYTES:
            return error_response(IntegrationError("too_large", "The result is bigger than the API returns.",
                                                   hint="Ask for a smaller page (limit)."))
        return Response(text, media_type="application/json")

    def mcp_caller(ctx: Any) -> Caller:
        client = ctx.request.scope["state"]["fd_client"]
        return Caller(client["id"], "mcp-http", client["name"])

    mcp = build_server(registry, service_factory, caller_for=mcp_caller)
    manager = StreamableHTTPSessionManager(
        app=mcp, max_request_body_size=MAX_BODY_BYTES, max_sessions=20,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(Guard(None, store, port).hosts),  # type: ignore[arg-type]
                                                    allowed_origins=[]))

    class MCPEndpoint:  # a plain ASGI app, so /mcp is served without a redirect to /mcp/
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await manager.handle_request(scope, receive, send)

    app = Starlette(routes=[Route("/api/v1/health", health), Route("/api/v1/openapi.json", spec),
                            Route("/api/v1/capabilities", capabilities),
                            Route("/api/v1/capabilities/{cap_id}/invoke", invoke, methods=["POST"]),
                            Route("/mcp", MCPEndpoint(), methods=["GET", "POST", "DELETE"])],
                    lifespan=lambda _app: manager.run())
    return Guard(app, store, port, limiter)


# ---- running it

def _parent_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def serve(port: int, parent_pid: int = 0) -> int:
    """``fabric-desktop integrations serve``: run until stopped, or until the app's engine ends."""
    import uvicorn

    from . import policy
    from ..service import data_dir
    from .mcp_server import _engine_service
    from .permissions import PermissionAuthorizer
    from .registry import default_registry

    root = data_dir()
    store = IntegrationStore.at(root)
    rules, settings = policy.current(), store.settings()
    if rules["enabled"] is False or not rules["http"]:
        print("Your organization doesn't allow the local API on this PC.", file=sys.stderr)
        return 2
    if not settings["enabled"] or not settings["http_enabled"]:
        print("The local API is turned off in Settings > Integrations.", file=sys.stderr)
        return 2
    registry = default_registry()
    registry.authorizer = PermissionAuthorizer(store)
    app = build_app(registry, store, port, _engine_service)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                                           timeout_graceful_shutdown=10, server_header=False))
    state = root / "integrations" / "http.json"

    def watch() -> None:
        while not server.should_exit:
            if not _parent_alive(parent_pid):
                server.should_exit = True
                return
            time.sleep(1)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()
    state.write_text(json.dumps({"pid": os.getpid(), "port": port, "started_at": time.time()}), encoding="utf-8")
    try:
        server.run()
    finally:
        state.unlink(missing_ok=True)
    return 0
