"""Behind a corporate network: every outbound call goes through the proxy, trusts the company's root
certificate, and fails with a line that says what to do, never a silent hang.

Everything here is local: a certificate authority made for the test, an HTTPS server it signs, and a
proxy that tunnels, refuses with 407, or swallows the call. No call leaves the machine.
"""

from __future__ import annotations

import ast
import datetime
import http.server
import json
import socket
import ssl
import threading
import time
import urllib.error
from pathlib import Path

import msal
import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from fabric_desktop import auth, credentials, http_session, net_trust, server

SIDECAR = Path(__file__).resolve().parents[1] / "fabric_desktop"
UPDATER = Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "src" / "updater.py"
# MSAL as it ships: conftest swaps in a copy that never reaches Entra; here only the local proxy is reached.
REAL_MSAL_APP = msal.PublicClientApplication
NETWORK_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
               "NO_PROXY", "no_proxy", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR")


# --- a company root certificate and a site it signs -------------------------------------------------

def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


@pytest.fixture(scope="module")
def company_ca(tmp_path_factory):
    """A root certificate as an HTTPS-inspecting network has, and a cert for localhost it signed."""
    folder = tmp_path_factory.mktemp("ca")
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name("Contoso Inspection Root")).issuer_name(_name("Contoso Inspection Root"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(_name("localhost")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (folder / "root.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "site.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (folder / "site.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    return folder


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"value": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _exclusive(sock: socket.socket) -> None:
    """On Windows, keep the port to this socket alone. Without it, another process in the run that
    binds the same port with SO_REUSEADDR (a server started on a "free" port found a moment before)
    can take its connections, and the caller sees the connection closed with no answer."""
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)


def _listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _exclusive(sock)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    return sock


class _Site(http.server.ThreadingHTTPServer):
    request_queue_size = 64  # the default 5 refuses callers on Windows when the run is busy
    allow_reuse_address = False

    def server_bind(self):
        _exclusive(self.socket)
        super().server_bind()

    def handle_error(self, request, client_address):
        # A caller that refuses the certificate drops the handshake; that is the test, not an error.
        pass


@pytest.fixture(scope="module", autouse=True)
def python_ssl():
    """Python's own SSLContext while these tests run. On Windows an earlier test in the same run can
    put truststore in place (cli.main -> net_trust.use_system_store), and truststore checks the peer's
    certificate on the server side too: the test server then refused every client, which sends none.
    The Windows store itself isn't what these tests check; they use REQUESTS_CA_BUNDLE."""
    injected = ssl.SSLContext.__module__ != "ssl"
    if injected:
        import truststore
        truststore.extract_from_ssl()
    yield
    if injected:
        truststore.inject_into_ssl()


@pytest.fixture(scope="module")
def site(company_ca, python_ssl):
    """https://localhost:<port>, its certificate signed by the company's root."""
    httpd = _Site(("127.0.0.1", 0), _Ok)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(company_ca / "site.pem", company_ca / "site.key")
    # The handshake runs in each request's own thread, not in the accept loop: one slow handshake
    # under a busy test run can't hold up the next caller's connection.
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"https://localhost:{httpd.server_address[1]}"
    httpd.shutdown()


# --- a proxy --------------------------------------------------------------------------------------

class Proxy:
    """A CONNECT proxy on 127.0.0.1. mode: "tunnel" passes the call on, "407" asks for its own
    sign-in, "silent" takes the connection and never answers."""

    def __init__(self, mode: str):
        self.mode, self.asked, self.errors = mode, [], []
        self.sock = _listener()
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.held: list[socket.socket] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn: socket.socket):
        try:
            self._answer(conn)
        except Exception as e:  # noqa: BLE001 - say what went wrong instead of dropping the caller with no answer
            self.errors.append(repr(e))
            try:
                reason = repr(e).encode(errors="replace")[:200]
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                             % (len(reason), reason))
            except OSError:
                pass
            conn.close()

    @staticmethod
    def _upstream(host: str, port: int) -> socket.socket:
        """The site, with a few tries: a busy machine can refuse a local connect once."""
        for attempt in range(5):
            try:
                return socket.create_connection((host, port), timeout=10)
            except (ConnectionRefusedError, ConnectionResetError, TimeoutError):
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
        raise AssertionError("unreachable")

    def _answer(self, conn: socket.socket):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                return conn.close()
            head += chunk
        self.asked.append(head.split(b"\r\n", 1)[0].decode())
        if self.mode == "silent":
            self.held.append(conn)  # keep it open, say nothing
            return
        if self.mode == "407":
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Negotiate\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            return conn.close()
        host, port = head.split(b" ", 2)[1].decode().rsplit(":", 1)
        upstream = self._upstream("127.0.0.1" if host == "localhost" else host, int(port))
        upstream.settimeout(None)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        ends = [2]
        for a, b in ((conn, upstream), (upstream, conn)):
            threading.Thread(target=self._pipe, args=(a, b, ends), daemon=True).start()

    _ends_lock = threading.Lock()

    @classmethod
    def _pipe(cls, a: socket.socket, b: socket.socket, ends: list[int]):
        """One direction of the tunnel. When ``a`` has said all it will, pass that on to ``b`` as a
        half-close and keep the other direction open: closing both here could drop bytes the other
        direction hasn't passed on yet. Both sockets close when both directions are done."""
        try:
            while data := a.recv(65536):
                b.sendall(data)
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        with cls._ends_lock:
            ends[0] -= 1
            last = ends[0] == 0
        if last:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def close(self):
        self.sock.close()
        for c in self.held:
            c.close()


@pytest.fixture
def network(monkeypatch):
    """A clean network setting: none of the machine's own proxy or certificate variables."""
    for name in NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    proxies: list[Proxy] = []

    def proxy(mode: str = "tunnel") -> Proxy:
        p = Proxy(mode)
        proxies.append(p)
        return p
    yield proxy
    for p in proxies:
        p.close()


# --- the engine's own HTTPS (requests: Fabric API, OneLake, Graph) ------------------------------------

def test_a_call_goes_through_the_configured_proxy_and_trusts_the_company_root(network, monkeypatch, company_ca, site):
    proxy = network()
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(company_ca / "root.pem"))
    r = http_session.new().get(f"{site}/v1/workspaces", timeout=10)
    assert r.status_code == 200 and r.json() == {"value": []}
    assert proxy.asked == [f"CONNECT {site.removeprefix('https://')} HTTP/1.1"]


def test_the_proxy_windows_is_set_to_is_used_when_no_variable_is_set(network, monkeypatch, company_ca, site):
    # requests reads the Windows proxy (Internet Options) through urllib's getproxies(); this stands in for it.
    proxy = network()
    monkeypatch.setattr(requests.utils, "getproxies", lambda: {"https": proxy.url})
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(company_ca / "root.pem"))
    try:
        r = http_session.session().get(f"{site}/v1/workspaces", timeout=10)
    except requests.RequestException as e:   # say what the test's own proxy saw, to tell where it failed
        pytest.fail(f"{e!r}; the proxy saw {proxy.asked} and raised {proxy.errors}")
    assert r.status_code == 200
    assert len(proxy.asked) == 1 and proxy.asked[0].startswith("CONNECT localhost:")


