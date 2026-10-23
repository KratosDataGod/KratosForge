"""KF-DE-003: every OneLake table write holds the table's write lease, so a notebook, a job, a
metadata run on this PC and the agent never write the same table at once.

The leases here are local files or a scripted DFS endpoint (tests/fake_http.py). Whether OneLake
itself honours the conditional requests between two machines is not verified by these tests."""

from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile, http_session, leases, meta_service, publish
from fabric_desktop.leases import LeaseBusy, Leases, LocalBackend
from fabric_desktop.meta import OneLakeStore
from fabric_desktop.meta_service import MetaMixin
from fabric_desktop.onelake import TableRef
from tests.fake_http import Reply, session


@pytest.fixture
def local_ref(tmp_path, monkeypatch):
    """TableRef pointed at a folder, as tests/test_spark_writer.py does."""
    root = tmp_path / "Tables"
    monkeypatch.setattr(TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish, "storage_options", lambda token: {})
    write_deltalake(str(root / "dbo" / "sales"), pa.table({"id": [0]}))
    return TableRef("ws1", "lh1", "dbo", "sales")


def held_by(ref, owner):
    lease = Leases(LocalBackend(publish._local_leases(ref)), owner, ttl=3600)
    assert lease.try_acquire("dbo.sales")
    return lease


def test_a_write_waits_for_another_writer_then_says_who_holds_the_table(local_ref, monkeypatch):
    monkeypatch.setattr(leases, "TABLE_LEASE_WAIT", 0.2)
    held_by(local_ref, "etl-01")
    with pytest.raises(LeaseBusy, match=f"dbo.sales is being written by etl-01 on {socket.gethostname()}"):
        publish.publish(pa.table({"id": [1]}), local_ref, "append", "token")
    assert DeltaTable(local_ref.uri).version() == 0  # nothing was written


def test_a_lease_left_by_a_killed_kernel_is_taken_over_once_it_expires(local_ref, monkeypatch):
    assert leases.TABLE_LEASE_TTL < leases.TABLE_LEASE_WAIT  # a dead writer's lease runs out before the next one gives up
    dead = Leases(LocalBackend(publish._local_leases(local_ref)), "KratosForge", ttl=-1)  # never renewed, already expired
    assert dead.try_acquire("dbo.sales")
    assert publish.publish(pa.table({"id": [1]}), local_ref, "append", "token").version() == 1


def test_a_write_goes_ahead_once_the_other_writer_finishes(local_ref):
    with leases.table_lease(LocalBackend(publish._local_leases(local_ref)), "dbo", "sales", owner="etl-01"):
        done = []
        writer = threading.Thread(target=lambda: done.append(
            publish.publish(pa.table({"id": [1]}), local_ref, "append", "token").version()))
        writer.start()
        time.sleep(0.3)
        assert not done  # still waiting for etl-01
    writer.join(10)
    assert done == [1]
    assert not list(publish._local_leases(local_ref).glob("*.json"))  # released


def test_four_writers_at_once_each_write_in_turn(local_ref):
    errors, versions = [], []

    def write(n):
        try:
            versions.append(publish.publish(pa.table({"id": [n] * 10}), local_ref, "append", "token").version())
        except Exception as exc:  # pragma: no cover - the test fails on it below
            errors.append(exc)

    writers = [threading.Thread(target=write, args=(n,)) for n in range(1, 5)]
    for w in writers:
        w.start()
    for w in writers:
        w.join(60)
    assert not errors and sorted(versions) == [1, 2, 3, 4]
    assert DeltaTable(local_ref.uri).to_pyarrow_table().num_rows == 41


