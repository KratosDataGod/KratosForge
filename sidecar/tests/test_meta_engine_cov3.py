"""A metadata project's run engine (meta/engine.py): the remaining edges. The pure helpers (clean rules,
Type 1 and Type 2 merges and their checks, the files a commit names), and engine methods built bare
with stand-ins for the store, so each path is reached without a real run. All on local disk."""

from __future__ import annotations

import datetime as dt
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop.meta import engine as eng
from fabric_desktop.meta.engine import CheckStop, DecisionNeeded, RunResult
from fabric_desktop.meta.project import Entity
from fabric_desktop.meta.store import LocalStore


def bare(**attrs):
    """An Engine with only the attributes a test's method reads."""
    e = object.__new__(eng.Engine)
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


def ent(name="silver.orders", **spec):
    return Entity(name, spec, Path("orders.toml"))


def res():
    return RunResult("r-20261119-120000-0123456789ab", "silver.orders", "ok")


PROJECT = SimpleNamespace(layer=lambda layer: SimpleNamespace(lakehouse="LH_Silver", drift="ignore"),
                          version="abc1234", fingerprint=lambda e: "deadbeef")


# ---- pure helpers


def test_a_type2_merge_without_type1_columns_acts_on_new_and_changed_keys_only():
    e = ent("gold.dim_customer", contract={"keys": ["id"]}, scd={"surrogate_key": "sk", "tracked": ["city"]})
    versions = pl.DataFrame({"id": [1, 2], "is_current": [True, True], "city": ["Oslo", "Rome"]}).lazy()
    incoming = pl.DataFrame({"id": [1, 2, 3], "city": ["Oslo", "Paris", "Bonn"]})
    out = eng._scd2_effective(versions, incoming, e)
    assert out.sort("id").to_dict(as_series=False) == {"id": [2, 3], "city": ["Paris", "Bonn"]}


def test_clean_rules_title_case_replace_parse_dates_and_leave_other_types_to_fill_and_round():
    df = pl.DataFrame({"name": ["ada lovelace", "N/A"], "day": ["2026-11-19", "19/11/2026"],
                       "amount": [1.256, None]})
    rules = [{"column": "name", "case": "title", "replace": {"N/A": "Unknown"}},
             {"column": "day", "parse_date": ["yyyy-MM-dd", "dd/MM/yyyy"]},
             {"column": "amount", "fill_null": 0.0, "round": 1}]
    out, changed = eng.apply_clean(df, rules, ["name"])
    assert out["name"].to_list() == ["Ada Lovelace", "Unknown"]
    assert out["day"].to_list() == [dt.date(2026, 11, 19)] * 2
    assert out["amount"].to_list() == [1.3, 0.0]
    assert changed == 5  # the ISO date reads the same as text, so only the other format counts


def test_files_a_commit_names_for_another_lakehouse_or_in_another_form_are_none():
    commit = {eng.FILES_KEY: json.dumps({"lakehouse": "LH_Landing", "files": [["a.csv", 3, "t"]]})}
    assert eng._committed_files(commit, "LH_Landing") == [{"path": "a.csv", "size": 3, "modified": "t"}]
    assert eng._committed_files(commit, "LH_Bronze") == []
    assert eng._committed_files({eng.FILES_KEY: "[1, 2]"}, "LH_Landing") == []  # a list has no .get
    assert eng._committed_files({eng.FILES_KEY: "not json"}, "LH_Landing") == []


def test_the_first_surrogate_key_of_an_empty_dimension_is_one():
    assert eng._next_key(None, "sk") == 1
    assert eng._next_key(pl.DataFrame({"sk": []}, schema={"sk": pl.Int64}), "sk") == 1
    assert eng._next_key(pl.DataFrame({"sk": [-1, 4]}), "sk") == 5


def test_an_empty_first_batch_column_already_in_the_table_s_type_keeps_it():
    e = ent(contract={"columns": {"id": "int64", "note": "string"}})
    first = pa.table({"id": pa.array([1], pa.int64()), "note": pa.array([None], pa.large_string())})
    schema, kept = eng._first_batch_schema(e, first, pl.Schema({"id": pl.Int64, "note": pl.String}))
    assert schema == first.schema and kept == {}


