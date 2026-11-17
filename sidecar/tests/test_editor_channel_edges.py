"""The editor channel VS Code calls: the edges the main tests don't reach. A busy server whose
refusal can't be sent, a connection whose thread can't start, starting twice and stopping with no
port file, a route that isn't one, a body that isn't JSON by its type, engine failures as 409 and
500, a caller from another address, and a refused body that ends early."""

from __future__ import annotations

import socketserver
from types import SimpleNamespace

import pytest
from test_editor_channel import Window, paired  # noqa: F401 - the paired-window fixture
from test_kernel_sessions import svc  # noqa: F401

from fabric_desktop import editor_channel as ec


class Handler(ec._Handler):
    owner = None


@pytest.fixture
def server():
    s = ec._Server(("127.0.0.1", 0), Handler)
    yield s
    s.server_close()


def test_a_busy_server_closes_a_connection_whose_refusal_cant_be_sent(server, monkeypatch):
    for _ in range(ec.MAX_CONNECTIONS):
        assert server._slots.acquire(blocking=False)
    closed = []
    monkeypatch.setattr(server, "shutdown_request", closed.append)

    class Conn:
        def setblocking(self, flag):
            pass

        def send(self, data):
            raise ConnectionResetError("gone")

    conn = Conn()
    server.process_request(conn, ("127.0.0.1", 50000))
    assert closed == [conn]


def test_a_connection_whose_thread_cant_start_gives_its_slot_back(server, monkeypatch):
    def no_thread(self, request, client_address):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(socketserver.ThreadingMixIn, "process_request", no_thread)
    with pytest.raises(RuntimeError):
        server.process_request(object(), ("127.0.0.1", 50000))
    for _ in range(ec.MAX_CONNECTIONS):  # every slot is free again
        assert server._slots.acquire(blocking=False)


def test_starting_twice_keeps_one_server_and_stopping_without_a_port_file_is_fine(tmp_path):
    channel = ec.Channel(SimpleNamespace(), tmp_path / "data")
    port = channel.start()
    try:
        assert channel.start() == port
        assert (tmp_path / "data" / ec.PORT_FILE).read_text(encoding="utf-8").split()[0] == str(port)
        (tmp_path / "data" / ec.PORT_FILE).unlink()
    finally:
        channel.stop()
    assert channel.port is None
    channel.stop()  # already stopped: nothing to do


def test_a_route_that_isnt_one_is_not_found(paired):  # noqa: F811
    svc, window, path = paired
    window.pair(svc)
    status, body = window.call("GET", "/v1/nothing")
    assert status == 404 and body["error"]["code"] == "not_found" and "GET /v1/nothing" in body["error"]["message"]


def test_a_body_that_doesnt_say_it_is_json_is_refused(paired):  # noqa: F811
    svc, window, path = paired
    window.pair(svc)
    import http.client
    con = http.client.HTTPConnection("127.0.0.1", window.port, timeout=30)
    con.request("POST", "/v1/notebook", body=b"{}", headers={"Host": f"127.0.0.1:{window.port}",
                                                             "Authorization": f"Bearer {window.token}",
                                                             "Content-Type": "text/plain"})
    res = con.getresponse()
    assert res.status == 415 and b"Content-Type: application/json" in res.read()
    con.close()


@pytest.mark.parametrize("error, status, code, says", [(ValueError("not now"), 409, "failed", "not now"),
                                                        (ZeroDivisionError("odd"), 500, "internal", "ZeroDivisionError: odd")])
def test_engine_failures_are_answered_and_the_channel_keeps_serving(paired, monkeypatch, error, status, code, says):  # noqa: F811
    svc, window, path = paired
    window.pair(svc)

    def fails(pairing, path):
        raise error

    monkeypatch.setattr(svc, "editor_channel_variables", fails)
    from urllib.parse import quote
    got_status, body = window.call("GET", f"/v1/variables?path={quote(path, safe='')}")
    assert got_status == status and body["error"]["code"] == code and body["error"]["message"] == says
    assert window.call("GET", "/v1/status")[0] == 200


def test_only_the_loopback_address_may_call():
    fake = SimpleNamespace(client_address=("10.0.0.5", 50000))
    with pytest.raises(ec.Refused) as raised:
        ec._Handler._guard(fake)
    assert raised.value.status == 403 and "Only programs on this PC" in raised.value.message


def test_a_refused_body_that_ends_early_is_left_as_it_is():
    reads = []

    class Rfile:
        def read1(self, n):
            reads.append(n)
            return b""

    fake = SimpleNamespace(_body_read=False, headers={"Content-Length": "10"}, rfile=Rfile(), timeout=5,
                           connection=SimpleNamespace(settimeout=lambda s: None))
    ec._Handler._discard_body(fake)
    assert reads == [10] and fake._body_read is True


def test_an_optional_field_left_out_is_none():
    assert ec._field({}, "window", required=False) is None
    with pytest.raises(ec.Refused):
        ec._field({"window": 5}, "window", required=False)


@pytest.mark.parametrize("method, path", [("GET", "/v1/status"), ("DELETE", "/v1/pair"), ("POST", "/v1/run")])
def test_a_call_that_reached_its_route_unpaired_is_still_refused(tmp_path, method, path):
    """authorize() stops these first; route() refuses them too, with no assert that python -O would remove."""
    channel = ec.Channel(SimpleNamespace(), tmp_path)
    with pytest.raises(ec.Refused) as err:
        channel.route(method, path, {}, None, {})
    assert (err.value.status, err.value.code) == (401, "not_paired")


def test_the_channel_checks_nothing_with_assert():
    import ast
    tree = ast.parse(open(ec.__file__, encoding="utf-8").read())
    assert not [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
