"""Local leases across processes, and a lease that can't be renewed.

The local backend's read-compare-replace was guarded by a lock inside one process only, so two
processes (the app and a scheduled run, or two agents) taking over the same expired lease could
both win: in 30 trials of 8 processes, 16 had more than one holder.
"""

import json
import multiprocessing as mp
import time

from fabric_desktop.leases import Held, Leases, LocalBackend

KEY = "LH_Bronze/dbo.events"


def _take_over(folder: str, etag: str, start: float, out) -> None:
    backend = LocalBackend(folder)
    while time.time() < start:
        pass
    body = json.dumps({"owner": "racer", "expires": time.time() + 120}).encode()
    out.put(backend.replace(KEY, body, etag) is not None)


def test_only_one_process_takes_over_an_expired_lease(tmp_path):
    ctx = mp.get_context("spawn")
    for trial in range(5):
        folder = tmp_path / str(trial)
        backend = LocalBackend(folder)
        etag = backend.create(KEY, json.dumps({"owner": "gone", "expires": 0}).encode())
        out = ctx.Queue()
        start = time.time() + 2.0  # every process is up and spinning before the race starts
        procs = [ctx.Process(target=_take_over, args=(str(folder), etag, start, out)) for _ in range(6)]
        for p in procs:
            p.start()
        winners = sum(out.get(timeout=60) for _ in procs)
        for p in procs:
            p.join(timeout=30)
        assert winners == 1, f"trial {trial}: {winners} processes took the lease"


class FlakyBackend(LocalBackend):
    def __init__(self, folder):
        super().__init__(folder)
        self.down = False

    def replace(self, key, data, etag):
        if self.down:
            raise ConnectionError("OneLake unreachable")
        return super().replace(key, data, etag)


def test_a_lease_that_cant_be_renewed_past_its_length_is_lost(tmp_path):
    clock = [1000.0]
    backend = FlakyBackend(tmp_path)
    leases = Leases(backend, "me", ttl=0.3, clock=lambda: clock[0])
    etag = leases.try_acquire(KEY)
    held = Held(leases, KEY, etag)
    backend.down = True
    held.start()
    time.sleep(0.25)  # a renewal fails while the lease still has time: not lost yet
    assert not held.lost
    clock[0] += 1.0   # now past its length with no renewal
    deadline = time.time() + 5
    while not held.lost and time.time() < deadline:
        time.sleep(0.05)
    held.stop()
    assert held.lost


def test_a_renewal_failure_inside_the_lease_length_keeps_it(tmp_path):
    backend = FlakyBackend(tmp_path)
    leases = Leases(backend, "me", ttl=0.3)
    held = Held(leases, KEY, leases.try_acquire(KEY))
    backend.down = True
    held.start()
    time.sleep(0.15)
    backend.down = False
    time.sleep(0.3)
    held.stop()
    assert not held.lost


def test_a_lease_file_another_writer_is_reading_is_still_let_go_and_renewed(tmp_path, monkeypatch):
    # Windows refuses to delete or replace a file while another process reads it (WinError 32);
    # writers waiting for the lease read it all the time, so a release must try again, not give up.
    import os
    from pathlib import Path

    backend = LocalBackend(tmp_path)
    etag = backend.create("dbo.sales", b"one")
    real_replace, real_unlink, refused = os.replace, Path.unlink, []

    def busy(real):
        def call(*args, **kwargs):
            if len(refused) % 3 < 2:  # refused twice, then allowed
                refused.append(args)
                raise PermissionError(32, "The process cannot access the file because it is being used by another process")
            refused.append(None)
            return real(*args, **kwargs)
        return call

    monkeypatch.setattr(os, "replace", busy(real_replace))
    etag = backend.replace("dbo.sales", b"two", etag)
    assert etag and backend.read("dbo.sales").data == b"two"
    monkeypatch.setattr(Path, "unlink", busy(real_unlink))
    assert backend.delete("dbo.sales", etag) is True
    monkeypatch.undo()
    assert backend.read("dbo.sales") is None and len(refused) == 6