def test_a_dimension_without_a_surrogate_key_stops():
    with pytest.raises(CheckStop, match=r"^gold\.dim: dimensions need \[scd\] surrogate_key$"):
        eng._surrogate_key(ent("gold.dim"))


def dim1():
    return ent("gold.dim_customer", contract={"keys": ["id"]}, scd={"surrogate_key": "sk"})


def test_a_type1_merge_onto_a_type2_table_stops_rather_than_lose_history():
    existing = pl.DataFrame({"sk": [1], "id": [1], "valid_from": [eng.FAR_PAST]})
    with pytest.raises(CheckStop, match="is a Type 2 table; turning it back into Type 1 would lose history"):
        eng.scd1_merge(existing, pl.DataFrame({"id": [1]}), dim1())


def test_a_type1_merge_with_keys_from_lh_keys_updates_matches_and_adds_the_rest():
    existing = pl.DataFrame({"sk": [-1, 1], "id": [None, 1], "city": ["Unknown", "Oslo"]})
    incoming = pl.DataFrame({"id": [1, 2], "city": ["Bergen", "Rome"], "sk": [1, 7]})
    out, info = eng.scd1_merge(existing, incoming, dim1())
    assert info == "1 new, 1 updated, keys from LH_Keys"
    assert out.to_dict(as_series=False) == {"sk": [-1, 1, 7], "id": [None, 1, 2], "city": ["Unknown", "Bergen", "Rome"]}


def test_a_type2_merge_onto_a_type1_table_asks_to_migrate():
    e = ent("gold.dim_customer", contract={"keys": ["id"]}, scd={"surrogate_key": "sk", "tracked": ["city"]})
    with pytest.raises(DecisionNeeded) as caught:
        eng.scd2_merge(pl.DataFrame({"sk": [1], "id": [1], "city": ["Oslo"]}), pl.DataFrame({"id": [1], "city": ["X"]}),
                       e, dt.date(2026, 11, 19))
    assert caught.value.options == ["migrate"] and "exists as a Type 1 table" in str(caught.value)


def test_type2_checks_name_every_broken_rule():
    d = dt.date
    df = pl.DataFrame({"sk": [1, 1, 2], "id": [1, 1, 1], "is_current": [True, True, False],
                       "valid_from": [d(2020, 1, 1), d(2021, 1, 1), d(2022, 1, 1)],
                       "valid_to": [d(2020, 6, 1), d(2022, 1, 1), d(9999, 12, 31)]})
    assert eng.scd2_invariants(df, "sk", ["id"]) == [
        "1 keys without exactly one current row", "surrogate key is not unique",
        "the Unknown member (-1) is missing", "1 gaps or overlaps in valid_from / valid_to"]


# ---- the record of runs that stopped


def stopped_engine(tmp_path, store):
    running = tmp_path / "running"
    running.mkdir(exist_ok=True)
    return bare(running_dir=running, run_id="r-20261119-130000-aaaaaaaaaaaa", store=store, project=PROJECT,
                run_context={})


def test_a_stopped_run_whose_record_cant_be_saved_keeps_its_note_for_the_next_run(tmp_path, monkeypatch):
    from fabric_desktop import schedule_service
    monkeypatch.setattr(schedule_service, "_pid_alive", lambda pid, identity: False)
    store = SimpleNamespace(env="dev", exists=lambda *a: False,
                            meta_where=lambda *a: pl.DataFrame({"run_id": []}, schema={"run_id": pl.String}))
    e = stopped_engine(tmp_path, store)
    saved = []
    e._after_commit = lambda r, what, where, write: saved.append(what) or False
    run = "r-20261119-110000-bbbbbbbbbbbb"
    path = tmp_path / "running" / f"{run}.json"
    path.write_text(json.dumps({"entity": "silver.orders", "pc": socket.gethostname(), "pid": -1, "run_id": run}),
                    encoding="utf-8")
    r = res()
    e._record_stopped(ent(), r)
    assert saved == ["the record of a run that stopped"]
    assert path.exists() and r.steps == []


