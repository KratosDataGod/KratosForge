"""HTTPS behind a network that inspects it: the Windows certificate store, and a one-line reason."""

import json
import ssl
import sys
import types

import requests
import urllib3

from fabric_desktop import net_trust, server


def test_windows_uses_the_windows_certificate_store(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=lambda: calls.append(1)))
    monkeypatch.setattr(net_trust, "_done", False)
    assert net_trust.use_system_store("win32") is True
    assert net_trust.use_system_store("win32") is True and calls == [1]  # once per process


def test_elsewhere_python_keeps_its_own_list(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", types.SimpleNamespace(inject_into_ssl=lambda: 1 / 0))
    monkeypatch.setattr(net_trust, "_done", False)
    assert net_trust.use_system_store("linux") is False


def test_a_missing_truststore_is_logged_and_https_still_works(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "truststore", None)  # import fails
    monkeypatch.setattr(net_trust, "_done", False)
    assert net_trust.use_system_store("win32") is False
    assert "Python's own certificate list" in caplog.text


def _refused() -> requests.exceptions.SSLError:
    # As requests raises it: SSLError(MaxRetryError(reason=SSLError(SSLCertVerificationError)))
    inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                            "unable to get local issuer certificate")
    retries = urllib3.exceptions.MaxRetryError(None, "/v1/workspaces", reason=urllib3.exceptions.SSLError(inner))
    return requests.exceptions.SSLError(retries)


def test_a_refused_certificate_is_recognized_through_its_causes():
    assert net_trust.certificate_refused(_refused())
    try:
        try:
            raise ssl.SSLCertVerificationError(1, "verify failed")
        except ssl.SSLError as exc:
            raise RuntimeError("Fabric call failed") from exc
    except RuntimeError as outer:
        assert net_trust.certificate_refused(outer)
    assert not net_trust.certificate_refused(requests.ConnectionError("Connection refused"))
    assert not net_trust.certificate_refused(ValueError("bad"))


def test_the_app_says_what_to_do_in_one_line(monkeypatch):
    class Svc:
        def list_workspaces(self):
            raise _refused()
    monkeypatch.setattr(server, "METHODS", {"list_workspaces"})
    monkeypatch.setattr(server.rpc_params, "check", lambda *a: None)
    monkeypatch.setattr(server.audit, "audited", lambda m: False)
    reply = server.handle(Svc(), json.dumps({"id": 1, "method": "list_workspaces", "params": {}}))
    assert reply["error"].startswith(net_trust.HINT)
    assert "CERTIFICATE_VERIFY_FAILED" in reply["error"]  # the detail stays after it


def test_other_failures_are_unchanged(monkeypatch):
    class Svc:
        def list_workspaces(self):
            raise requests.ConnectionError("Connection refused")
    monkeypatch.setattr(server, "METHODS", {"list_workspaces"})
    monkeypatch.setattr(server.rpc_params, "check", lambda *a: None)
    monkeypatch.setattr(server.audit, "audited", lambda m: False)
    reply = server.handle(Svc(), json.dumps({"id": 1, "method": "list_workspaces", "params": {}}))
    assert reply["error"] == "ConnectionError: Connection refused"
