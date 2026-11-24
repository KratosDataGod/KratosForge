"""The editor channel's exact limits and refusals, pinned so a changed number or a dropped check
fails a test: the connection cap and backlog, the hello's length, the oldest extension API, the
discard and field limits, routes that only match on both method and path, the Host and Origin
checks, an empty or broken body, and starting over a data folder that's already there."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop import editor_channel as ec


class Handler(ec._Handler):
    owner = None


def refused(call, *args):
    with pytest.raises(ec.Refused) as raised:
        call(*args)
    return raised.value


def test_sixteen_connections_are_served_at_once_and_twice_that_wait_in_the_backlog():
    server = ec._Server(("127.0.0.1", 0), Handler)
    try:
        assert [server._slots.acquire(blocking=False) for _ in range(17)] == [True] * 16 + [False]
    finally:
        server.server_close()
    assert ec._Server.request_queue_size == 32


def test_the_hello_is_32_characters(tmp_path):
    assert len(ec.Channel(SimpleNamespace(), tmp_path).hello) == 32


def test_an_extension_speaking_api_0_is_told_to_update(tmp_path):
    channel = ec.Channel(SimpleNamespace(), tmp_path)
    err = refused(channel.route, "GET", "/v1/hello", {"nonce": "n" * 16, "api": "0"}, None, None)
    assert (err.status, err.code) == (426, "extension_too_old")
    assert channel.route("GET", "/v1/hello", {"nonce": "n" * 16, "api": "1"}, None, None)["api"] == 1


def _discard(length):
    reads = []

    class Rfile:
        def read1(self, n):
            reads.append(n)
            return b"x" * n

    fake = SimpleNamespace(_body_read=False, headers={"Content-Length": str(length)}, rfile=Rfile(), timeout=5,
                           connection=SimpleNamespace(settimeout=lambda s: None))
    ec._Handler._discard_body(fake)
    return reads


def test_a_refused_body_of_up_to_64_kib_is_read_and_a_bigger_one_isnt():
    assert _discard(65536) == [65536]
    assert _discard(65537) == []


def test_a_field_is_at_most_4096_characters_by_default():
    assert ec._field({"path": "p" * 4096}, "path") == "p" * 4096
    err = refused(ec._field, {"path": "p" * 4097}, "path")
    assert err.status == 400 and "at most 4096" in err.message


@pytest.mark.parametrize("method, path", [("DELETE", "/v1/status"), ("POST", "/v1/nothing")])
def test_a_route_matches_on_its_method_and_its_path_both(tmp_path, method, path):
    channel = ec.Channel(SimpleNamespace(), tmp_path)  # a service with no methods: no route may reach it
    err = refused(channel.route, method, path, {}, {"id": "p"}, {"path": "x"})
    assert (err.status, err.code) == (404, "not_found")


def _guarded(monkeypatch, headers):
    monkeypatch.setattr(ec, "peer", SimpleNamespace(checked=lambda: False))
    fake = SimpleNamespace(client_address=("127.0.0.1", 50000), owner=SimpleNamespace(port=9000), headers=headers)
    return ec._Handler._guard(fake)


def test_a_wrong_host_or_any_origin_is_refused(monkeypatch):
    assert _guarded(monkeypatch, {"Host": "localhost:9000"}) is None
    err = refused(_guarded, monkeypatch, {"Host": "evil.example:9000"})
    assert err.status == 403 and "Host header" in err.message
    err = refused(_guarded, monkeypatch, {"Host": "127.0.0.1:9000", "Origin": "null"})
    assert err.status == 403 and "Web pages" in err.message


def _body(length, data):
    fake = SimpleNamespace(headers={"Content-Length": str(length), "Content-Type": "application/json"},
                           rfile=SimpleNamespace(read=lambda n: data), _body_read=False)
    return fake, (lambda: ec._Handler._body(fake))


def test_an_empty_body_is_an_empty_object_and_a_broken_one_is_refused():
    fake, read = _body(0, b"")
    assert read() == {} and fake._body_read is True
    fake, read = _body(5, b"{oops")
    err = refused(read)
    assert (err.status, err.message) == (400, "The body isn't JSON.")


def test_the_channel_starts_over_a_data_folder_that_already_exists(tmp_path):
    channel = ec.Channel(SimpleNamespace(), tmp_path)
    try:
        port = channel.start()
        assert (tmp_path / ec.PORT_FILE).read_text(encoding="utf-8") == f"{port} {channel.hello}"
    finally:
        channel.stop()


def test_a_nonce_is_at_most_128_characters_and_an_api_of_six_digits_is_read(tmp_path):
    channel = ec.Channel(SimpleNamespace(), tmp_path)
    assert channel.route("GET", "/v1/hello", {"nonce": "n" * 128, "api": "000001"}, None, None)["api"] == 1
    err = refused(channel.route, "GET", "/v1/hello", {"nonce": "n" * 129}, None, None)
    assert err.status == 400 and "at most 128" in err.message


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args)) or {}


def test_pairing_without_a_window_or_client_and_polling_without_a_version_use_their_defaults(tmp_path):
    svc = Recorder()
    channel = ec.Channel(svc, tmp_path)
    channel.route("POST", "/v1/pair", {}, None, {})
    channel.route("GET", "/v1/run", {"path": "a.ipynb"}, {"id": "p"}, None)
    assert svc.calls == [("editor_channel_pair", (None, None)),
                         ("editor_channel_poll", ({"id": "p"}, "a.ipynb", -1, 0.0))]


def test_the_user_check_is_given_port_0_when_the_channel_has_no_port(monkeypatch):
    asked = []
    monkeypatch.setattr(ec, "peer", SimpleNamespace(checked=lambda: True,
                                                    same_user=lambda a, b: asked.append((a, b)) or True))
    fake = SimpleNamespace(client_address=("127.0.0.1", 50000), owner=SimpleNamespace(port=None),
                           headers={"Host": "127.0.0.1:None"})
    ec._Handler._guard(fake)
    assert asked == [(50000, 0)]


def test_a_body_of_exactly_1_mib_is_read():
    fake, read = _body(ec.MAX_BODY_BYTES, b"{}")
    assert read() == {}


def test_a_refused_one_byte_body_is_read_and_one_without_a_read_flag_is_left():
    assert _discard(1) == [1]
    reads = []
    fake = SimpleNamespace(headers={"Content-Length": "10"}, rfile=SimpleNamespace(read1=reads.append),
                           timeout=5, connection=SimpleNamespace(settimeout=lambda s: None))
    ec._Handler._discard_body(fake)  # no _body_read at all: taken as already read
    assert reads == []


@pytest.mark.parametrize("error, status, code, message", [
    (None, 200, None, None),
    (PermissionError("not yours"), 403, "refused", "not yours"),
    (LookupError("'gone'"), 404, "not_found", "gone"),
    (ValueError("bad"), 409, "failed", "bad"),
])
def test_each_answer_says_whether_it_worked(error, status, code, message):
    replies = []

    def route(*args):
        if error:
            raise error
        return {"fine": 1}

    fake = SimpleNamespace(_guard=lambda: None, path="/v1/status", headers={},
                           owner=SimpleNamespace(authorize=lambda *a: None, route=route),
                           _reply=lambda s, p: replies.append((s, p)))
    ec._Handler._serve(fake, "GET")
    if error is None:
        assert replies == [(200, {"ok": True, "result": {"fine": 1}})]
    else:
        assert replies == [(status, {"ok": False, "error": {"code": code, "message": message}})]


def test_the_channel_makes_a_data_folder_whose_parent_isnt_there_yet(tmp_path):
    channel = ec.Channel(SimpleNamespace(), tmp_path / "Local" / "KratosForge")
    try:
        channel.start()
        assert (tmp_path / "Local" / "KratosForge" / ec.PORT_FILE).is_file()
    finally:
        channel.stop()


def test_a_refused_body_is_still_read_with_half_a_second_left(monkeypatch):
    clock = iter([0.0, 1.5, 1.9])
    monkeypatch.setattr(ec, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    assert _discard(10) == [10]
