"""A PC clock that is off by minutes or hours says so, and never fails silently (enterprise/clock-skew.md).

A wrong date makes a valid certificate look expired or not valid yet, so those refusals ask to check
the clock, not to add a root certificate. The Ready to work list compares the PC's clock with the
last HTTPS server's Date. An audit line written after the clock was set back stays in trail order
and is noted, not reported as a moved line.
"""
from __future__ import annotations

import datetime as dt
import http.server
import importlib.util
import ipaddress
import ssl
import threading
import types
from pathlib import Path

import pytest

from fabric_desktop import audit_chain, http_session, net_trust, readiness

spec = importlib.util.spec_from_file_location(
    "updater", Path(__file__).resolve().parents[2] / "app" / "src-tauri" / "src" / "updater.py")
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def _https(tmp_path: Path, not_before: dt.timedelta, not_after: dt.timedelta) -> tuple[str, Path, http.server.HTTPServer]:
    """A local HTTPS server whose certificate a test CA signed, valid from now+not_before to now+not_after."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.timezone.utc)

    def name(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = (x509.CertificateBuilder().subject_name(name("Test CA")).issuer_name(name("Test CA"))
          .public_key(ca_key.public_key()).serial_number(1)
          .not_valid_before(now - dt.timedelta(days=30)).not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(name("127.0.0.1")).issuer_name(name("Test CA"))
            .public_key(key.public_key()).serial_number(2)
            .not_valid_before(now + not_before).not_valid_after(now + not_after)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    ca_file, chain, key_file = tmp_path / "ca.pem", tmp_path / "chain.pem", tmp_path / "key.pem"
    ca_file.write_bytes(ca.public_bytes(pem))
    chain.write_bytes(leaf.public_bytes(pem) + ca.public_bytes(pem))
    key_file.write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(chain, key_file)
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"https://127.0.0.1:{server.server_address[1]}", ca_file, server


@pytest.fixture
def no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("dates", [(dt.timedelta(days=-3), dt.timedelta(days=-1)),     # expired, or the PC is days ahead
                                   (dt.timedelta(hours=2), dt.timedelta(days=2))])     # not valid yet: the PC is behind
def test_requests_to_a_certificate_outside_its_dates_asks_to_check_the_clock(tmp_path, no_proxy, dates):
    import requests
    url, ca, server = _https(tmp_path, *dates)
    try:
        with pytest.raises(requests.exceptions.SSLError) as caught:
            requests.get(url, verify=str(ca), timeout=10)
    finally:
        server.shutdown()
    assert net_trust.explain(caught.value) == net_trust.CLOCK_HINT


@pytest.mark.parametrize("dates", [(dt.timedelta(days=-3), dt.timedelta(days=-1)),
                                   (dt.timedelta(hours=2), dt.timedelta(days=2))])
def test_delta_rs_to_a_certificate_outside_its_dates_asks_to_check_the_clock(tmp_path, no_proxy, monkeypatch, dates):
    """delta-rs (rustls) says "invalid peer certificate" and, on a line of its own, "certificate expired"
    or "certificate not valid yet"."""
    deltalake = pytest.importorskip("deltalake")
    url, ca, server = _https(tmp_path, *dates)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))  # trusted, so only the dates are wrong
    try:
        with pytest.raises(OSError) as caught:
            deltalake.DeltaTable("abfss://c@acct.dfs.fabric.microsoft.com/t",
                                 storage_options={"bearer_token": "x", "azure_storage_endpoint": url,
                                                  "max_retries": "0", "retry_timeout": "5s", "timeout": "10s"})
    finally:
        server.shutdown()
    text = str(caught.value)
    if "invalid peer certificate" not in text:
        pytest.skip(f"delta-rs didn't check the certificate here: {text[-200:]}")
    assert net_trust.explain(caught.value) == net_trust.CLOCK_HINT


def test_windows_and_older_rustls_words_for_dates_ask_to_check_the_clock():
    windows = ssl.SSLCertVerificationError("A required certificate is not within its validity period when verifying "
                                           "against the current system clock or the timestamp in the signed file.")
    assert net_trust.explain(windows) == net_trust.CLOCK_HINT
    assert net_trust.explain(OSError("Generic error: invalid peer certificate: NotValidYet")) == net_trust.CLOCK_HINT
    assert net_trust.explain(OSError("invalid peer certificate: UnknownIssuer")) == net_trust.HINT  # not about dates


def test_pip_and_the_update_check_ask_to_check_the_clock():
    log = ("WARNING: Retrying ... SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate "
           "verify failed: certificate is not yet valid (_ssl.c:1032)'))': /simple/x/\nERROR: No matching distribution found for x\n")
    assert "date and time" in net_trust.pip_unreachable(log, "Ask IT.")
    expired = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")
    assert updater.explain(expired, "x").startswith(updater.CLOCK_HINT)
    untrusted = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")
    assert updater.explain(untrusted, "x").startswith(updater.CERT_HINT)


def test_the_clock_hints_never_offer_to_skip_the_check():
    for line in (net_trust.CLOCK_HINT, updater.CLOCK_HINT):
        low = line.lower()
        assert "verify=false" not in low and "insecure" not in low and "skip" not in low


def _response(url: str, date: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(url=url, headers={"Date": date} if date else {})


def test_the_server_time_is_read_from_https_answers_only(monkeypatch):
    monkeypatch.setattr(http_session, "_offset", None)
    stamp = "Wed, 11 Nov 2026 01:00:00 GMT"
    server = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.timezone.utc).timestamp()
    http_session.note_server_time(_response("http://127.0.0.1:9/x", stamp), now=server + 7200)
    assert http_session.clock_offset() is None  # not over HTTPS
    http_session.note_server_time(_response("https://api.fabric.microsoft.com/v1", "not a date"), now=server)
    assert http_session.clock_offset() is None
    http_session.note_server_time(_response("https://api.fabric.microsoft.com/v1", stamp), now=server + 7200)
    assert http_session.clock_offset() == {"seconds": 7200, "host": "api.fabric.microsoft.com", "at": server + 7200}


def test_ready_to_work_says_how_far_the_clock_is_off():
    assert readiness.clock_check(None) is None  # no server has answered yet: no row
    assert readiness.clock_check({"seconds": 90, "host": "api.fabric.microsoft.com"})["status"] == "ok"
    ahead = readiness.clock_check({"seconds": 7200, "host": "api.fabric.microsoft.com"})
    assert ahead["status"] == "warn" and "2 hours ahead of api.fabric.microsoft.com's" in ahead["text"]
    behind = readiness.clock_check({"seconds": -600, "host": "api.fabric.microsoft.com"})
    assert behind["status"] == "warn" and "10 minutes behind" in behind["text"]
    assert "Set time automatically" in behind["text"]
    rows = readiness.checks(None, list, list, {}, lambda: {"seconds": -600, "host": "h"})
    assert rows[-1]["key"] == "clock"
    assert all(r["key"] != "clock" for r in readiness.checks(None, list, list, {}))


def test_an_audit_line_written_after_the_clock_went_back_a_month_stays_in_order(tmp_path):
    for at in ("2026-12-01T00:10:00.000+00:00", "2026-11-30T23:20:00.000+00:00", "2026-12-01T00:30:00.000+00:00"):
        audit_chain.append(tmp_path, {"at": at, "action": "x"})
    result = audit_chain.verify(tmp_path)
    assert result["ok"] and result["lines"] == 3, result
    assert [p.name for p in audit_chain.files(tmp_path)] == ["audit-2026-12.jsonl"]
    assert len(result["notes"]) == 1 and "50 minutes earlier" in result["notes"][0]


def test_small_clock_corrections_are_not_noted_and_large_ones_are(tmp_path):
    for at in ("2026-11-10T10:00:00.000+00:00", "2026-11-10T09:58:00.000+00:00",   # 2 minutes back: NTP, not noted
               "2026-11-10T12:00:00.000+00:00", "2026-11-10T09:00:00.000+00:00"):  # 3 hours back: noted
        audit_chain.append(tmp_path, {"at": at, "action": "x"})
    result = audit_chain.verify(tmp_path)
    assert result["ok"] and len(result["notes"]) == 1 and "3 hours earlier" in result["notes"][0]


def test_a_line_moved_between_files_is_still_a_problem(tmp_path):
    """The month rule doesn't hide a real change: a line taken out of order still breaks the chain."""
    for at in ("2026-11-10T10:00:00.000+00:00", "2026-11-10T11:00:00.000+00:00", "2026-12-01T10:00:00.000+00:00"):
        audit_chain.append(tmp_path, {"at": at, "action": "x"})
    nov = tmp_path / "audit-2026-11.jsonl"
    first, second = nov.read_text(encoding="utf-8").splitlines()
    nov.write_text(second + "\n" + first + "\n", encoding="utf-8")
    assert not audit_chain.verify(tmp_path)["ok"]
