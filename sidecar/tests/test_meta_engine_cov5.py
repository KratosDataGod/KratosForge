"""The metadata engine's table logic (meta/engine.py), the paths the other tests leave out: each
[[clean]] rule on its own, the files a commit says it read, a first batch whose empty columns take the
table's types, dimensions that are refused or that take their keys from LH_Keys, a Type 1 table met by a
Type 2 load, each Type 2 invariant that can break, and what counts as running out of memory."""

import datetime as dt
import errno
import json
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop.meta import engine
from fabric_desktop.meta.project import Entity

DAY = dt.date(2026, 3, 1)


def _dim(**scd):
    spec = {"load": {"mode": "scd2" if "tracked" in scd else "scd1"}, "contract": {"keys": ["id"]},
            "scd": {"surrogate_key": "sk", **scd}}
    return Entity("gold.dim_x", spec, Path("dim_x.toml"))


# ---- [[clean]] rules


def test_each_clean_rule_changes_only_what_it_says_and_counts_the_changes():
    df = pl.DataFrame({"id": [1, 2, 3], "name": [" ann ", "BOB", "n/a"], "city": ["oslo", "rome", "x"],
                       "day": ["2026-01-31", "31/01/2026", "never"], "amount": [1.234, None, 2.5]})
    rules = [{"column": "name", "trim": True, "case": "title", "null_if": ["N/A"]},
             {"column": "city", "case": "upper", "replace": {"X": "Unknown"}},
             {"column": "day", "parse_date": ["yyyy-MM-dd", "dd/MM/yyyy"]},
             {"column": "amount", "fill_null": 0.0, "round": 1},
             {"column": "missing", "trim": True}]
    out, changed = engine.apply_clean(df, rules, ["id"])
    assert out["name"].to_list() == ["Ann", "Bob", None]
    assert out["city"].to_list() == ["OSLO", "ROME", "Unknown"]
    assert out["day"].to_list() == [dt.date(2026, 1, 31), dt.date(2026, 1, 31), None]
    assert out["amount"].to_list() == [1.2, 0.0, 2.5]
    assert changed == 3 + 3 + 2 + 2  # a date already written as 2026-01-31 reads the same, so it isn't counted


# ---- files a commit read


def test_the_files_a_commit_read_come_only_from_its_own_lakehouse_and_well_formed_lists():
    commit = {engine.FILES_KEY: json.dumps({"lakehouse": "LH_Bronze", "files": [["Files/a.csv", 10, "2026-01-01"]]})}
    assert engine._committed_files(commit, "LH_Bronze") == [{"path": "Files/a.csv", "size": 10, "modified": "2026-01-01"}]
    assert engine._committed_files(commit, "LH_Silver") == []
    assert engine._committed_files({engine.FILES_KEY: "[1, 2]"}, "LH_Bronze") == []
    assert engine._committed_files({engine.FILES_KEY: "{not json"}, "LH_Bronze") == []
    assert engine._committed_files({engine.FILES_KEY: json.dumps({"lakehouse": "LH_Bronze", "files": [["a", "big", "x"]]})},
                                   "LH_Bronze") == []


def test_the_next_surrogate_key_follows_the_highest_and_never_starts_below_one():
    assert engine._next_key(None, "sk") == 1
    assert engine._next_key(pl.DataFrame({"sk": [-1]}), "sk") == 1
    assert engine._next_key(pl.DataFrame({"sk": [-1, 4, 2]}), "sk") == 5


# ---- batched loads


def test_a_first_batch_empty_column_takes_the_tables_type():
    e = Entity("silver.x", {"contract": {"keys": ["id"]}}, Path("x.toml"))
    first = pa.table({"id": pa.array([1, 2], pa.int64()), "note": pa.array([None, None], pa.string()),
                      "amount": pa.array([None, None], pa.float64())})
    table = pl.Schema({"id": pl.Int64, "note": pl.Int32, "amount": pl.Float64})
    schema, kept = engine._first_batch_schema(e, first, table)
    assert schema.field("note").type == pa.int32() and schema.field("amount").type == pa.float64()
    assert kept == {"note": pl.Int32}
    overwrite = Entity("silver.x", {"contract": {"keys": ["id"], "overwrite_schema": True}}, Path("x.toml"))
    assert engine._first_batch_schema(overwrite, first, table) == (first.schema, {})


# ---- dimensions


