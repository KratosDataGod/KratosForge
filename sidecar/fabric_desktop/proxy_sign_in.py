"""Windows sign-in to a corporate proxy that answers 407 (Preview spike, off by default).

Some proxies refuse a tunnel with ``407 Proxy Authentication Required`` until the client signs in
with the Windows account (``Negotiate``: Kerberos, or NTLM when Kerberos isn't set up). Edge and
other WinINet/WinHTTP apps do that without asking; requests and urllib3 don't. With the policy file
setting ``"proxy_windows_sign_in": true``, the shared HTTP session (http_session.py) opens its
HTTPS tunnels itself and answers that challenge with a token Windows makes for the signed-in
account (SSPI, through ctypes). No password is read, typed, kept or logged, and no token is logged.

Where a token goes:
- Only to the proxy requests already chose for the request (HTTPS_PROXY or the Windows proxy
  setting), only when IT named that proxy in ``proxy_windows_sign_in_hosts``, and only in the
  CONNECT that asks it for a tunnel. The token is made for that proxy's
  name (``HTTP/<proxy host>``) and for no other host.
- Never to the server at the far end: a 407 or 401 that comes back inside the tunnel is the
  server's, and it reaches the caller as it is, unanswered.
- Never for plain-http requests or for an ``https://`` proxy, which keep requests' own handling.
- Never as ``Basic``: there is no password to send.

TLS to the server is unchanged: urllib3 still wraps the tunnel with the same SSL context and checks
the server's certificate and name. What this doesn't cover yet is in
/mnt/project-files/enterprise/ (the findings note): MSAL's own session, the updater (urllib), the
engine download (curl), delta-rs and DuckDB.
"""

from __future__ import annotations

import base64
import socket
import sys
from typing import Any, BinaryIO, Callable, Protocol

from requests.adapters import HTTPAdapter
from requests.utils import prepend_scheme_if_needed, select_proxy
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.util import connection as urllib3_connection
from urllib3.util.url import parse_url

POLICY_KEY = "proxy_windows_sign_in"
POLICY_HOSTS_KEY = "proxy_windows_sign_in_hosts"
# Proxy-Authenticate schemes answered, in the order preferred, and the SSPI package for each.
PACKAGES = {"negotiate": "Negotiate", "ntlm": "NTLM"}
MAX_ROUNDS = 4  # NTLM takes three CONNECTs on one connection; Kerberos one or two.
MAX_HEAD = 64 * 1024  # bytes of status line and headers read from the proxy
MAX_BODY = 1024 * 1024  # bytes of a 407 page read and dropped so the connection can be reused


class ProxySignInError(OSError):
    """The proxy didn't open the tunnel. The message never holds a token."""


class Context(Protocol):
    """One sign-in conversation with one proxy (SspiContext, or a stand-in in tests)."""

    def step(self, challenge: bytes | None) -> bytes: ...

    def close(self) -> None: ...


ContextFactory = Callable[[str, str], Context]  # (SSPI package, proxy host) -> context


def enabled() -> bool:
    """Whether the policy file turned it on (Windows only: there is no SSPI elsewhere)."""
    from . import app_settings

    return sys.platform == "win32" and app_settings.value(POLICY_KEY) is True


def allowed_hosts() -> frozenset[str]:
    """The proxies IT allows to receive a Windows sign-in (lower case). A proxy not named here
    gets none, so a changed proxy setting can't collect the account's NTLM answer."""
    from . import app_settings

    hosts = app_settings.value(POLICY_HOSTS_KEY)
    return frozenset(h.strip().lower() for h in hosts if isinstance(h, str) and h.strip()) if isinstance(hosts, list) else frozenset()