def test_no_proxy_skips_the_proxy(network, monkeypatch, company_ca, site):
    proxy = network()
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(company_ca / "root.pem"))
    assert http_session.new().get(f"{site}/x", timeout=10).status_code == 200
    assert proxy.asked == []


def test_a_site_the_pc_doesnt_trust_says_to_add_the_root_certificate(network, monkeypatch, site):
    monkeypatch.setenv("HTTPS_PROXY", network().url)
    with pytest.raises(requests.exceptions.SSLError) as caught:
        http_session.new().get(f"{site}/x", timeout=10)
    assert net_trust.explain(caught.value) == net_trust.HINT


def test_a_proxy_that_wants_its_own_sign_in_says_so(network, monkeypatch, site):
    monkeypatch.setenv("HTTPS_PROXY", network("407").url)
    with pytest.raises(requests.exceptions.ProxyError) as caught:
        http_session.new().get(f"{site}/x", timeout=10)
    assert net_trust.explain(caught.value) == net_trust.PROXY_HINT


def test_a_proxy_that_never_answers_times_out_with_a_reason(network, monkeypatch, site):
    monkeypatch.setenv("HTTPS_PROXY", network("silent").url)
    started = time.monotonic()
    with pytest.raises(requests.exceptions.RequestException) as caught:
        http_session.new().get(f"{site}/x", timeout=0.5)
    assert time.monotonic() - started < 5
    assert net_trust.explain(caught.value) == net_trust.TIMEOUT_HINT


def test_a_call_with_no_timeout_still_has_one(monkeypatch):
    seen = []
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **kw: seen.append(kw["timeout"]))
    http_session.new().get("https://api.fabric.microsoft.com/v1/workspaces")
    assert seen == [http_session.DEFAULT_TIMEOUT]


def test_the_app_shows_the_proxy_reason_first(monkeypatch):
    class Svc:
        def list_workspaces(self):
            raise requests.exceptions.ProxyError("Tunnel connection failed: 407 Proxy Authentication Required")
    monkeypatch.setattr(server, "METHODS", {"list_workspaces"})
    monkeypatch.setattr(server.rpc_params, "check", lambda *a: None)
    monkeypatch.setattr(server.audit, "audited", lambda m: False)
    reply = server.handle(Svc(), json.dumps({"id": 1, "method": "list_workspaces", "params": {}}))
    assert reply["error"].startswith(net_trust.PROXY_HINT) and "407" in reply["error"]


