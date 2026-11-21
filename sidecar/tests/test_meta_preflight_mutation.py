"""Mutation-testing follow-up for meta/preflight.py (docs/quality/mutation-testing.md, batch 44).

The Delta read estimate before a load: rows and bytes counted from the log's add actions (files
without a row count still counted, at zero rows), sizes shown in MB or GB, and the memory guard
that stops a read only when the read with its headroom needs more than is free."""

from __future__ import annotations

import polars as pl
import pyarrow as pa

from fabric_desktop.meta import preflight
from fabric_desktop.meta.preflight import HEADROOM, Estimate, available_memory, estimate, mb, too_big


class _Log:
    """A table whose log holds the given add actions, as DeltaTable.get_add_actions(flatten=True) shows them."""

    def __init__(self, **columns):
        self.columns = columns

    def get_add_actions(self, flatten=False):
        assert flatten
        return pa.table(self.columns)


SCHEMA = {"id": pl.Int64, "name": pl.String}


# ---- mb ---------------------------------------------------------------------------------------------

def test_sizes_switch_to_gb_at_exactly_one_gb():
    assert mb(1024 ** 3) == "1.0 GB"
    assert mb(1024 ** 3 - 1) == "1024.0 MB"


def test_sizes_divide_by_powers_of_1024():
    assert mb(100 * 1024 ** 3) == "100.0 GB"
    assert mb(100 * 1024 ** 2) == "100.0 MB"
    assert mb(0) == "0.1 MB"


# ---- Estimate ---------------------------------------------------------------------------------------

def test_an_estimate_is_exact_unless_told_otherwise():
    e = Estimate(files=1, files_total=1, rows=5, bytes_on_disk=0, memory_bytes=0)
    assert e.exact_rows is True
    assert e.text() == "about 5 rows in 1 of 1 file (0.1 MB on disk, 0.1 MB in memory)"
    e2 = Estimate(files=1, files_total=2, rows=5, bytes_on_disk=0, memory_bytes=0, exact_rows=False)
    assert e2.text().startswith("about at least 5 rows in 1 of 2 files ")


# ---- estimate ---------------------------------------------------------------------------------------

def test_files_without_a_row_count_add_no_rows_and_make_the_count_inexact():
    log = _Log(num_records=pa.array([10, None, 5], pa.int64()), size_bytes=pa.array([100, 200, 300], pa.int64()))
    e = estimate(log, ["id"], SCHEMA)
    assert (e.files, e.files_total, e.rows, e.exact_rows) == (3, 3, 15, False)
    assert e.memory_bytes == 15 * 8
    assert e.bytes_on_disk == 300  # 600 bytes, one of two columns


def test_a_log_without_row_counts_counts_every_file_at_zero_rows():
    log = _Log(size_bytes=pa.array([100, 200], pa.int64()))
    e = estimate(log, ["id", "name"], SCHEMA)
    assert (e.files, e.rows, e.exact_rows, e.bytes_on_disk) == (2, 0, False, 300)


def test_nothing_after_the_watermark_reads_no_bytes():
    log = _Log(num_records=pa.array([10, 20], pa.int64()), size_bytes=pa.array([100, 200], pa.int64()),
               **{"max.id": pa.array([3, 4], pa.int64())})
    e = estimate(log, ["id", "name"], SCHEMA, watermark=("id", 4))
    assert (e.files, e.files_total, e.rows, e.bytes_on_disk, e.memory_bytes) == (0, 2, 0, 0, 0)  # all columns: the whole of nothing
    # The watermark keeps only files whose max is past it; a file at the watermark is already loaded.
    e = estimate(log, ["id"], SCHEMA, watermark=("id", 3))
    assert (e.files, e.rows) == (1, 20)


def test_an_empty_schema_reads_no_columns_and_does_not_divide_by_zero():
    log = _Log(num_records=pa.array([10], pa.int64()), size_bytes=pa.array([100], pa.int64()))
    e = estimate(log, ["id"], {})
    assert (e.rows, e.bytes_on_disk, e.memory_bytes) == (10, 0, 0)


# ---- the guard --------------------------------------------------------------------------------------

def test_available_memory_is_what_free_memory_reports(monkeypatch):
    monkeypatch.setattr(preflight.free_memory, "available", lambda: 123_456)
    assert available_memory() == 123_456
    monkeypatch.setattr(preflight.free_memory, "available", lambda: None)
    assert available_memory() is None


def test_a_read_that_exactly_fits_with_its_headroom_runs():
    e = Estimate(files=1, files_total=1, rows=100, bytes_on_disk=1000, memory_bytes=1000)
    assert too_big(e, int(1000 * HEADROOM)) == ""
    assert too_big(e, int(1000 * HEADROOM) - 1) != ""
    assert too_big(e, None) == ""