def _response(rfile: BinaryIO) -> tuple[str, int, dict[str, list[str]]]:
    """(HTTP version, status, headers by lower-case name) of the proxy's answer."""
    line = rfile.readline(MAX_HEAD)
    if not line:
        raise ProxySignInError("the proxy closed the connection")
    parts = line.decode("latin-1").split(None, 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise ProxySignInError("the proxy's answer wasn't HTTP")
    headers: dict[str, list[str]] = {}
    total = len(line)
    while True:
        raw = rfile.readline(MAX_HEAD)
        total += len(raw)
        if total > MAX_HEAD:
            raise ProxySignInError("the proxy's answer was too long")
        if raw in (b"\r\n", b"\n", b""):
            break
        name, _, value = raw.decode("latin-1").partition(":")
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    return parts[0], int(parts[1]), headers


def _read_exactly(rfile: BinaryIO, size: int) -> None:
    while size > 0:
        chunk = rfile.read(min(size, 65536))
        if not chunk:
            raise ProxySignInError("the proxy closed the connection")
        size -= len(chunk)


def _drop_body(rfile: BinaryIO, headers: dict[str, list[str]]) -> None:
    """Read the 407 page so the next CONNECT starts on a clean connection."""
    if any("chunked" in v.lower() for v in headers.get("transfer-encoding", [])):
        total = 0
        while True:
            digits = rfile.readline(1024).split(b";")[0].strip()
            if not digits or len(digits) > 8 or any(c not in b"0123456789abcdefABCDEF" for c in digits):
                raise ProxySignInError("the proxy's sign-in page wasn't readable")
            size = int(digits, 16)
            total += size
            if total > MAX_BODY:
                raise ProxySignInError("the proxy's sign-in page was too long")
            if size == 0:
                while rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                    pass
                return
            _read_exactly(rfile, size + 2)
    lengths = headers.get("content-length", ["0"])
    size = int(lengths[0]) if lengths[0].isdigit() else 0
    if size > MAX_BODY:
        raise ProxySignInError("the proxy's sign-in page was too long")
    _read_exactly(rfile, size)


def _closing(version: str, headers: dict[str, list[str]]) -> bool:
    words = {w.strip().lower() for v in headers.get("connection", []) + headers.get("proxy-connection", [])
             for w in v.split(",")}
    return "close" in words or (version == "HTTP/1.0" and "keep-alive" not in words)


def offer(challenges: list[str]) -> tuple[str, bytes | None] | None:
    """The sign-in this client can give from the proxy's Proxy-Authenticate headers: (SSPI package,
    the proxy's token or None). None when it only offers what this can't answer (Basic, Digest)."""
    found: dict[str, bytes | None] = {}
    for challenge in challenges:
        for part in challenge.split(","):  # several schemes can share one header
            scheme, _, data = part.strip().partition(" ")
            if scheme.lower() in PACKAGES and scheme.lower() not in found:
                try:
                    found[scheme.lower()] = base64.b64decode(data.strip(), validate=True) if data.strip() else None
                except ValueError:
                    raise ProxySignInError("the proxy's sign-in token wasn't readable") from None
    for scheme, package in PACKAGES.items():
        if scheme in found:
            return package, found[scheme]
    return None


def _connect_request(host: str, port: int, scheme: str | None, token: bytes | None) -> bytes:
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}", "Proxy-Connection: Keep-Alive"]
    if scheme and token:
        lines.append(f"Proxy-Authorization: {scheme} {base64.b64encode(token).decode('ascii')}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def open_tunnel(connect: Callable[[], socket.socket], proxy_host: str, host: str, port: int,
                new_context: ContextFactory) -> socket.socket:
    """A socket tunnelled through the proxy to host:port, signing in with Windows when the proxy
    asks. ``connect`` opens a new connection to the proxy (used again only when the proxy closes
    the connection before any token was sent)."""
    sock = connect()
    context: Context | None = None
    scheme: str | None = None
    token: bytes | None = None
    try:
        for round_ in range(MAX_ROUNDS + 1):
            sock.sendall(_connect_request(host, port, scheme, token))
            rfile = sock.makefile("rb", buffering=0)  # unbuffered: nothing past the answer is taken
            try:
                version, status, headers = _response(rfile)  # type: ignore[arg-type]
                if status == 200:
                    return sock
                if status != 407:
                    raise ProxySignInError(f"the proxy answered {status} when asked for a tunnel")
                if round_ == MAX_ROUNDS:
                    raise ProxySignInError("the proxy kept asking for a sign-in")
                chosen = offer(headers.get("proxy-authenticate", []))
                if chosen is None:
                    raise ProxySignInError("the proxy asks for a sign-in other than Windows sign-in")
                package, challenge = chosen
                if context is None:
                    context, scheme = new_context(package, proxy_host), "NTLM" if package == "NTLM" else "Negotiate"
                    challenge = None  # the first token never depends on the proxy
                elif challenge is None:
                    raise ProxySignInError("the proxy turned down the Windows sign-in")
                token = context.step(challenge)
                first_round = round_ == 0
                closing = _closing(version, headers)
                if not closing:
                    _drop_body(rfile, headers)  # type: ignore[arg-type]
            finally:
                rfile.close()
            if closing:
                if not first_round:  # NTLM's later rounds only make sense on the same connection
                    raise ProxySignInError("the proxy closed the connection during the sign-in")
                sock.close()
                sock = connect()
        raise ProxySignInError("the proxy kept asking for a sign-in")  # not reached
    except BaseException:
        sock.close()
        raise
    finally:
        if context is not None:
            context.close()


