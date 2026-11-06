"""Windows sign-in to a proxy that answers 407 (proxy_sign_in.py, Preview spike).

A local proxy asks for Negotiate or NTLM sign-in the way a corporate proxy does. Everywhere, a
stand-in plays Windows' part with fixed tokens, so the CONNECT rounds, the rule that only the proxy
is answered, and TLS through the tunnel are checked. On Windows, one more test lets Windows itself
make the tokens (SSPI) and the local proxy check them with Windows too (AcceptSecurityContext), so
the real ctypes path signs in end to end with the account the tests run as. No call leaves the PC.
"""

from __future__ import annotations

import base64
import socket
import sys
import threading

import pytest
import requests

from fabric_desktop import app_settings, http_session, proxy_sign_in
from fabric_desktop.proxy_sign_in import ProxySignInError, WindowsSignInAdapter
from test_corporate_network import NETWORK_ENV, company_ca, python_ssl, site  # noqa: F401 - fixtures

NTLM_ROUNDS = [(None, b"first"), (b"challenge", b"answer")]


class FakeWindows:
    """Plays SSPI: the same tokens every time, and a record of what was asked of it."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.closed = 0

    def __call__(self, package: str, proxy_host: str) -> FakeWindows:
        self.started.append((package, proxy_host))
        return self

    def step(self, challenge: bytes | None) -> bytes:
        return dict(NTLM_ROUNDS)[challenge]

    def close(self) -> None:
        self.closed += 1


def scripted(scheme: str):
    """A proxy-side check that wants "first", sends "challenge", then wants "answer"."""

    def check(state: dict, token: bytes | None) -> tuple[bool, bytes | None]:
        if token == b"first":
            return False, b"challenge"
        return token == b"answer" and state.get("connection") is not None, None
    check.scheme = scheme  # type: ignore[attr-defined]
    return check


class SignInProxy:
    """A CONNECT proxy on 127.0.0.1 that asks for sign-in. check(state, token) -> (signed in, token
    to send back). close_first: answer the first 407 with Connection: close (as some proxies do)."""

    def __init__(self, check, scheme: str = "NTLM", close_first: bool = False, offer: str | None = None,
                 origin_407: bool = False) -> None:
        self.check, self.scheme, self.close_first, self.offer = check, scheme, close_first, offer or scheme
        self.origin_407 = origin_407
        self.authorizations: list[tuple[int, str | None]] = []  # (connection number, header)
        self.connections = 0
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._one, args=(conn, self.connections), daemon=True).start()

    @staticmethod
    def _head(rfile) -> dict[str, str] | None:
        line = rfile.readline()
        if not line:
            return None
        headers = {"_line": line.decode().strip()}
        while (raw := rfile.readline()) not in (b"\r\n", b""):
            name, _, value = raw.decode().partition(":")
            headers[name.strip().lower()] = value.strip()
        return headers

    def _one(self, conn: socket.socket, number: int) -> None:
        rfile = conn.makefile("rb", buffering=0)
        state: dict = {"connection": number}
        tunnelled = False
        try:
            while (head := self._head(rfile)) is not None:
                auth = head.get("proxy-authorization")
                self.authorizations.append((number, auth))
                token = None
                if auth:
                    scheme, _, data = auth.partition(" ")
                    token = base64.b64decode(data) if scheme == self.scheme else b""
                signed_in, reply = (False, None) if auth is None else self.check(state, token)
                if signed_in:
                    tunnelled = True
                    self._tunnel(conn, head["_line"].split()[1])
                    return
                if auth is not None and reply is None:
                    conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    return
                challenge = self.offer + (" " + base64.b64encode(reply).decode() if reply else "")
                closing = self.close_first and auth is None
                page = b"<html>Sign in to the proxy</html>"
                if closing or auth is None:
                    conn.sendall((f"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: {challenge}\r\n"
                                  f"Content-Length: {len(page)}\r\n{'Connection: close' if closing else 'Proxy-Connection: keep-alive'}"
                                  "\r\n\r\n").encode() + page)
                else:  # a chunked page, as Squid sends
                    conn.sendall((f"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: {challenge}\r\n"
                                  "Transfer-Encoding: chunked\r\n\r\n").encode()
                                 + f"{len(page):x}\r\n".encode() + page + b"\r\n0\r\n\r\n")
                if closing:
                    return
        except OSError:
            pass  # the client hung up, as it does after a refusal
        finally:
            rfile.close()
            if not tunnelled:
                conn.close()

    def _tunnel(self, conn: socket.socket, target: str) -> None:
        host, port = target.rsplit(":", 1)
        upstream = socket.create_connection(("127.0.0.1" if host == "localhost" else host, int(port)))
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        for a, b in ((conn, upstream), (upstream, conn)):
            threading.Thread(target=_pipe, args=(a, b), daemon=True).start()

    def close(self) -> None:
        self.sock.close()


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


@pytest.fixture
def clean_network(monkeypatch, company_ca):
    for name in NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(company_ca / "root.pem"))
    proxies: list[SignInProxy] = []

    def make(*args, **kwargs) -> SignInProxy:
        p = SignInProxy(*args, **kwargs)
        proxies.append(p)
        monkeypatch.setenv("HTTPS_PROXY", p.url)
        return p
    yield make
    for p in proxies:
        p.close()


def _session(windows: FakeWindows) -> requests.Session:
    s = requests.Session()
    s.mount("https://", WindowsSignInAdapter(new_context=windows, allowed=frozenset({"127.0.0.1"}), max_retries=0))
    return s


def test_ntlm_signs_in_over_three_rounds_on_one_connection(clean_network, site):
    proxy, windows = clean_network(scripted("NTLM")), FakeWindows()
    r = _session(windows).get(site + "/workspaces", timeout=10)
    assert r.status_code == 200 and r.json() == {"value": []}
    assert windows.started == [("NTLM", "127.0.0.1")] and windows.closed == 1
    first = proxy.authorizations[0][0]
    assert proxy.authorizations == [(first, None),
                                    (first, "NTLM " + base64.b64encode(b"first").decode()),
                                    (first, "NTLM " + base64.b64encode(b"answer").decode())]


def test_negotiate_after_a_proxy_that_closes_its_first_407(clean_network, site):
    def kerberos(state, token):  # Kerberos: one token, no challenge back
        return token == b"first", None
    proxy, windows = clean_network(kerberos, scheme="Negotiate", close_first=True), FakeWindows()
    assert _session(windows).get(site, timeout=10).status_code == 200
    assert windows.started == [("Negotiate", "127.0.0.1")]
    assert [a for _, a in proxy.authorizations] == [None, "Negotiate " + base64.b64encode(b"first").decode()]
    assert proxy.authorizations[0][0] != proxy.authorizations[1][0]  # a new connection after the close


def test_negotiate_is_preferred_when_the_proxy_offers_both(clean_network, site):
    def kerberos(state, token):
        return token == b"first", None
    clean_network(kerberos, scheme="Negotiate", offer="NTLM, Negotiate")
    windows = FakeWindows()
    assert _session(windows).get(site, timeout=10).status_code == 200
    assert windows.started[0][0] == "Negotiate"


def test_a_proxy_that_only_takes_a_password_gets_nothing(clean_network, site):
    proxy, windows = clean_network(scripted("Basic"), scheme="Basic"), FakeWindows()
    with pytest.raises(requests.exceptions.ConnectionError, match="other than Windows sign-in"):
        _session(windows).get(site, timeout=10)
    assert windows.started == [] and [a for _, a in proxy.authorizations] == [None]


def test_a_refused_sign_in_fails_without_the_token_in_the_message(clean_network, site):
    def refuse(state, token):
        return False, None
    clean_network(refuse), FakeWindows()
    with pytest.raises(requests.exceptions.ConnectionError) as failure:
        _session(FakeWindows()).get(site, timeout=10)
    text = str(failure.value)
    assert "403" in text and base64.b64encode(b"first").decode() not in text


def test_a_407_from_the_server_behind_the_proxy_is_never_answered(clean_network, company_ca, monkeypatch):
    """The server at the far end asks for proxy sign-in inside the tunnel: that's not the proxy, so
    it reaches the caller as it is and Windows is never asked for a token."""
    import http.server
    import ssl

    class Greedy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(407)
            self.send_header("Proxy-Authenticate", "Negotiate")
            self.send_header("WWW-Authenticate", "Negotiate")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Greedy)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(company_ca / "site.pem", company_ca / "site.key")
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        proxy, windows = clean_network(scripted("NTLM")), FakeWindows()
        r = _session(windows).get(f"https://localhost:{httpd.server_address[1]}/", timeout=10)
        assert r.status_code == 407
        assert len(windows.started) == 1  # the proxy's own sign-in only
        assert len(proxy.authorizations) == 3  # and no fourth CONNECT for the server's 407
    finally:
        httpd.shutdown()


def test_the_servers_certificate_is_still_checked_through_the_tunnel(clean_network, site, monkeypatch):
    clean_network(scripted("NTLM"))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", requests.certs.where())  # a bundle without the company root
    with pytest.raises(requests.exceptions.SSLError):
        _session(FakeWindows()).get(site, timeout=10)


def test_plain_http_and_proxies_with_a_user_name_keep_requests_own_handling():
    adapter = WindowsSignInAdapter(new_context=FakeWindows(), allowed=frozenset({"Proxy"}))
    assert adapter._tunnel_proxy("https://api.fabric.microsoft.com/v1", {"https": "http://proxy:8080"}) == ("proxy", 8080)
    assert adapter._tunnel_proxy("http://intranet/x", {"http": "http://proxy:8080"}) is None
    assert adapter._tunnel_proxy("https://a/", {"https": "http://user:pw@proxy:8080"}) is None
    assert adapter._tunnel_proxy("https://a/", {"https": "https://proxy:8443"}) is None
    assert adapter._tunnel_proxy("https://a/", {}) is None


def test_a_proxy_it_didnt_name_gets_no_sign_in(clean_network, site, monkeypatch, tmp_path):
    proxy, windows = clean_network(scripted("NTLM")), FakeWindows()
    s = requests.Session()
    s.mount("https://", WindowsSignInAdapter(new_context=windows, allowed=frozenset({"proxy.contoso.com"}), max_retries=0))
    with pytest.raises(requests.exceptions.ProxyError):
        s.get(site, timeout=10)
    assert windows.started == [] and all(a is None for _, a in proxy.authorizations)
    monkeypatch.setenv("ProgramData", str(tmp_path))
    assert proxy_sign_in.allowed_hosts() == frozenset()  # none named in policy: none get a sign-in


def test_the_proxys_offer_is_read_as_windows_sends_it():
    assert proxy_sign_in.offer(["Negotiate"]) == ("Negotiate", None)
    assert proxy_sign_in.offer(["NTLM " + base64.b64encode(b"x").decode()]) == ("NTLM", b"x")
    assert proxy_sign_in.offer(["Basic realm=\"proxy\"", "NTLM"]) == ("NTLM", None)
    assert proxy_sign_in.offer(["Basic realm=\"proxy\""]) is None
    with pytest.raises(ProxySignInError):
        proxy_sign_in.offer(["NTLM !!!"])


@pytest.mark.parametrize("size", [b"-5", b"zz", b"", b"123456789"])
def test_a_407_page_with_a_bad_chunk_size_is_refused(size):
    import io
    with pytest.raises(ProxySignInError, match="wasn't readable"):
        proxy_sign_in._drop_body(io.BytesIO(size + b"\r\nxxxxx\r\n0\r\n\r\n"), {"transfer-encoding": ["chunked"]})


def test_it_is_off_unless_the_policy_file_turns_it_on(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path))
    assert app_settings.value("proxy_windows_sign_in") is False
    assert "proxy_windows_sign_in" not in app_settings.USER_KEYS  # IT's choice, not a personal setting
    assert type(http_session._Session().get_adapter("https://x/")) is requests.adapters.HTTPAdapter
    monkeypatch.setattr(proxy_sign_in, "enabled", lambda: True)
    assert type(http_session._Session().get_adapter("https://x/")) is WindowsSignInAdapter


# --- Windows itself, end to end ---------------------------------------------------------------------

def _windows_proxy_check():
    """The proxy's side with Windows' own SSPI (AcceptSecurityContext), so the tokens the app's
    ctypes code makes are checked by Windows, not by a script."""
    import ctypes
    from ctypes import wintypes

    from fabric_desktop.proxy_sign_in import _Buffer, _BufferDesc, _Handle  # type: ignore[attr-defined]

    secur32 = ctypes.WinDLL("secur32")
    acquire = secur32.AcquireCredentialsHandleW
    acquire.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Handle), ctypes.POINTER(ctypes.c_longlong)]
    acquire.restype = ctypes.c_long
    accept = secur32.AcceptSecurityContext
    accept.argtypes = [ctypes.POINTER(_Handle), ctypes.POINTER(_Handle), ctypes.POINTER(_BufferDesc), wintypes.ULONG,
                       wintypes.ULONG, ctypes.POINTER(_Handle), ctypes.POINTER(_BufferDesc),
                       ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(ctypes.c_longlong)]
    accept.restype = ctypes.c_long
    secur32.FreeContextBuffer.argtypes = [ctypes.c_void_p]
    credentials = _Handle()
    assert acquire(None, "Negotiate", 1, None, None, None, None, ctypes.byref(credentials),
                   ctypes.byref(ctypes.c_longlong())) == 0  # SECPKG_CRED_INBOUND
    results: list[int] = []

    def check(state: dict, token: bytes | None) -> tuple[bool, bytes | None]:
        keep = ctypes.create_string_buffer(token or b"", len(token or b""))
        in_buffer = _Buffer(len(token or b""), 2, ctypes.cast(keep, ctypes.c_void_p))
        in_desc = _BufferDesc(0, 1, ctypes.pointer(in_buffer))
        out_buffer = _Buffer(0, 2, None)
        out_desc = _BufferDesc(0, 1, ctypes.pointer(out_buffer))
        new = _Handle()
        status = accept(ctypes.byref(credentials), ctypes.byref(state["ctx"]) if "ctx" in state else None,
                        ctypes.byref(in_desc), 0x100 | 0x800, 0x10, ctypes.byref(new), ctypes.byref(out_desc),
                        ctypes.byref(wintypes.ULONG()), ctypes.byref(ctypes.c_longlong())) & 0xFFFFFFFF
        results.append(status)
        state.setdefault("ctx", new)
        reply = ctypes.string_at(out_buffer.data, out_buffer.size) if out_buffer.data else None
        if out_buffer.data:
            secur32.FreeContextBuffer(out_buffer.data)
        if status == 0:
            return True, None
        return False, reply if status == 0x90312 else None
    return check, results


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sign-in needs Windows' SSPI")
def test_windows_signs_in_to_a_proxy_that_windows_checks(clean_network, site):
    check, results = _windows_proxy_check()
    proxy = clean_network(check, scheme="Negotiate")
    s = requests.Session()
    s.mount("https://", WindowsSignInAdapter(allowed=frozenset({"127.0.0.1"}), max_retries=0))  # the real SspiContext
    r = s.get(site, timeout=20)
    assert r.status_code == 200, (r.status_code, [hex(x) for x in results])
    assert results[-1] == 0  # SEC_E_OK: Windows accepted the account's sign-in
    assert all(a is None or a.startswith("Negotiate ") for _, a in proxy.authorizations)
