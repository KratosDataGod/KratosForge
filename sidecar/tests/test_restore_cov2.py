"""A table's versions (meta/restore.py) read from a Delta history that has a commit with no time, metrics
that aren't numbers, and an entry that isn't a version. The table is a stand-in for its log."""

from __future__ import annotations

import pytest

from fabric_desktop.meta import restore


class Table:
    def __init__(self, uri, storage_options=None):
        self.uri = uri

    def history(self, limit):
        return [
            {"version": 2, "timestamp": 3_000, "operation": "WRITE", "operationMetrics": {"num_added_rows": "x"}},
            {"operation": "CHECKPOINT"},  # no version: not a commit to list
            {"version": 1, "operation": "WRITE", "operationMetrics": {"numOutputRows": 5}},  # no timestamp
            {"version": 0, "timestamp": 1_000, "operation": "CREATE TABLE", "operationMetrics": {"other": 1}},
        ][:limit]

    def version(self):
        return 2


def test_a_history_without_times_metrics_or_versions_still_lists_each_version(monkeypatch):
    monkeypatch.setattr(restore, "DeltaTable", Table)
    listed = restore.versions("lake/t", None, retention_days=7)
    assert [(v["version"], v["at"], v["rows"], v["current"], v["restorable"]) for v in listed] == [
        (2, "1970-01-01T00:00:03+00:00", None, True, False),  # rows that aren't a number: none said
        (1, None, 5, False, True),
        (0, "1970-01-01T00:00:01+00:00", None, False, True),
    ]
    assert all(v["why_not"] is None for v in listed)


@pytest.mark.parametrize("metrics, rows", [
    ({"num_added_rows": "", "numOutputRows": "12"}, 12),  # an empty count is skipped for the next
    ({"numTargetRowsInserted": None}, None),
    ({"num_output_rows": [3]}, None),
    (None, None),
])
def test_the_rows_a_commit_wrote(metrics, rows):
    assert restore._rows({"operationMetrics": metrics}) == rows
