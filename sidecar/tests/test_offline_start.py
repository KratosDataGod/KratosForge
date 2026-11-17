"""Start-up with no internet, or behind a proxy that blocks only Microsoft's sign-in host: the engine
answers its first call at once, shows the saved sign-in, and runs local work. What needs the network
says so in one line, and a sign-in setup that can't reach Microsoft is waited for once, not per call.

Sockets are blocked in-process: a blocked host's name doesn't resolve and a connection to it is
refused, as on a PC with no network or a proxy that drops the host. Loopback stays open.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import msal
import pytest
import requests

from fabric_desktop import auth, net_trust, service as service_mod
from fabric_desktop.service import Service
from test_offline import no_proxy  # noqa: F401 - fixture

LOOPBACK = {"localhost", "127.0.0.1", "::1", ""}
SIGN_IN = "login.microsoftonline.com"


def block(monkeypatch, hosts: set[str] | None = None) -> list[str]:
    """Block every host but loopback (``hosts`` None), or only ``hosts``. Returns the hosts tried."""
    tried: list[str] = []

    def blocked(host: object) -> bool:
        name = str(host or "").lower()
        if name in LOOPBACK:
            return False
        tried.append(name)
        return hosts is None or name in hosts

    real_gai, real_connect = socket.getaddrinfo, socket.socket.connect

    def getaddrinfo(host, *args, **kwargs):
        if blocked(host):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real_gai(host, *args, **kwargs)

    def connect(self, address):
        if isinstance(address, tuple) and blocked(address[0]):
            raise ConnectionRefusedError(111, "Connection refused")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return tried


def saved_sign_in(local_app_data) -> None:
    """A cache with one account, as MSAL writes it after a sign-in (no tokens: none are needed here)."""
    folder = local_app_data / "FabricDesktop"
    folder.mkdir(parents=True, exist_ok=True)
    account = {"home_account_id": "oid1.tid1", "environment": SIGN_IN, "realm": "tid1", "local_account_id": "oid1",
               "username": "pilot@contoso.example", "authority_type": "MSSTS"}
    (folder / "msal_cache.bin").write_text(json.dumps({"Account": {"oid1.tid1-login.microsoftonline.com-tid1": account}}))


def build(app: Service) -> object:
    return app.auth  # builds MSAL's app the first time


@pytest.fixture
def real_msal(monkeypatch):
    """MSAL with its own HTTP client, not conftest's stand-in for Microsoft: block() keeps it off the network."""
    monkeypatch.setattr(msal, "PublicClientApplication", msal.PublicClientApplication.__mro__[1])


@pytest.fixture
def app(monkeypatch, tmp_path, no_proxy):  # noqa: F811
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = Service(root=tmp_path / "FabricDesktop")
    monkeypatch.setattr(svc, "_start_retention_sweep", lambda: None)
    monkeypatch.setattr(svc, "_start_usage_send", lambda: None)
    return svc


@pytest.mark.parametrize("hosts", [None, {SIGN_IN}], ids=["offline", "proxy-blocks-sign-in"])
def test_the_first_call_shows_the_saved_sign_in_without_the_network(monkeypatch, tmp_path, app, hosts):
    saved_sign_in(tmp_path)
    tried = block(monkeypatch, hosts)
    started = time.monotonic()
    env = app.environment()
    assert time.monotonic() - started < 2
    assert env["user"] == "pilot@contoso.example" and env["tenant"] == "tid1"
    assert tried == [] and app._auth is None  # nothing asked Microsoft


def test_signed_out_offline_is_signed_out_not_an_error(monkeypatch, app):
    block(monkeypatch)
    assert app.environment()["user"] is None