def leftovers_engine(tmp_path, monkeypatch, found):
    from fabric_desktop import leases, maintenance
    monkeypatch.setattr(leases, "holding", lambda key: True)
    monkeypatch.setattr(maintenance, "stopped_run_leftovers", lambda uri, since: found)
    return bare(store=LocalStore(tmp_path), project=PROJECT)


def test_a_stopped_run_that_left_no_files_adds_no_clean_up_step(tmp_path, monkeypatch):
    e = leftovers_engine(tmp_path, monkeypatch, {"refused": "", "files": [], "bytes": 0})
    r = res()
    e._stopped_leftovers(ent(), "r-x", {"started_at": "2026-11-19T10:00:00+00:00"}, r)
    assert r.steps == []


def test_a_committed_but_unrecorded_run_whose_record_cant_be_saved_adds_no_step(tmp_path):
    run = "r-20261119-090000-cccccccccccc"
    old = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).timestamp() * 1000)
    store = SimpleNamespace(
        env="dev", history=lambda *a: [{"fabric_desktop_run_id": run, "timestamp": old, "version": 4}],
        meta_where=lambda *a: pl.DataFrame({"run_id": []}, schema={"run_id": pl.String}),
        read_where=lambda *a: (pl.DataFrame({"_run_id": [run], "_loaded_at": [dt.date(2026, 11, 18)]}), None))
    e = bare(store=store, project=PROJECT, run_id="r-20261119-130000-aaaaaaaaaaaa")
    rows = []
    e._after_commit = lambda r, what, where, write: rows.append(what) or False
    r = res()
    e._record_unlogged(ent(), r)
    assert rows == ["the record of a run that stopped"] and r.steps == []


def test_an_after_commit_append_goes_ahead_when_the_table_s_version_cant_be_read():
    def version(*where):
        raise OSError("offline")

    asked = []
    store = SimpleNamespace(version=version, has_commit=lambda *a: asked.append(a))
    e = bare(store=store, run_id="r-1")
    written = []
    assert e._after_commit(res(), "the watermark", ("LH_Meta", "meta", "watermarks"), written.append) is True
    assert len(written) == 1 and list(written[0]) == [eng.APPEND_KEY] and asked == []


def test_a_local_table_with_no_sync_conflicts_has_no_sync_note(tmp_path):
    e = bare(store=LocalStore(tmp_path), project=PROJECT)
    r = res()
    assert e._sync_note(ent(), r) == "" and r.steps == []


# ---- reading


def test_the_saved_watermark_stands_when_the_table_s_history_cant_be_read():
    def history(*a):
        raise OSError("no log")

    store = SimpleNamespace(meta_where=lambda *a: pl.DataFrame({"value": ["5"], "run_id": ["r-1"]}), history=history)
    e = bare(store=store, project=PROJECT)
    assert e._watermark(ent(load={"watermark": "id"})) == "5"


def files_engine(store):
    return bare(store=store, project=PROJECT, load_date=dt.date(2026, 11, 19), _files_read=None)


def test_files_that_cant_be_listed_stop_a_landing_entity_and_raise_for_its_own_files():
    def list_files(where, pattern):
        raise LookupError("no LH_Landing")

    e = files_engine(SimpleNamespace(env="test", list_files=list_files))
    with pytest.raises(CheckStop, match=r"^there's no landing zone for test yet: .*\(no LH_Landing\)$"):
        e._read(ent(source={"landing": "crm/*.csv"}), res(), True)
    with pytest.raises(LookupError, match="no LH_Landing"):
        e._read(ent(source={"files": "crm/*.csv"}), res(), True)


def test_listed_files_that_read_as_nothing_stop_the_entity():
    store = SimpleNamespace(list_files=lambda *a: [{"path": "a.csv", "size": 1, "modified": "t"}],
                            read_files=lambda *a: (None, []))
    with pytest.raises(CheckStop, match=r"^no files match crm/\*\.csv in LH_Silver/Files$"):
        files_engine(store)._read(ent(source={"files": "crm/*.csv"}), res(), True)