def test_a_onelake_write_asks_the_tables_own_lakehouse_and_writes_nothing_while_it_is_held(monkeypatch):
    monkeypatch.setattr(leases, "TABLE_LEASE_WAIT", 0)
    live = {"owner": "etl-01", "expires": time.time() + 3600}
    fake, transport = session(lambda sent: Reply(409) if sent.method == "PUT" else Reply(200, live, {"ETag": '"e1"'}))
    monkeypatch.setattr(http_session, "session", lambda: fake)
    monkeypatch.setattr(delta_profile, "check_target", lambda uri, opts: None)
    monkeypatch.setattr(publish, "write_deltalake", lambda *a, **k: pytest.fail("wrote while the table was held"))
    ref = TableRef("ws-guid", "lh-guid", "dbo", "sales")
    with pytest.raises(LeaseBusy, match="etl-01"):
        publish.publish(pa.table({"id": [1]}), ref, "overwrite", "token")
    first = transport.sent[0]
    assert first.method == "PUT" and first.headers["If-None-Match"] == "*"
    assert first.base == "https://onelake.dfs.fabric.microsoft.com/ws-guid/lh-guid/Files/_fabric_desktop/leases/dbo.sales.json"
    assert first.headers["Authorization"] == "Bearer token"


class Desktop(MetaMixin):
    def __init__(self):
        self.auth = SimpleNamespace(storage_token=lambda: "token")


def test_a_metadata_run_and_a_notebook_write_wait_for_each_other(tmp_path, monkeypatch):
    def folder(ws, lh, *a, **k):
        return LocalBackend(tmp_path / "onelake" / ws / lh)

    monkeypatch.setattr(meta_service, "OneLakeBackend", folder)  # the environment's LH_Meta lease
    monkeypatch.setattr(leases, "OneLakeBackend", folder)  # the lease in the table's own lakehouse
    monkeypatch.setattr(leases, "TABLE_LEASE_WAIT", 0)
    monkeypatch.setattr(delta_profile, "check_target", lambda uri, opts: None)
    monkeypatch.setattr(publish, "write_deltalake", lambda *a, **k: pytest.fail("wrote while the table was held"))
    store = OneLakeStore("dev", "ws1", {"LH_Meta": "meta1", "LH_Silver": "lh2"}, lambda: "token", tmp_path / "cache")
    project = SimpleNamespace(layer=lambda name: SimpleNamespace(lakehouse="LH_Silver"))
    entity = SimpleNamespace(layer="silver", schema="dbo", table="customers")
    lock = Desktop()._table_lock(project, "dev", store, owner="KratosForge", seconds=1)

    with lock(entity):  # a run on this PC holds both leases; a notebook writing the table is turned away
        assert (tmp_path / "onelake" / "ws1" / "meta1" / "LH_Silver__dbo.customers.json").exists()
        with pytest.raises(LeaseBusy, match="dbo.customers is being written by KratosForge"):
            publish.publish(pa.table({"id": [1]}), TableRef("ws1", "lh2", "dbo", "customers"), "append", "token")

    notebook = Leases(folder("ws1", "lh2"), "a notebook", ttl=3600)
    assert notebook.try_acquire("dbo.customers")  # and while a notebook writes it, the run waits
    with pytest.raises(LeaseBusy, match="a notebook"):
        with lock(entity):
            pytest.fail("ran while a notebook held the table")
    assert not (tmp_path / "onelake" / "ws1" / "meta1" / "LH_Silver__dbo.customers.json").exists()  # let go


def test_a_hung_renewal_times_out_well_inside_the_lease_and_is_reported_lost(monkeypatch):
    import requests

    from fabric_desktop.leases import LeaseLost
    from tests.fake_http import FakeTransport

    timeouts, created = [], []

    class Hangs(FakeTransport):
        def send(self, request, stream=False, timeout=None, **kw):
            timeouts.append(timeout)
            if request.method == "PUT" and "If-Match" in request.headers:  # a renewal: OneLake doesn't answer
                raise requests.Timeout("read timed out")
            if request.method == "PUT":
                created.append(request.url)
            return super().send(request, stream=stream, timeout=timeout, **kw)

    transport = Hangs(lambda sent: Reply(201, None, {"ETag": '"e1"'}))
    fake = requests.Session()
    fake.mount("https://", transport)
    monkeypatch.setattr(http_session, "session", lambda: fake)
    ttl = 0.9
    with pytest.raises(LeaseLost):
        with leases.onelake_table_lease("ws", "lh", "dbo", "sales", lambda: "token", owner="KratosForge", ttl=ttl):
            time.sleep(ttl * 1.6)  # a long write while every renewal hangs
    assert created and all(t is not None and t <= ttl / 9 for t in timeouts)
    assert leases.lease_timeout(leases.TABLE_LEASE_TTL) * 3 <= leases.TABLE_LEASE_TTL / 3
