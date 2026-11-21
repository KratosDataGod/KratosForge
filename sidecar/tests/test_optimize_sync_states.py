"""ESI-105: what is waiting for the central lakehouse, as Synced, Pending, Synchronizing, Failed,
Conflict or Offline, per kind and for the lakehouse, through optimize_status."""

from __future__ import annotations

import sqlite3
import time

import pytest
from deltalake.exceptions import CommitFailedError

from fabric_desktop import sqlitefile
from fabric_desktop.optimize import outbox as ob
from fabric_desktop.optimize import schema, setup
from fabric_desktop.optimize.shared import Shared
from test_optimize_outbox import Clock, _offline, _token, payload
from test_optimize_service import svc, xray_result  # noqa: F401 - the fixture is used by name


def runs(*ids):
    return {"rows": [{"execution_id": i} for i in ids]}


def test_nothing_waiting_is_synced(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    got = box.sync_state("lh1")
    assert got["state"] == ob.SYNCED and got["dropped"] == 0 and got["last_sync"] is None
    assert {k: v["state"] for k, v in got["kinds"].items()} == {ob.ANALYSIS: ob.SYNCED, ob.LOCAL_EXECUTIONS: ob.SYNCED}


@pytest.mark.parametrize("reason", ob.REASONS)
def test_each_reason_is_its_kinds_state(tmp_path, reason):
    box = ob.Outbox(tmp_path / "o.sqlite")
    box.add("lh1", ob.LOCAL_EXECUTIONS, runs("a", "b"), "why", reason)
    got = box.sync_state("lh1")
    assert got["state"] == reason
    assert got["kinds"][ob.LOCAL_EXECUTIONS] == {"state": reason, "pending": 1, "pending_rows": 2}
    assert got["kinds"][ob.ANALYSIS]["state"] == ob.SYNCED


def test_the_worst_reason_wins_and_kinds_stay_apart(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    box.add("lh1", ob.ANALYSIS, payload(1), "throttled", ob.PENDING)
    box.add("lh1", ob.ANALYSIS, payload(2), "down", ob.OFFLINE)
    box.add("lh1", ob.LOCAL_EXECUTIONS, runs("a"), "newer schema", ob.CONFLICT)
    got = box.sync_state("lh1")
    assert got["kinds"][ob.ANALYSIS]["state"] == ob.OFFLINE and got["kinds"][ob.ANALYSIS]["pending"] == 2
    assert got["kinds"][ob.LOCAL_EXECUTIONS]["state"] == ob.CONFLICT
    assert got["state"] == ob.CONFLICT
    assert box.sync_state("lh2")["state"] == ob.SYNCED  # kept per lakehouse


def test_a_retry_updates_the_reason(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    item = box.add("lh1", ob.ANALYSIS, payload(), "down", ob.OFFLINE)
    box.failed(item, "403 Forbidden", ob.FAILED)
    assert box.sync_state("lh1")["state"] == ob.FAILED
    box.sent(item)
    assert box.sync_state("lh1")["state"] == ob.SYNCED and box.sync_state("lh1")["last_sync"]


def test_synchronizing_while_a_send_runs_and_a_stale_mark_is_ignored(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock)
    box.add("lh1", ob.ANALYSIS, payload(), "down", ob.OFFLINE)
    with box.syncing("lh1"):
        got = box.sync_state("lh1")
        assert got["state"] == ob.SYNCHRONIZING and got["waiting_state"] == ob.OFFLINE and got["syncing_since"]
    assert box.sync_state("lh1")["state"] == ob.OFFLINE
    with pytest.raises(RuntimeError):
        with box.syncing("lh1"):
            raise RuntimeError("send failed")
    assert box.sync_state("lh1")["syncing_since"] is None  # cleared on the way out
    # A mark left by an app that closed mid-send stops counting.
    with box._tx() as db:
        db.execute("update outbox_state set syncing_since = ? where lakehouse_id = 'lh1'", (clock.now,))
    assert box.sync_state("lh1")["state"] == ob.SYNCHRONIZING
    clock.now += ob.STALE_SYNC_SECONDS
    assert box.sync_state("lh1")["state"] == ob.OFFLINE


def test_dropped_items_are_still_shown(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite", max_items=1)
    box.add("lh1", ob.ANALYSIS, payload(1), "down", ob.OFFLINE)
    box.add("lh1", ob.ANALYSIS, payload(2), "down", ob.OFFLINE)
    got = box.sync_state("lh1")
    assert got["dropped"] == 1 and got["kinds"][ob.ANALYSIS]["pending"] == 1


def test_an_unknown_reason_is_refused(tmp_path):
    box = ob.Outbox(tmp_path / "o.sqlite")
    with pytest.raises(ValueError, match="reason"):
        box.add("lh1", ob.ANALYSIS, payload(), "x", "broken")
    item = box.add("lh1", ob.ANALYSIS, payload(), "x")
    with pytest.raises(ValueError, match="reason"):
        box.failed(item, "x", ob.SYNCED)


def test_backoff_is_unchanged(tmp_path):
    clock = Clock()
    box = ob.Outbox(tmp_path / "o.sqlite", clock=clock)
    item = box.add("lh1", ob.ANALYSIS, payload(), "down", ob.OFFLINE)
    clock.now += ob.BACKOFF_SECONDS
    box.failed(item, "down", ob.OFFLINE)
    assert box.due("lh1") == []
    clock.now += ob.BACKOFF_SECONDS * 2
    assert len(box.due("lh1")) == 1


def test_an_outbox_an_older_app_made_gains_the_columns_and_keeps_its_items(tmp_path):
    path, at = tmp_path / "o.sqlite", time.time()
    with sqlitefile.connect(path) as db:
        db.executescript("""
            create table outbox (id text primary key, lakehouse_id text not null, kind text not null,
                payload text not null, rows integer not null, created_at real not null,
                attempts integer not null default 0, next_at real not null, last_error text);
            create table outbox_state (lakehouse_id text primary key, last_sync real, last_error text,
                last_error_at real, dropped integer not null default 0);
        """)
        db.execute("insert into outbox values ('old', 'lh1', 'analysis', '{\"run\": {\"run_id\": \"r0\"}}', 1, ?, 1, ?,"
                   " 'down')", (at, at))
    box = ob.Outbox(path)
    got = box.sync_state("lh1")
    assert got["state"] == ob.PENDING and got["kinds"][ob.ANALYSIS]["pending"] == 1  # no reason: waiting to retry
    box.add("lh1", ob.LOCAL_EXECUTIONS, runs("a"), "down", ob.OFFLINE)
    assert box.sync_state("lh1")["state"] == ob.OFFLINE
    ob.Outbox(path)  # opening again changes nothing
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from outbox").fetchone()[0] == 2


# ---------- through the service

@pytest.mark.parametrize("exc, state", [
    (ConnectionError("network down"), ob.OFFLINE),
    (TimeoutError("timed out"), ob.OFFLINE),
    (CommitFailedError("another writer committed first"), ob.PENDING),
])
def test_a_queued_write_says_why(svc, monkeypatch, exc, state):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    _offline(monkeypatch, "record_analysis", exc)
    assert svc._xray_share(xray_result(), _token)["queued"] == 1
    sync = svc.optimize_status()["sync"]
    assert sync["state"] == state and sync["kinds"][ob.ANALYSIS]["state"] == state
    assert sync["kinds"][ob.LOCAL_EXECUTIONS]["state"] == ob.SYNCED


@pytest.mark.parametrize("exc, state", [
    (PermissionError("403 Forbidden"), ob.FAILED),
    (PermissionError("This lakehouse is open read-only for you"), ob.FAILED),
    (setup.SchemaMismatch(setup.NEWER), ob.CONFLICT),
    (setup.SchemaMismatch(setup.UPGRADE), ob.CONFLICT),
    # Typed, not matched on wording: the same text in a plain PermissionError is a refusal.
    (PermissionError(setup.MESSAGES[setup.NEWER]), ob.FAILED),
])
def test_a_waiting_item_the_lakehouse_refuses_this_person_or_schema_is_marked(svc, monkeypatch, exc, state):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    flaky = _offline(monkeypatch, "record_analysis", ConnectionError("down"))
    svc._xray_share(xray_result(), _token)
    assert svc.optimize_status()["sync"]["state"] == ob.OFFLINE
    monkeypatch.undo()
    _offline(monkeypatch, "record_analysis", exc)
    assert flaky["down"]
    out = svc.optimize_switch(lakehouse)
    assert out["sync"]["sent"] == 0 and out["sync"]["waiting"] == 1
    assert svc.optimize_status()["sync"]["state"] == state


def test_a_send_shows_synchronizing_while_it_runs_then_synced(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    state = _offline(monkeypatch, "record_analysis", ConnectionError("down"))
    svc._xray_share(xray_result(), _token)
    state["down"] = False
    seen = []
    real = Shared.record_analysis

    def watching(self, *a, **k):
        seen.append(svc.optimize_status()["sync"]["state"])
        return real(self, *a, **k)
    monkeypatch.setattr(Shared, "record_analysis", watching)
    svc.optimize_switch(lakehouse)
    assert seen == [ob.SYNCHRONIZING]
    sync = svc.optimize_status()["sync"]
    assert sync["state"] == ob.SYNCED and sync["last_sync"]


def test_no_lakehouse_no_sync_state(svc):  # noqa: F811
    assert svc.optimize_status()["sync"] is None


def test_a_newer_lakehouse_found_while_sending_is_a_conflict(svc, monkeypatch):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    lakehouse = svc.optimize_status()["active"]["lakehouse_id"]
    _offline(monkeypatch, "record_analysis", ConnectionError("down"))
    svc._xray_share(xray_result(), _token)
    monkeypatch.undo()
    # Another installation upgraded the lakehouse past what this app knows: the real check refuses.
    monkeypatch.setattr(setup, "applied_versions", lambda lake: [schema.latest() + 1])
    out = svc.optimize_switch(lakehouse)
    assert out["sync"]["waiting"] == 1 and "newer Optimize schema" in out["sync"]["error"]
    assert svc.optimize_status()["sync"]["state"] == ob.CONFLICT


def test_a_table_an_older_lakehouse_lacks_is_a_typed_mismatch(tmp_path):
    from fabric_desktop.optimize.installation import Installation
    from fabric_desktop.optimize.lakehouse import LocalLakehouse

    lake = LocalLakehouse(tmp_path / "lakehouse")
    with pytest.raises(setup.SchemaMismatch, match="older Optimize schema") as err:
        setup.write(lake, Installation(tmp_path / "pc" / "optimize"), "pricing", [{"record_id": "p1"}], version=1)
    assert err.value.state == setup.UPGRADE
    assert isinstance(err.value, PermissionError)  # callers that catch PermissionError still do


def test_a_mark_that_cant_be_cleared_never_hides_the_sends_error(tmp_path, monkeypatch):
    box = ob.Outbox(tmp_path / "o.sqlite")
    real, calls = box._tx, []

    def tx():
        calls.append(1)
        if len(calls) == 2:  # the clear
            raise sqlite3.OperationalError("database is locked")
        return real()
    monkeypatch.setattr(box, "_tx", tx)
    with pytest.raises(RuntimeError, match="send failed"):
        with box.syncing("lh1"):
            raise RuntimeError("send failed")
