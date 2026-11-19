"""Kill tests for meta/restore.py (docs/quality/mutation-testing.md, batch 112).

A commit's time and rows, how many commits are read, which VACUUM sets the horizon and how far back
it reaches, and a restore that refuses when the files it needs are gone.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop.meta import restore

DAY = 86_400_000  # ms


class _Table:
    """A DeltaTable stand-in: ``commits`` oldest first, each with a version and a timestamp (ms)."""

    limits: list[int] = []

    def __init__(self, commits):
        self.commits = commits

    def history(self, limit):
        _Table.limits.append(limit)
        return list(reversed(self.commits))[:limit]

    def version(self):
        return max(c["version"] for c in self.commits)


def _with(monkeypatch, commits):
    _Table.limits = []
    monkeypatch.setattr(restore, "DeltaTable", lambda uri, storage_options=None: _Table(commits))


def _write(v, day):
    return {"version": v, "timestamp": day * DAY, "operation": "WRITE"}


def test_a_commit_time_is_milliseconds_since_1970_in_utc():
    assert restore._at(1_000_000) == dt.datetime(1970, 1, 1, 0, 16, 40, tzinfo=dt.timezone.utc)
    assert restore._ts(None) is None


def test_rows_come_from_the_commit_metrics():
    assert restore._rows({"operationMetrics": {"num_added_rows": "5"}}) == 5
    assert restore._rows({"operationMetrics": {"numOutputRows": 7}}) == 7
    assert restore._rows({}) is None


def test_fifty_versions_are_listed_and_a_thousand_commits_read_for_vacuums(monkeypatch):
    _with(monkeypatch, [_write(v, v) for v in range(60)])
    rows = restore.versions("t", None, 7)
    assert len(rows) == 50 and rows[0]["version"] == 59
    assert _Table.limits == [50, 1000]


def test_without_a_vacuum_every_earlier_version_can_be_restored(monkeypatch):
    _with(monkeypatch, [_write(0, 0), _write(1, 1), _write(2, 30)])
    rows = {r["version"]: r for r in restore.versions("t", None, 7)}
    assert rows[0]["restorable"] and rows[1]["restorable"] and not rows[2]["restorable"]
    assert rows[1]["at"] == "1970-01-02T00:00:00+00:00"


def test_a_vacuum_reaches_back_by_its_own_retention(monkeypatch):
    # Version 1 is superseded on day 7, version 2 on day 9; a VACUUM on day 10 that keeps 2 days.
    vacuum = {"version": 4, "timestamp": 10 * DAY, "operation": "VACUUM START",
              "operationParameters": {"specifiedRetentionMillis": str(2 * DAY)}}
    _with(monkeypatch, [_write(0, 0), _write(1, 1), _write(2, 7), _write(3, 9), vacuum])
    rows = {r["version"]: r for r in restore.versions("t", None, 30)}
    assert rows[2]["restorable"]  # superseded on day 9, after day 8
    assert not rows[1]["restorable"] and rows[1]["why_not"].startswith("VACUUM has removed its files")


def test_a_vacuum_without_its_retention_uses_the_layer_s(monkeypatch):
    vacuum = {"version": 3, "timestamp": 10 * DAY, "operation": "VACUUM START", "operationParameters": {}}
    _with(monkeypatch, [_write(0, 0), _write(1, 2), _write(2, 4), vacuum])
    rows = {r["version"]: r for r in restore.versions("t", None, 7)}
    assert not rows[0]["restorable"]  # superseded on day 2, before day 3
    assert rows[1]["restorable"]      # superseded on day 4


def test_restoring_to_a_version_whose_files_are_gone_changes_nothing(tmp_path):
    uri = str(tmp_path / "t")
    for i in range(3):
        write_deltalake(uri, pl.DataFrame({"a": [i]}).to_arrow(), mode="overwrite")
    DeltaTable(uri).vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
    before = DeltaTable(uri).version()
    with pytest.raises(Exception):  # noqa: B017 - deltalake's own error for the missing files
        restore.restore(uri, None, 0)
    assert DeltaTable(uri).version() == before


def test_the_latest_vacuum_s_horizon_counts(monkeypatch):
    first = {"version": 2, "timestamp": 5 * DAY, "operation": "VACUUM START",
             "operationParameters": {"specifiedRetentionMillis": str(DAY)}}
    second = {"version": 5, "timestamp": 10 * DAY, "operation": "VACUUM START",
              "operationParameters": {"specifiedRetentionMillis": str(2 * DAY)}}
    _with(monkeypatch, [_write(0, 0), _write(1, 1), first, _write(3, 7), _write(4, 9), second])
    rows = {r["version"]: r for r in restore.versions("t", None, 30)}
    assert not rows[1]["restorable"]  # superseded on day 7, before the second VACUUM's day 8
    assert rows[3]["restorable"]      # superseded on day 9


def test_maintenance_is_marked_and_only_a_version_that_cannot_be_restored_says_why(monkeypatch):
    optimize = {"version": 1, "timestamp": DAY, "operation": "OPTIMIZE"}
    _with(monkeypatch, [_write(0, 0), optimize, _write(2, 2)])
    rows = {r["version"]: r for r in restore.versions("t", None, 7)}
    assert rows[1]["maintenance"] and not rows[0]["maintenance"]
    assert rows[0]["restorable"] and rows[0]["why_not"] is None
    assert rows[2]["current"] and rows[2]["why_not"] is None


class _Restorable:
    version_now = 3
    metrics: dict = {}

    def __init__(self, uri, storage_options=None):
        pass

    def version(self):
        return _Restorable.version_now

    def restore(self, version, **kw):
        _Restorable.version_now += 1
        return _Restorable.metrics


@pytest.mark.parametrize("metrics, counts", [({}, (0, 0)), ({"numRestoredFile": 0, "numRemovedFile": None}, (0, 0)),
                                             ({"numRestoredFile": "3", "numRemovedFile": 2}, (3, 2))])
def test_a_restore_counts_the_files_it_restored_and_removed(monkeypatch, metrics, counts):
    _Restorable.version_now, _Restorable.metrics = 3, metrics
    monkeypatch.setattr(restore, "DeltaTable", _Restorable)
    monkeypatch.setattr(restore.delta_profile, "note_after_write", lambda table: None)
    assert restore.restore("t", None, 1) == {"from_version": 3, "to_version": 1, "new_version": 4,
                                              "files_restored": counts[0], "files_removed": counts[1]}


def test_the_horizon_is_exact_to_the_millisecond_and_the_hour(monkeypatch):
    hour = 3_600_000
    # A VACUUM on day 10 that keeps 2 days: its horizon is day 8 exactly.
    vacuum = {"version": 3, "timestamp": 10 * DAY, "operation": "VACUUM START",
              "operationParameters": {"specifiedRetentionMillis": str(2 * DAY)}}
    _with(monkeypatch, [_write(0, 0), {**_write(1, 0), "timestamp": 8 * DAY + 1}, _write(2, 9), vacuum])
    assert {r["version"]: r for r in restore.versions("t", None, 30)}[0]["restorable"]
    # A VACUUM with the layer's 7 days: its horizon is day 3, so a version superseded an hour before is gone.
    vacuum = {"version": 3, "timestamp": 10 * DAY, "operation": "VACUUM START"}
    _with(monkeypatch, [_write(0, 0), {**_write(1, 0), "timestamp": 3 * DAY - hour}, _write(2, 9), vacuum])
    assert not {r["version"]: r for r in restore.versions("t", None, 7)}[0]["restorable"]
