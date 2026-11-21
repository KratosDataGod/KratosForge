"""meta/restore.py: rows that can't be read, and history entries that aren't versions."""

from __future__ import annotations

from fabric_desktop.meta import restore

DAY = 86_400_000  # ms


class _Table:
    """A DeltaTable stand-in: ``commits`` oldest first."""

    def __init__(self, commits):
        self.commits = commits

    def history(self, limit):
        return list(reversed(self.commits))[:limit]

    def version(self):
        return max(c["version"] for c in self.commits if "version" in c)


def test_rows_that_arent_a_number_are_unknown():
    assert restore._rows({"operationMetrics": {"num_added_rows": "many"}}) is None
    assert restore._rows({"operationMetrics": {"num_added_rows": ["1"]}}) is None
    # The first metric that is there decides; an empty one is skipped.
    assert restore._rows({"operationMetrics": {"num_added_rows": "", "numOutputRows": "7"}}) == 7


def test_history_entries_without_a_version_are_left_out(monkeypatch):
    commits = [{"version": 0, "timestamp": DAY, "operation": "CREATE TABLE"},
               {"timestamp": 2 * DAY, "operation": "CHECKPOINT"},  # no version: not a commit to restore
               {"version": 1, "timestamp": 3 * DAY, "operation": "WRITE",
                "operationMetrics": {"num_added_rows": "x"}}]
    monkeypatch.setattr(restore, "DeltaTable", lambda uri, storage_options=None: _Table(commits))
    out = restore.versions("t", None, 7)
    assert [v["version"] for v in out] == [1, 0]
    assert out[0]["current"] is True and out[0]["restorable"] is False and out[0]["rows"] is None
    assert out[1]["restorable"] is True and out[1]["why_not"] is None
