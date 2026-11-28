"""Mutation batch 512, a re-score of warehouse.py (batches 6 and 156): clones, a snapshot's timestamp
and loads refused by name, statement numbers in a change, a commit the warehouse refuses, each try's
login timeout, the parts of a run of statements, a parameter only T-SQL sees, and the result's types
and notes. No warehouse is reached: the connection is a stand-in, as in test_warehouse_mutation.py."""

from __future__ import annotations

import datetime as dt
import decimal
import itertools
import types

import pyarrow as pa
import pytest

from fabric_desktop import tsql_clone
from fabric_desktop import warehouse as W
from fabric_desktop.frames import NOTE_KEY
from tests.test_warehouse_mutation import Con, Cursor, SERVER

UNREAD = "it couldn't be read as T-SQL, so it can't be checked; a procedure or function definition can be"
LOAD = ("{} loads files into the warehouse, which isn't run from the SQL page. Use the Load files with COPY INTO "
        "template and run it in Fabric's query editor.")


def _v(n, kind, reason):
    return W.sql_policy.Verdict(n, kind, reason)


def test_join_units_makes_one_batch_per_statement():
    assert W.join_units(["SELECT 1\n", "\nSELECT 2"]) == "SELECT 1\nGO\nSELECT 2"


# ---- a run that only reads


def test_a_clone_in_a_read_is_refused_by_its_statement():
    assert W.verdicts("CREATE TABLE dbo.c AS CLONE OF dbo.s") == [
        _v(1, "fabric_mutation", "CREATE TABLE dbo.c AS CLONE OF dbo.s makes a table in the warehouse, which is a change")]


def test_an_unreadable_clone_in_a_read_is_refused_by_its_shape():
    assert W.verdicts("CREATE TABLE AS CLONE OF") == [
        _v(1, "fabric_mutation", "CREATE TABLE ... AS CLONE OF makes a table in the warehouse, which is a change")]


def test_a_snapshot_timestamp_in_a_read_is_refused_as_statement_1():
    assert W.verdicts("ALTER DATABASE snap SET TIMESTAMP = '2026-01-01T00:00:00'") == [
        _v(1, "fabric_mutation", tsql_clone.SNAPSHOT_TIMESTAMP)]


@pytest.mark.parametrize("query, word", [("COPY INTO dbo.t FROM 'https://x/y.csv'", "COPY INTO"),
                                         ("bulk   insert dbo.t FROM 'x'", "BULK INSERT")])
def test_a_load_in_a_read_is_refused_by_name(query, word):
    assert W.verdicts(query) == [_v(1, "fabric_mutation", LOAD.format(word))]


# ---- a change's statements


def test_an_unreadable_second_batch_is_statement_2():
    assert W.verdicts("SELECT 1\nGO\nSELECT ((", writes=True) == [
        _v(1, "read_only", "reads the warehouse's tables"), _v(2, "fabric_mutation", UNREAD)]


def test_a_clone_in_the_second_batch_is_statement_2():
    assert W.verdicts("SELECT 1\nGO\nCREATE TABLE dbo.c AS CLONE OF dbo.s", writes=True) == [
        _v(1, "read_only", "reads the warehouse's tables"),
        _v(2, "warehouse_write", "CREATE TABLE dbo.c AS CLONE OF dbo.s makes a zero-copy clone of dbo.s as it is now")]


def test_nothing_after_unreadable_text_in_a_batch_is_checked():
    assert W.verdicts("SELECT ((\nCREATE TABLE dbo.c AS CLONE OF dbo.s", writes=True) == [
        _v(1, "fabric_mutation", UNREAD)]


def test_a_clone_at_a_time_too_long_ago_is_refused_and_one_a_day_ago_is_made():
    old = W.verdicts("CREATE TABLE dbo.c AS CLONE OF dbo.s AT '2020-01-01T00:00:00'", writes=True)
    assert [(v.kind, v.reason.startswith("AT '2020-01-01T00:00:00' is more than")) for v in old] == [("fabric_mutation", True)]
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    [new] = W.verdicts(f"CREATE TABLE dbo.c AS CLONE OF dbo.s AT '{when}'", writes=True)
    assert new.kind == "warehouse_write"
    assert new.reason.endswith(f"as it was at {when.replace('T', ' ')} UTC")


# ---- the commit


def test_a_commit_the_warehouse_refuses_for_its_own_reason_kept_nothing():
    class Refuses(Con):
        def commit(self):
            raise RuntimeError("Violation of PRIMARY KEY constraint 'pk'")
    with pytest.raises(W.Interrupted) as caught:
        W._commit(Refuses(Cursor()))
    assert caught.value.kind == "sql"
    assert str(caught.value).startswith("The warehouse didn't commit the change, so nothing was kept.")
    assert str(caught.value).endswith("Violation of PRIMARY KEY constraint 'pk'")


