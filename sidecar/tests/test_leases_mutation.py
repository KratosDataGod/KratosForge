"""leases.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 4) showed no test
pinned. The file lock's wait and unlock on both platforms, retries on a lease file another writer has
open, OneLake's answers (a lost race, a refusal at each step, throttling), when a lease counts as
expired, the waits between tries, the table lease's lengths, and what holder() and holding() say.
Every OneLake answer is a stand-in; nothing reaches a tenant."""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import pytest
import requests

from fabric_desktop import leases as L


# ---- the file lock

@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_a_lock_another_holder_keeps_times_out_after_the_wait_not_before(tmp_path):
    path = tmp_path / "x.lock"
    with L._file_lock(path):
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="x.lock is held by another process"):
            with L._file_lock(path, timeout=0.3):
                pass
        assert time.monotonic() - start >= 0.28
    with L._file_lock(path, timeout=0.3):  # free again
        pass


class _FakeMsvcrt:
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, refusals):
        self.refusals, self.calls = refusals, []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode != self.LK_UNLCK and self.refusals:
            self.refusals -= 1
            raise OSError(36, "locked")


def _windows(monkeypatch, refusals):
    fake = _FakeMsvcrt(refusals)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(L, "os", types.SimpleNamespace(name="nt"))
    return fake


def test_windows_locks_and_unlocks_the_first_byte_from_the_start(tmp_path, monkeypatch):
    path = tmp_path / "x.lock"
    path.write_bytes(b"0123")  # opened to append, so it starts at the end unless sought back
    fake = _windows(monkeypatch, refusals=2)
    with L._file_lock(path, timeout=5):
        pass
    assert fake.calls == [(2, 1, 0), (2, 1, 0), (2, 1, 0), (0, 1, 0)]


def test_windows_waits_out_its_timeout_then_gives_up(tmp_path, monkeypatch):
    fake = _windows(monkeypatch, refusals=10_000)
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="is held by another process"):
        with L._file_lock(tmp_path / "x.lock", timeout=0.3):
            pass
    assert time.monotonic() - start >= 0.28
    assert all(mode == fake.LK_NBLCK for mode, _, _ in fake.calls)


# ---- a lease file another writer has open (Windows refuses with PermissionError)

class _QuietTime:
    """leases' own ``time``, with sleep a no-op. Only the module's name is replaced, so the real
    time.sleep that the rest of the process (and pytest) uses is left alone."""

    def sleep(self, seconds):
        pass

    def __getattr__(self, name):
        return getattr(time, name)


def test_a_change_refused_every_time_is_tried_50_times_then_raised(monkeypatch):
    monkeypatch.setattr(L, "time", _QuietTime())
    calls = []

    def refuse():
        calls.append(1)
        raise PermissionError("in use")
    with pytest.raises(PermissionError):
        L._while_shared(refuse)
    assert len(calls) == 50


def test_a_change_refused_a_few_times_goes_through(monkeypatch):
    monkeypatch.setattr(L, "time", _QuietTime())
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
    assert L._while_shared(flaky) is None
    assert len(calls) == 3


def test_a_read_refused_every_time_is_tried_ten_times_then_raised(tmp_path, monkeypatch):
    backend = L.LocalBackend(tmp_path)
    backend.create("k", b"{}")
    monkeypatch.setattr(L, "time", _QuietTime())
    calls = []

    def refuse(self):
        calls.append(1)
        raise PermissionError("in use")
    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(PermissionError):
        backend.read("k")
    assert len(calls) == 10


def test_delete_with_a_stale_etag_says_no_and_keeps_the_file(tmp_path):
    backend = L.LocalBackend(tmp_path)
    backend.create("k", b"{}")
    assert backend.delete("k", "not-its-etag") is False
    assert backend.read("k") is not None


def test_a_replace_whose_copy_cant_be_written_raises_that_error(tmp_path, monkeypatch):
    backend = L.LocalBackend(tmp_path)
    etag = backend.create("k", b"{}")

    def full(self, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Path, "write_bytes", full)
    with pytest.raises(OSError, match="No space left"):
        backend.replace("k", b'{"a": 1}', etag)
    monkeypatch.undo()
    assert backend.read("k").data == b"{}"


# ---- OneLake (a stand-in session)

class _Resp:
    def __init__(self, status, headers=None, content=b""):
        self.status_code, self.headers, self.content, self.text = status, headers or {}, content, ""
        self.url, self.reason = "https://onelake.example/x", ""

    def json(self):
        return {}


class _Session:
    def __init__(self, put=201, append=202, flush=200, get=200, delete=200, get_headers=None):
        self.answers = {"put": put, "append": append, "flush": flush, "get": get, "delete": delete}
        self.get_headers = get_headers or {}
        self.calls = []

    def _answer(self, what, **kw):
        self.calls.append((what, kw.get("timeout")))
        status = self.answers[what]
        status = status.pop(0) if isinstance(status, list) else status
        return _Resp(status, {"ETag": f'"{what}"'} if what != "get" else self.get_headers)

    def put(self, url, **kw):
        return self._answer("put", **kw)

    def patch(self, url, **kw):
        return self._answer("append" if kw["params"]["action"] == "append" else "flush", **kw)

    def get(self, url, **kw):
        return self._answer("get", **kw)

    def delete(self, url, **kw):
        return self._answer("delete", **kw)


def _onelake(session, **kw):
    return L.OneLakeBackend("ws", "lh", lambda: "token", session=session, sleep=lambda s: None, **kw)


