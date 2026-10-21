"""Failures around the table commit never put rows in twice or report a failed write as ok.

The watermark, run log and quarantine are written after the table commit. A failure there used to
send the whole write round again (duplicating appended rows) or leave a rerun to load the same rows
again; quarantined rows from a write that failed were kept, and quarantined again by the retry.
Those follow-up appends are retried, and an attempt that committed before it failed (a lost
response) is not appended a second time.
"""

import polars as pl
import pytest
from deltalake.exceptions import CommitFailedError
from test_large_tables import DAY1, DAY2, DAY3, Setup, entity, rows


def failing(store, table, times, exc=None):
    """Make store.meta_append(table) raise ``times`` times (None: always)."""
    real, left = store.meta_append, [times]

    def meta_append(name, data, **kw):
        if name == table and data and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            raise exc or CommitFailedError(f"{name}: metadata changed since last commit")
        return real(name, data, **kw)
    store.meta_append = meta_append
    return real


def ids(s: Setup) -> list[int]:
    return sorted(s.table()["id"].to_list())


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
@pytest.mark.parametrize("keys", [True, False], ids=["keyed", "keyless"])
def test_a_watermark_save_that_fails_once_never_writes_the_rows_twice(tmp_path, merge, keys):
    s = Setup(tmp_path, merge, keys=keys)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    s.source(rows([10, 11, 12], DAY2))
    failing(s.store, "watermarks", 1)
    res = s.run(day=DAY2)
    assert res.status == "ok" and res.rows_written == 3
    assert ids(s) == [0, 1, 2, 10, 11, 12]


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
def test_a_watermark_that_cant_be_saved_is_taken_from_the_commit(tmp_path, merge):
    s = Setup(tmp_path, merge, keys=False)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    s.source(rows([10, 11, 12], DAY2))
    real = failing(s.store, "watermarks", None, OSError("OneLake unreachable"))
    res = s.engine(DAY2).run("bronze.events")
    # The rows are committed, so the run says so, and says what wasn't recorded.
    assert res.status == "ok" and res.delta_version is not None
    assert "couldn't save the watermark" in res.message
    s.store.meta_append = real
    # The next run goes by the watermark the commit carries: nothing new, nothing written twice.
    s.run(day=DAY3)
    assert ids(s) == [0, 1, 2, 10, 11, 12]


def test_a_run_log_that_cant_be_saved_doesnt_lose_the_result(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    failing(s.store, "run_log", None, ConnectionError("network down"))
    res = s.engine(DAY1).run("bronze.events")
    assert res.status == "ok" and res.rows_written == 3
    assert "couldn't save the run log" in res.message
    assert ids(s) == [0, 1, 2]


def test_a_failed_table_write_quarantines_nothing(tmp_path):
    s = Setup(tmp_path, True)
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = "{s.src.as_posix()}"
        [load]
        mode = "incremental"
        watermark = "modified"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        name = "string"
        amount = "float64"
        modified = "date"
        [[check]]
        name = "positive"
        column = "amount"
        range = {{ min = 0 }}
        on_fail = "quarantine"
    """)
    s.source(rows([1, 2], DAY1).vstack(rows([3], DAY1, amount=-1)), mode="overwrite")
    real_write, calls = s.store.write, [0]

    def write(lakehouse, schema, table, df, *a, **kw):
        if schema != "quarantine" and table == "events":
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("connection reset while writing")
        return real_write(lakehouse, schema, table, df, *a, **kw)
    s.store.write = write
    first = s.engine(DAY1).run("bronze.events")
    assert first.status == "failed"
    assert s.store.read("LH_Bronze", "quarantine", "dbo_events") is None
    s.run(day=DAY1)
    q = s.store.read("LH_Bronze", "quarantine", "dbo_events")
    assert q.height == 1 and q["id"].to_list() == ["3"]
    assert ids(s) == [1, 2]


def commits_then_raises(store, schema, table, exc=None):
    """Make the first append to ``schema``.``table`` commit and then raise, as when the response to a
    commit that went through is lost. Returns how many times that table was written."""
    real, calls = store.write, [0]

    def write(lakehouse, sch, tbl, df, *a, **kw):
        out = real(lakehouse, sch, tbl, df, *a, **kw)
        if (sch, tbl) == (schema, table):
            calls[0] += 1
            if calls[0] == 1:
                raise exc or ConnectionError("connection reset after the commit")
        return out
    store.write = write
    return calls


def checked(s: Setup) -> None:
    """bronze.events with a quarantine check, and a source with a column the contract doesn't have
    (bronze keeps it and notes it in the schema history)."""
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = "{s.src.as_posix()}"
        [load]
        mode = "incremental"
        watermark = "modified"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        name = "string"
        amount = "float64"
        modified = "date"
        [[check]]
        name = "positive"
        column = "amount"
        range = {{ min = 0 }}
        on_fail = "quarantine"
    """)
    df = rows([1, 2], DAY1).vstack(rows([3], DAY1, amount=-1))
    s.source(df.with_columns(pl.lit("x").alias("extra")), mode="overwrite")


@pytest.mark.parametrize("schema,table", [("meta", "run_log"), ("meta", "dq_results"), ("meta", "schema_history"),
                                          ("meta", "watermarks"), ("quarantine", "dbo_events")])
def test_an_append_that_commits_then_fails_is_not_appended_again(tmp_path, schema, table):
    s = Setup(tmp_path, True)
    checked(s)
    calls = commits_then_raises(s.store, schema, table)
    res = s.engine(DAY1).run("bronze.events")
    assert calls[0] == 1, f"{table} was appended again after its commit went through"
    assert res.status == "ok" and "couldn't save" not in res.message
    run = res.run_id
    if schema == "quarantine":
        q = s.store.read("LH_Bronze", "quarantine", "dbo_events")
        assert q["id"].to_list() == ["3"]
    else:
        got = s.store.meta(table).filter(pl.col("run_id") == run)
        assert got.height == 1, got
    assert ids(s) == [1, 2]
    if table == "watermarks":
        assert s.store.meta("watermarks")["value"].to_list() == [str(DAY1)]


def test_an_append_that_failed_before_committing_is_tried_again(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    failing(s.store, "run_log", 1, CommitFailedError("run_log: metadata changed since last commit"))
    res = s.engine(DAY1).run("bronze.events")
    assert res.status == "ok" and "couldn't save" not in res.message
    assert s.store.meta("run_log").filter(pl.col("run_id") == res.run_id).height == 1
