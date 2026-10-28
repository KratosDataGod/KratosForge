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


def _data_files(s: Setup) -> set[str]:
    from pathlib import Path
    root = Path(s.store.uri("LH_Bronze", "dbo", "events"))
    return {p.relative_to(root).as_posix() for p in root.rglob("*.parquet") if "_delta_log" not in p.parts}


def test_a_stream_that_stops_removes_the_files_it_wrote(tmp_path, monkeypatch):
    """PB-067: delta-rs had written data files when the stream stopped; none of them stay, and files
    the log names or that were there before are untouched."""
    import pyarrow as pa
    from fabric_desktop.meta import store as store_mod
    from fabric_desktop.meta.store import LocalStore
    real = store_mod.write_deltalake
    # Files of 1 MB, so delta-rs writes some before the stream ends (by default only past ~100 MB).
    monkeypatch.setattr(store_mod, "write_deltalake", lambda *a, **kw: real(*a, **kw, target_file_size=1_000_000))
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Bronze", "dbo", "t", pl.DataFrame({"a": [1]}))
    folder = tmp_path / "LH_Bronze" / "Tables" / "dbo" / "t"
    stray = "part-00000-from-another-writer.snappy.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(folder / stray)
    kept = {p.name for p in folder.glob("*.parquet")}
    schema = pa.schema([("a", pa.int64())])

    def batches():  # big enough that delta-rs writes files before the stream ends
        for i in range(4):
            yield pa.record_batch([pa.array(range(i * 500_000, (i + 1) * 500_000))], schema=schema)
        raise RuntimeError("a check stopped the run")
    with pytest.raises(Exception):
        store.write_stream("LH_Bronze", "dbo", "t", pa.RecordBatchReader.from_batches(schema, batches()), read_version=0)
    assert store.last_cleanup["removed"] >= 1, store.last_cleanup
    assert {p.name for p in folder.glob("*.parquet")} == kept and DeltaTable(str(folder)).version() == 0
    assert DeltaTable(str(folder)).to_pyarrow_table().num_rows == 1


def test_a_batched_load_that_stops_says_what_it_cleaned_up(tmp_path):
    s = setup(tmp_path, "s", True, checks=UNIQUE_ID)
    assert run(s, batch_rows=200).status == "ok"
    kept = _data_files(s)
    s.source(rows([5], DAYS[5]))  # id 5 again, in the last batch: the run stops there
    res = run(s, batch_rows=200, day=DAY2)
    assert res.status == "failed" and len(history(s)) == 1 and _data_files(s) == kept
    step = next(st for st in res.steps if st.name == "clean up")
    assert step.detail.startswith("removed ") and "this write left" in step.detail


def test_files_are_left_for_vacuum_when_another_writer_committed_meanwhile(tmp_path):
    from fabric_desktop.meta.store import LocalStore
    s = setup(tmp_path, "s", True)
    assert run(s, batch_rows=200).status == "ok"
    store: LocalStore = s.store
    version = store.version("LH_Bronze", "dbo", "events")
    before = _data_files(s)
    store.write("LH_Bronze", "dbo", "events", s.table().head(1), mode="append")  # the other writer
    left = "part-00001-left.snappy.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(store.uri("LH_Bronze", "dbo", "events") + "/" + left)
    out = store._remove_unwritten("LH_Bronze", "dbo", "events", before, version)
    assert out["removed"] == 0 and "VACUUM" in out["note"] and left in _data_files(s)


def test_in_onelake_the_files_are_listed_and_removed_through_its_file_api(tmp_path):
    from fabric_desktop.meta.store import OneLakeStore

    class Files:
        def __init__(self):
            self.deleted = []

        def list(self, ws, lh, directory, recursive=False):
            assert (ws, lh, directory, recursive) == ("ws1", "lh1", "Tables/dbo/events", True)
            return [{"path": "Tables/dbo/events/_delta_log/00000000000000000000.json", "is_dir": False},
                    {"path": "Tables/dbo/events/_delta_log", "is_dir": True},
                    {"path": "Tables/dbo/events/part-1.parquet", "is_dir": False}]

        def delete(self, ws, lh, path, recursive=True):
            self.deleted.append((ws, lh, path, recursive))
    files = Files()
    store = OneLakeStore("dev", "ws1", {"LH_Bronze": "lh1"}, lambda: "tok", tmp_path, files=files)
    assert store._data_files("LH_Bronze", "dbo", "events") == {"part-1.parquet"}
    store._delete_data_files("LH_Bronze", "dbo", "events", ["part-1.parquet"])
    assert files.deleted == [("ws1", "lh1", "Tables/dbo/events/part-1.parquet", False)]