def test_files_read_without_source_columns_get_no_load_date():
    store = SimpleNamespace(list_files=lambda *a: [{"path": "a.csv", "size": 1, "modified": "t"}],
                            read_files=lambda *a: (pl.DataFrame({"id": [1]}), ["a.csv"]))
    e = ent(source={"files": "crm/*.csv", "add_source_columns": False},
            contract={"columns": {"id": "int64", "_load_date": "date"}})
    r = res()
    df = files_engine(store)._read(e, r, True)
    assert df.columns == ["id"] and r.rows_read == 1 and r.steps[0].detail == "1 file"


def test_a_source_table_or_link_that_doesn_t_exist_stops_the_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "open_table", lambda uri, options: None)
    e = bare(store=SimpleNamespace(), link_table=lambda alias: "abfss://x", source_options=lambda: {})
    with pytest.raises(CheckStop, match=r"^the source table crm\.accounts doesn't exist$"):
        e._open_source(ent(source={"table": str(tmp_path / "t"), "from": "crm.accounts"}))
    with pytest.raises(CheckStop, match=r"^the source table link crm doesn't exist$"):
        e._open_source(ent(source={"link": "crm"}))


def test_load_deletes_stop_when_the_source_has_no_key_column(monkeypatch):
    monkeypatch.setattr(eng, "lazy", lambda *a: pl.DataFrame({"other": [1]}).lazy())
    e = bare(_src_keys=None)
    e._open_source = lambda en: (None, "uri", None)
    with pytest.raises(CheckStop, match=r"^load deletes: the source has no key column id$"):
        e._source_keys(ent(source={"table": "t"}, contract={"keys": ["id"], "columns": {"id": "int64"}}))


def test_batches_apply_only_to_a_delta_source_without_a_custom_step():
    e = bare()
    assert e._batched_applies(ent(load={"batch_by": "day"}, source={"files": "a/*.csv"}), None) is False
    assert e._batched_applies(ent(load={"batch_by": "day"}, source={"table": "t"},
                                  step={"custom": "x:y"}), None) is False


# ---- contract, lookups, checks


def test_a_strict_layer_stops_on_a_new_source_column():
    e = bare(project=PROJECT)
    en = ent(source={"table": "t"}, contract={"columns": {"id": "int64"}, "drift": "strict"})
    with pytest.raises(CheckStop, match=r"^schema drift: new columns extra \(layer silver does not allow new columns\)$"):
        e._contract_in(en, pl.DataFrame({"id": [1], "extra": ["x"]}), res())


def test_a_lookup_to_a_dimension_not_loaded_yet_stops():
    dim = ent("gold.dim_customer", scd={"surrogate_key": "sk"})
    project = SimpleNamespace(entities={"gold.dim_customer": dim}, layer=PROJECT.layer)
    e = bare(project=project, store=SimpleNamespace(env="dev", scan=lambda *a: None))
    with pytest.raises(CheckStop, match=r"^gold\.dim_customer has not been loaded in dev yet$"):
        e._lookup(pl.DataFrame({"cid": [1]}), {"dim": "gold.dim_customer", "as": "customer_sk", "on": "cid"}, res())


def test_a_replay_works_out_again_only_derived_columns_whose_inputs_it_has():
    en = ent(contract={"columns": {"id": "int64", "qty": "int64", "twice": "int64"}},
             derive=[{"column": "twice", "expr": "qty * 2"}, {"column": "gone", "expr": "missing + 1"}])
    rows = pl.DataFrame({"id": [1], "qty": [4], "twice": [0], "_check": ["c"], "_run_id": ["r"],
                         "_loaded_at": [None], "_qid": ["q"]})
    e = bare(_replay_rows=rows, _dq=[], run_id="r-1")
    r = res()
    out = e._replayed(en, r)
    assert out.to_dict(as_series=False) == {"id": [1], "qty": [4], "twice": [8]}
    assert [s.name for s in r.steps] == ["read", "derive"] and r.steps[1].detail == "twice"


def test_output_missing_a_contract_column_stops():
    en = ent(contract={"columns": {"id": "int64", "qty": "int64"}})
    with pytest.raises(CheckStop, match=r"^output is missing contract columns qty$"):
        bare(_evolved=[])._contract_out(en, pl.DataFrame({"id": [1]}), res())


