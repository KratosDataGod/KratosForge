"""Mutation-testing kill tests for net_trust (batch 86): which failures get which one-line reason, how pip's
output is read, and the root-certificate file written for gRPC. Public certificates only: the "certificates"
below are a few made-up DER bytes, which the module only base64-wraps."""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import certifi
import requests
import urllib3

from fabric_desktop import jsonfile, net_trust

_REAL_WRITE_TEXT = jsonfile.write_text


def test_a_new_process_has_not_switched_to_the_windows_store_yet(tmp_path):
    # Checked in a process of its own, so the flag is the one the module starts with.
    sidecar = Path(net_trust.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(sidecar), "LOCALAPPDATA": str(tmp_path), "HOME": str(tmp_path)}
    code = "from fabric_desktop import net_trust as n; print(n._done, n.use_system_store('linux'))"
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_certificate_dates_reads_any_case_and_says_so_as_a_bool():
    assert net_trust.certificate_dates("x509: Certificate Has Expired") is True
    assert net_trust.certificate_dates("cert_e_expired") is True
    assert net_trust.certificate_dates(OSError("invalid peer certificate: NotValidYet")) is True
    assert net_trust.certificate_dates("unable to get local issuer certificate") is False


def _pem(der: bytes) -> str:
    return ssl.DER_cert_to_PEM_cert(der)


def test_pem_blocks_are_each_certificate_alone_and_normalised():
    one, two = _pem(b"\x30\x03\x02\x01\x07"), _pem(b"\x30\x03\x02\x01\x08")
    text = "junk before\n" + "  " + one.replace("\n", "  \n") + "between\n" + two + "-----BEGIN CERTIFICATE-----\nAAAA\n"
    assert net_trust._pem_blocks(text) == [one, two]  # stripped lines, one block each; the unfinished one is dropped
    assert net_trust._pem_blocks("no certificates here\n") == []


class _Store:
    def __init__(self, certs):
        self.certs = certs

    def __call__(self, name):
        assert name == "ROOT"
        return self.certs


def test_windows_roots_keep_only_x509_roots_trusted_for_server_sign_in(tmp_path, monkeypatch):
    every, server, code_signing, other_encoding = (b"\x30\x03\x02\x01\x11", b"\x30\x03\x02\x01\x12",
                                                    b"\x30\x03\x02\x01\x13", b"\x30\x03\x02\x01\x14")
    monkeypatch.setattr(net_trust.sys, "platform", "win32")
    monkeypatch.setattr(ssl, "enum_certificates", _Store([
        (every, "x509_asn", True),
        (server, "x509_asn", {"1.3.6.1.5.5.7.3.2", net_trust._SERVER_AUTH}),
        (code_signing, "x509_asn", {"1.3.6.1.5.5.7.3.3"}),
        (other_encoding, "pkcs_7_asn", True),
    ]), raising=False)
    path = net_trust.windows_roots_pem(tmp_path)
    assert path == tmp_path / net_trust.ROOTS_FILE
    blocks = net_trust._pem_blocks(path.read_text(encoding="ascii"))
    public = set(net_trust._pem_blocks(Path(certifi.where()).read_text(encoding="ascii")))
    assert set(blocks) - public == {_pem(every), _pem(server)}
    assert path.read_text(encoding="ascii") == "".join(sorted(set(blocks)))


def test_windows_roots_rewrite_a_stale_file_and_leave_a_current_one(tmp_path, monkeypatch):
    monkeypatch.setattr(net_trust.sys, "platform", "win32")
    monkeypatch.setattr(ssl, "enum_certificates", _Store([(b"\x30\x03\x02\x01\x21", "x509_asn", True)]), raising=False)
    writes = []

    def write_text(path, text, newline=None):
        writes.append(path)
        _REAL_WRITE_TEXT(path, text, newline=newline)

    monkeypatch.setattr(jsonfile, "write_text", write_text)
    stale = tmp_path / net_trust.ROOTS_FILE
    stale.write_text("an older list\n", encoding="ascii")
    path = net_trust.windows_roots_pem(tmp_path)
    assert writes == [stale] and _pem(b"\x30\x03\x02\x01\x21") in path.read_text(encoding="ascii")
    assert net_trust.windows_roots_pem(tmp_path) == path and writes == [stale]  # the same list: not written again


