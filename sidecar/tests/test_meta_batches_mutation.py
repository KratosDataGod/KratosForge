"""Mutation batch 42: the batch plan for full loads bigger than memory (meta/batches.py).

Each test pins one rule the module's own tests left open: the row target is a "no more than" limit,
a batch never splits a value, the empty-value batch comes last, and the folded step text stops at
three results. No files, no Delta table: these are the pure helpers.
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.meta import batches, preflight


def _counts(pairs: list[tuple[object, int]]) -> pl.DataFrame:
    return pl.DataFrame({"k": [v for v, _ in pairs], "len": [n for _, n in pairs]},
                        schema={"k": pl.Int64, "len": pl.UInt32})


def _est(rows: int, memory: int) -> preflight.Estimate:
    return preflight.Estimate(files=1, files_total=1, rows=rows, bytes_on_disk=memory, memory_bytes=memory)


# --- Batch ---------------------------------------------------------------------------------------

def test_a_batch_selects_its_values_inclusive_of_both_ends():
    df = pl.DataFrame({"k": [1, 2, 3, 4, None]})
    assert df.filter(batches.Batch(2, 3, 2).expr("k"))["k"].to_list() == [2, 3]


def test_the_empty_value_batch_selects_only_empty_rows():
    df = pl.DataFrame({"k": [1, None, 3, None]})
    out = df.filter(batches.Batch(None, None, 2, nulls=True).expr("k"))
    assert out.height == 2 and out["k"].null_count() == 2


def test_a_batch_of_one_value_names_it_once_and_a_range_names_both_ends():
    assert batches.Batch(5, 5, 1).text() == "5"
    assert batches.Batch(5, 9, 3).text() == "5 to 9"
    assert batches.Batch(None, None, 4, nulls=True).text() == "rows with no value"
    assert batches.Batch(1, 2, 1).nulls is False  # a batch is of values unless it says otherwise


@pytest.mark.parametrize("n, text", [(0, "0 batches"), (1, "1 batch"), (2, "2 batches")])
def test_count_says_batch_only_for_one(n, text):
    assert batches.count(n) == text


# --- batch_rows ----------------------------------------------------------------------------------

@pytest.mark.parametrize("est, available", [
    (_est(0, 1000), 10**9),    # no rows to go on
    (_est(1000, 0), 10**9),    # no size to go on
    (_est(1000, 1000), None),  # free memory can't be told
])
def test_without_an_estimate_or_free_memory_the_default_size_is_used(est, available):
    assert batches.batch_rows(est, available) == batches.DEFAULT_BATCH_ROWS


def test_batch_size_is_the_memory_share_over_headroom_per_row():
    # 100 bytes a row; 6 GB free * 0.5 / 3.0 = 1 GB, so 10M rows
    est = _est(1_000_000, 100_000_000)
    assert batches.batch_rows(est, 6_000_000_000) == int(6_000_000_000 * batches.MEMORY_SHARE
                                                         / preflight.HEADROOM / 100)
    assert batches.batch_rows(est, 6_000_000_000) == 10_000_000


def test_a_small_memory_never_goes_below_the_minimum_batch():
    est = _est(1_000, 1_000_000)  # 1 KB a row
    assert batches.batch_rows(est, 1_000_000) == batches.MIN_BATCH_ROWS
    # just above the minimum stays as computed
    above = int((batches.MIN_BATCH_ROWS + 10) * 1000 * preflight.HEADROOM / batches.MEMORY_SHARE)
    assert batches.batch_rows(est, above) == batches.MIN_BATCH_ROWS + 10


# --- plan ----------------------------------------------------------------------------------------

def test_values_fill_a_batch_up_to_exactly_the_target():
    out = batches.plan(_counts([(1, 4), (2, 6), (3, 1)]), "k", 10)
    assert [(b.lo, b.hi, b.rows) for b in out] == [(1, 2, 10), (3, 3, 1)]


def test_one_row_over_the_target_starts_a_new_batch():
    out = batches.plan(_counts([(1, 5), (2, 6)]), "k", 10)
    assert [(b.lo, b.hi, b.rows) for b in out] == [(1, 1, 5), (2, 2, 6)]


def test_a_value_bigger_than_the_target_is_a_batch_of_its_own():
    out = batches.plan(_counts([(1, 2), (2, 50), (3, 2)]), "k", 10)
    assert [(b.lo, b.hi, b.rows) for b in out] == [(1, 1, 2), (2, 2, 50), (3, 3, 2)]


def test_values_are_planned_in_order_whatever_order_they_come_in():
    out = batches.plan(_counts([(3, 1), (1, 1), (2, 1)]), "k", 2)
    assert [(b.lo, b.hi) for b in out] == [(1, 2), (3, 3)]


def test_empty_values_come_last_as_one_batch():
    out = batches.plan(_counts([(None, 7), (1, 3)]), "k", 100)
    assert [(b.lo, b.hi, b.rows, b.nulls) for b in out] == [(1, 1, 3, False), (None, None, 7, True)]


def test_no_empty_value_batch_when_there_are_none():
    assert all(not b.nulls for b in batches.plan(_counts([(1, 3), (2, 3)]), "k", 4))
    # a null group that counts no rows makes no batch either
    assert [b.nulls for b in batches.plan(_counts([(None, 0), (1, 3)]), "k", 4)] == [False]


def test_a_column_of_only_empty_values_is_one_batch():
    out = batches.plan(_counts([(None, 9)]), "k", 4)
    assert [(b.rows, b.nulls) for b in out] == [(9, True)]


# --- fold_steps ----------------------------------------------------------------------------------

def _step(name, rows, seconds, detail=""):
    return SimpleNamespace(name=name, rows=rows, seconds=seconds, detail=detail)


def test_steps_fold_by_name_in_the_order_they_first_ran():
    out = batches.fold_steps([_step("read", 10, 1.0), _step("clean", 9, 0.5), _step("read", 5, 0.25)])
    assert [o[0] for o in out] == ["read", "clean"]
    assert out[0][:3] == ("read", 15, 1.25)
    assert out[1][:3] == ("clean", 9, 0.5)


def test_seconds_are_rounded_to_milliseconds():
    assert batches.fold_steps([_step("a", 1, 0.12345), _step("a", 1, 0.0002)])[0][2] == 0.124
    assert batches.fold_steps([_step("a", 1, 0.1231)])[0][2] == 0.123


def test_the_same_detail_is_said_once_and_empty_details_are_left_out():
    out = batches.fold_steps([_step("a", 1, 0, "ok"), _step("a", 1, 0, "ok"), _step("a", 1, 0, "")])
    assert out[0][3] == "ok"


def test_three_different_details_are_all_said():
    out = batches.fold_steps([_step("a", 1, 0, t) for t in ("x", "y", "z")])
    assert out[0][3] == "x; y; z"


def test_more_than_three_details_say_how_many_more():
    out = batches.fold_steps([_step("a", 1, 0, t) for t in ("w", "x", "y", "z", "v")])
    assert out[0][3] == "w; x; y; and 2 other results"


def test_a_fourth_detail_is_counted_not_dropped():
    out = batches.fold_steps([_step("a", 1, 0, t) for t in ("w", "x", "y", "z")])
    assert out[0][3] == "w; x; y; and 1 other results"
