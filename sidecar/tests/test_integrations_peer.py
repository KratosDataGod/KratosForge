"""The local API answers only programs running as the same Windows account (integrations/peer.py)."""

import socket
import sys

import pytest
from starlette.testclient import TestClient

from fabric_desktop.integrations import peer
from fabric_desktop.integrations.http_server import Guard
from fabric_desktop.integrations.store import IntegrationStore

LOOP = peer.LOOPBACK
ME, OTHER = b"sid-me", b"sid-other"


def row(local_port, remote_port, pid, addr=LOOP, state=5):
    return (state, addr, local_port, remote_port, pid)


def test_ports_come_in_network_byte_order():
    assert peer._port(0x1FBA) == 0xBA1F  # 47647 as GetExtendedTcpTable gives it
    assert peer._port(0x00000050) == 0x5000


def test_the_caller_is_the_client_end_of_that_connection():
    rows = [row(47615, 51000, 4), row(51000, 47615, 100), row(51001, 47615, 200)]
    assert peer.caller_pid(51000, 47615, rows) == 100
    assert peer.caller_pid(51002, 47615, rows) is None  # gone, or never there
    assert peer.caller_pid(51000, 47615, rows + [row(51000, 47615, 300)]) is None  # not one answer: refused
    assert peer.caller_pid(51000, 47615, [row(51000, 47615, 100, addr=0x0100000A)]) is None  # not loopback


def test_same_user_only_when_windows_confirms_it():
    rows = [row(51000, 47615, 100), row(51001, 47615, 200), row(51002, 47615, 300)]

    def sid_of(pid):
        if pid == 300:
            raise OSError(5, "Access is denied")  # another account's process
        return {100: ME, 200: OTHER}[pid]

    def check(port):
        return peer.same_user(port, 47615, rows=rows, sid_of=sid_of, own=lambda: ME)
    assert check(51000) is True
    assert check(51001) is False
    assert check(51002) is False
    assert check(51003) is False


def test_a_table_that_cant_be_read_refuses():
    def broken():
        raise OSError(87, "GetExtendedTcpTable failed")
    assert peer.same_user(51000, 47615, rows=[row(51000, 47615, 1)], sid_of=lambda p: broken(), own=lambda: ME) is False


def test_another_accounts_listener_stops_the_api_starting():
    def mine(pid):
        return ME
    assert not peer.listener_is_other_user(47615, rows=[row(47615, 0, 10, state=2)], sid_of=mine, own=lambda: ME)
    assert peer.listener_is_other_user(47615, rows=[row(47615, 0, 10, state=2)], sid_of=lambda p: OTHER, own=lambda: ME)
    assert peer.listener_is_other_user(47615, rows=[row(47615, 0, 10, addr=0, state=2)],  # every address
                                       sid_of=lambda p: OTHER, own=lambda: ME)

    def denied(pid):
        raise OSError(5, "Access is denied")
    assert peer.listener_is_other_user(47615, rows=[row(47615, 0, 10, state=2)], sid_of=denied, own=lambda: ME)
    assert not peer.listener_is_other_user(47616, rows=[row(47615, 0, 10, state=2)], sid_of=denied, own=lambda: ME)


async def ok_app(scope, receive, send):
    if scope["type"] == "lifespan":
        while (await receive())["type"] != "lifespan.shutdown":
            await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def guarded(tmp_path, same_user):
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    guard = Guard(ok_app, store, 47615, same_user=same_user)

    async def as_loopback(scope, receive, send):  # what uvicorn gives for a real connection
        if scope["type"] == "http":
            scope = dict(scope, client=("127.0.0.1", 51000))
        await guard(scope, receive, send)
    return TestClient(as_loopback, base_url="http://127.0.0.1:47615")


def test_the_guard_refuses_another_account_before_anything_else(tmp_path):
    asked = []

    def other(peer_port, server_port):
        asked.append((peer_port, server_port))
        return False
    with guarded(tmp_path, other) as c:
        r = c.get("/api/v1/health")  # even the open paths
    assert r.status_code == 403 and "Windows account" in r.json()["error"]["message"]
    assert asked == [(51000, 47615)]


def test_the_guard_lets_the_same_account_through(tmp_path):
    with guarded(tmp_path, lambda peer_port, server_port: True) as c:
        assert c.get("/api/v1/health").status_code == 200


def test_off_windows_nobody_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(peer, "checked", lambda: False)
    assert Guard(ok_app, IntegrationStore(tmp_path / "i.sqlite"), 47615).same_user is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' TCP table and process tokens")
def test_real_windows_confirms_this_processs_own_connection():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, (_, peer_port) = server.accept()
    try:
        assert peer.same_user(peer_port, port) is True
        assert peer.same_user(peer_port + 1 if peer_port < 65535 else 1024, port) is False
        assert not peer.listener_is_other_user(port)
    finally:
        for s in (client, accepted, server):
            s.close()
