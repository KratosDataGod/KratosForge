"""Full loads in batches ([load] batch_by): the same rows as one big read, in one Delta commit."""

import datetime as dt
import json

import polars as pl
import pytest
from deltalake import DeltaTable
from test_large_tables import DAY1, DAY2, Setup, entity, rows

from fabric_desktop.meta import Engine, Project, batches
from fabric_desktop.meta.project import validate

DAYS = [DAY1 + dt.timedelta(days=i) for i in range(6)]


def full_entity(s: Setup, extra: str = "", load: str = 'mode = "full"', checks: str = "") -> None:
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = {json.dumps(s.src.as_posix())}
        [load]
        {load}
        {extra}
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        name = "string"
        amount = "float64"
        modified = "date"
        doubled = "float64"
        [[derive]]
        column = "doubled"
        expr = "amount * 2"
        {checks}
    """)


QUARANTINE_NEGATIVE = """
        [[check]]
        name = "no_negative"
        expr = "amount >= 0"
        on_fail = "quarantine"
"""
UNIQUE_ID = """
        [[check]]
        column = "id"
        unique = true
        on_fail = "stop"
"""


def setup(tmp_path, name, batched: bool, **kw) -> Setup:
    s = Setup(tmp_path / name, True)
    full_entity(s, 'batch_by = "modified"' if batched else "", **kw)
    for i, day in enumerate(DAYS):  # 6 days of 100 rows, one with negative amounts
        s.source(rows(range(i * 100, i * 100 + 100), day, amount=-1 if i == 3 else i))
    return s


def run(s: Setup, batch_rows=None, day=DAY1, backfill=None):
    engine = Engine(Project(s.root), s.store, day)
    engine.batch_rows = batch_rows
    return engine.run("bronze.events", backfill=backfill)


def history(s: Setup) -> list[dict]:
    return DeltaTable(s.store.uri("LH_Bronze", "dbo", "events")).history()


def test_a_batched_full_load_writes_the_same_rows_in_one_commit(tmp_path):
    one, many = setup(tmp_path, "one", False, checks=QUARANTINE_NEGATIVE), setup(tmp_path, "many", True,
                                                                                checks=QUARANTINE_NEGATIVE)
    a, b = run(one), run(many, batch_rows=200)
    assert a.status == b.status == "ok", (a.message, b.message)
    assert many.table().drop("_loaded_at").equals(one.table().drop("_loaded_at"))
    assert many.table().height == 500 and (b.rows_read, b.rows_written, b.quarantined) == (600, 500, 100)
    steps = {st.name: st for st in b.steps}
    assert "3 batches by modified, the largest 200 rows" in steps["plan batches"].detail
    assert steps["read"].rows == 600 and steps["derive"].rows == 600
    assert steps["write"].detail.startswith("replaced with 500 rows from 3 batches, in one Delta commit")
    assert len(history(many)) == 1  # one commit for the whole load
    dq = many.store.meta("dq_results").filter(pl.col("run_id") == b.run_id)
    assert dq.select("check", "failed").rows() == [("no_negative", 100)]  # one result for the run, not per batch
    assert len(DeltaTable(many.store.uri("LH_Bronze", "quarantine", "dbo_events")).to_pyarrow_table()) == 100


def test_a_check_that_stops_in_a_later_batch_leaves_the_table_as_it_was(tmp_path):
    s = setup(tmp_path, "s", True, checks=UNIQUE_ID)
    assert run(s, batch_rows=200).status == "ok"
    before = s.table()
    s.source(rows([5], DAYS[5]))  # id 5 again, in the last batch: unique across batches
    res = run(s, batch_rows=200, day=DAY2)
    assert res.status == "failed" and "check id failed on 1 row" in res.message
    assert len(history(s)) == 1 and s.table().equals(before)


def test_rows_with_no_value_are_a_batch_of_their_own(tmp_path):
    s = setup(tmp_path, "s", True)
    s.source(rows([900, 901], None))
    res = run(s, batch_rows=250)
    assert res.status == "ok", res.message
    assert s.table().height == 602 and s.table()["modified"].null_count() == 2
    assert "4 batches by modified" in res.steps[0].detail


def test_an_incremental_first_load_is_batched_and_the_next_run_reads_only_new_rows(tmp_path):
    s = setup(tmp_path, "s", True, load='mode = "incremental"\nwatermark = "modified"')
    res = run(s, batch_rows=200)
    assert res.status == "ok" and res.steps[0].name == "plan batches"
    assert s.store.meta("watermarks")["value"][-1] == DAYS[5].isoformat()
    s.source(rows([600, 1], DAYS[5] + dt.timedelta(days=1), amount=7))
    nxt = run(s, batch_rows=200, day=DAY2)
    assert nxt.status == "ok" and nxt.rows_read == 2 and not any(st.name == "plan batches" for st in nxt.steps)
    assert s.table().height == 601 and s.table().filter(pl.col("id") == 1)["amount"].item() == 7


def test_a_full_rebuild_keeps_rows_the_source_lost_as_the_unbatched_rebuild_does(tmp_path):
    out = {}
    for name, batched in (("one", False), ("many", True)):
        s = setup(tmp_path, name, batched, load='mode = "incremental"\nwatermark = "modified"')
        assert run(s, batch_rows=200).status == "ok"
        s.source(rows(range(150), DAYS[0], amount=5), mode="overwrite")  # the source now has 300 rows
        s.source(rows(range(150, 300), DAYS[1], amount=5))
        res = run(s, batch_rows=200, day=DAY2, backfill="full")
        assert res.status == "ok", res.message
        out[name] = (s.table().drop("_loaded_at"), res)
    assert out["many"][0].equals(out["one"][0]) and out["many"][0].height == 600
    write = next(st.detail for st in out["many"][1].steps if st.name == "write")
    assert "rebuilt: 300 rows from the full source, kept 300 rows whose keys the source doesn't have" in write


def test_the_guard_checks_the_largest_batch(tmp_path):
    s = setup(tmp_path, "s", True)
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = 200
    engine.available_memory = lambda: 30_000  # fits two days at most, with the headroom
    res = engine.run("bronze.events")
    assert res.status == "failed" and "the batch 2026-10-01 to 2026-10-02 alone holds 200 rows" in res.message
    engine.batch_rows = 100
    assert engine.run("bronze.events").status == "ok"


def test_without_a_batch_size_it_comes_from_free_memory(tmp_path):
    s = setup(tmp_path, "s", True)
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.available_memory = lambda: 1 << 30
    res = engine.run("bronze.events")
    assert res.status == "ok" and res.steps[0].name == "read"  # it all fits in one batch: one ordinary read
    assert engine.preflight("bronze.events")["batch_by"] == "modified"


def test_batch_by_is_checked_when_saved(tmp_path):
    s = Setup(tmp_path, True)
    for extra, problem in [('batch_by = "nope"', "names one contract column"),
                           ('batch_by = "doubled"', "not one this entity makes")]:
        full_entity(s, extra)
        assert any(problem in err for err in validate(Project(s.root))), extra
    full_entity(s, 'batch_by = "modified"')
    assert not [err for err in validate(Project(s.root)) if "batch_by" in err]


def test_changing_batch_by_is_not_a_logic_change(tmp_path):
    s = setup(tmp_path, "s", False, load='mode = "incremental"\nwatermark = "modified"')
    assert run(s).status == "ok"
    before = Project(s.root).fingerprint(Project(s.root).entities["bronze.events"])
    full_entity(s, 'batch_by = "modified"', load='mode = "incremental"\nwatermark = "modified"')
    assert Project(s.root).fingerprint(Project(s.root).entities["bronze.events"]) == before


def test_the_plan_groups_consecutive_values_and_never_splits_one():
    counts = pl.DataFrame({"d": [3, 1, 2, 4, None], "len": [50, 120, 40, 10, 5]})
    plan = batches.plan(counts, "d", 100)
    assert [(b.lo, b.hi, b.rows, b.nulls) for b in plan] == [(1, 1, 120, False), (2, 4, 100, False),
                                                              (None, None, 5, True)]
    assert plan[1].text() == "2 to 4" and plan[2].text() == "rows with no value"


@pytest.mark.parametrize("free, expected", [(None, batches.DEFAULT_BATCH_ROWS), (1 << 20, batches.MIN_BATCH_ROWS),
                                            (12 << 30, int(12 * (1 << 30) * 0.5 / 3 / 40))])
def test_rows_per_batch_follow_free_memory(free, expected):
    from fabric_desktop.meta.preflight import Estimate
    est = Estimate(files=1, files_total=1, rows=1_000_000, bytes_on_disk=0, memory_bytes=40_000_000)
    assert batches.batch_rows(est, free) == expected