def test_checks_not_null_accepted_values_a_range_with_only_a_max_and_unknown_kinds():
    en = ent(check=[{"column": "id", "not_null": True, "on_fail": "warn"},
                    {"column": "kind", "accepted_values": ["a", "b"], "on_fail": "warn"},
                    {"column": "qty", "range": {"max": 10}, "on_fail": "warn"},
                    {"column": "qty", "name": "mystery"}])
    df = pl.DataFrame({"id": [1, None, 3], "kind": ["a", "z", None], "qty": [5, 11, None]})
    e = bare(_dq=[], _seen=None, run_id="r-1")
    out, _ = e._checks(en, df, res())
    assert out.height == 3
    assert [(d["check"], d["failed"]) for d in e._dq] == [("id", 1), ("kind", 1), ("qty", 1)]


def test_schema_history_from_before_versions_were_recorded_numbers_on_from_its_runs(monkeypatch):
    monkeypatch.setattr(eng.drift_mod, "schema_changes", lambda a, b: [{"change": "added", "column": "x"}])
    seen = pl.DataFrame({"entity": ["silver.orders"] * 3, "run_id": ["r-1", "r-1", "r-2"]})
    e = bare(_schema_from=None, run_id="r-9", store=SimpleNamespace(read_where=lambda *a: (seen, None)))
    e._table_schema = lambda en, v: None
    r = res()
    r.delta_version = 7
    assert e._schema_rows(ent(), r) == [{"entity": "silver.orders", "run_id": "r-9", "delta_version": 7,
                                         "schema_version": 3, "change": "added", "column": "x"}]


# ---- dimensions written by MERGE


def dim_store(table, upserts=None):
    return SimpleNamespace(version=lambda *a: 3, scan=lambda *a: None if table is None else table.lazy(),
                           count=lambda *a: 0 if table is None else table.height,
                           upsert=lambda *a, **k: (upserts.append((a, k)) or (4, {})))


def dim2(**scd):
    return ent("gold.dim_customer", contract={"keys": ["code"], "columns": {"code": "float64", "city": "string"}},
               load={"mode": "scd2"}, scd={"surrogate_key": "sk", "tracked": ["city"]} | scd)


def test_a_dimension_table_without_the_keys_or_with_changed_types_is_rewritten():
    e = bare(project=PROJECT, load_date=dt.date(2026, 11, 19))
    no_current = pl.DataFrame({"sk": [1], "code": [1.0], "city": ["Oslo"]})
    e.store = dim_store(no_current)
    assert e._write_dimension(dim2(), pl.DataFrame({"code": [1.0], "city": ["X"]}), {}) == ""
    table = pl.DataFrame({"sk": [1], "code": [1.0], "city": ["Oslo"], "is_current": [True]})
    e.store = dim_store(table)
    why = e._write_dimension(dim2(), pl.DataFrame({"code": ["1"], "city": ["X"]}), {})
    assert why == "rewrote the table because column types changed (code Float64 to String)"


def test_a_type2_dimension_with_float_keys_merges_without_a_range_filter():
    d = dt.date
    table = pl.DataFrame({"sk": [-1, 1, 2, 3], "code": [None, 1.0, 2.0, 3.0], "city": ["Unknown", "Oslo", "Rome", "Bonn"],
                          "valid_from": [eng.FAR_PAST] * 4, "valid_to": [eng.FAR_FUTURE] * 4,
                          "is_current": [True] * 4})
    upserts = []
    e = bare(project=PROJECT, load_date=d(2026, 11, 19), store=dim_store(table, upserts))
    version, detail, rows = e._write_dimension(dim2(), pl.DataFrame({"code": [1.0], "city": ["Bergen"]}), {})
    assert version == 4 and rows == 4
    assert detail.startswith("0 new, 1 changed (new versions), 0 Type 1 updates; read 2 rows of the table for 1 business key")
    out = upserts[0][0][3]
    assert sorted(out["sk"].to_list()) == [1, 4]