def test_other_errors_get_no_network_reason():
    assert net_trust.explain(requests.ConnectionError("Connection refused")) is None
    assert net_trust.explain(ValueError("bad")) is None
    assert net_trust.explain(TimeoutError("the kernel didn't start in 60 s")) is None  # not a network call
    assert net_trust.explain(urllib.error.URLError(TimeoutError("timed out"))) == net_trust.TIMEOUT_HINT


# --- sign-in (MSAL) -----------------------------------------------------------------------------------

def test_sign_in_calls_have_a_timeout(monkeypatch, tmp_path):
    made = []

    class App:
        def __init__(self, *args, **kwargs):
            made.append(kwargs)

    monkeypatch.setattr(msal, "PublicClientApplication", App)
    monkeypatch.setattr(msal, "ConfidentialClientApplication", App)
    monkeypatch.setattr(auth, "_broker_available", lambda: False)
    auth.Authenticator(cache_path=tmp_path / "cache.bin")
    monkeypatch.setenv("KF_TEST_SECRET", "s")
    credentials.ServicePrincipal(tenant_id="t", client_id="c", client_secret_env="KF_TEST_SECRET")
    assert [k.get("timeout") for k in made] == [auth.SIGN_IN_TIMEOUT, http_session.DEFAULT_TIMEOUT]


def test_managed_identity_uses_a_session_with_a_timeout(monkeypatch):
    got = {}
    monkeypatch.setattr(msal, "ManagedIdentityClient", lambda identity, http_client: got.setdefault("http", http_client))
    credentials.ManagedIdentity()
    assert isinstance(got["http"], http_session._Session)


def test_msal_goes_through_the_proxy_and_gives_up_when_it_never_answers(network, monkeypatch):
    proxy = network("silent")
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    started = time.monotonic()
    with pytest.raises(requests.exceptions.RequestException) as caught:
        # Constructing it already asks Entra for the tenant's sign-in settings.
        REAL_MSAL_APP("00000000-0000-0000-0000-000000000000", authority=auth.AUTHORITY, timeout=0.5)
    assert time.monotonic() - started < 10
    assert proxy.asked and proxy.asked[0].startswith("CONNECT login.microsoftonline.com:443")
    assert net_trust.explain(caught.value) == net_trust.TIMEOUT_HINT


# --- the update check (the shell's updater.py, standard library only) ----------------------------------

@pytest.fixture
def updater():
    import importlib.util
    spec = importlib.util.spec_from_file_location("updater_net", UPDATER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_update_check_goes_through_the_proxy_and_says_why_it_failed(network, monkeypatch, updater):
    proxy = network("407")
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    monkeypatch.setattr(updater, "channel", lambda: "preview")
    got = updater.latest_release()
    assert proxy.asked and proxy.asked[0].startswith("CONNECT github.com:443")
    assert got["commit"] is None and got["error"].startswith(updater.PROXY_HINT)


def test_the_update_check_names_a_certificate_and_a_timeout(updater):
    cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer"))
    assert updater.explain(cert, "d") == f"{updater.CERT_HINT} (d)"
    assert updater.explain(urllib.error.URLError(TimeoutError("timed out")), "d") == f"{updater.TIMEOUT_HINT} (d)"
    assert updater.explain(OSError("No route to host"), "No route to host") == "No route to host"


def test_the_update_check_trusts_the_windows_certificate_store(updater, monkeypatch):
    # urllib's default HTTPS context loads Windows' ROOT and CA stores (ssl.create_default_context ->
    # load_default_certs), so the update check needs no truststore. It must not pass its own context.
    seen = {}
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=None, **kw: seen.update(kw, timeout=timeout))
    updater._get("https://github.com/x")
    assert "context" not in seen and "cafile" not in seen and seen["timeout"]


# --- no outbound call is written without a timeout ----------------------------------------------------

def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            yield node, owner, name, {k.arg for k in node.keywords}


def test_every_outbound_call_names_a_timeout_or_uses_the_shared_session():
    bad = []
    for path in [*sorted(SIDECAR.rglob("*.py")), UPDATER]:
        rel = path.name if path == UPDATER else path.relative_to(SIDECAR).as_posix()
        uses_requests = "import requests" in path.read_text(encoding="utf-8")  # else "requests" is a local name
        for node, owner, name, kws in _calls(path):
            where = f"{rel}:{node.lineno}"
            if uses_requests and owner == "requests" and name in {"get", "post", "put", "patch", "delete", "head", "request"} \
                    and "timeout" not in kws:
                bad.append(f"{where} requests.{name} without timeout")
            if uses_requests and owner == "requests" and name == "Session" and rel != "http_session.py":
                bad.append(f"{where} its own requests.Session (use http_session)")
            if name == "urlopen" and "timeout" not in kws:
                bad.append(f"{where} urlopen without timeout")
            if owner == "msal" and name in {"PublicClientApplication", "ConfidentialClientApplication"} \
                    and "timeout" not in kws and "http_client" not in kws:
                bad.append(f"{where} msal.{name} without timeout")
    assert bad == []