# ---- the login timeout of each try


def _connect_with(monkeypatch, times):
    seen = []

    def connect(server, database, token, login_timeout):
        seen.append(login_timeout)
        return "con"
    monkeypatch.setattr(W, "connect", connect)
    assert W.open_connection(SERVER, "WH", lambda: "t", sleep=lambda s: None, clock=lambda: next(times)) == "con"
    return seen


def test_a_try_with_time_to_spare_waits_60_seconds_to_log_in(monkeypatch):
    assert _connect_with(monkeypatch, iter([0.0, 0.0])) == [60]


def test_a_try_with_under_a_second_left_still_waits_1_second(monkeypatch):
    assert _connect_with(monkeypatch, iter([0.0, W.CONNECT_DEADLINE_S - 0.5])) == [1]


# ---- a run


def _run(monkeypatch, tmp_path, job, cursor):
    con = Con(cursor)
    monkeypatch.setattr(W, "open_connection", lambda server, database, token: con)
    said = []
    (tmp_path / "parts").mkdir(exist_ok=True)
    W.run({"out": str(tmp_path / "r.arrow"), "parts_dir": str(tmp_path / "parts"),
           "warehouse": {"server": SERVER, "database": "WH"}, **job}, lambda: "t",
          lambda event, **f: said.append((event, f)))
    return said


def test_a_statement_that_changes_rows_says_how_many(monkeypatch, tmp_path):
    q = "UPDATE dbo.a SET x = 1"
    said = _run(monkeypatch, tmp_path, {"query": q, "statements": [q], "writes": True}, Cursor(rowcounts=[3]))
    [part] = [f for e, f in said if e == "part"]
    assert part["affected"] == 3


def test_a_statement_with_rows_says_nothing_changed_and_its_time_to_the_hundredth(monkeypatch, tmp_path):
    ticks = itertools.count(1000.0, 0.1111)
    monkeypatch.setattr(W, "time", types.SimpleNamespace(monotonic=lambda: next(ticks)))
    q = "SELECT 1 AS a"
    said = _run(monkeypatch, tmp_path, {"query": q, "statements": [q]},
                Cursor(results=[([("a", int, None, None, None, None, True)], [(1,)])]))
    [part] = [f for e, f in said if e == "part"]
    assert "affected" not in part and part["rows"] == 1
    assert part["seconds"] in {round(k * 0.1111, 2) for k in range(1, 10)}


def test_a_statement_with_no_rows_and_no_count_says_nothing_changed(monkeypatch, tmp_path):
    q = "SELECT 1 AS a"
    said = _run(monkeypatch, tmp_path, {"query": q, "statements": [q]}, Cursor())
    [part] = [f for e, f in said if e == "part"]
    assert "affected" not in part and part["rows"] == 0


def test_a_parameter_only_t_sql_sees_needs_a_value(monkeypatch, tmp_path):
    # In T-SQL a backslash doesn't escape a quote, so :x is outside the strings.
    with pytest.raises(ValueError, match="Give a value for :x"):
        _run(monkeypatch, tmp_path, {"query": "SELECT 'a\\', :x, ''"}, Cursor())


def test_a_reads_own_sql_error_is_the_drivers_with_nothing_chained(monkeypatch, tmp_path):
    class Fails(Cursor):
        def execute(self, sql, *params):
            raise RuntimeError("Invalid column name 'z'.")
    with pytest.raises(RuntimeError, match="Invalid column name") as caught:
        _run(monkeypatch, tmp_path, {"query": "SELECT z FROM dbo.t"}, Fails())
    assert caught.value.__cause__ is None


# ---- the result's types and notes


def _field(kind, precision=None, scale=None):
    return W._schema([("a", kind, None, None, precision, scale, True)], []).field("a")


def test_a_decimal_of_precision_38_is_a_decimal_and_39_is_text():
    assert _field(decimal.Decimal, 38, 2).type == pa.decimal128(38, 2)
    assert _field(decimal.Decimal, 39, 2).type == pa.string()


def test_a_time_with_a_seventh_digit_carries_its_note_and_a_plain_column_none():
    note = _field(dt.time, None, 7).metadata[NOTE_KEY].decode()
    assert note == ("times are kept to the microsecond, so the seventh digit of their fraction isn't kept. To keep it, "
                    "convert the column to text in the query, such as CAST(col AS VARCHAR(34))")
    assert _field(int).metadata is None