def test_an_alias_host_counts_as_microsofts_sign_in(tmp_path):
    saved_sign_in(tmp_path)
    path = tmp_path / "FabricDesktop" / "msal_cache.bin"
    path.write_text(path.read_text().replace(SIGN_IN, "login.windows.net"))
    assert (auth.cached_account(path) or {}).get("username") == "pilot@contoso.example"
    path.write_text(path.read_text().replace('"MSSTS"', '"MSA"'))  # not an Entra account: MSAL skips it too
    assert auth.cached_account(path) is None


@pytest.mark.parametrize("hosts", [None, {SIGN_IN}], ids=["offline", "proxy-blocks-sign-in"])
def test_a_sign_in_setup_that_cant_reach_microsoft_fails_once_then_at_once(monkeypatch, app, hosts, real_msal):
    block(monkeypatch, hosts)
    built = []
    real = service_mod.Authenticator
    monkeypatch.setattr(service_mod, "Authenticator", lambda *a, **k: built.append(1) or real(*a, **k))
    with pytest.raises(requests.exceptions.ConnectionError) as first:
        build(app)
    assert net_trust.explain(first.value) == net_trust.OFFLINE_HINT
    started = time.monotonic()
    for _ in range(5):  # a page's loads, queued behind the first
        with pytest.raises(requests.exceptions.ConnectionError) as again:
            build(app)
        assert net_trust.explain(again.value) == net_trust.OFFLINE_HINT
    assert time.monotonic() - started < 1 and built == [1]
    app._auth_failed = (*app._auth_failed[:2], time.monotonic() - 1)  # AUTH_RETRY_SECONDS later
    with pytest.raises(requests.exceptions.ConnectionError):
        build(app)
    assert built == [1, 1]  # tried again once the wait is over
    with pytest.raises(requests.exceptions.ConnectionError):
        app.sign_in()  # the person pressed Sign in: tried again at once
    assert built == [1, 1, 1]


def test_a_network_that_drops_the_traffic_doesnt_hold_up_local_work(monkeypatch, tmp_path, app):
    # No answer at all (a firewall that drops it): MSAL would wait out every address of the host.
    saved_sign_in(tmp_path)
    block(monkeypatch)
    answer = threading.Event()

    class Waiting:
        def __init__(self, client_id, interactive=True):
            self.client_id = client_id
            answer.wait(30)

    monkeypatch.setattr(service_mod, "Authenticator", Waiting)
    monkeypatch.setattr(service_mod, "AUTH_WAIT_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(requests.exceptions.ConnectTimeout) as caught:
        build(app)
    assert net_trust.explain(caught.value) == net_trust.TIMEOUT_HINT and SIGN_IN in str(caught.value)
    rows = {r["key"]: r for r in app.readiness_check()}  # Home's first check
    assert rows["sign_in"]["status"] == "ok" and net_trust.TIMEOUT_HINT in rows["fabric"]["text"]
    assert app.new_notebook() and time.monotonic() - started < 2
    answer.set()  # Microsoft answers after all: the setup that carried on is used
    deadline = time.monotonic() + 10
    while app._auth is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert isinstance(build(app), Waiting)


def test_a_failure_that_isnt_the_network_is_not_remembered(monkeypatch, app):
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise ValueError("bad client id")

    monkeypatch.setattr(service_mod, "Authenticator", broken)
    for _ in range(2):
        with pytest.raises(ValueError):
            build(app)
    assert calls == [1, 1]


def test_ready_to_work_says_signed_in_and_that_fabric_cant_be_reached(monkeypatch, tmp_path, app, real_msal):
    saved_sign_in(tmp_path)
    block(monkeypatch)
    rows = {r["key"]: r for r in app.readiness_check()}
    assert rows["engine"]["status"] == "ok"
    assert rows["sign_in"]["status"] == "ok" and "pilot@contoso.example" in rows["sign_in"]["text"]
    assert rows["fabric"]["status"] == "fail" and net_trust.OFFLINE_HINT in rows["fabric"]["text"]


def test_local_notebooks_work_offline(monkeypatch, app):
    block(monkeypatch)
    made = app.new_notebook()
    assert made and app.list_notebooks()