def test_a_type2_dimension_merge_that_breaks_the_type2_rules_stops(monkeypatch):
    table = pl.DataFrame({"sk": [-1, 1, 2, 3], "code": [None, 1.0, 2.0, 3.0], "city": ["Unknown", "Oslo", "Rome", "Bonn"],
                          "valid_from": [eng.FAR_PAST] * 4, "valid_to": [eng.FAR_FUTURE] * 4, "is_current": [True] * 4})
    monkeypatch.setattr(eng, "scd2_invariants", lambda out, sk, bk: ["surrogate key is not unique"])
    e = bare(project=PROJECT, load_date=dt.date(2026, 11, 19), store=dim_store(table, []))
    with pytest.raises(CheckStop, match=r"^Type 2 checks failed: surrogate key is not unique$"):
        e._write_dimension(dim2(), pl.DataFrame({"code": [1.0], "city": ["Bergen"]}), {})


# ---- watermark and migration


def test_a_lookback_that_reads_only_older_rows_keeps_the_watermark():
    e = bare(_wm_floor="10")
    en = ent(load={"watermark": "id", "lookback": 5})
    assert e._new_watermark(en, pl.DataFrame({"id": [7, 8]}), None) == "10"
    assert e._new_watermark(en, pl.DataFrame({"id": [7, 12]}), None) == "12"


def test_a_migration_of_a_table_that_isn_t_there_has_nothing_to_migrate():
    e = bare()
    e.read_entity = lambda name: None
    r = res()
    e._migrate(ent("gold.dim_customer"), r)
    assert [(s.name, s.rows, s.detail) for s in r.steps] == [("migrate", 0, "nothing to migrate")]



def test_a_table_outside_this_pc_has_no_sync_note_and_an_entity_without_a_watermark_column_keeps_the_saved_one():
    store = SimpleNamespace(meta_where=lambda *a: pl.DataFrame({"value": ["5"], "run_id": ["r-1"]}))
    e = bare(store=store, project=PROJECT)
    r = res()
    assert e._sync_note(ent(), r) == "" and r.steps == []
    assert e._watermark(ent(load={"mode": "full"})) == "5"


def test_a_type2_rewrite_that_breaks_the_type2_rules_stops_before_writing(monkeypatch):
    monkeypatch.setattr(eng, "scd2_invariants", lambda out, sk, bk: ["the Unknown member (-1) is missing"])
    written = []
    store = SimpleNamespace(exists=lambda *a: False, read_at=lambda *a: (None, None),
                            write=lambda *a, **k: written.append(a))
    e = bare(store=store, project=PROJECT, run_id="r-1", load_date=dt.date(2026, 11, 19), merge_writes=False,
             _files_read=None, _schema_from=None, _wm_floor=None)
    e._gone_keys = lambda *a: None
    with pytest.raises(CheckStop, match=r"^Type 2 checks failed: the Unknown member \(-1\) is missing$"):
        e._write_once(dim2(), pl.DataFrame({"code": [1.0], "city": ["Oslo"]}), res(), None)
    assert written == []


# ---- full loads in batches


def batched_setup(tmp_path, **kw):
    from test_batches import setup
    return setup(tmp_path, "s", True, **kw)


def batched_run(s, batch_rows=200, python_stream=False):
    from fabric_desktop.meta import Engine, Project
    from test_batches import DAY1
    if python_stream:  # the stream is read here rather than inside the Delta writer, so every batch is seen
        real = s.store.write_stream

        def write_stream(lh, schema, table, reader, **kw):
            t = reader.read_all()
            return real(lh, schema, table, pa.RecordBatchReader.from_batches(t.schema, t.to_batches()), **kw)
        s.store.write_stream = write_stream
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = batch_rows
    return engine.run("bronze.events")


def test_a_batch_column_the_source_doesn_t_have_stops_the_run(tmp_path):
    from test_batches import setup
    s = setup(tmp_path, "s", False, load='mode = "full"\nbatch_by = "modified"\n[source.rename]\nlegacy_day = "modified"')
    r = batched_run(s)
    assert r.status == "failed" and r.message == "batch_by modified: the source has no column legacy_day"