class ProxyError(OSError):
    pass


class _FromProxy(ProxyError):
    pass


def test_a_proxy_refusal_is_known_by_its_class_or_its_words():
    assert net_trust.proxy_refused(_FromProxy("no"))  # a subclass of a ProxyError counts
    assert net_trust.proxy_refused(OSError("Tunnel connection failed: 403 Forbidden"))
    assert net_trust.proxy_refused(OSError("407 Proxy Authentication Required"))
    assert not net_trust.proxy_refused(OSError("Connection reset by peer"))
    assert net_trust.explain(OSError("Tunnel connection failed: 403")) == net_trust.PROXY_HINT


def _requests_error(cause: BaseException) -> requests.ConnectionError:
    try:
        try:
            raise cause
        except BaseException as exc:
            raise requests.ConnectionError("failed") from exc
    except requests.ConnectionError as outer:
        return outer


def test_unreachable_needs_an_http_library_in_the_chain():
    assert net_trust.unreachable(_requests_error(socket.gaierror(11001, "getaddrinfo failed")))
    assert net_trust.unreachable(_requests_error(ConnectionRefusedError(10061, "refused")))
    # No HTTP call involved: a plain False, as the annotation says.
    assert net_trust.unreachable(socket.gaierror(11001, "getaddrinfo failed")) is False
    assert net_trust.unreachable(ConnectionRefusedError(10061, "the kernel's port")) is False
    assert not net_trust.unreachable(requests.ConnectionError("Connection reset by peer"))


def test_timed_out_needs_an_http_library_and_a_timeout():
    assert net_trust.timed_out(requests.ReadTimeout("read timed out"))
    assert not net_trust.timed_out(TimeoutError("the kernel didn't start"))
    assert not net_trust.timed_out(requests.ConnectionError("reset"))
    unresolved = urllib3.exceptions.NameResolutionError("host", None, socket.gaierror(11001, "x"))
    assert not net_trust.timed_out(unresolved)  # subclasses ConnectTimeoutError, but no timeout happened
    assert net_trust.explain(requests.ReadTimeout("read timed out")) == net_trust.TIMEOUT_HINT


FIX = "Ask IT to allow the index."


def test_pip_certificate_lines():
    plain = net_trust.pip_unreachable("ERROR: ... [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer", FIX)
    assert plain.startswith("pip couldn't check the package index's certificate, so") and "root certificate" in plain
    truststore = net_trust.pip_unreachable("SSLCertVerificationError('not trusted')", FIX)
    assert truststore == plain
    dated = net_trust.pip_unreachable("CERTIFICATE_VERIFY_FAILED certificate has expired", FIX)
    assert dated.startswith("pip couldn't check the package index's certificate at this PC's date and time")
    assert FIX not in plain and FIX not in dated


def test_pip_proxy_lines_name_the_fix():
    for log in ("ProxyError('Cannot connect to proxy.')", "Tunnel connection failed: 407"):
        line = net_trust.pip_unreachable(log, FIX)
        assert line == f"The proxy refused pip's connection to the package index, so it reported the packages as not found. {FIX}"


def test_pip_unreachable_lines_name_the_fix_for_each_marker():
    for marker in ("NameResolutionError", "NewConnectionError", "Failed to establish a new connection",
                   "ConnectTimeoutError", "Read timed out"):
        line = net_trust.pip_unreachable(f"WARNING: Retrying after {marker}\nERROR: No matching distribution found", FIX)
        assert line.startswith("pip couldn't reach the package index from this PC") and line.endswith(FIX), marker
    assert net_trust.pip_unreachable("ERROR: No matching distribution found for x", FIX) is None
