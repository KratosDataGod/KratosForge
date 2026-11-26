"""The editor channel (editor_channel.py), mutation batch 277: what its first mutation score found
untested. A wrong Host or a browser Origin refused before the request reaches its route, the
success and refusal flags in every answer, the hello's limits, routes that take only their own
method, a body of exactly the limit, an empty body, and a refused request's answer that doesn't wait
on a body already read or never sent."""

from __future__ import annotations

import http.client
import json
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import editor_channel as ec

TOKEN = "kfp_good"  # pragma: allowlist secret (test value)


class Service:
    """Just enough of the engine for the channel: open, one pairing, and calls recorded."""

    def __init__(self):
        self.calls = []
        self.editor_pairings = SimpleNamespace(check=lambda token: {"window": "w"} if token == TOKEN else None)

    def editor_channel_open(self):
        return True

    def __getattr__(self, name):
        if not name.startswith("editor_channel_"):
            raise AttributeError(name)

        def record(*args):
            self.calls.append((name, args))
            return {"called": name}
        return record


@pytest.fixture
def channel(tmp_path):
    svc = Service()
    ch = ec.Channel(svc, tmp_path / "data")
    ch.start()
    yield ch, svc
    ch.stop()


def _call(ch, method, path, body=None, headers=None, raw=None):
    con = http.client.HTTPConnection("127.0.0.1", ch.port, timeout=30)
    h = {"Host": f"127.0.0.1:{ch.port}", **(headers or {})}
    data = raw
    if body is not None:
        data = json.dumps(body)
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    try:
        con.request(method, path, body=data, headers=h)
        res = con.getresponse()
        return res.status, json.loads(res.read())
    finally:
        con.close()


def _hello(ch):
    return {"X-KratosForge-Hello": ch.hello}


def test_a_wrong_host_is_refused_before_a_valid_pairing_request_reaches_the_app(channel):
    ch, svc = channel
    status, res = _call(ch, "POST", "/v1/pair", {"window": "C:\\repos\\sales"}, {**_hello(ch), "Host": "evil.example"})
    assert status == 403 and "Host header" in res["error"]["message"]
    assert svc.calls == []


def test_a_web_pages_origin_is_refused_before_a_valid_pairing_request_reaches_the_app(channel):
    ch, svc = channel
    status, res = _call(ch, "POST", "/v1/pair", {"window": "C:\\repos\\sales"},
                        {**_hello(ch), "Origin": "https://evil.example"})
    assert status == 403 and "Web pages" in res["error"]["message"]
    assert svc.calls == []


