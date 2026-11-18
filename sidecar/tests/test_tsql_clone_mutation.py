"""tsql_clone.py's edges, pinned so that changes to its bounds and constants fail a test (mutation testing,
batch 88): a statement cut short at the end of a batch, where a clone statement ends, how far after
ALTER DATABASE the SET TIMESTAMP is looked for, and an AT time read to the millisecond.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from fabric_desktop import tsql_clone

NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.timezone.utc)


def test_a_clone_is_a_value_that_cant_be_changed():
    [found] = tsql_clone.clones("CREATE TABLE dbo.c AS CLONE OF dbo.t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.at = "2026-11-01T10:00:00"  # type: ignore[misc]


@pytest.mark.parametrize("batch", ["SELECT 'abc", "SELECT [abc", "/* never closed"])
def test_a_batch_the_tokenizer_cant_read_has_no_clone_and_no_snapshot_move(batch):
    assert tsql_clone.clones(batch) == []
    assert tsql_clone.snapshot_timestamp(batch) is False
    assert tsql_clone.local_refusal(batch) == ""


@pytest.mark.parametrize("batch", ["SELECT 1; CREATE", "SELECT 1; CREATE TABLE", "SELECT 1; CREATE TABLE c AS",
                                   "SELECT 1; CREATE TABLE c AS CLONE"])
def test_a_create_cut_short_before_clone_of_is_not_a_clone(batch):
    assert tsql_clone.clones(batch) == []


def test_clone_of_with_no_source_is_returned_with_no_names_so_it_is_refused_by_name():
    batch = "CREATE TABLE c AS CLONE OF"
    [found] = tsql_clone.clones(batch)
    assert (found.clone, found.source, found.at, found.start, found.end) == ((), (), None, 0, len(batch))
    assert "couldn't be read" in tsql_clone.local_refusal(batch)


def test_a_source_name_cut_short_after_its_dot_keeps_the_part_before_it():
    batch = "CREATE TABLE c AS CLONE OF dbo."
    [found] = tsql_clone.clones(batch)
    assert (found.clone, found.source) == (("c",), ("dbo",))
    assert batch[found.start:found.end] == "CREATE TABLE c AS CLONE OF dbo"


@pytest.mark.parametrize("batch", ["create table c as clone of t;", "CREATE TABLE dbo.c AS CLONE OF dbo.t",
                                   "CREATE TABLE c AS CLONE OF t AT '2026-11-01T10:00:00';"])
def test_a_clone_statement_ends_at_its_semicolon_or_at_the_end_of_the_batch(batch):
    [found] = tsql_clone.clones(batch)
    assert (found.start, found.end) == (0, len(batch))


def test_the_text_after_a_clones_semicolon_is_left_alone():
    batch = "CREATE TABLE c AS CLONE OF t; SELECT 1"
    [found] = tsql_clone.clones(batch)
    assert batch[found.start:found.end] == "CREATE TABLE c AS CLONE OF t;"
    assert tsql_clone.without(batch, [found]) == " " * 29 + " SELECT 1"


@pytest.mark.parametrize("batch", ["SELECT 1; ALTER", "ALTER", "UPDATE t SET timestamp = 1",
                                   "ALTER DATABASE s MODIFY NAME = t", "ALTER TABLE t SET TIMESTAMP = 1"])
def test_set_timestamp_counts_only_after_alter_database(batch):
    assert tsql_clone.snapshot_timestamp(batch) is False


def test_set_timestamp_right_after_alter_database_is_found():
    assert tsql_clone.snapshot_timestamp("ALTER DATABASE SET TIMESTAMP = CURRENT_TIMESTAMP") is True


def test_set_timestamp_is_looked_for_within_ten_words_of_alter_database():
    assert tsql_clone.snapshot_timestamp("ALTER DATABASE a b c d e f g h SET TIMESTAMP = 1") is True
    # TIMESTAMP is the eleventh word after DATABASE: past the ten looked at.
    assert tsql_clone.snapshot_timestamp("ALTER DATABASE a b c d e f g h i SET TIMESTAMP = 1") is False


def test_a_clone_time_half_a_second_after_now_is_later_than_now():
    assert "later than now" in tsql_clone.at_problem("2026-11-17T12:00:00.500", NOW)
    assert tsql_clone.at_problem("2026-11-17T12:00:00.000", NOW) == ""


def test_a_clone_time_equal_to_now_to_the_millisecond_is_not_later():
    now = NOW.replace(microsecond=999000)
    assert tsql_clone.at_problem("2026-11-17T12:00:00.999", now) == ""
    assert "later than now" in tsql_clone.at_problem("2026-11-17T12:00:00.999", now.replace(microsecond=998000))


def test_a_batch_with_neither_gets_an_empty_refusal():
    out = tsql_clone.local_refusal("SELECT 1")
    assert isinstance(out, str) and out == ""
