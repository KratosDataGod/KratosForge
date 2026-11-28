"""Mutation batch 547: optimize/outbox.py, re-scored. The survivors were the counts a kind that isn't
one of KINDS starts from in ``sync_state`` (an item a newer app queued, say)."""

from __future__ import annotations

import sqlite3

from fabric_desktop.optimize import outbox as ob


def test_a_kind_this_app_doesnt_know_is_counted_from_nothing(tmp_path):
    path = tmp_path / "outbox.sqlite"
    box = ob.Outbox(path, clock=lambda: 1_000_000.0)
    box.add("lh1", ob.LOCAL_EXECUTIONS, {"rows": [{"execution_id": "e1"}, {"execution_id": "e2"}]})
    with sqlite3.connect(path) as db:
        db.execute("insert into outbox (id, lakehouse_id, kind, payload, rows, created_at, attempts, next_at, reason) "
                   "values ('x1', 'lh1', 'newer_kind', '{}', 3, 1000000.0, 1, 1000060.0, 'offline')")
    db.close()
    got = box.sync_state("lh1")
    assert got["kinds"]["newer_kind"] == {"state": ob.OFFLINE, "pending": 1, "pending_rows": 3}
    assert got["kinds"][ob.LOCAL_EXECUTIONS] == {"state": ob.PENDING, "pending": 1, "pending_rows": 2}
    assert got["kinds"][ob.ANALYSIS] == {"state": ob.SYNCED, "pending": 0, "pending_rows": 0}
    assert got["state"] == ob.OFFLINE