def test_a_dimension_without_a_surrogate_key_is_refused_by_name():
    e = Entity("gold.dim_y", {"contract": {"keys": ["id"]}}, Path("dim_y.toml"))
    with pytest.raises(engine.CheckStop, match="gold.dim_y: dimensions need \\[scd\\] surrogate_key"):
        engine.scd1_merge(None, pl.DataFrame({"id": [1]}), e)


def test_a_type_2_table_isnt_turned_back_into_type_1():
    existing, _ = engine.scd2_merge(None, pl.DataFrame({"id": [1], "city": ["Oslo"]}), _dim(tracked=["city"]), DAY)
    with pytest.raises(engine.CheckStop, match="gold.dim_x is a Type 2 table; turning it back into Type 1 would lose history"):
        engine.scd1_merge(existing, pl.DataFrame({"id": [1], "city": ["Rome"]}), _dim())


def test_a_type_1_table_met_by_a_type_2_load_asks_to_migrate():
    existing, _ = engine.scd1_merge(None, pl.DataFrame({"id": [1], "city": ["Oslo"]}), _dim())
    with pytest.raises(engine.DecisionNeeded, match="exists as a Type 1 table") as e:
        engine.scd2_merge(existing, pl.DataFrame({"id": [1], "city": ["Rome"]}), _dim(tracked=["city"]), DAY)
    assert e.value.options == ["migrate"]


def test_keys_from_lh_keys_are_used_as_they_come():
    e = _dim()
    first, detail = engine.scd1_merge(None, pl.DataFrame({"id": [2, 1], "sk": [20, 10], "city": ["Rome", "Oslo"]}), e)
    assert detail == "2 new, keys from LH_Keys"
    assert first.columns[0] == "sk" and first["sk"].to_list() == [-1, 10, 20]
    assert first.filter(pl.col("sk") == -1)["city"].to_list() == ["Unknown"]
    later, detail = engine.scd1_merge(first, pl.DataFrame({"id": [1, 3], "sk": [10, 30], "city": ["Lima", "Pisa"]}), e)
    assert detail == "1 new, 1 updated, keys from LH_Keys"
    assert dict(zip(later["sk"].to_list(), later["city"].to_list(), strict=True)) == {-1: "Unknown", 10: "Lima", 20: "Rome", 30: "Pisa"}


def test_each_type_2_invariant_that_breaks_is_named():
    good, _ = engine.scd2_merge(None, pl.DataFrame({"id": [1, 2], "city": ["Oslo", "Rome"]}), _dim(tracked=["city"]), DAY)
    assert engine.scd2_invariants(good, "sk", ["id"]) == []
    two_current = pl.concat([good, good.filter(pl.col("sk") == 1).with_columns(pl.lit(9, pl.Int64).alias("sk"))])
    assert engine.scd2_invariants(two_current, "sk", ["id"]) == ["1 keys without exactly one current row",
                                                                  "1 gaps or overlaps in valid_from / valid_to"]
    dup_sk = pl.concat([good, good.filter(pl.col("sk") == 2).with_columns(pl.lit(3, pl.Int64).alias("id"))])
    assert "surrogate key is not unique" in engine.scd2_invariants(dup_sk, "sk", ["id"])
    assert engine.scd2_invariants(good.filter(pl.col("sk") != -1), "sk", ["id"]) == ["the Unknown member (-1) is missing"]


# ---- running out of memory


def test_running_out_of_memory_is_found_anywhere_in_the_chain_but_a_full_temp_drive_isnt_it():
    try:
        try:
            raise MemoryError()
        except MemoryError as inner:
            raise RuntimeError("the read failed") from inner
    except RuntimeError as outer:
        assert engine._out_of_memory(outer)
    assert engine._out_of_memory(RuntimeError("memory allocation of 1048576 bytes failed"))
    assert engine._out_of_memory(RuntimeError("Out of Memory Error: failed to allocate"))
    assert not engine._out_of_memory(RuntimeError("Out of Memory Error: failed to offload data block"))
    looped = RuntimeError("a")
    looped.__context__ = RuntimeError("b")
    looped.__context__.__context__ = looped
    assert not engine._out_of_memory(looped)


# ---- a bare engine with stand-ins (as test_meta_engine_more_edges), nothing run


def bare(**attrs):
    e = object.__new__(engine.Engine)
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


