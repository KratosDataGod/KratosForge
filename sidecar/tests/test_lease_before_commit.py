"""A writer checks its lease right before the commit, not only after it (HD-016).

The lease was checked only when the write was over, so a writer whose lease had lapsed still
committed, possibly over another writer's rows, and was told afterwards.
"""

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import leases, publish
from fabric_desktop.leases import Leases, LeaseLost, LocalBackend, check_held
from fabric_desktop.meta.store import LocalStore

KEY = "LH_Bronze/dbo.events"


def _leases(tmp_path, clock):
    # A long lease, so the renewer doesn't beat during the test; the clock is moved by hand.
    return Leases(LocalBackend(tmp_path / "leases"), "me", ttl=300, clock=lambda: clock[0])


def test_outside_any_lease_the_check_does_nothing():
    check_held()


def test_a_fresh_lease_passes_and_one_unrenewed_for_two_thirds_of_its_length_stops_the_commit(tmp_path):
    clock = [1000.0]
    with _leases(tmp_path, clock).hold(KEY) as held:
        check_held()
        clock[0] += 199  # renewed within two thirds of 300 s: still time to commit
        check_held()
        clock[0] += 1
        with pytest.raises(LeaseLost, match="Nothing was written"):
            check_held()
        held._renewed = clock[0]  # a renewal came through after all


def test_a_lease_taken_over_stops_the_commit(tmp_path):
    clock = [1000.0]
    with _leases(tmp_path, clock).hold(KEY) as held:
        held.lost = True
        with pytest.raises(LeaseLost):
            check_held()
        held.lost = False  # so leaving the block releases it as usual


def test_every_lease_the_writer_is_inside_is_checked_and_leaving_it_forgets_it(tmp_path):
    clock = [1000.0]
    ls = _leases(tmp_path, clock)
    with ls.hold("LH_Meta/meta.events"):
        with ls.hold(KEY) as inner:
            inner.lost = True
            with pytest.raises(LeaseLost, match="dbo.events"):
                check_held()
            inner.lost = False
        assert leases._HELD.get() and len(leases._HELD.get()) == 1
    assert leases._HELD.get() == ()


@pytest.mark.parametrize("call", [
    lambda s: s.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2]}), mode="append"),
    lambda s: s.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2]}), mode="overwrite", read_version=0),
    lambda s: s.write_stream("LH_Bronze", "dbo", "events", pa.table({"id": [2]}).to_reader(), read_version=0),
    lambda s: s.upsert("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1, 2]}), on=["id"]),
    lambda s: s.merge_insert("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [2]}), on=["id"]),
])
def test_a_metadata_write_with_a_lapsed_lease_commits_nothing(tmp_path, call):
    store = LocalStore(tmp_path / "env", "dev")
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1]}), mode="overwrite")
    uri = store.uri("LH_Bronze", "dbo", "events")
    clock = [1000.0]
    with _leases(tmp_path, clock).hold(KEY):
        clock[0] += 250
        with pytest.raises(LeaseLost):
            call(store)
    assert DeltaTable(uri).version() == 0


def test_a_published_write_with_a_lapsed_lease_commits_nothing(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "env", "dev")
    uri = store.uri("LH_Bronze", "dbo", "events")
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1]}), mode="overwrite")

    class Ref:  # publish() only needs the table's address and name (for its lease)
        pass
    ref = Ref()
    ref.uri, ref.schema, ref.table = uri, "dbo", "events"
    clock = [1000.0]
    real = leases.Leases
    monkeypatch.setattr(leases, "Leases", lambda *a, **k: real(*a, **(k | {"clock": lambda: clock[0], "ttl": 300})))

    def lapse(*a, **k):  # the lease is taken; time passes before the commit, with no renewal
        clock[0] += 250
        return None
    monkeypatch.setattr(publish.delta_profile, "open_target", lambda *a, **k: lapse())
    with pytest.raises(LeaseLost):
        publish.publish(pa.table({"id": [2]}), ref, "append", "token")
    assert DeltaTable(uri).version() == 0
