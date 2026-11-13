"""Mutation batch 6: the warehouse run's checks and the worker around them (warehouse.py) at their edges.

No warehouse is reached: the connection is a stand-in, as in test_warehouse_edges.py."""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import itertools
import logging
import types

import pyarrow as pa
import pytest

from fabric_desktop import warehouse as W

SERVER = "abc.datawarehouse.fabric.microsoft.com"


class Cursor:
    def __init__(self, results=(), rowcounts=()):
        self._results = list(results)  # each: (description, rows)
        self._rowcounts = list(rowcounts)
        self.description, self._rows, self.rowcount = None, [], -1

    def execute(self, sql, *params):
        if self._results:
            self.description, self._rows = self._results.pop(0)
        else:
            self.description, self._rows = None, []
        self.rowcount = self._rowcounts.pop(0) if self._rowcounts else -1

    def fetchmany(self, size):
        rows, self._rows = self._rows, []
        return rows

    def nextset(self):
        return False


class Con:
    timeout = 0

    def __init__(self, cursor):
        self._cursor, self.events = cursor, []

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def _run(monkeypatch, tmp_path, query, cursor, writes=False):
    con = Con(cursor)
    monkeypatch.setattr(W, "open_connection", lambda server, database, token: con)
    said = []
    W.run({"query": query, "out": str(tmp_path / "r.arrow"), "writes": writes,
           "warehouse": {"server": SERVER, "database": "WH"}}, lambda: "t",
          lambda event, **f: said.append((event, f)))
    return said, con


# ---- the checks


def test_one_query_that_reads_is_read_only():
    assert [(v.statement, v.kind) for v in W.verdicts("SELECT 1 AS a")] == [(1, "read_only")]


@pytest.mark.parametrize("fn", ["OPENROWSET", "OPENQUERY", "OPENDATASOURCE"])
def test_a_read_that_reaches_outside_the_warehouse_is_refused(fn):
    query = f"SELECT * FROM {fn}('x', 'y', 'z') AS t"
    (v,) = W.verdicts(query)
    assert v.kind == "fabric_mutation" and v.reason == f"{fn} reads outside the warehouse"


def test_change_statements_are_numbered_across_batches():
    out = W.verdicts("UPDATE dbo.a SET x = 1; SELECT 1\nGO\nCOMMIT\nGO\nDELETE FROM dbo.b WHERE id = 1", writes=True)
    assert [(v.statement, v.kind) for v in out] == [(1, "warehouse_write"), (2, "read_only"), (3, "fabric_mutation"),
                                                     (4, "warehouse_write")]


def test_a_batch_with_comments_and_strings_is_still_checked_by_name():
    out = W.verdicts("-- a note\nEXEC [sys].[sp_executesql] N'SELECT 1' /* more */", writes=True)
    assert out[0].kind == "fabric_mutation" and out[0].reason == W._DYNAMIC
    fine = W.verdicts("-- sp_executesql in a comment\nUPDATE dbo.a SET note = 'sp_executesql' WHERE id = 1",
                      writes=True)
    assert [v.kind for v in fine] == ["warehouse_write"]


def test_a_comment_between_words_still_keeps_them_apart():
    (v,) = W.verdicts("EXEC/* run it */sp_executesql N'SELECT 1'", writes=True)
    assert v.kind == "fabric_mutation" and v.reason == W._DYNAMIC
    (v,) = W.verdicts("EXEC /* run it */ @sql", writes=True)
    assert v.kind == "fabric_mutation" and v.reason == W._DYNAMIC


# ---- the endpoint and the connection


def test_an_endpoint_cant_be_changed_after_it_is_resolved():
    end = W.Endpoint(SERVER, "WH")
    with pytest.raises(dataclasses.FrozenInstanceError):
        end.server = "elsewhere"  # type: ignore[misc]


def test_a_missing_server_says_fabric_gave_no_connection():
    class Fabric:
        def get_warehouse(self, ws, item):
            return {"displayName": "WH", "properties": {"connectionString": ""}}
    with pytest.raises(ValueError, match="didn't give this warehouse's SQL connection"):
        W.resolve(Fabric(), {"kind": "warehouse", "workspace_id": "w", "item_id": "i"})


def test_the_access_token_attribute_is_sql_servers():
    assert W.SQL_COPT_SS_ACCESS_TOKEN == 1256