class _TunnelConnection(HTTPSConnection):
    """An HTTPS connection whose socket is a tunnel this module opened (set by the pool)."""

    proxy_address: tuple[str, int] = ("", 0)
    new_context: ContextFactory | None = None

    def _new_conn(self) -> socket.socket:
        proxy_host, proxy_port = self.proxy_address
        factory = self.new_context or SspiContext

        def connect() -> socket.socket:
            return urllib3_connection.create_connection((proxy_host, proxy_port), self.timeout,  # type: ignore[arg-type]
                                                        source_address=self.source_address,
                                                        socket_options=self.socket_options)

        return open_tunnel(connect, proxy_host, self.host, self.port, factory)


class WindowsSignInAdapter(HTTPAdapter):
    """requests' adapter, except that an https request through an http:// proxy goes through a
    tunnel opened by ``open_tunnel``. Everything else is requests' own handling."""

    def __init__(self, *args: Any, new_context: ContextFactory | None = None,
                 allowed: frozenset[str] | None = None, **kwargs: Any) -> None:
        self._new_context = new_context
        self._allowed = allowed_hosts() if allowed is None else frozenset(h.lower() for h in allowed)
        self._pools: dict[tuple[Any, ...], HTTPSConnectionPool] = {}
        super().__init__(*args, **kwargs)

    def _tunnel_proxy(self, url: str, proxies: dict[str, str] | None) -> tuple[str, int] | None:
        proxy = select_proxy(url, proxies)
        if not proxy or not url.lower().startswith("https://"):
            return None
        parsed = parse_url(prepend_scheme_if_needed(proxy, "http"))
        if parsed.scheme != "http" or not parsed.host or parsed.auth:
            return None  # an https:// proxy, or one with a user name: requests decides
        if parsed.host.lower() not in self._allowed:
            return None  # a proxy IT didn't name gets no sign-in
        return parsed.host, parsed.port or 80

    def get_connection_with_tls_context(self, request: Any, verify: Any, proxies: dict[str, str] | None = None,
                                        cert: Any = None) -> Any:
        proxy = self._tunnel_proxy(request.url or "", proxies)
        if proxy is None:
            return super().get_connection_with_tls_context(request, verify, proxies, cert)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        key = (proxy, host_params["host"], host_params["port"], tuple(sorted((k, id(v)) for k, v in pool_kwargs.items())))
        pool = self._pools.get(key)
        if pool is None:
            connection = type("TunnelConnection", (_TunnelConnection,),
                              {"proxy_address": proxy, "new_context": staticmethod(self._new_context)
                               if self._new_context else None})
            pool = HTTPSConnectionPool(host_params["host"], host_params["port"], maxsize=self._pool_maxsize,
                                       block=self._pool_block, **pool_kwargs)
            pool.ConnectionCls = connection
            self._pools[key] = pool
        return pool

    def request_url(self, request: Any, proxies: Any) -> str:
        if self._tunnel_proxy(request.url or "", proxies) is not None:
            return str(request.path_url)  # a tunnelled request names only the path, as without a proxy
        return str(super().request_url(request, proxies))

    def close(self) -> None:
        for pool in self._pools.values():
            pool.close()
        self._pools.clear()
        super().close()


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Handle(ctypes.Structure):  # SecHandle / CredHandle / CtxtHandle
        _fields_ = [("lower", ctypes.c_size_t), ("upper", ctypes.c_size_t)]

    class _Buffer(ctypes.Structure):  # SecBuffer
        _fields_ = [("size", wintypes.ULONG), ("kind", wintypes.ULONG), ("data", ctypes.c_void_p)]

    class _BufferDesc(ctypes.Structure):  # SecBufferDesc
        _fields_ = [("version", wintypes.ULONG), ("count", wintypes.ULONG), ("buffers", ctypes.POINTER(_Buffer))]

    _secur32 = ctypes.WinDLL("secur32")
    _acquire = _secur32.AcquireCredentialsHandleW
    _acquire.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Handle), ctypes.POINTER(ctypes.c_longlong)]
    _acquire.restype = ctypes.c_long
    _initialize = _secur32.InitializeSecurityContextW
    _initialize.argtypes = [ctypes.POINTER(_Handle), ctypes.POINTER(_Handle), wintypes.LPCWSTR, wintypes.ULONG,
                            wintypes.ULONG, wintypes.ULONG, ctypes.POINTER(_BufferDesc), wintypes.ULONG,
                            ctypes.POINTER(_Handle), ctypes.POINTER(_BufferDesc), ctypes.POINTER(wintypes.ULONG),
                            ctypes.POINTER(ctypes.c_longlong)]
    _initialize.restype = ctypes.c_long
    _complete = _secur32.CompleteAuthToken
    _complete.argtypes = [ctypes.POINTER(_Handle), ctypes.POINTER(_BufferDesc)]
    _complete.restype = ctypes.c_long
    _free_buffer = _secur32.FreeContextBuffer
    _free_buffer.argtypes = [ctypes.c_void_p]
    _free_buffer.restype = ctypes.c_long
    _delete_context = _secur32.DeleteSecurityContext
    _delete_context.argtypes = [ctypes.POINTER(_Handle)]
    _delete_context.restype = ctypes.c_long
    _free_credentials = _secur32.FreeCredentialsHandle
    _free_credentials.argtypes = [ctypes.POINTER(_Handle)]
    _free_credentials.restype = ctypes.c_long

    _OUTBOUND = 2  # SECPKG_CRED_OUTBOUND
    _TOKEN = 2  # SECBUFFER_TOKEN
    _NATIVE = 0x10  # SECURITY_NATIVE_DREP
    _FLAGS = 0x100 | 0x800  # ISC_REQ_ALLOCATE_MEMORY | ISC_REQ_CONNECTION
    _OK, _CONTINUE, _COMPLETE, _COMPLETE_AND_CONTINUE = 0, 0x90312, 0x90313, 0x90314

    class SspiContext:
        """The signed-in Windows account's sign-in to one proxy, through SSPI. Windows makes each
        token; this class only carries them. Nothing here reads or holds a password."""

        def __init__(self, package: str, proxy_host: str) -> None:
            if package not in PACKAGES.values():
                raise ProxySignInError("the proxy asks for a sign-in other than Windows sign-in")
            self._target = f"HTTP/{proxy_host}"
            self._credentials: Any = _Handle()
            self._context: Any = None
            status = _acquire(None, package, _OUTBOUND, None, None, None, None, ctypes.byref(self._credentials),
                              ctypes.byref(ctypes.c_longlong()))
            if status != _OK:
                raise ProxySignInError(f"Windows couldn't start a {package} sign-in (0x{status & 0xFFFFFFFF:08X})")

        def step(self, challenge: bytes | None) -> bytes:
            out_buffer = _Buffer(0, _TOKEN, None)
            out_desc = _BufferDesc(0, 1, ctypes.pointer(out_buffer))
            in_desc = None
            keep: Any = None
            if challenge is not None:
                keep = ctypes.create_string_buffer(challenge, len(challenge))
                in_buffer = _Buffer(len(challenge), _TOKEN, ctypes.cast(keep, ctypes.c_void_p))
                in_desc = _BufferDesc(0, 1, ctypes.pointer(in_buffer))
            # Later rounds pass the same handle as phContext and phNewContext, as SSPI expects.
            new_context = self._context if self._context is not None else _Handle()
            attributes = wintypes.ULONG()
            status = _initialize(ctypes.byref(self._credentials), ctypes.byref(self._context) if self._context else None,
                                 self._target, _FLAGS, 0, _NATIVE, ctypes.byref(in_desc) if in_desc else None, 0,
                                 ctypes.byref(new_context), ctypes.byref(out_desc), ctypes.byref(attributes),
                                 ctypes.byref(ctypes.c_longlong()))
            status &= 0xFFFFFFFF
            try:
                if status not in (_OK, _CONTINUE, _COMPLETE, _COMPLETE_AND_CONTINUE):
                    raise ProxySignInError(f"Windows couldn't sign in to the proxy (0x{status:08X})")
                if self._context is None:
                    self._context = new_context
                if status in (_COMPLETE, _COMPLETE_AND_CONTINUE):
                    _complete(ctypes.byref(self._context), ctypes.byref(out_desc))
                return ctypes.string_at(out_buffer.data, out_buffer.size) if out_buffer.data else b""
            finally:
                if out_buffer.data:
                    _free_buffer(out_buffer.data)

        def close(self) -> None:
            if self._context is not None:
                _delete_context(ctypes.byref(self._context))
                self._context = None
            if self._credentials is not None:
                _free_credentials(ctypes.byref(self._credentials))
                self._credentials = None

else:
    class SspiContext:  # type: ignore[no-redef]
        """No SSPI off Windows: the setting does nothing there (enabled() is False)."""

        def __init__(self, package: str, proxy_host: str) -> None:
            raise ProxySignInError("Windows sign-in to a proxy needs Windows")

        def step(self, challenge: bytes | None) -> bytes:
            raise ProxySignInError("Windows sign-in to a proxy needs Windows")

        def close(self) -> None:
            return None