def test_a_source_that_fits_in_one_batch_is_read_the_ordinary_way(tmp_path):
    s = batched_setup(tmp_path)
    r = batched_run(s, batch_rows=10_000)
    assert r.status == "ok", r.message
    assert r.steps[0].name == "read" and not any(st.name == "plan batches" for st in r.steps)
    assert s.table().height == 600


def test_an_empty_source_is_read_the_ordinary_way(tmp_path):
    from test_batches import rows, DAYS
    s = batched_setup(tmp_path)
    s.source(rows([], DAYS[0]), mode="overwrite")
    r = batched_run(s)
    assert not any(st.name == "plan batches" for st in r.steps)
    assert r.rows_read == 0


def test_without_the_memory_guard_a_batch_too_big_for_memory_is_tried_anyway(tmp_path):
    from fabric_desktop.meta import Engine, Project
    from test_batches import DAY1
    s = batched_setup(tmp_path, load='mode = "full"\nmemory_guard = false')
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = 200
    engine.available_memory = lambda: 30_000  # the guard would stop a batch of 200 rows
    r = engine.run("bronze.events")
    assert r.status == "ok", r.message
    assert any(st.name == "plan batches" for st in r.steps) and s.table().height == 600


def test_a_batched_first_load_with_soft_deletes_writes_every_row_live(tmp_path):
    s = batched_setup(tmp_path, load='mode = "incremental"\nwatermark = "modified"\ndeletes = "soft"')
    r = batched_run(s, python_stream=True)
    assert r.status == "ok", r.message
    t = s.table()
    assert t.height == 600 and t["is_deleted"].to_list() == [False] * 600 and t["deleted_at"].null_count() == 600
    assert any(st.name == "plan batches" for st in r.steps)


def test_a_check_that_stops_in_the_first_batch_writes_nothing(tmp_path):
    from test_batches import DAYS, UNIQUE_ID, rows
    s = batched_setup(tmp_path, checks=UNIQUE_ID)
    s.source(rows([5], DAYS[0]))  # id 5 twice in the first batch
    r = batched_run(s)
    assert r.status == "failed" and "check id failed on 2 rows" in r.message
    assert not Path(s.store.uri("LH_Bronze", "dbo", "events")).exists()


def test_a_later_batch_of_another_kind_read_in_python_is_refused_and_nothing_is_written(tmp_path):
    from deltalake import DeltaTable, write_deltalake
    from test_batches import _scored, _scored_table
    s = _scored_table(tmp_path)
    version = DeltaTable(s.store.uri("LH_Bronze", "dbo", "events")).version()
    write_deltalake(str(s.src), _scored([None, "x", "x"]).to_arrow(), mode="overwrite", schema_mode="overwrite")
    r = batched_run(s, batch_rows=100, python_stream=True)
    assert r.status == "failed" and r.message.startswith("schema change refused: score int64 to string")
    assert DeltaTable(s.store.uri("LH_Bronze", "dbo", "events")).version() == version


def test_later_batches_read_in_python_take_the_first_one_s_columns_and_types(tmp_path):
    from deltalake import write_deltalake
    from test_batches import _scored, _scored_table
    s = _scored_table(tmp_path)
    write_deltalake(str(s.src), _scored([None, 5, None]).with_columns(pl.col("score").cast(pl.Int32)).to_arrow(),
                    mode="overwrite", schema_mode="overwrite")
    r = batched_run(s, batch_rows=100, python_stream=True)
    assert r.status == "ok", r.message
    t = s.table()
    assert t.schema["score"] == pl.Int64 and t["score"].drop_nulls().unique().to_list() == [5]


def test_a_batched_write_that_fails_on_its_own_fails_the_run_with_its_error(tmp_path):
    from fabric_desktop.meta import Engine, Project
    from test_batches import DAY1
    s = batched_setup(tmp_path)

    def write_stream(lh, schema, table, reader, **kw):
        reader.read_all()
        raise OSError("the disk went away")
    s.store.write_stream = write_stream
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = 200
    r = engine.run("bronze.events")
    assert r.status == "failed" and "the disk went away" in r.message
    assert not any(st.name == "clean up" for st in r.steps)
    assert not Path(s.store.uri("LH_Bronze", "dbo", "events")).exists()