@pytest.mark.parametrize("number", W.TRANSIENT_LOGIN_ERRORS)
def test_each_transient_login_error_is_tried_again(number):
    assert W.TRANSIENT_LOGIN_ERRORS == (40197, 40501, 40613, 49918, 49919, 49920, 10928, 10929, 4221)
    assert W.transient_login_error(RuntimeError(f"login failed ({number})")) == number


def test_the_retry_log_counts_the_next_try(monkeypatch, caplog):
    tries = []

    def connect(*a, **k):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("busy (40501)")
        return "con"
    monkeypatch.setattr(W, "connect", connect)
    with caplog.at_level(logging.INFO, logger=W._log.name):
        assert W.open_connection(SERVER, "WH", lambda: "t", sleep=lambda s: None) == "con"
    assert "trying again in 5s (2 of 4)" in caplog.text


# ---- preconditions


def _p(**kw):
    return {"kind": "table", "schema": "dbo", "name": "t", "exists": True, "columns": ["a"], **kw}


def test_precondition_names_up_to_128_characters():
    assert W.precondition_problem(_p(name="n" * 128)) is None
    assert W.precondition_problem(_p(name="n" * 129)) == "a precondition names the object's schema and name"


def test_precondition_columns_one_to_1024_each_one_to_128_characters():
    bad = "a precondition on an existing table gives its column names"
    assert W.precondition_problem(_p(columns=["c"] * 1024)) is None
    assert W.precondition_problem(_p(columns=["c"] * 1025)) == bad
    assert W.precondition_problem(_p(columns=[])) == bad
    assert W.precondition_problem(_p(columns=["c" * 128])) is None
    assert W.precondition_problem(_p(columns=["c" * 129])) == bad
    assert W.precondition_problem(_p(columns=[""])) == bad


# ---- the worker


def test_a_change_counts_a_single_affected_row(monkeypatch, tmp_path):
    said, con = _run(monkeypatch, tmp_path, "UPDATE dbo.a SET x = 1 WHERE id = 1", Cursor(rowcounts=[1]), writes=True)
    event, fields = said[-1]
    assert event == "done" and fields["affected"] == 1 and fields["committed"] is True
    assert con.events == ["commit", "close"]


def test_a_run_with_no_result_reports_no_rows(monkeypatch, tmp_path):
    said, _con = _run(monkeypatch, tmp_path, "UPDATE dbo.a SET x = 1 WHERE id = 1", Cursor(), writes=True)
    event, fields = said[-1]
    assert event == "done" and fields["rows"] == 0 and fields["batches"] == [] and fields["columns"] == []


def test_progress_is_said_once_the_progress_time_has_passed(monkeypatch, tmp_path):
    ticks = itertools.count(0.0, 10.0)
    monkeypatch.setattr(W, "time", types.SimpleNamespace(monotonic=lambda: next(ticks)))  # this module's name only
    said = []

    class Many(Cursor):
        def __init__(self):
            super().__init__()
            self.description = [("a", int, None, None, None, None, True)]
            self._chunks = [[(1,)], [(2,)]]

        def fetchmany(self, size):
            return self._chunks.pop(0) if self._chunks else []
    rows, sizes, _schema = W._write_result(Many(), tmp_path / "r.arrow", lambda e, **f: said.append((e, f)))
    assert (rows, sizes) == (2, [1, 1])
    assert said == [("progress", {"rows": 1}), ("progress", {"rows": 2})]


def test_no_progress_is_said_before_its_time(monkeypatch, tmp_path):
    monkeypatch.setattr(W, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    said = []

    class One(Cursor):
        def __init__(self):
            super().__init__()
            self.description = [("a", int, None, None, None, None, True)]
            self._chunks = [[(1,)]]

        def fetchmany(self, size):
            return self._chunks.pop(0) if self._chunks else []
    W._write_result(One(), tmp_path / "r.arrow", lambda e, **f: said.append(e))
    assert said == []


def test_a_one_digit_decimal_keeps_its_type():
    schema = W._schema([("d", decimal.Decimal, None, None, 1, 0, True)], [(decimal.Decimal("3"),)])
    assert schema.field("d").type == pa.decimal128(1, 0)


def test_a_datetime_column_of_only_nulls_has_no_time_zone():
    schema = W._schema([("t", dt.datetime, None, None, None, None, True)], [(None,), (None,)])
    assert schema.field("t").type == pa.timestamp("us")
