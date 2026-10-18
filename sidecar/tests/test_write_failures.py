"""Failures around the table commit never put rows in twice or report a failed write as ok.

The watermark, run log and quarantine are written after the table commit. A failure there used to
send the whole write round again (duplicating appended rows) or leave a rerun to load the same rows
again; quarantined rows from a write that failed were kept, and quarantined again by the retry.
"""

import pytest
from deltalake.exceptions import CommitFailedError
from test_large_tables import DAY1, DAY2, DAY3, Setup, entity, rows


def failing(store, table, times, exc=None):
    """Make store.meta_append(table) raise ``times`` times (None: always)."""
    real, left = store.meta_append, [times]

    def meta_append(name, data):
        if name == table and data and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            raise exc or CommitFailedError(f"{name}: metadata changed since last commit")
        return real(name, data)
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