def test_onelake_requests_wait_60_seconds_by_default():
    s = _Session()
    assert _onelake(s).create("k", b"{}") == '"flush"'
    assert [t for _, t in s.calls] == [60, 60, 60]


def test_a_change_must_finish_within_three_request_timeouts():
    assert _onelake(_Session(), timeout=10, clock=lambda: 100.0)._deadline() == 130.0


@pytest.mark.parametrize("status", [409, 412])
def test_a_create_that_finds_the_lease_there_is_a_lost_race(status):
    s = _Session(put=status)
    assert _onelake(s).create("k", b"{}") is None
    assert [w for w, _ in s.calls] == ["put"]


@pytest.mark.parametrize("step", ["put", "append", "flush"])
def test_a_400_at_any_step_of_a_write_is_an_error(step):
    s = _Session(**{step: 400})
    with pytest.raises(requests.HTTPError, match="write the lease for k"):
        _onelake(s).create("k", b"{}")


def test_a_400_reading_or_releasing_is_an_error():
    with pytest.raises(requests.HTTPError, match="read the lease for k"):
        _onelake(_Session(get=400)).read("k")
    with pytest.raises(requests.HTTPError, match="release the lease for k"):
        _onelake(_Session(delete=400)).delete("k", "e")


def test_a_throttled_change_is_sent_three_times_in_all():
    s = _Session(put=429)
    with pytest.raises(requests.HTTPError, match="write the lease for k"):
        _onelake(s).create("k", b"{}")
    assert [w for w, _ in s.calls] == ["put", "put", "put"]


# ---- expiry and length

def _stored(body, modified=1000.0, store_now=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    return L.Stored(data, "e", modified, store_now)


def test_an_empty_lease_counts_as_written_when_the_file_was():
    leases = L.Leases(None, "me", ttl=120)
    assert leases._expires(_stored(b"", modified=1000.0)) == (1120.0, "?")


@pytest.mark.parametrize("age, expired", [(120.5, False), (121.0, False), (121.5, True)])
def test_on_the_stores_clock_a_lease_expires_a_second_after_its_length(age, expired):
    leases = L.Leases(None, "me", ttl=120)
    assert leases._expired(_stored({"ttl": 120}, modified=1000.0, store_now=1000.0 + age)) is expired


def test_a_lease_shorter_than_a_second_keeps_its_own_length():
    assert L.Leases(None, "me", ttl=120)._length(_stored({"ttl": 0.5})) == 0.5


def test_a_lease_lasts_120_seconds_by_default_counted_from_the_clock(tmp_path):
    backend = L.LocalBackend(tmp_path)
    leases = L.Leases(backend, "me", clock=lambda: 5000.0)
    assert leases.try_acquire("k")
    body = json.loads(backend.read("k").data)
    assert (body["expires"], body["ttl"], body["owner"]) == (5120.0, 120, "me")


def test_holder_names_the_owner_or_nobody(tmp_path):
    leases = L.Leases(L.LocalBackend(tmp_path), "writer-1")
    assert leases.holder("k") == ""
    leases.try_acquire("k")
    assert leases.holder("k") == "writer-1"


# ---- waiting for a lease

def test_the_wait_between_tries_doubles_up_to_15_seconds(tmp_path):
    backend = L.LocalBackend(tmp_path)
    L.Leases(backend, "other", ttl=10_000).try_acquire("k")
    now = [0.0]
    waits = []

    def sleep(s):
        waits.append(s)
        now[0] += s
    leases = L.Leases(backend, "me", wait=60, clock=lambda: now[0], sleep=sleep)
    with pytest.raises(L.LeaseBusy):
        with leases.hold("k"):
            pass
    assert waits == [1.0, 2.0, 4.0, 8.0, 15, 15, 15]


def test_a_writer_waits_120_seconds_by_default(tmp_path):
    backend = L.LocalBackend(tmp_path)
    L.Leases(backend, "other", ttl=10_000).try_acquire("k")
    now = [0.0]

    def sleep(s):
        now[0] += s
    with pytest.raises(L.LeaseBusy, match="k is being written by other on "):
        with L.Leases(backend, "me", clock=lambda: now[0], sleep=sleep).hold("k"):
            pass
    assert now[0] == 120


def test_the_renewer_thread_is_a_daemon_and_stop_waits_30_seconds_for_it(tmp_path):
    leases = L.Leases(L.LocalBackend(tmp_path), "me")
    etag = leases.try_acquire("k")
    held = L.Held(leases, "k", etag)
    assert held._thread.daemon is True
    joined = []
    held._thread = types.SimpleNamespace(join=lambda timeout=None: joined.append(timeout))
    held.stop()
    assert joined == [30]


def test_holding_is_true_only_inside_a_live_lease_on_that_key(tmp_path):
    leases = L.Leases(L.LocalBackend(tmp_path), "me", ttl=60)
    assert L.holding("k") is False
    with leases.hold("k") as held:
        assert L.holding("k") is True
        assert L.holding("other") is False
        held.lost = True
        assert L.holding("k") is False
        held.lost = False
    assert L.holding("k") is False


def test_table_leases_last_60_seconds_and_wait_90(monkeypatch):
    made = []

    class Recorder:
        def __init__(self, backend, owner, ttl, wait):
            made.append((owner, ttl, wait))

        def hold(self, key):
            return key
    monkeypatch.setattr(L, "Leases", Recorder)
    assert L.table_lease(object(), "dbo", "t", "me") == "dbo.t"
    assert made == [("me", 60, 90)]