class _Store:
    def __init__(self, fail_with=None, committed_on=None):
        self.fail_with, self.committed_on, self.writes, self.asked = fail_with, committed_on, [], []

    def version(self, *where):
        raise OSError("the table's log can't be read")

    def has_commit(self, *args):
        self.asked.append(args)
        return self.committed_on is not None and len(self.writes) >= self.committed_on


def test_an_append_that_committed_before_it_failed_isnt_appended_again(monkeypatch):
    monkeypatch.setattr(engine, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    store = _Store(committed_on=1)

    def write(meta):
        store.writes.append(meta)
        raise ConnectionError("the response was lost")
    res = type("R", (), {"steps": [], "message": ""})()
    assert bare(store=store, run_id="r1")._after_commit(res, "the run log", ("LH", "meta", "run_log"), write) is True
    assert len(store.writes) == 1 and res.steps == []
    token = store.writes[0][engine.APPEND_KEY]
    assert store.asked == [("LH", "meta", "run_log", engine.APPEND_KEY, token, None)]  # the version couldn't be read


def test_an_append_the_full_disk_stopped_says_the_tables_write_stands(monkeypatch):
    monkeypatch.setattr(engine, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    store = _Store()

    def write(meta):
        store.writes.append(meta)
        raise OSError(errno.ENOSPC, "No space left on device")
    res = type("R", (), {"steps": [], "message": "loaded"})()
    assert bare(store=store, run_id="r1")._after_commit(res, "the watermark", ("LH", "meta", "watermarks"), write) is False
    assert len(store.writes) == engine.AFTER_COMMIT_ATTEMPTS
    note = ("couldn't save the watermark, as the disk is full. The table's write stands; free some space before the "
            "next run")
    assert res.message == f"loaded; {note}" and res.steps[-1].detail == note


def test_without_a_watermark_column_the_saved_watermark_is_all_there_is():
    saved = pl.DataFrame({"entity": ["silver.x", "silver.x"], "value": ["1", "2"], "run_id": ["a", "b"]})
    store = type("S", (), {"meta_where": staticmethod(lambda table, cond: saved)})()
    e = Entity("silver.x", {"load": {"mode": "incremental"}}, Path("x.toml"))
    assert bare(store=store)._watermark(e) == "2"


def _files_entity(src):
    return Entity("bronze.orders", {"load": {"mode": "full"}, "source": src, "contract": {"keys": ["id"]}}, Path("o.toml"))


def test_a_landing_pattern_with_no_landing_zone_yet_stops_with_where_to_make_one():
    def list_files(where, pattern):
        raise LookupError("LH_Landing isn't there")
    store = type("S", (), {"env": "test", "list_files": staticmethod(list_files)})()
    eng = bare(store=store, landing_lakehouse=lambda pattern: "LH_Landing")
    with pytest.raises(engine.CheckStop, match=r"there's no landing zone for test yet: create or join the platform in "
                                               r"Platform > Settings \(LH_Landing isn't there\)"):
        eng._read(_files_entity({"landing": "crm/*.csv"}), type("R", (), {"steps": []})(), False)


def test_files_that_were_listed_but_read_as_nothing_stop_the_entity():
    listed = [{"path": "Files/a.csv", "size": 1, "modified": "x"}]
    store = type("S", (), {"env": "test", "list_files": staticmethod(lambda w, p: listed),
                           "read_files": staticmethod(lambda w, p, todo: (None, []))})()
    eng = bare(store=store, lakehouse=lambda e: "LH_Bronze")
    with pytest.raises(engine.CheckStop, match="no files match Files/raw/\\*.csv in LH_Bronze/Files"):
        eng._read(_files_entity({"files": "Files/raw/*.csv"}), type("R", (), {"steps": []})(), False)


def test_a_linked_source_table_that_isnt_there_is_named_by_its_link(tmp_path):
    eng = bare(link_table=lambda link: str(tmp_path / "gone"), source_options=lambda: {})
    with pytest.raises(engine.CheckStop, match="the source table link crm.accounts doesn't exist"):
        eng._open_source(_files_entity({"link": "crm.accounts"}))


def test_deletes_need_the_key_column_in_the_source(tmp_path):
    from deltalake import write_deltalake
    uri = str(tmp_path / "src")
    write_deltalake(uri, pl.DataFrame({"code": [1, 2]}).to_arrow())
    eng = bare(_src_keys=None, source_options=lambda: {})
    with pytest.raises(engine.CheckStop, match="load deletes: the source has no key column id"):
        eng._source_keys(_files_entity({"table": uri}))