def test_every_answer_says_whether_it_worked(channel):
    ch, _ = channel
    status, res = _call(ch, "GET", "/v1/status", headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and res["ok"] is True
    for error, status in ((PermissionError("no"), 403), (LookupError("gone"), 404), (ValueError("bad"), 409)):
        def fail(*_args, error=error):
            raise error
        ch.service.editor_channel_notebook = fail
        got, res = _call(ch, "POST", "/v1/notebook", {"path": "p"}, {"Authorization": f"Bearer {TOKEN}"})
        assert got == status and res["ok"] is False, error


def test_a_route_takes_only_its_own_method():
    """DELETE ends a pairing only at /v1/pair, and POST interrupts only at /v1/interrupt."""
    svc = Service()
    ch = ec.Channel(svc, "unused")
    for method, path in (("DELETE", "/v1/status"), ("DELETE", "/v1/run"), ("POST", "/v1/nothing"),
                         ("GET", "/v1/interrupt")):
        with pytest.raises(ec.Refused) as err:
            ch.route(method, path, {"path": "p"}, {"window": "w"}, {"path": "p"})
        assert err.value.status == 404, (method, path)
    assert svc.calls == []


def test_a_poll_without_a_version_asks_for_the_latest():
    svc = Service()
    ec.Channel(svc, "unused").route("GET", "/v1/run", {"path": "p"}, {"window": "w"}, None)
    assert svc.calls == [("editor_channel_poll", ({"window": "w"}, "p", -1, 0.0))]


def test_a_pairing_request_without_a_window_reaches_the_app_which_says_why():
    svc = Service()
    ec.Channel(svc, "unused").route("POST", "/v1/pair", {}, None, {})
    assert svc.calls == [("editor_channel_pair", (None, None))]


def test_the_hellos_nonce_and_api_limits(channel):
    ch, _ = channel
    assert _call(ch, "GET", "/v1/hello?nonce=" + "n" * 128)[0] == 200
    assert _call(ch, "GET", "/v1/hello?nonce=" + "n" * 129)[0] == 400
    assert _call(ch, "GET", "/v1/hello?nonce=" + "n" * 16 + "&api=100000")[0] == 200  # six digits is a version
    status, res = _call(ch, "GET", "/v1/hello?nonce=" + "n" * 16 + "&api=0")
    assert status == 426 and res["error"]["code"] == "extension_too_old"


def test_a_field_of_exactly_its_limit_is_taken_and_one_more_is_refused():
    assert ec._field({"path": "p" * 4096}, "path") == "p" * 4096
    with pytest.raises(ec.Refused):
        ec._field({"path": "p" * 4097}, "path")


def test_an_empty_body_is_an_empty_object_and_a_body_of_exactly_the_limit_is_read(channel):
    ch, svc = channel
    auth = {"Authorization": f"Bearer {TOKEN}"}
    status, res = _call(ch, "POST", "/v1/notebook", raw="", headers={**auth, "Content-Length": "0"})
    assert status == 400 and res["error"]["message"].startswith("path should be text")
    body = '{"path": "p", "pad": "' + "x" * (ec.MAX_BODY_BYTES - 24) + '"}'
    assert len(body) == ec.MAX_BODY_BYTES
    status, res = _call(ch, "POST", "/v1/notebook", raw=body, headers=auth)
    assert status == 200 and svc.calls == [("editor_channel_notebook", ({"window": "w"}, "p"))]


def test_a_body_that_isnt_json_says_so(channel):
    ch, _ = channel
    status, res = _call(ch, "POST", "/v1/notebook", raw="{nope", headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 400 and res["error"]["message"] == "The body isn't JSON."


def test_a_refusal_after_the_body_was_read_is_answered_at_once(channel):
    """Nothing is left to discard, so the answer doesn't wait DISCARD_SECONDS for more."""
    ch, _ = channel
    start = time.monotonic()
    assert _call(ch, "POST", "/v1/notebook", raw="{nope", headers={"Authorization": f"Bearer {TOKEN}"})[0] == 400
    assert time.monotonic() - start < ec.DISCARD_SECONDS * 0.75


def test_a_refusal_of_a_request_with_no_body_is_answered_at_once(channel):
    ch, _ = channel
    start = time.monotonic()
    assert _call(ch, "GET", "/v1/status")[0] == 401
    assert time.monotonic() - start < ec.DISCARD_SECONDS * 0.75


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


def test_a_refused_body_of_one_byte_up_to_64_kib_is_read_and_a_larger_one_isnt():
    assert ec.DISCARD_BYTES == 64 * 1024
    assert _discard(1) == [1]
    assert _discard(ec.DISCARD_BYTES) == [ec.DISCARD_BYTES]
    assert _discard(ec.DISCARD_BYTES + 1) == []


def test_the_channels_documented_limits():
    """16 connections at once (the module's docs), with a backlog of twice that, and a 32-character hello."""
    assert ec.MAX_CONNECTIONS == 16 and ec._Server.request_queue_size == 32
    assert len(ec.Channel(Service(), "unused").hello) == 32


def test_starting_makes_the_data_folder_and_its_parents(tmp_path):
    ch = ec.Channel(Service(), tmp_path / "a" / "b" / "c")
    try:
        port = ch.start()
        assert (tmp_path / "a" / "b" / "c" / ec.PORT_FILE).read_text(encoding="utf-8").split() == [str(port), ch.hello]
    finally:
        ch.stop()
