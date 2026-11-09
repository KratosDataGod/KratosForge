"""A network that inspects HTTPS re-signs every site with the company's own root certificate, which
IT adds to Windows. Each client either trusts the Windows store or says the certificate isn't
trusted, and nothing offers to skip the check (enterprise/tls-inspection.md)."""
from __future__ import annotations

import http.server
import ssl
import subprocess
import sys
import threading
import types
from pathlib import Path

import certifi
import pytest

from fabric_desktop import net_trust
from fabric_desktop.engines import dbconnect, runtime_worker

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"


@pytest.fixture
def untrusted_https(tmp_path):
    """A local HTTPS server whose certificate nothing on this PC trusts, as an inspecting proxy's
    would be before IT adds its root."""
    key, cert = tmp_path / "k.pem", tmp_path / "c.pem"
    made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                           "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                          capture_output=True, check=False)
    if made.returncode:
        pytest.skip("openssl can't make a test certificate here")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_requests_to_an_untrusted_certificate_says_so(untrusted_https, monkeypatch):
    import requests
    monkeypatch.setenv("NO_PROXY", "*")
    with pytest.raises(requests.exceptions.SSLError) as caught:
        requests.get(untrusted_https, timeout=10)
    assert net_trust.explain(caught.value) == net_trust.HINT


def test_delta_rs_to_an_untrusted_certificate_says_so(untrusted_https, monkeypatch):
    """delta-rs (object_store, rustls) says "invalid peer certificate", which now gets the line."""
    deltalake = pytest.importorskip("deltalake")
    monkeypatch.setenv("NO_PROXY", "*")
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(OSError) as caught:
        deltalake.DeltaTable("abfss://c@acct.dfs.fabric.microsoft.com/t",
                             storage_options={"bearer_token": "x", "azure_storage_endpoint": untrusted_https,
                                              "max_retries": "0", "retry_timeout": "5s", "timeout": "10s"})
    assert "invalid peer certificate" in str(caught.value)
    assert net_trust.explain(caught.value) == net_trust.HINT


def test_the_hint_never_offers_to_skip_the_check():
    for line in (net_trust.HINT, net_trust.pip_unreachable("SSLCertVerificationError('x')", "Ask IT.") or ""):
        low = line.lower()
        assert "verify=false" not in low and "trusted-host" not in low and "insecure" not in low and "skip" not in low


def test_pip_on_windows_reports_truststores_refusal_as_a_certificate():
    """pip checks against Windows (truststore), whose refusal names no OpenSSL code."""
    log = ("WARNING: Retrying (Retry(total=2...)) after connection broken by 'SSLError(SSLCertVerificationError('A certificate "
           "chain processed, but terminated in a root certificate which is not trusted by the trust provider.'))': /simple/x/\n"
           "ERROR: No matching distribution found for x\n")
    assert "root certificate" in net_trust.pip_unreachable(log, "Ask IT.")


class _Store:
    def __init__(self, certs):
        self.certs = certs

    def __call__(self, name):
        assert name == "ROOT"
        return self.certs


def test_windows_roots_are_written_for_grpc(tmp_path, monkeypatch, untrusted_https):
    cert = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(("127.0.0.1", int(untrusted_https.rsplit(":", 1)[1]))))
    monkeypatch.setattr(net_trust.sys, "platform", "win32")
    other = b"\x30\x03\x02\x01\x01"  # not used for HTTPS: left out
    monkeypatch.setattr(ssl, "enum_certificates", _Store([(cert, "x509_asn", True),
                                                          (other, "x509_asn", {"1.3.6.1.5.5.7.3.3"})]), raising=False)
    path = net_trust.windows_roots_pem(tmp_path)
    assert path == tmp_path / "windows-roots.pem"
    text = path.read_text()
    blocks = net_trust._pem_blocks(text)
    assert net_trust._pem_blocks(ssl.DER_cert_to_PEM_cert(cert))[0] in blocks
    # The public list stays in: the file replaces gRPC's own, and Windows fetches public roots on demand.
    public = net_trust._pem_blocks(Path(certifi.where()).read_text())
    assert public and set(public) <= set(blocks)
    assert len(blocks) == len(set(blocks)) and not list(tmp_path.glob("*.tmp"))
    assert net_trust.windows_roots_pem(tmp_path) == path  # unchanged: not rewritten
    monkeypatch.setattr(ssl, "enum_certificates", _Store([]), raising=False)
    assert net_trust.windows_roots_pem(tmp_path / "none") is None
    monkeypatch.setattr(net_trust.sys, "platform", "linux")
    assert net_trust.windows_roots_pem(tmp_path) is None


def test_databricks_connect_gets_windows_roots_unless_it_names_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(net_trust, "windows_roots_pem", lambda folder: folder / "windows-roots.pem")
    m2m = {"platform": "databricks", "host": HOST,
           "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "env:SP"}}
    env = dbconnect.worker_env(m2m, "c1", {"PATH": "/bin"}, environ={"SP": "s"}, roots_dir=tmp_path)
    assert env["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == str(tmp_path / "windows-roots.pem")
    env = dbconnect.worker_env(m2m, "c1", {"PATH": "/bin", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": "C:\\it\\roots.pem"},
                               environ={"SP": "s"}, roots_dir=tmp_path)
    assert env["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == "C:\\it\\roots.pem"
    assert "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH" not in dbconnect.worker_env(m2m, "c1", {"PATH": "/bin"}, environ={"SP": "s"})


def test_the_databricks_connect_worker_trusts_windows(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=lambda: calls.append(1)))
    monkeypatch.setattr(runtime_worker.sys, "platform", "win32")
    runtime_worker._trust_windows()
    assert calls == [1]
    monkeypatch.setattr(runtime_worker.sys, "platform", "linux")
    runtime_worker._trust_windows()
    assert calls == [1]
