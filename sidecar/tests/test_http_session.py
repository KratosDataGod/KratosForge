"""HD-051: the Fabric, OneLake, Power BI and admin clients share one pooled session, and one retry
policy: reads are retried on 429/503, writes are sent once (FileStore writes and lease changes wait out a 429 first, #1287 #1315)."""

from __future__ import annotations

import threading
import types

import pytest
import requests
from requests.adapters import BaseAdapter, HTTPAdapter

from fabric_desktop import http_session
from fabric_desktop.central.client import FabricPlatform
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.leases import OneLakeBackend
from fabric_desktop.onelake import FileStore, TableCatalog


class Scripted(BaseAdapter):
    """A transport that answers with the given statuses in turn (the last repeats) and records each request."""

    def __init__(self, *statuses: int):
        super().__init__()
        self.statuses = list(statuses)
        self.sent: list[tuple[str, str, object]] = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.sent.append((request.method, request.url, timeout))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        resp = requests.Response()
        resp.status_code = status
        resp._content = b'{"value": [], "paths": []}'
        resp.headers["Retry-After"] = "0"
        resp.request, resp.url = request, request.url
        return resp

    def close(self):
        pass


@pytest.fixture
def shared():
    """A fresh shared session, emptied again afterwards so no test leaks its transport."""
    http_session.reset()
    yield http_session.session()
    http_session.reset()


def transport(session: requests.Session, *statuses: int) -> Scripted:
    adapter = Scripted(*statuses)
    session.mount("https://", adapter)
    return adapter


def token() -> str:
    return "tok"


def no_sleep(_seconds: float) -> None:
    pass


def test_clients_built_without_a_session_use_the_shared_one(shared):
    clients = [FabricClient(token), TableCatalog(token), FileStore(token),
               OneLakeBackend("ws", "lh", token), FabricPlatform(token, "ws", "fn")]
    assert all(c._http is shared for c in clients)


def test_the_shared_session_is_pooled_and_reused(shared):
    assert http_session.session() is shared
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(http_session.session())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(s is shared for s in seen)
    adapter = shared.get_adapter("https://api.fabric.microsoft.com/v1/workspaces")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter._pool_maxsize == http_session.POOL_SIZE
    assert adapter.max_retries.total == 0  # urllib3 never repeats a request behind the retry policy's back


def test_the_shared_session_sets_a_default_timeout_and_keeps_no_cookies(shared):
    sent = transport(shared, 200)
    shared.get("https://api.fabric.microsoft.com/v1/workspaces")
    shared.get("https://api.fabric.microsoft.com/v1/workspaces", timeout=5)
    assert [t for _, _, t in sent.sent] == [http_session.DEFAULT_TIMEOUT, 5]
    shared.cookies.set("ARRAffinity", "x", domain="api.fabric.microsoft.com")
    assert len(shared.cookies) == 0


@pytest.mark.parametrize("status", [429, 503])
def test_reads_retry_through_the_shared_session(shared, status):
    sent = transport(shared, status, 200)
    assert FileStore(token, sleep=no_sleep).read_bytes("ws", "lh", "Files/a.csv") is not None
    assert [m for m, _, _ in sent.sent] == ["GET", "GET"]

    sent = transport(shared, status, 200)
    assert FabricClient(token, sleep=no_sleep).list_workspaces() == []
    assert [m for m, _, _ in sent.sent] == ["GET", "GET"]

    sent = transport(shared, status, 200)
    assert OneLakeBackend("ws", "lh", token, sleep=no_sleep).read("k") is not None
    assert [m for m, _, _ in sent.sent] == ["GET", "GET"]


@pytest.mark.parametrize("status", [429, 503])
def test_onelake_writes_are_sent_once(shared, status):
    if status == 503:  # FileStore waits out throttling (#1287); a 5xx may mean the write happened
        sent = transport(shared, status)
        with pytest.raises(requests.HTTPError):
            FileStore(token, sleep=no_sleep).upload("ws", "lh", "Files/a.csv", b"x")
        assert [m for m, _, _ in sent.sent] == ["PUT"]

        sent = transport(shared, status)
        with pytest.raises(requests.HTTPError):
            FileStore(token, sleep=no_sleep).delete("ws", "lh", "Files/a.csv")
        assert [m for m, _, _ in sent.sent] == ["DELETE"]

    sent = transport(shared, status)
    with pytest.raises(requests.HTTPError):
        OneLakeBackend("ws", "lh", token, sleep=no_sleep).create("k", b"{}")
    # A lease change waits out a throttle a few times (#1315): conditional, so it can't take a lease.
    assert [m for m, _, _ in sent.sent] == ["PUT"] * (3 if status == 429 else 1)


def test_filestore_writes_wait_out_throttling(shared):
    sent = transport(shared, 429, 201, 202, 200)
    FileStore(token, sleep=no_sleep).upload("ws", "lh", "Files/a.csv", b"x")
    assert [m for m, _, _ in sent.sent] == ["PUT", "PUT", "PATCH", "PATCH"]

    sent = transport(shared, 429, 200)
    FileStore(token, sleep=no_sleep).delete("ws", "lh", "Files/a.csv")
    assert [m for m, _, _ in sent.sent] == ["DELETE", "DELETE"]


def test_fabric_writes_are_not_repeated_after_a_5xx(shared):
    sent = transport(shared, 503)
    with pytest.raises(FabricError):
        FabricClient(token, sleep=no_sleep).create_workspace("Sales")
    assert [m for m, _, _ in sent.sent] == ["POST"]


def test_a_platform_change_without_call_once_is_sent_once(shared):
    """A change UDF_Platform may have made is never sent again as it is. A read is (#1040), and so is a
    change sent through call_once, which the platform makes once per call id."""
    sent = transport(shared, 404, 503)  # an older UDF_Platform: no call_once, then the change itself
    with pytest.raises(requests.HTTPError):
        FabricPlatform(token, "ws", "fn", sleep=no_sleep).call("save_domain", domain={"name": "Sales"})
    assert [u.rsplit("/functions/", 1)[1] for _, u, _ in sent.sent] == ["call_once/invoke", "save_domain/invoke"]


def test_a_platform_read_is_sent_again_on_503(shared):
    sent = transport(shared, 503)
    with pytest.raises(requests.HTTPError):
        FabricPlatform(token, "ws", "fn", sleep=no_sleep).call("key_stats", name="customer")
    assert [m for m, _, _ in sent.sent] == ["POST"] * 3


def test_an_injected_session_wins(shared):
    sent = transport(shared, 200)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return types.SimpleNamespace(status_code=200, headers={}, content=b"x", raise_for_status=lambda: None)

    own = types.SimpleNamespace(get=get)
    store = FileStore(token, session=own)  # type: ignore[arg-type]
    assert store._http is own
    assert store.read_bytes("ws", "lh", "Files/a.csv") == b"x"
    assert calls and sent.sent == []
    for client in (FabricClient(token, session=own), TableCatalog(token, session=own),  # type: ignore[arg-type]
                   OneLakeBackend("ws", "lh", token, session=own), FabricPlatform(token, "ws", "fn", session=own)):  # type: ignore[arg-type]
        assert client._http is own


def test_a_child_process_gets_its_own_session(shared, monkeypatch):
    monkeypatch.setattr(http_session.os, "getpid", lambda: -1)
    assert http_session.session() is not shared
