"""WR-226: table clones and warehouse snapshots, as Microsoft's docs describe them.

A Fabric Warehouse change may hold CREATE TABLE ... AS CLONE OF: the confirmation names the clone, its source
and the UTC time it is cloned as of, and a clone Fabric can't make (another warehouse, a time it can't read,
later than now or older than any warehouse keeps) is refused by name before anything is sent. A read-only run
names it as a change. ALTER DATABASE ... SET TIMESTAMP, which moves a warehouse snapshot, is refused by name
on both. The local engine refuses both by name: a clone there would be a full copy with a history of its own,
never the zero-copy clone a warehouse makes, and it has no snapshots. Nothing reaches Fabric.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import tsql_clone, warehouse
from fabric_desktop.localdata import LocalData, tsql

NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.timezone.utc)


def recent(days=3):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


# ---- reading the statement

@pytest.mark.parametrize("batch, clone, source, at", [
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t", ("dbo", "c"), ("dbo", "t"), None),
    ("create table c as clone of t;", ("c",), ("t",), None),
    ("CREATE TABLE [sales].[c 1] AS CLONE OF [dbo].[t] AT '2026-11-01T10:00:00.325'", ("sales", "c 1"), ("dbo", "t"),
     "2026-11-01T10:00:00.325"),
    ("/* CREATE TABLE x AS CLONE OF y */ CREATE TABLE dbo.c AS CLONE OF dbo.t AT N'2026-11-01T10:00:00'",
     ("dbo", "c"), ("dbo", "t"), "2026-11-01T10:00:00"),
    ("CREATE TABLE wh.dbo.c AS CLONE OF wh2.dbo.t", ("wh", "dbo", "c"), ("wh2", "dbo", "t"), None),
])
def test_a_clone_is_read_with_its_names_and_time(batch, clone, source, at):
    [found] = tsql_clone.clones(batch)
    assert (found.clone, found.source, found.at) == (clone, source, at)
    assert batch[found.start:found.end].upper().startswith("CREATE TABLE")


@pytest.mark.parametrize("batch", [
    "SELECT 'CREATE TABLE a AS CLONE OF b' AS words",
    "SELECT [CREATE TABLE a AS CLONE OF b] FROM t",
    "-- CREATE TABLE a AS CLONE OF b\nSELECT 1",
    "CREATE TABLE dbo.c AS SELECT * FROM dbo.t",
    "CREATE TABLE dbo.c (clone INT, [of] INT)",
])
def test_words_in_strings_comments_names_or_other_creates_are_not_a_clone(batch):
    assert tsql_clone.clones(batch) == []
    assert not tsql_clone.snapshot_timestamp(batch)


def test_a_clone_among_other_statements_keeps_its_place():
    batch = "UPDATE dbo.a SET x = 1 WHERE y = 2\nCREATE TABLE dbo.c AS CLONE OF dbo.t;\nDELETE FROM dbo.b WHERE z = 3"
    [found] = tsql_clone.clones(batch)
    rest = tsql_clone.without(batch, [found])
    assert len(rest) == len(batch) and "CLONE" not in rest and "UPDATE dbo.a" in rest and "DELETE FROM dbo.b" in rest


@pytest.mark.parametrize("batch", [
    "ALTER DATABASE [Sales snapshot] SET TIMESTAMP = CURRENT_TIMESTAMP",
    "alter database snap set timestamp = '2025-04-27T18:10:00.00';",
])
def test_a_snapshot_timestamp_is_found(batch):
    assert tsql_clone.snapshot_timestamp(batch)


@pytest.mark.parametrize("at, says", [
    ("2026-11-01T10:00:00", ""),
    ("2026-11-01T10:00:00.5", ""),
    ("2026-11-01T10:00:00.325", ""),
    ("2026-07-20T12:00:00", ""),  # 120 days back, to the second
    ("2026-11-01 10:00:00", "isn't a time a clone can take"),
    ("2026-11-01T10:00", "isn't a time a clone can take"),
    ("2026-11-01T10:00:00.1234", "isn't a time a clone can take"),
    ("2026-02-30T10:00:00", "isn't a time a clone can take"),
    ("", "isn't a time a clone can take"),
    ("2026-11-17T12:00:01", "later than now (UTC)"),
    ("2026-07-20T11:59:59", "more than 120 days ago"),
])
def test_a_clone_time_is_utc_to_the_millisecond_within_what_a_warehouse_keeps(at, says):
    problem = tsql_clone.at_problem(at, NOW)
    assert (problem == "") if not says else says in problem


# ---- a Fabric Warehouse change

def test_a_change_names_the_clone_its_source_and_its_utc_time():
    [now] = warehouse.verdicts("CREATE TABLE dbo.c AS CLONE OF dbo.t", writes=True)
    assert (now.kind, now.reason) == ("warehouse_write",
                                      "CREATE TABLE dbo.c AS CLONE OF dbo.t makes a zero-copy clone of dbo.t as it is now")
    at = recent()
    [then] = warehouse.verdicts(f"CREATE TABLE dbo.c AS CLONE OF dbo.t AT '{at}'", writes=True)
    assert then.kind == "warehouse_write" and then.reason.endswith(f"as it was at {at.replace('T', ' ')} UTC")


@pytest.mark.parametrize("query, says", [
    ("CREATE TABLE c AS CLONE OF otherwh.dbo.t", "otherwh.dbo.t names a database"),
    ("CREATE TABLE wh.dbo.c AS CLONE OF dbo.t", "wh.dbo.c names a database"),
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t AT '2026-11-01 10:00'", "isn't a time a clone can take"),
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t AT", "isn't a time a clone can take"),
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t AT '2020-01-01T00:00:00'", "more than 120 days ago"),
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t AT '2999-01-01T00:00:00'", "later than now"),
    ("CREATE TABLE AS CLONE OF dbo.t", "couldn't be read"),
    ("ALTER DATABASE [Sales snapshot] SET TIMESTAMP = CURRENT_TIMESTAMP", "Capture new state"),
])
def test_a_clone_fabric_cant_make_or_a_snapshot_move_is_refused_before_anything_is_sent(query, says):
    verdicts = warehouse.verdicts(query, writes=True)
    assert verdicts[-1].kind == "fabric_mutation" and says in verdicts[-1].reason


def test_a_clone_among_a_changes_statements_is_checked_in_its_place():
    batch = (f"UPDATE dbo.a SET x = 1 WHERE y = 2\nCREATE TABLE dbo.c AS CLONE OF dbo.t AT '{recent()}';\n"
             "DELETE FROM dbo.b WHERE z = 3")
    verdicts = warehouse.verdicts(batch, writes=True)
    assert [(v.statement, v.kind) for v in verdicts] == [(1, "warehouse_write"), (2, "warehouse_write"),
                                                          (3, "warehouse_write")]
    assert "UPDATE dbo.a" in verdicts[0].reason and "CLONE OF dbo.t" in verdicts[1].reason
    assert "DELETE dbo.b" in verdicts[2].reason
    bad = warehouse.verdicts(batch.replace("dbo.t AT", "other.dbo.t AT"), writes=True)
    assert [v.kind for v in bad] == ["warehouse_write", "fabric_mutation", "warehouse_write"]
    script = warehouse.verdicts("CREATE TABLE dbo.c AS CLONE OF dbo.t\nGO\nALTER DATABASE s SET TIMESTAMP = "
                                "CURRENT_TIMESTAMP", writes=True)
    assert [v.kind for v in script] == ["warehouse_write", "fabric_mutation"]


def test_a_read_only_run_names_a_clone_or_a_snapshot_move_as_what_it_is():
    [clone] = warehouse.verdicts("CREATE TABLE dbo.c AS CLONE OF dbo.t")
    assert clone.kind == "fabric_mutation"
    assert clone.reason == "CREATE TABLE dbo.c AS CLONE OF dbo.t makes a table in the warehouse, which is a change"
    [snap] = warehouse.verdicts("ALTER DATABASE s SET TIMESTAMP = CURRENT_TIMESTAMP")
    assert snap.kind == "fabric_mutation" and "warehouse snapshot" in snap.reason
    [read] = warehouse.verdicts("SELECT 'CREATE TABLE a AS CLONE OF b' AS x")
    assert read.kind == "read_only"


# ---- the local engine

@pytest.mark.parametrize("query, says", [
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t", ("CREATE TABLE dbo.c AS CLONE OF dbo.t isn't mapped yet. In a Fabric "
                                              "Warehouse a clone is zero-copy")),
    ("CREATE TABLE dbo.c AS CLONE OF dbo.t AT '2026-11-01T10:00:00'",
     "run SELECT * FROM dbo.t OPTION (FOR TIMESTAMP AS OF '2026-11-01T10:00:00')."),
    ("CREATE TABLE AS CLONE OF dbo.t", "this one couldn't be read"),
    ("ALTER DATABASE s SET TIMESTAMP = CURRENT_TIMESTAMP", "the local engine has no warehouse snapshots"),
])
def test_the_local_engine_refuses_a_clone_or_a_snapshot_move_by_name(query, says):
    with pytest.raises(ValueError, match="can't be run on the local engine") as caught:
        tsql.translate(query)
    assert says in str(caught.value)


def test_a_local_clone_is_refused_and_leaves_no_table_while_the_copy_it_names_runs(tmp_path):
    data = LocalData(tmp_path / "local")
    engine = data.engine(data.create("Clones")["id"])
    try:
        engine.execute("CREATE TABLE t USING DELTA AS SELECT 1 AS id")
        engine.execute("INSERT INTO t SELECT 2")
        with pytest.raises(ValueError, match="so it isn't made here"):
            engine.execute("CREATE TABLE dbo.c AS CLONE OF dbo.t", dialect="tsql")
        assert "c" not in [r[0] for r in engine.execute("SELECT table_name FROM information_schema.tables")["rows"]]
        engine.execute("CREATE TABLE dbo.c AS SELECT * FROM dbo.t", dialect="tsql")  # the copy the message names
        assert engine.execute("SELECT id FROM dbo.c ORDER BY id", dialect="tsql")["rows"] == [[1], [2]]
        with pytest.raises(ValueError, match="no warehouse snapshots"):
            engine.execute("ALTER DATABASE s SET TIMESTAMP = CURRENT_TIMESTAMP", dialect="tsql")
    finally:
        data.close_all()
