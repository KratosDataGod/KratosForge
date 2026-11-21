"""Table previews (preview.py), the paths the other tests leave: a row count when the Delta log's file stats
can't be read, and when a file has none. The Delta table is a stand-in for the log's add actions."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pyarrow as pa

from fabric_desktop import preview


def test_a_log_whose_stats_cant_be_read_has_no_row_count(caplog):
    def unflattenable(flatten):
        raise ValueError("can't flatten this log")
    with caplog.at_level(logging.INFO, logger=preview._log.name):
        assert preview.row_count(SimpleNamespace(get_add_actions=unflattenable)) is None
    assert "No row count from the Delta log's file stats: ValueError" in caplog.text


def test_a_file_without_stats_leaves_the_count_unknown():
    table = SimpleNamespace(get_add_actions=lambda flatten: pa.table({"num_records": pa.array([3, None], pa.int64())}))
    assert preview.row_count(table) is None
