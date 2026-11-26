"""A writer whose PC sleeps past its lease's expiry never commits on waking.

The lease's age is wall time (Leases.clock is time.time), which runs on through sleep, so on
waking check() says the lease may be gone; another writer may take it over while the PC sleeps,
and the sleeper's renewer, once it runs, finds the file changed and gives the lease up. The
clock here is moved by hand, hours at a time, as a sleep would.
"""

import threading
import time

import polars as pl
import pytest
from deltalake import DeltaTable

from fabric_desktop.leases import Leases, LeaseLost, LocalBackend, check_held
from fabric_desktop.meta.store import LocalStore

KEY = "LH_Bronze/dbo.events"
HOURS = 3 * 3600.0


class Asleep(LocalBackend):
    """The sleeper's view of the lease folder: its renewer's writes wait until the PC wakes."""

    def __init__(self, folder):
        super().__init__(folder)
        self.awake = threading.Event()
        self.awake.set()

    def replace(self, key, data, etag):
        assert self.awake.wait(10), "the test never woke the PC"
        return super().replace(key, data, etag)


def _wait_for(done, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_holder_that_sleeps_past_expiry_is_taken_over_and_never_commits(tmp_path):
    store = LocalStore(tmp_path / "env", "dev")
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1]}), mode="overwrite")
    uri = store.uri("LH_Bronze", "dbo", "events")
    clock = [1000.0]
    sleeper_backend = Asleep(tmp_path / "leases")
    # A short real beat (ttl/3 = 0.1 s) so the renewer runs during the test; the lease's age
    # comes from the hand-moved clock alone.
    sleeper = Leases(sleeper_backend, "me", ttl=0.3, clock=lambda: clock[0])
    other = Leases(LocalBackend(tmp_path / "leases"), "other", ttl=0.3, clock=lambda: clock[0])

    with pytest.raises(LeaseLost, match="expired while writing"):
        with sleeper.hold(KEY) as held:
            check_held()
            sleeper_backend.awake.clear()  # the PC sleeps: the renewer stalls at its next beat
            clock[0] += HOURS

            # Another writer takes the expired lease over while this PC sleeps.
            assert other.try_acquire(KEY) is not None
            assert sleeper.holder(KEY) == "other"

            # On waking, the commit is refused before the renewer has even run.
            with pytest.raises(LeaseLost, match="Nothing was written"):
                check_held()
            with pytest.raises(LeaseLost):
                store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2]}), mode="append")

            sleeper_backend.awake.set()  # the renewer runs: the file isn't its own any more
            _wait_for(lambda: held.lost)
            with pytest.raises(LeaseLost):
                check_held()

    assert DeltaTable(uri).version() == 0  # nothing committed
    assert sleeper.holder(KEY) == "other"  # leaving the block didn't release the other's lease


def test_a_holder_that_sleeps_past_expiry_with_nobody_waiting_may_carry_on_once_renewed(tmp_path):
    """Nobody took the lease over (the renewal's conditional write matched), so it is still ours:
    the renewer renews it on waking and the commit may go ahead. Until then the commit waits."""
    clock = [1000.0]
    backend = Asleep(tmp_path / "leases")
    sleeper = Leases(backend, "me", ttl=0.3, clock=lambda: clock[0])
    with sleeper.hold(KEY) as held:
        backend.awake.clear()
        clock[0] += HOURS
        with pytest.raises(LeaseLost):
            check_held()
        backend.awake.set()
        _wait_for(lambda: held._renewed == clock[0])
        check_held()
        assert not held.lost
    assert sleeper.holder(KEY) == ""  # released as usual


def test_a_holder_whose_renewer_cant_reach_the_store_on_waking_gives_the_lease_up(tmp_path):
    clock = [1000.0]

    class Offline(LocalBackend):
        down = False

        def replace(self, key, data, etag):
            if self.down:
                raise OSError("network is unreachable")
            return super().replace(key, data, etag)

    backend = Offline(tmp_path / "leases")
    sleeper = Leases(backend, "me", ttl=0.3, clock=lambda: clock[0])
    with pytest.raises(LeaseLost, match="expired while writing"):
        with sleeper.hold(KEY) as held:
            backend.down = True
            clock[0] += HOURS
            _wait_for(lambda: held.lost)
            with pytest.raises(LeaseLost):
                check_held()
