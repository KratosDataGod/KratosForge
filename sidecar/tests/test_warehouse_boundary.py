"""The warehouse run at its exact edges (boundary re-score of batch 6, mutation batch 156,
docs/quality/mutation-testing.md). No warehouse is reached: the connection is a stand-in, as in
test_warehouse_mutation.py."""

from __future__ import annotations

import decimal
import itertools
import types

import pyarrow as pa
import pytest

from fabric_desktop import tsql_clone
from fabric_desktop import warehouse as W
from tests.test_warehouse_mutation import Con, Cursor, SERVER


def test_a_transaction_wrapping_nothing_is_still_taken_off():
    assert W.without_outer_transaction("BEGIN TRAN\n\nCOMMIT") == ("", True)


def test_a_begin_and_a_commit_with_nothing_between_are_sent_as_they_are():
    assert W.change_units(["BEGIN TRAN", "COMMIT"]) == ["BEGIN TRAN", "COMMIT"]
    assert W.change_units(["BEGIN TRAN", "SELECT 1", "COMMIT"]) == ["SELECT 1"]


def test_six_digits_of_a_second_need_no_note():
    assert W._time_note(6, False) == "" and W._time_note(7, False) != ""


def test_a_clone_named_schema_dot_table_names_no_database():
    clone = tsql_clone.Clone(clone=("s", "t"), source=("s", "u"), at=None, start=0, end=10)
    assert W._clone_verdict(1, clone).kind == "warehouse_write"
    clone = tsql_clone.Clone(clone=("d", "s", "t"), source=("s", "u"), at=None, start=0, end=10)
    assert W._clone_verdict(1, clone).kind == "fabric_mutation"


def test_a_first_line_of_exactly_80_characters_is_shown_whole():
    assert W._first_line("a" * 80) == "a" * 80
    assert W._first_line("a" * 81) == "a" * 80 + "…"


def test_a_login_turned_away_every_time_is_tried_exactly_connect_tries_times(monkeypatch):
    tries = []

    def connect(*a, **k):
        tries.append(1)
        raise RuntimeError("busy (40501)")
    monkeypatch.setattr(W, "connect", connect)
    with pytest.raises(ConnectionError, match=f"after {W.CONNECT_TRIES} tries"):
        W.open_connection(SERVER, "WH", lambda: "t", sleep=lambda s: None, clock=lambda: 0.0)
    assert len(tries) == W.CONNECT_TRIES == 4


def test_a_decimal_with_a_precision_of_0_isnt_typed_as_a_decimal():
    assert W._schema([("a", decimal.Decimal, None, None, 0, 0, True)], []).field("a").type == pa.string()
    assert W._schema([("a", decimal.Decimal, None, None, 1, 0, True)], []).field("a").type == pa.decimal128(1, 0)


def test_progress_is_said_at_exactly_the_progress_time(monkeypatch, tmp_path):
    ticks = itertools.count(0.0, W.PROGRESS_SECONDS)  # 0.25 apart, exactly
    monkeypatch.setattr(W, "time", types.SimpleNamespace(monotonic=lambda: next(ticks)))
    said = []

    class One(Cursor):
        def __init__(self):
            super().__init__()
            self.description = [("a", int, None, None, None, None, True)]
            self._chunks = [[(1,)]]

        def fetchmany(self, size):
            return self._chunks.pop(0) if self._chunks else []
    W._write_result(One(), tmp_path / "r.arrow", lambda e, **f: said.append((e, f)))
    assert said == [("progress", {"rows": 1})]


def test_a_statement_that_changes_no_rows_says_it_changed_0(monkeypatch, tmp_path):
    con = Con(Cursor(rowcounts=[0]))
    monkeypatch.setattr(W, "open_connection", lambda server, database, token: con)
    said = []
    (tmp_path / "parts").mkdir()
    W.run({"query": "UPDATE dbo.a SET x = 1 WHERE 1 = 0", "statements": ["UPDATE dbo.a SET x = 1 WHERE 1 = 0"],
           "parts_dir": str(tmp_path / "parts"), "out": str(tmp_path / "r.arrow"), "writes": True,
           "warehouse": {"server": SERVER, "database": "WH"}}, lambda: "t",
          lambda event, **f: said.append((event, f)))
    [part] = [f for e, f in said if e == "part"]
    assert part["affected"] == 0 and part["rows"] == 0


def test_a_retry_that_would_end_exactly_at_the_deadline_isnt_made(monkeypatch):
    tries = []

    def connect(*a, **k):
        tries.append(1)
        raise RuntimeError("busy (40501)")
    monkeypatch.setattr(W, "connect", connect)
    # The deadline is set at 0; the first wait (5 s) would end exactly at it.
    times = iter([0.0, 0.0, W.CONNECT_DEADLINE_S - W.CONNECT_WAITS[0]])
    with pytest.raises(ConnectionError, match="after 1 tries"):
        W.open_connection(SERVER, "WH", lambda: "t", sleep=lambda s: None, clock=lambda: next(times))
    assert len(tries) == 1
