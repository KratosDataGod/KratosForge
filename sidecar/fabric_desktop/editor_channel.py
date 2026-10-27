"""The editor channel: how a paired VS Code window runs notebook cells in KratosForge (docs/vscode.md).

The engine listens on 127.0.0.1, on a port of its own choosing, while the person has switched
"VS Code can run notebooks" on in Settings > Integrations. It writes ``<port> <hello>`` to
``editor-port`` in this Windows user's data folder; ``hello`` is random each time.

Every request passes the same checks before anything else: it comes from 127.0.0.1, its ``Host``
names this port on a loopback name, it has no browser ``Origin`` (so a web page can't call it,
and DNS rebinding can't either), and its body is at most 1 MiB of JSON. Asking to pair needs
``X-KratosForge-Hello`` (only programs that can read this user's data folder have it); everything
else needs ``Authorization: Bearer <token>`` from a pairing (editor_pairing.py).

    GET    /v1/hello?nonce=         {"proof"}: HMAC-SHA256(hello, nonce), so a window can tell it's
                                    KratosForge before it sends the hello or a token
    POST   /v1/pair                 {"window", "client"}  ->  {"id", "code", "expires_in"}
    GET    /v1/pair/<id>            {"state": waiting | paired | denied | expired, "token" once}
    DELETE /v1/pair                 ends this window's pairing
    GET    /v1/status               the pairing, and the app's version
    POST   /v1/notebook             {"path"}  ->  {"name"}: the notebook a file is
    POST   /v1/run                  {"path", "code"}  ->  the cell's first snapshot
    GET    /v1/run?path=&version=&wait=   the running (or last) cell, as the app polls it
    POST   /v1/interrupt            {"path"}
    GET    /v1/variables?path=

Runs, stops and pair/unpair calls go into the app's audit trail (audit.py). Nothing here can
change settings or pairings beyond ending the caller's own.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .redact import secrets as redact_secrets

MAX_BODY_BYTES = 1024 * 1024
MAX_WAIT_SECONDS = 25.0
PORT_FILE = "editor-port"


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Channel:
    """The listening server. ``service`` is the engine's Service (editor_service.EditorMixin)."""

    def __init__(self, service: Any, data_dir: Path):
        self.service = service
        self.data_dir = Path(data_dir)
        self.hello = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    def start(self) -> int:
        if self._server is not None:
            return self.port  # type: ignore[return-value]
        channel = self

        class Handler(_Handler):
            owner = channel

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="editor-channel")
        self._thread.start()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / PORT_FILE).write_text(f"{self.port} {self.hello}", encoding="utf-8")
        return self.port  # type: ignore[return-value]

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        try:
            (self.data_dir / PORT_FILE).unlink()
        except OSError:
            pass

    # ---- routes

    def authorize(self, method: str, path: str, headers: Any) -> dict[str, Any] | None:
        """Who is calling, checked before the body is read: the hello for asking to pair, else a
        pairing that's still valid while the switch is on and the policy allows it."""
        if not self.service.editor_channel_open():
            raise Refused(403, "off", "VS Code can't run notebooks in KratosForge now: it's switched off in "
                          "Settings > Integrations, or your organization doesn't allow it.")
        if method == "GET" and path == "/v1/hello":
            return None  # proves who's listening; needs nothing and gives nothing away
        if (method == "POST" and path == "/v1/pair") or (method == "GET" and path.startswith("/v1/pair/")):
            self._hello(headers)
            return None
        return self._paired(headers)

    def route(self, method: str, path: str, query: dict[str, str], pairing: dict[str, Any] | None, body: Any) -> Any:
        svc = self.service
        if method == "GET" and path == "/v1/hello":
            nonce = _field(query, "nonce", limit=128)
            if len(nonce) < 16:
                raise Refused(400, "invalid_input", "nonce should be at least 16 characters.")
            return {"proof": hmac.new(self.hello.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()}
        if method == "POST" and path == "/v1/pair":
            return svc.editor_channel_pair(_field(body, "window", required=False), _field(body, "client", required=False))
        if method == "GET" and path.startswith("/v1/pair/"):
            return svc.editor_channel_collect(path[len("/v1/pair/"):])
        assert pairing is not None  # authorize() let only the two calls above through without one
        if method == "DELETE" and path == "/v1/pair":
            return svc.editor_channel_unpair(pairing)
        if method == "GET" and path == "/v1/status":
            return {"app": "KratosForge", "version": __version__, "pairing": pairing}
        if method == "POST" and path == "/v1/notebook":
            return {"name": svc.editor_channel_notebook(pairing, _field(body, "path"))}
        if method == "POST" and path == "/v1/run":
            return svc.editor_channel_run(pairing, _field(body, "path"), _field(body, "code", limit=MAX_BODY_BYTES))
        if method == "GET" and path == "/v1/run":
            return svc.editor_channel_poll(pairing, _field(query, "path"), _int(query.get("version"), -1),
                                           min(max(_float(query.get("wait"), 0.0), 0.0), MAX_WAIT_SECONDS))
        if method == "POST" and path == "/v1/interrupt":
            return svc.editor_channel_interrupt(pairing, _field(body, "path"))
        if method == "GET" and path == "/v1/variables":
            return svc.editor_channel_variables(pairing, _field(query, "path"))
        raise Refused(404, "not_found", f"{method} {path} isn't something the editor channel does.")

    def _hello(self, headers: Any) -> None:
        if not secrets.compare_digest(str(headers.get("X-KratosForge-Hello") or ""), self.hello):
            raise Refused(401, "unauthorized", "Asking to pair needs the hello from editor-port in your KratosForge data folder.")

    def _paired(self, headers: Any) -> dict[str, Any]:
        auth = str(headers.get("Authorization") or "")
        pairing = self.service.editor_pairings.check(auth[7:]) if auth.startswith("Bearer ") else None
        if pairing is None:
            raise Refused(401, "not_paired", "This VS Code window isn't paired with KratosForge, or its pairing ended. "
                          "Pick KratosForge as the kernel again to pair it.")
        return pairing


def _field(values: Any, key: str, required: bool = True, limit: int = 4096) -> Any:
    value = values.get(key) if isinstance(values, dict) else None
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value) or len(value) > limit:
        raise Refused(400, "invalid_input", f"{key} should be text of at most {limit} characters.")
    return value


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _Handler(BaseHTTPRequestHandler):
    owner: Channel
    server_version = "KratosForge-editor"
    sys_version = ""
    timeout = 30  # seconds a connection may sit without sending

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's name
        pass  # no request lines on the engine's stderr

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's names
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve("DELETE")

    def _serve(self, method: str) -> None:
        try:
            self._guard()
            url = urlsplit(self.path)
            pairing = self.owner.authorize(method, url.path, self.headers)  # before any body is read
            body = self._body() if method == "POST" else None
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            result = self.owner.route(method, url.path, query, pairing, body)
            self._reply(200, {"ok": True, "result": result})
        except Refused as exc:
            self._reply(exc.status, {"ok": False, "error": {"code": exc.code, "message": exc.message}})
        except PermissionError as exc:
            self._reply(403, {"ok": False, "error": {"code": "refused", "message": redact_secrets(str(exc))}})
        except LookupError as exc:
            self._reply(404, {"ok": False, "error": {"code": "not_found", "message": redact_secrets(str(exc).strip("'\""))}})
        except (ValueError, RuntimeError) as exc:
            self._reply(409, {"ok": False, "error": {"code": "failed", "message": redact_secrets(str(exc))}})
        except Exception as exc:  # noqa: BLE001 - the window gets a reason; the engine goes on
            self._reply(500, {"ok": False, "error": {"code": "internal", "message": redact_secrets(f"{type(exc).__name__}: {exc}")}})

    def _guard(self) -> None:
        if self.client_address[0] != "127.0.0.1":
            raise Refused(403, "refused", "Only programs on this PC can use the editor channel.")
        port = self.owner.port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise Refused(403, "refused", "The Host header must name this PC's loopback address and port.")
        if self.headers.get("Origin") is not None:
            raise Refused(403, "refused", "Web pages can't use the editor channel.")

    def _body(self) -> Any:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            raise Refused(411, "invalid_input", "Send the body with a Content-Length.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Refused(400, "invalid_input", "Content-Length isn't a number.") from None
        if length < 0:
            raise Refused(400, "invalid_input", "Content-Length can't be negative.")
        if length > MAX_BODY_BYTES:
            raise Refused(413, "too_large", "Requests are at most 1 MiB.")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise Refused(415, "invalid_input", "Send JSON (Content-Type: application/json).")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise Refused(400, "invalid_input", "The body isn't JSON.") from None

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

