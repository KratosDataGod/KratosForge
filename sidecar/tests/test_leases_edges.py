"""Table write leases (leases.py): the edges test_table_leases.py and test_leases_local.py leave out.
A lease file that a folder without hard links creates in place, a read Windows refuses for a moment,
the Windows file lock, every refusal OneLake can give a lease change, a lease released just as it was
looked at, and a release that fails. No network: a stand-in HTTP session."""

from __future__ import annotations

import json
import logging
import sys
import types

import pytest
import requests

from fabric_desktop import leases
from fabric_desktop.leases import Leases, LocalBackend, OneLakeBackend, Stored


# --------------------------------------------------------------------------- a folder on this PC

def test_a_folder_without_hard_links_creates_the_lease_in_place_and_once(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError(1, "Operation not permitted")
    monkeypatch.setattr(leases.os, "link", no_links)
    backend = LocalBackend(tmp_path)
    etag = backend.create("ws/t", b"first")
    assert etag == LocalBackend._etag(b"first") and backend.read("ws/t").data == b"first"
    assert backend.create("ws/t", b"second") is None and backend.read("ws/t").data == b"first"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ws__t.json"]  # no .tmp left behind


def test_a_read_windows_refuses_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    backend = LocalBackend(tmp_path)
    backend.create("t", b"x")
    real, refusals = leases.Path.read_bytes, [2]

    def read_bytes(self):
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(13, "being replaced")
        return real(self)
    monkeypatch.setattr(leases.Path, "read_bytes", read_bytes)
    monkeypatch.setattr(leases.time, "sleep", lambda s: None)
    assert backend.read("t").data == b"x"


def test_a_read_windows_keeps_refusing_is_an_error(tmp_path, monkeypatch):
    backend = LocalBackend(tmp_path)
    backend.create("t", b"x")

    def read_bytes(self):
        raise PermissionError(13, "being replaced")
    monkeypatch.setattr(leases.Path, "read_bytes", read_bytes)
    monkeypatch.setattr(leases.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        backend.read("t")


class FakeMsvcrt:
    LK_LOCK, LK_UNLCK = 1, 0

    def __init__(self, busy):
        self.busy, self.calls = busy, []

    def locking(self, fd, mode, nbytes):
        self.calls.append(mode)
        if mode == self.LK_LOCK and self.busy:
            self.busy -= 1
            raise OSError(36, "Resource deadlock avoided")


def test_on_windows_the_file_lock_waits_for_another_process_then_lets_go(tmp_path, monkeypatch):
    fake = FakeMsvcrt(busy=1)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    # only leases sees Windows: os.name itself would turn every Path into a WindowsPath
    monkeypatch.setattr(leases, "os", types.SimpleNamespace(name="nt"))
    with leases._file_lock(tmp_path / "locks" / ".lock"):
        assert fake.calls == [1, 1]
    assert fake.calls == [1, 1, 0]


# --------------------------------------------------------------------------- OneLake

class Resp:
    def __init__(self, status, headers=None, content=b"", body=None):
        self.status_code, self.headers, self.content, self._body = status, headers or {}, content, body
        self.text = json.dumps(body) if body is not None else ""
        self.closed = False

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body

    def close(self):
        self.closed = True


class Session:
    """Answers each request with the next response in ``answers`` and records it."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def _next(self, method, url, **kw):
        self.sent.append((method, kw.get("params"), kw.get("headers", {})))
        return self.answers.pop(0)

    def put(self, url, **kw):
        return self._next("PUT", url, **kw)

    def patch(self, url, **kw):
        return self._next("PATCH", url, **kw)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def delete(self, url, **kw):
        return self._next("DELETE", url, **kw)


def backend(*answers, clock=lambda: 0.0, timeout=10):
    session, waits = Session(*answers), []
    b = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=waits.append, timeout=timeout, clock=clock)
    return b, session, waits


def test_a_lease_is_written_in_three_conditional_steps():
    b, s, _ = backend(Resp(201, {"ETag": "e1"}), Resp(202), Resp(200, {"ETag": "e2"}))
    assert b.create("t", b"body") == "e2"
    assert [m for m, *_ in s.sent] == ["PUT", "PATCH", "PATCH"]
    assert s.sent[0][2]["If-None-Match"] == "*" and s.sent[2][2]["If-Match"] == "e1"
    assert s.sent[2][1] == {"action": "flush", "position": "4"} and s.sent[0][2]["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("answers, what", [
    ([Resp(403, {"x-ms-error-code": "AuthorizationPermissionMismatch"})], "create"),
    ([Resp(201, {"ETag": "e1"}), Resp(500, body={"error": {"code": "InternalError"}})], "append"),
    ([Resp(201, {"ETag": "e1"}), Resp(202), Resp(400)], "flush"),
])
def test_a_refused_step_is_an_error_that_names_the_lease(answers, what):
    b, _, _ = backend(*answers)
    with pytest.raises(requests.HTTPError, match="OneLake didn't write the lease for t"):
        b.create("t", b"body")


def test_a_lease_replaced_between_create_and_flush_is_a_lost_race():
    b, _, _ = backend(Resp(200, {"ETag": "e1"}), Resp(202), Resp(412))
    assert b.replace("t", b"body", "e0") is None


def test_a_flush_without_an_etag_keeps_the_one_from_the_create():
    b, s, _ = backend(Resp(201, {}), Resp(202), Resp(200, {}))
    assert b.create("t", b"x") == ""
    assert "If-Match" not in s.sent[2][2]


def test_a_throttled_change_waits_and_is_sent_again():
    throttled = Resp(429, {"Retry-After": "2"})
    b, s, waits = backend(throttled, Resp(201, {"ETag": "e1"}), Resp(202), Resp(200, {"ETag": "e2"}))
    assert b.create("t", b"x") == "e2"
    assert waits == [2.0] and throttled.closed and len(s.sent) == 4


def test_a_throttle_longer_than_the_change_has_left_fails_while_the_lease_is_held():
    b, s, waits = backend(Resp(429, {"Retry-After": "60"}), timeout=1)  # wait capped at 5 s, deadline 3 s
    with pytest.raises(requests.HTTPError, match=r"\(429"):
        b.create("t", b"x")
    assert waits == [] and len(s.sent) == 1


def test_a_lease_that_isnt_there_reads_as_none_and_a_refused_read_is_an_error():
    b, _, _ = backend(Resp(404), Resp(403))
    assert b.read("t") is None
    with pytest.raises(requests.HTTPError, match="read the lease for t"):
        b.read("t")


def test_a_read_without_dates_counts_from_this_pcs_clock(monkeypatch):
    monkeypatch.setattr(leases.time, "time", lambda: 1234.0)
    b, _, _ = backend(Resp(200, {"ETag": "e"}, b"{}"),
                      Resp(200, {"ETag": "e", "Last-Modified": "Fri, 06 Nov 2026 10:00:00 GMT", "Date": "soon"}, b"{}"))
    assert b.read("t") == Stored(b"{}", "e", 1234.0)
    one_date = b.read("t")
    assert one_date.store_now is None and one_date.modified > 1234.0


@pytest.mark.parametrize("status, released", [(200, True), (404, False), (409, False), (412, False)])
def test_a_release_says_whether_it_let_go(status, released):
    b, s, _ = backend(Resp(status))
    assert b.delete("t", "e1") is released and s.sent[0][2]["If-Match"] == "e1"


def test_a_refused_release_is_an_error():
    b, _, _ = backend(Resp(403))
    with pytest.raises(requests.HTTPError, match="release the lease for t"):
        b.delete("t", "e1")


# --------------------------------------------------------------------------- taking and letting go

class Memory:
    """A backend whose answers a test sets."""

    def __init__(self, create=(), read=(), delete_error=None):
        self._create, self._read, self.delete_error, self.deleted = list(create), list(read), delete_error, []

    def create(self, key, data):
        return self._create.pop(0)

    def read(self, key):
        return self._read.pop(0)

    def replace(self, key, data, etag):
        return "renewed"

    def delete(self, key, etag):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(key)
        return True


def test_a_lease_released_just_as_it_was_looked_at_is_created_again():
    lease = Leases(Memory(create=[None, "e2"], read=[None]), "me", clock=lambda: 100.0)
    assert lease.try_acquire("t") == "e2"


@pytest.mark.parametrize("body, length", [
    ({"expires": 160.0, "acquired_at": "1970-01-01T00:01:40+00:00"}, 60.0),  # from before bodies said ttl
    ({"ttl": "nan"}, 120),
    ({"ttl": -5}, 120),
    ({"expires": 1e400, "acquired_at": "x"}, 120),
    ([1], 120),
])
def test_a_lease_runs_for_what_its_body_says_or_this_writers_length(body, length):
    lease = Leases(Memory(), "me", ttl=120)
    assert lease._length(Stored(json.dumps(body).encode(), "e", 0.0, 1.0)) == length


def test_the_busy_message_names_the_holder_without_a_machine_when_the_body_has_none():
    stored = Stored(json.dumps({"owner": "agent-7", "expires": 999}).encode(), "e", 0.0)
    lease = Leases(Memory(read=[stored, None, Stored(b"[1]", "e", 0.0)]), "me")
    assert lease._holder_and_host("t") == "agent-7"
    assert lease._holder_and_host("t") == ""
    assert lease._holder_and_host("t") == "?"


def test_a_release_that_fails_is_logged_and_the_lease_left_to_expire(caplog):
    memory = Memory(create=["e1"], delete_error=requests.ConnectionError("network down"))
    lease = Leases(memory, "me", ttl=600)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.leases"):
        with lease.hold("t"):
            pass
    assert "Couldn't release the lease; it expires on its own" in caplog.text
