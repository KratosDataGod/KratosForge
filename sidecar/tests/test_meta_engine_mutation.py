"""A metadata project's run engine (meta/engine.py): the behaviour its sampled mutants showed no test held
(docs/quality/mutation-testing.md). Most tests build the engine bare, with stand-ins for its store and
project, so nothing is run; a batched load runs on real Delta tables in a temp folder."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.meta import engine as eng
from fabric_desktop.meta.engine import CheckStop, RunResult
from fabric_desktop.meta.store import LocalStore


def bare(**attrs):
    """An Engine with only the attributes a test's method reads."""
    e = object.__new__(eng.Engine)
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


def project(**entities):
    return SimpleNamespace(layer=lambda name: SimpleNamespace(lakehouse=f"LH_{name.title()}"), entities=entities,
                           version="abc1234", fingerprint=lambda e: "0123abcd")


def entity(name="silver.orders", **kw):
    schema, table = name.split(".")
    return SimpleNamespace(name=name, schema="dbo", table=table, layer=schema, spec=kw.pop("spec", {}), **kw)


class Store:
    """A store that keeps every meta append and answers the rest from what a test gives it."""

    def __init__(self, env="dev", **answers):
        self.env, self.appended = env, []
        for k, v in answers.items():
            setattr(self, k, v)

    def version(self, *where):
        return 0

    def has_commit(self, *args):
        return False

    def meta_append(self, table, rows, metadata=None):
        self.appended.append((table, rows))


# ---- the engine's own wiring


def test_a_link_resolver_given_is_the_one_used_and_without_one_a_link_stops_the_run():
    store = SimpleNamespace(env="test", options=lambda: None)
    linked = eng.Engine(project(), store, dt.date(2026, 11, 11), link_table=lambda alias: f"abfss://ws/{alias}")
    assert linked.link_table("crm") == "abfss://ws/crm"
    unlinked = eng.Engine(project(), store, dt.date(2026, 11, 11))
    with pytest.raises(CheckStop, match="^link crm can't be resolved here: .* for test in metadata/links/crm.toml$"):
        unlinked.link_table("crm")


def test_a_table_on_this_pc_has_a_folder_for_the_disk_full_message_and_one_in_onelake_has_none(tmp_path):
    local = LocalStore(tmp_path, "dev")
    where = ("LH_Silver", "dbo", "orders")
    assert bare(store=local)._local_path(where) == str(tmp_path / "LH_Silver" / "Tables" / "dbo" / "orders")
    assert bare(store=SimpleNamespace(uri=lambda *w: "abfss://x"))._local_path(where) is None


# ---- landing zones


def test_dev_doesnt_read_a_secure_zone_that_isnt_opened_to_it_and_prod_does():
    zone = {"id": "z9", "name": "HR", "secure": True, "devtest": "none"}
    asked = []
    for env in ("dev", "test"):
        store = SimpleNamespace(env=env, landing_zone=lambda source: zone,
                                landing_lakehouse=lambda z: asked.append(z) or "LH_HR")
        with pytest.raises(CheckStop, match="^hr is in the secure zone HR, which Dev and Test don't read$"):
            bare(store=store).landing_lakehouse("/hr/people/*.csv")
    assert asked == []
    prod = SimpleNamespace(env="prod", landing_zone=lambda source: zone, landing_lakehouse=lambda z: "LH_HR")
    assert bare(store=prod).landing_lakehouse("hr/people/*.csv") == "LH_HR"
    opened = SimpleNamespace(env="dev", landing_zone=lambda source: dict(zone, devtest="read"),
                             landing_lakehouse=lambda z: "LH_HR")
    assert bare(store=opened).landing_lakehouse("hr/people/*.csv") == "LH_HR"


# ---- a run that committed and was never recorded


def unlogged_store(minutes_ago: float):
    run = "r-20261111-120000-0123456789ab"
    stamp = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).timestamp() * 1000)
    return run, Store(history=lambda *a: [{"fabric_desktop_run_id": run, "timestamp": stamp, "version": 4}],
                      meta_where=lambda *a: pl.DataFrame({"run_id": []}, schema={"run_id": pl.String}),
                      read_where=lambda *a: (pl.DataFrame({"_run_id": [run], "_loaded_at": [dt.date(2026, 11, 11)]}),
                                             None))


def test_a_commit_just_past_a_quarter_of_an_hour_with_no_record_is_recorded():
    run, store = unlogged_store(15.5)
    e = bare(project=project(), store=store, run_id="r-20261112-090000-aaaaaaaaaaaa")
    res = RunResult(e.run_id, "silver.orders", "ok")
    e._record_unlogged(entity(), res)
    [(table, [row])] = store.appended
    assert table == "run_log" and row["run_id"] == run and row["delta_version"] == 4 and row["rows_written"] == 1
    assert row["load_date"] == "2026-11-11" and row["status"] == "ok"
    assert [(s.name, s.rows, s.seconds, s.detail) for s in res.steps] == [
        ("record", 0, 0.0, (f"recorded run {run}, which stopped after its commit (Delta version 4) and before "
                            "it was recorded"))]


def test_a_commit_under_a_quarter_of_an_hour_old_may_still_be_recording_itself():
    _, store = unlogged_store(14.5)
    e = bare(project=project(), store=store, run_id="r-20261112-090000-aaaaaaaaaaaa")
    res = RunResult(e.run_id, "silver.orders", "ok")
    e._record_unlogged(entity(), res)
    assert store.appended == [] and res.steps == []


# ---- the watermark


def watermark_engine(spec):
    saved = pl.DataFrame({"entity": ["silver.orders"], "value": ["5"], "run_id": ["r-saved"]})
    history = [{"fabric_desktop_watermark": "9", "fabric_desktop_run_id": "r-unsaved"}]
    return bare(project=project(), store=Store(meta_where=lambda *a: saved, history=lambda *a: history)), entity(spec=spec)


def test_a_watermark_committed_but_not_saved_is_the_one_read():
    e, ent = watermark_engine({"load": {"watermark": "modified"}})
    assert e._watermark(ent) == "9"


def test_without_a_watermark_column_only_the_saved_value_counts():
    e, ent = watermark_engine({"load": {}})
    assert e._watermark(ent) == "5"


# ---- [load] deletes


def test_a_row_already_marked_deleted_isnt_gone_again():
    table = pl.DataFrame({"id": [1, 2, 3], "is_deleted": [False, True, None]})
    ent = entity(mode="incremental", deletes="soft", keys=["id"], max_deletes=("share", 1.0))
    e = bare(project=project(), store=SimpleNamespace(scan=lambda *a: table.lazy()),
             _src_keys=pl.DataFrame({"id": [1]}))
    res = RunResult("r", ent.name, "ok")
    gone = e._gone_keys(ent, pl.DataFrame(), None, res)
    assert gone["id"].to_list() == [3]
    assert res.steps[-1].detail == ("compared 1 source key with 2 live keys in the table: 1 key gone from the "
                                    "source to mark deleted")


# ---- which loads go in batches


@pytest.mark.parametrize("mode, backfill, exists, batched", [
    ("full", None, True, True),
    ("incremental", "full", True, True),
    ("incremental", None, False, True),
    ("incremental", None, True, False),
    ("scd1", None, False, False),
    ("full", "replay", True, False),
])
def test_which_loads_from_a_delta_source_go_in_batches(mode, backfill, exists, batched):
    ent = entity(mode=mode, spec={"load": {"batch_by": "day"}, "source": {"table": "abfss://ws/t"}})
    e = bare(project=project(), store=SimpleNamespace(exists=lambda *a: exists))
    assert e._batched_applies(ent, backfill) is batched


def test_a_batched_replace_says_how_many_rows_it_wrote_and_keeps_none(tmp_path):
    from test_batches import run, setup

    s = setup(tmp_path, "s", True)
    res = run(s, batch_rows=200)
    assert res.status == "ok", res.message
    [write] = [st.detail for st in res.steps if st.name == "write"]  # the ordinary path doesn't write it again
    assert res.delta_version == 0 and res.rows_written == 600
    assert sorted(s.table()["id"].to_list()) == list(range(600)) and s.table()["name"].null_count() == 0
    assert write == "replaced with 600 rows from 3 batches, in one Delta commit; Delta version 0"
    again = run(s, batch_rows=200, day=dt.date(2026, 10, 2))  # a full load over its own table replaces it too
    assert [st.detail for st in again.steps if st.name == "write"] == [
        "replaced with 600 rows from 3 batches, in one Delta commit; Delta version 1"]


# ---- surrogate keys, clean rules, lookups


def test_an_entity_without_keys_blocks_is_not_keyed_and_one_with_them_is(monkeypatch):
    calls = []
    monkeypatch.setattr(eng.keys_mod, "assign", lambda store, p, e, df: calls.append(e.name) or (
        df.with_columns(pl.lit(7).alias("order_sk")), ["3 keys, 1 new", "LH_Keys version 2"]))
    df = pl.DataFrame({"order_id": [1, 2, 3]})
    e = bare(project=project(), keys_store="keys")
    res = RunResult("r", "silver.orders", "ok")
    monkeypatch.setattr(eng.keys_mod, "entity_keys", lambda e: [])
    assert e._keys(entity(), df, res) is df and calls == [] and res.steps == []
    monkeypatch.setattr(eng.keys_mod, "entity_keys", lambda e: [{"column": "order_sk"}])
    out = e._keys(entity(), df, res)
    assert out["order_sk"].to_list() == [7, 7, 7] and calls == ["silver.orders"]
    assert [(s.name, s.rows, s.detail) for s in res.steps] == [("surrogate keys", 3, "3 keys, 1 new; LH_Keys version 2")]


def test_a_clean_rule_changes_its_column_and_one_for_a_column_not_there_is_skipped():
    df = pl.DataFrame({"name": [" Ada ", "Bob"], "id": [1, 2]})
    out, changed = eng.apply_clean(df, [{"column": "name", "trim": True}, {"column": "gone", "trim": True}], ["id"])
    assert out["name"].to_list() == ["Ada", "Bob"] and changed == 1 and out.columns == ["name", "id"]


def lookup_engine(dim: pl.DataFrame):
    dim_e = SimpleNamespace(name="gold.dim_customer", schema="dbo", table="dim_customer", layer="gold",
                            surrogate_key="customer_sk")
    return bare(project=project(**{dim_e.name: dim_e}), store=SimpleNamespace(env="dev", scan=lambda *a: dim.lazy()))


LOOKUP = {"dim": "gold.dim_customer", "as": "customer_sk", "on": "customer_id", "at": "order_date"}


def test_a_lookup_in_a_type_1_dimension_ignores_the_fact_date():
    dim = pl.DataFrame({"customer_id": [None, 10], "customer_sk": [-1, 1]})
    facts = pl.DataFrame({"customer_id": [10, 11, 10],
                          "order_date": [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)]})
    res = RunResult("r", "gold.fact_sales", "ok")
    out = lookup_engine(dim)._lookup(facts, LOOKUP, res)
    assert out["customer_sk"].to_list() == [1, -1, 1] and out.columns == ["customer_id", "order_date", "customer_sk"]
    assert res.steps[-1].detail == "1 row to the Unknown member (-1)"


def test_a_lookup_in_a_type_2_dimension_finds_the_version_of_the_fact_date():
    dim = pl.DataFrame({"customer_id": [None, 10, 10], "customer_sk": [-1, 1, 2],
                        "valid_from": [eng.FAR_PAST, eng.FAR_PAST, dt.date(2026, 6, 1)],
                        "valid_to": [eng.FAR_FUTURE, dt.date(2026, 6, 1), eng.FAR_FUTURE]})
    facts = pl.DataFrame({"customer_id": [10, 10], "order_date": [dt.date(2026, 1, 1), dt.date(2026, 7, 1)]})
    out = lookup_engine(dim)._lookup(facts, LOOKUP, RunResult("r", "gold.fact_sales", "ok"))
    assert out["customer_sk"].to_list() == [1, 2]


# ---- small helpers


def test_a_source_key_that_cant_be_read_as_the_key_type_stops_the_deletes():
    before = pl.DataFrame({"id": ["1", "x", None, "y"]})
    after = pl.DataFrame({"id": [1, None, None, None]})
    with pytest.raises(CheckStop, match=r"^load deletes: 2 source keys in id couldn't be read as int64 \(for example "
                                        r"'x'\), so they would look gone from the source\."):
        eng._stop_if_unreadable(before, after, ["id"], "int64")
    eng._stop_if_unreadable(before.head(1), after.head(1), ["id"], "int64")  # every key read: nothing to stop


def test_a_source_with_no_live_rows_stops_the_deletes_and_an_empty_table_has_none_to_lose():
    ent = entity(mode="incremental", deletes="hard", keys=["id"], max_deletes=("share", 1.0))
    empty = pl.DataFrame({"id": []}, schema={"id": pl.Int64})
    e = bare(project=project(), store=SimpleNamespace(scan=lambda *a: pl.DataFrame({"id": [1, 2]}).lazy()),
             _src_keys=empty)
    with pytest.raises(CheckStop, match="^load deletes: the source has no live rows, so every row of silver.orders "
                                        "would be removed. Nothing was written; check the source$"):
        e._gone_keys(ent, pl.DataFrame(), None, RunResult("r", ent.name, "ok"))
    e = bare(project=project(), store=SimpleNamespace(scan=lambda *a: empty.lazy()), _src_keys=empty)
    assert e._gone_keys(ent, pl.DataFrame(), None, RunResult("r", ent.name, "ok")).height == 0


# ---- the run dialog's estimate


def test_only_a_delta_source_is_estimated_and_a_table_source_is(monkeypatch):
    est = SimpleNamespace(to_dict=lambda: {"rows": 10})
    monkeypatch.setattr(eng.preflight, "too_big", lambda est, free: f"too big for {free}")
    files = entity(spec={"source": {"files": "crm/*.csv"}})
    table = entity(name="silver.events", spec={"source": {"table": "abfss://ws/t"}})
    planned = []
    e = bare(project=project(**{files.name: files, table.name: table}), available_memory=lambda: 99,
             _plan_read=lambda ent, full: planned.append((ent.name, full)) or (None, [], est, "full; 10 rows"))
    assert e.preflight(files.name) == {"entity": files.name, "estimate": None,
                                       "note": "only Delta sources (tables, links, entities) are estimated"}
    assert e.preflight(table.name, "full") == {"entity": table.name, "estimate": {"rows": 10}, "detail": "full; 10 rows",
                                               "free_memory_bytes": 99, "stop": "too big for 99", "guard": True}
    assert planned == [("silver.events", True)]


# ---- custom code


def test_a_custom_step_may_add_a_lookup_s_columns_and_a_hook_may_not(monkeypatch):
    adds = lambda df, ctx: df.with_columns(pl.lit(10).alias("customer_id"))  # noqa: E731
    monkeypatch.setattr(eng, "load_python", lambda ref: adds)
    ent = entity(name="gold.fact_sales", keys=["order_id"], columns={"order_id": "int64"}, hook_adds=[],
                 mode_columns=lambda: set(), spec={"lookup": [{"dim": "gold.dim_customer", "on": "customer_id"}]})
    e = bare(_evolved=[])
    df = pl.DataFrame({"order_id": [1]})
    res = RunResult("r", ent.name, "ok")
    out = e._call(ent, "steps:api", df, SimpleNamespace(notes=["fetched 1"]), res, "custom step")
    assert out.columns == ["order_id", "customer_id"]
    assert res.steps[-1].detail == "steps:api (+0 rows); fetched 1"
    with pytest.raises(CheckStop, match="^hook before_write steps:api added columns customer_id that are not in "
                                        "the contract$"):
        e._call(ent, "steps:api", df, SimpleNamespace(notes=[]), res, "hook before_write")


# ---- how the rows go in


def merge_plan(table, df, backfill=None, **kw):
    ent = entity(**({"mode": "incremental", "keys": ["id"], "soft_deletes": False} | kw))
    e = bare(project=project(), merge_writes=True,
             store=SimpleNamespace(scan=lambda *a: None if table is None else table.lazy()))
    return e._merge_plan(ent, df, backfill)


def test_new_rows_with_a_repeated_key_rewrite_the_table_and_others_merge_or_append():
    table = pl.DataFrame({"id": [1], "v": ["a"]})
    assert merge_plan(table, pl.DataFrame({"id": [2, 2], "v": ["b", "c"]})) == (
        "", "rewrote the table because new rows repeat a key (id)")
    assert merge_plan(table, pl.DataFrame({"id": [1, 2], "v": ["b", "c"]})) == ("upsert", "")
    assert merge_plan(table, pl.DataFrame({"id": [2, 2], "v": ["b", "c"]}), keys=[]) == ("append", "")
    assert merge_plan(table, pl.DataFrame({"id": [2], "v": ["b"]}), backfill="replay") == ("upsert", "")
    assert merge_plan(table, pl.DataFrame({"id": [2], "v": ["b"]}), backfill="full") == ("", "")
    assert merge_plan(None, pl.DataFrame({"id": [2], "v": ["b"]})) == ("", "")
    assert merge_plan(table, pl.DataFrame({"id": [2], "v": ["b"]}), soft_deletes=True) == (
        "", 'rewrote the table once to add is_deleted and deleted_at (deletes = "soft")')
    assert merge_plan(table, pl.DataFrame({"id": [2], "v": [3]})) == (
        "", "rewrote the table because column types changed (v String to Int64)")


def test_a_dimension_s_new_rows_that_repeat_a_key_or_change_a_type_are_rewritten_with_why():
    table = pl.DataFrame({"customer_sk": [-1, 1], "customer_id": [None, 10], "name": ["Unknown", "Ada"]})
    ent = entity(name="gold.dim_customer", mode="scd1", surrogate_key="customer_sk", keys=["customer_id"])
    e = bare(project=project(), store=SimpleNamespace(version=lambda *a: 3, scan=lambda *a: table.lazy()))
    twice = pl.DataFrame({"customer_id": [11, 11], "name": ["Bo", "Bob"]})
    assert e._write_dimension(ent, twice, {}) == "rewrote the table because new rows repeat a key (customer_id)"
    typed = pl.DataFrame({"customer_id": [11], "name": [5]})
    assert e._write_dimension(ent, typed, {}) == "rewrote the table because column types changed (name String to Int64)"


def test_a_load_with_a_watermark_carries_it_in_its_commit_and_one_without_carries_none(tmp_path):
    from test_batches import DAYS, history, run, setup

    marked = setup(tmp_path, "marked", False, load='mode = "incremental"\nwatermark = "modified"')
    assert run(marked).status == "ok"
    assert history(marked)[0]["fabric_desktop_watermark"] == DAYS[5].isoformat()
    plain = setup(tmp_path, "plain", False)
    assert run(plain).status == "ok"
    assert "fabric_desktop_watermark" not in history(plain)[0]


def test_a_column_the_first_batch_leaves_empty_takes_the_table_s_type_unless_the_schema_is_overwritten():
    import pyarrow as pa

    first = pa.table({"id": pa.array([1, 2], pa.int64()), "note": pa.array([None, None], pa.string())})
    table = pl.Schema({"id": pl.Int64, "note": pl.Int32})
    schema, kept = eng._first_batch_schema(entity(), first, table)
    assert schema.field("note").type == pa.int32() and schema.field("id").type == pa.int64()
    assert kept == {"note": pl.Int32}
    assert eng._first_batch_schema(entity(), first, None) == (first.schema, {})
    overwrite = entity(spec={"contract": {"overwrite_schema": True}})
    assert eng._first_batch_schema(overwrite, first, table) == (first.schema, {})


def test_a_type_2_change_closes_the_current_version_and_keeps_one_unknown_member():
    ent = entity(name="gold.dim_customer", surrogate_key="customer_sk", keys=["customer_id"],
                 spec={"scd": {"tracked": ["city"]}})
    day = dt.date(2026, 11, 11)
    existing = pl.DataFrame({"customer_sk": [-1, 1], "customer_id": [None, 10], "city": ["Unknown", "Leeds"],
                             "valid_from": [eng.FAR_PAST, eng.FAR_PAST], "valid_to": [eng.FAR_FUTURE, eng.FAR_FUTURE],
                             "is_current": [True, True]})
    incoming = pl.DataFrame({"customer_id": [11, 10], "city": ["Bath", "York"]})
    out, info = eng.scd2_merge(existing, incoming, ent, day)
    assert out.select("customer_sk", "customer_id", "city", "valid_from", "valid_to", "is_current").rows() == [
        (-1, None, "Unknown", eng.FAR_PAST, eng.FAR_FUTURE, True),
        (1, 10, "Leeds", eng.FAR_PAST, day, False),
        (2, 10, "York", day, eng.FAR_FUTURE, True),  # changed keys' new versions first, then new keys
        (3, 11, "Bath", eng.FAR_PAST, eng.FAR_FUTURE, True)]
    assert info == "1 new, 1 changed (new versions), 0 Type 1 updates"
    assert eng.scd2_invariants(out, "customer_sk", ["customer_id"]) == []


# ---- a run that needs a decision


def run_bare(monkeypatch, transform):
    """A run of one entity, with its steps from the read to the write replaced by ``transform``."""
    monkeypatch.setattr(eng, "validate", lambda p, day: [])
    ent = entity()
    store = Store()
    p = project(**{ent.name: ent})
    p.purge_code = lambda: None
    noop = lambda *a: None  # noqa: E731
    e = bare(project=p, store=store, load_date=dt.date(2026, 11, 11), table_lock=lambda e: eng.nullcontext(),
             _record_unlogged=noop, _sync_note=lambda *a: "", _record_stopped=noop, _mark_running=noop,
             _table_schema=noop, _batched_applies=lambda *a: False, _transform=transform, _running=None,
             _record_files=noop, run_context={})
    return e.run(ent.name), store


def test_a_run_that_needs_a_decision_returns_it_with_the_options_and_records_nothing(monkeypatch):
    def transform(e, res, backfill):
        raise eng.DecisionNeeded("silver.orders's logic changed since its last run.", ["none", "full"])

    res, store = run_bare(monkeypatch, transform)
    assert (res.status, res.options) == ("needs_decision", ["none", "full"])
    assert res.message == "silver.orders's logic changed since its last run." and store.appended == []


def test_a_run_with_nothing_new_is_a_good_run_that_read_nothing(monkeypatch):
    def transform(e, res, backfill):
        raise eng.NothingNew("no new files: all 2 files matching crm/*.csv are loaded")

    res, store = run_bare(monkeypatch, transform)
    assert res.status == "ok" and res.message == ""
    assert [(s.name, s.rows, s.seconds, s.detail) for s in res.steps] == [
        ("read", 0, 0.0, "no new files: all 2 files matching crm/*.csv are loaded")]
    [(table, [row])] = store.appended
    assert table == "run_log" and row["status"] == "ok" and row["rows_read"] == 0


# ---- Delta sources


def test_a_source_table_or_link_that_doesnt_exist_stops_the_run_naming_it(monkeypatch):
    monkeypatch.setattr(eng, "open_table", lambda uri, options: None)
    e = bare(link_table=lambda alias: f"abfss://ws/{alias}", source_options=lambda: {"account_name": "ws"})
    cases = [({"table": "abfss://ws/lh/Tables/orders", "from": "crm.orders"}, "crm.orders"),
             ({"table": "/data/orders"}, "/data/orders"), ({"link": "sales"}, "link sales")]
    for src, what in cases:
        with pytest.raises(CheckStop, match=f"^the source table {what} doesn't exist$"):
            e._open_source(entity(spec={"source": src}))
    monkeypatch.setattr(eng, "open_table", lambda uri, options: ("table", uri, options))
    assert e._open_source(entity(spec={"source": {"link": "sales"}})) == (
        ("table", "abfss://ws/sales", {"account_name": "ws"}), "abfss://ws/sales", {"account_name": "ws"})


def test_a_soft_delete_source_s_deleted_keys_are_gone_from_it(monkeypatch):
    src = pl.DataFrame({"id": [1, 2, 3], "is_deleted": [False, True, None],
                        "deleted_at": [None, dt.datetime(2026, 11, 1), None]})
    monkeypatch.setattr(eng, "lazy", lambda *a: src.lazy())
    ent = entity(keys=["id"], columns={"id": "int64"}, spec={"source": {"entity": "bronze.orders"}})
    e = bare(_src_keys=None, _open_source=lambda ent: ("t", "uri", None))
    assert sorted(e._source_keys(ent)["id"].to_list()) == [1, 3]
    e = bare(_src_keys=None, _open_source=lambda ent: ("t", "uri", None))
    monkeypatch.setattr(eng, "lazy", lambda *a: src.drop("deleted_at").lazy())  # not a soft-delete table
    assert sorted(e._source_keys(ent)["id"].to_list()) == [1, 2, 3]


# ---- checks


def test_a_not_null_check_and_a_unique_check_each_run_on_their_own():
    ent = entity(spec={"check": [{"column": "id", "not_null": True, "on_fail": "quarantine"},
                                 {"column": "code", "unique": True, "on_fail": "quarantine"}]})
    e = bare(run_id="r", load_date=dt.date(2026, 11, 11), _dq=[], _seen=None, _held=[])
    df = pl.DataFrame({"id": [1, None, 3, 4], "code": ["a", "b", "c", "c"]})
    res = RunResult("r", ent.name, "ok")
    kept, n_bad = e._checks(ent, df, res)
    assert kept["id"].to_list() == [1] and n_bad == 3
    assert [(d["check"], d["failed"]) for d in e._dq] == [("id", 1), ("code", 2)]
    assert e._held[0]["_check"].to_list() == ["id", "code", "code"]
    assert res.steps[-1].detail == "2 checks, 3 rows quarantined"


def test_a_type_2_merge_of_a_few_keys_that_breaks_the_history_writes_nothing():
    # Key 10 has two versions from the same day: closing them for a change leaves a gap between them.
    table = pl.DataFrame({"customer_sk": [-1, 1, 2], "customer_id": [None, 10, 10], "city": ["Unknown", "Leeds", "Hull"],
                          "valid_from": [eng.FAR_PAST] * 3, "valid_to": [eng.FAR_FUTURE] * 3,
                          "is_current": [True, False, True]})
    ent = entity(name="gold.dim_customer", mode="scd2", surrogate_key="customer_sk", keys=["customer_id"],
                 date_column=None, spec={"scd": {"tracked": ["city"]}})
    upserts = []
    store = SimpleNamespace(version=lambda *a: 3, scan=lambda *a: table.lazy(), count=lambda *a: 100,
                            upsert=lambda *a, **k: upserts.append(a))
    e = bare(project=project(), store=store, load_date=dt.date(2026, 11, 11))
    with pytest.raises(CheckStop, match="^Type 2 checks failed: 1 gaps or overlaps in valid_from / valid_to$"):
        e._write_dimension(ent, pl.DataFrame({"customer_id": [10], "city": ["York"]}), {})
    assert upserts == []


def test_a_soft_delete_merge_marks_a_gone_key_deleted_and_keeps_its_row(tmp_path):
    from test_batches import DAYS, run, setup
    from test_large_tables import rows

    s = setup(tmp_path, "s", False, load='mode = "incremental"\ndeletes = "soft"')
    assert run(s).status == "ok" and s.table().height == 600
    s.source(rows(range(5, 600), DAYS[0]), mode="overwrite")  # keys 0..4 are gone from the source
    res = run(s, day=dt.date(2026, 10, 2))
    assert res.status == "ok", res.message
    table = s.table()
    assert table.height == 600
    assert sorted(table.filter(pl.col("is_deleted"))["id"].to_list()) == [0, 1, 2, 3, 4]
    write = next(st.detail for st in res.steps if st.name == "write")
    assert "merged 595 rows on id" in write and "marked 5 rows deleted (gone from the source)" in write


@pytest.mark.parametrize("case, out", [("lower", "ada lovelace"), ("upper", "ADA LOVELACE"), ("title", "Ada Lovelace"),
                                       (None, "aDA lovelace")])
def test_a_clean_rule_s_case(case, out):
    df = pl.DataFrame({"name": ["aDA lovelace"], "id": [1]})
    cleaned, changed = eng.apply_clean(df, [{"column": "name", "case": case}], ["id"])
    assert cleaned["name"].to_list() == [out] and changed == (out != "aDA lovelace")


def test_the_last_good_run_is_this_entity_s_newest_ok_one():
    log = pl.DataFrame({"entity": ["silver.orders", "silver.orders", "gold.fact_sales", "silver.orders"],
                        "status": ["ok", "ok", "ok", "failed"], "run_id": ["r1", "r2", "r3", "r4"]})
    e = bare(store=SimpleNamespace(meta_where=lambda table, where: log.filter(where)))
    assert e._last_ok(entity())["run_id"] == "r2"
    assert e._last_ok(entity("silver.customers")) is None


# ---- reading files


def test_files_listed_that_read_as_nothing_stop_the_run():
    files = [{"path": "crm/a.csv", "size": 3, "modified": "2026-11-11T08:00:00"}]
    store = SimpleNamespace(env="dev", list_files=lambda where, pattern: files,
                            read_files=lambda where, pattern, todo: (None, []))
    ent = entity(mode="full", spec={"source": {"files": "crm/*.csv"}})
    with pytest.raises(CheckStop, match=r"^no files match crm/\*.csv in LH_Silver/Files$"):
        bare(project=project(), store=store)._read(ent, RunResult("r", ent.name, "ok"), False)


def test_a_later_batch_whose_type_the_table_s_widens_after_an_empty_first_batch_is_written(tmp_path):
    from deltalake import DeltaTable, write_deltalake
    from test_batches import DAYS, _batched, _scored_table
    from test_large_tables import rows

    s = _scored_table(tmp_path)  # score is an int64 column the contract doesn't declare; now int32
    days = [rows(range(i * 100, i * 100 + 100), day, amount=i) for i, day in enumerate(DAYS[:3])]
    scores = [None, 5, 6]
    src = pl.concat([d.with_columns(pl.lit(v, dtype=pl.Int32).alias("score")) for d, v in zip(days, scores, strict=True)])
    write_deltalake(str(s.src), src.to_arrow(), mode="overwrite", schema_mode="overwrite")
    res = _batched(s).run("bronze.events")
    assert res.status == "ok", res.message
    table = pl.from_arrow(DeltaTable(s.store.uri("LH_Bronze", "dbo", "events")).to_pyarrow_table())
    assert table["score"].dtype == pl.Int64 and sorted(table["score"].drop_nulls().unique().to_list()) == [5, 6]


def test_a_lookback_that_read_only_older_rows_keeps_the_watermark_where_it_was():
    ent = entity(spec={"load": {"watermark": "modified"}})
    df = pl.DataFrame({"modified": [dt.date(2026, 11, 8), dt.date(2026, 11, 9)]})
    e = bare(_wm_floor="2026-11-10")
    assert e._new_watermark(ent, df, None) == "2026-11-10"
    assert e._new_watermark(ent, df, "full") == "2026-11-09"  # a rebuild goes by what it read
    assert e._new_watermark(ent, df, "replay") is None
    e._wm_floor = "2026-11-01"
    assert e._new_watermark(ent, df, None) == "2026-11-09"


def test_a_key_this_run_brings_isnt_gone_though_the_source_key_list_misses_it():
    table = pl.DataFrame({"id": [1, 2, 3]})
    ent = entity(mode="incremental", deletes="hard", keys=["id"], max_deletes=("count", 5))
    e = bare(project=project(), store=SimpleNamespace(scan=lambda *a: table.lazy()), _src_keys=pl.DataFrame({"id": [1]}))
    gone = e._gone_keys(ent, pl.DataFrame({"id": ["3"], "v": ["x"]}), "none", RunResult("r", ent.name, "ok"))
    assert gone["id"].to_list() == [2]


def test_files_a_commit_lists_but_no_saved_list_has_are_recovered_once():
    import json

    saved = pl.DataFrame({"path": ["crm/a.csv"], "size": [3], "modified": ["m1"], "run_id": ["r1"]})
    listed = json.dumps({"lakehouse": "LH_Bronze", "files": [["crm/a.csv", 3, "m1"], ["crm/b.csv", 4, "m2"]]})
    history = [{"fabric_desktop_run_id": "r2", eng.FILES_KEY: listed},
               {"fabric_desktop_run_id": "r1", eng.FILES_KEY: listed}]
    e = bare(project=project(), _files_recovered=[],
             store=SimpleNamespace(meta_where=lambda *a: saved, history=lambda *a: history))
    done = e._loaded_files(entity(), "LH_Bronze", RunResult("r", "silver.orders", "ok"))
    assert done == {("crm/a.csv", 3, "m1"), ("crm/b.csv", 4, "m2")}
    assert e._files_recovered == [{"entity": "silver.orders", "lakehouse": "LH_Bronze", "run_id": "r2",
                                   "path": "crm/b.csv", "size": 4, "modified": "m2"}]


# ---- replaying quarantined rows


def test_a_replay_works_out_again_only_the_derived_columns_whose_inputs_it_has():
    ent = entity(columns={"id": "int64", "amount": "float64", "doubled": "float64"}, mode_columns=lambda: set(),
                 spec={"derive": [{"column": "doubled", "expr": "amount * 2"}, {"column": "rate", "expr": "fx * 2"}]})
    rows = pl.DataFrame({"id": ["1"], "amount": ["2.5"], "doubled": ["99"], "_check": ["types"], "_run_id": ["r0"],
                         "_loaded_at": ["2026-11-10"], "_qid": ["q1"]})
    e = bare(run_id="r", _replay_rows=rows, _dq=[], _evolved=[])
    res = RunResult("r", ent.name, "ok")
    out = e._replayed(ent, res)
    assert out.rows() == [(1, 2.5, 5.0)] and res.rows_read == 1
    assert [(s.name, s.detail) for s in res.steps] == [("read", "1 quarantined row to replay"), ("derive", "doubled")]


# ---- a run whose process ended before it did


def test_a_run_that_stopped_before_writing_is_recorded_as_failed_and_its_note_removed(tmp_path, monkeypatch):
    import json
    import socket

    from fabric_desktop import schedule_service

    monkeypatch.setattr(schedule_service, "_pid_alive", lambda pid, identity: False)
    stopped = "r-20261111-110000-0123456789ab"
    note = tmp_path / f"{stopped}.json"
    note.write_text(json.dumps({"run_id": stopped, "entity": "silver.orders", "pc": socket.gethostname(), "pid": -5,
                                "started_at": "2026-11-11T11:00:00+00:00", "load_date": "2026-11-11", "worker": "w1"}),
                    encoding="utf-8")
    store = Store(exists=lambda *a: False,
                  meta_where=lambda *a: pl.DataFrame({"run_id": []}, schema={"run_id": pl.String}))
    e = bare(project=project(), store=store, running_dir=tmp_path, run_id="r-20261111-120000-aaaaaaaaaaaa",
             run_context={"worker": "w2"})
    res = RunResult(e.run_id, "silver.orders", "ok")
    e._record_stopped(entity(), res)
    [(table, [row])] = store.appended
    assert table == "run_log" and (row["run_id"], row["status"], row["worker"]) == (stopped, "failed", "w1")
    assert [(s.name, s.rows, s.seconds, s.detail) for s in res.steps] == [
        ("record", 0, 0.0, f"recorded run {stopped}, which stopped before it wrote anything")]
    assert not note.exists()


@pytest.mark.parametrize("found, step", [
    ({"refused": "the table has a commit from after the run started", "files": [], "bytes": 0},
     ("clean up", 0, 1.235, "run r-x stopped mid-write; the table has a commit from after the run started")),
    ({"refused": "", "files": ["a.parquet", "b.parquet"], "bytes": 1000 * 1024 * 1024},
     ("clean up", 2, 1.235, ("dry run: run r-x stopped mid-write and left 2 files (1000.0 MB) that no version of "
                             "the table names; nothing was removed"))),
])
def test_what_a_stopped_run_left_is_counted_to_the_millisecond(tmp_path, monkeypatch, found, step):
    from fabric_desktop import leases, maintenance

    ticks = iter([10.0, 11.23456])
    monkeypatch.setattr(eng, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    monkeypatch.setattr(leases, "holding", lambda key: True)
    monkeypatch.setattr(maintenance, "stopped_run_leftovers", lambda uri, since: found)
    e = bare(project=project(), store=LocalStore(tmp_path, "dev"))
    res = RunResult("r", "silver.orders", "ok")
    e._stopped_leftovers(entity(), "r-x", {"started_at": "2026-11-11T11:00:00+00:00"}, res)
    assert [(s.name, s.rows, s.seconds, s.detail) for s in res.steps] == [step]


def test_a_source_key_the_table_s_key_type_cant_hold_stops_the_deletes_with_why():
    ent = entity(mode="incremental", deletes="hard", keys=["id"], max_deletes=("share", 1.0))
    e = bare(project=project(), store=SimpleNamespace(scan=lambda *a: pl.DataFrame({"id": [1, 2]}).lazy()),
             _src_keys=pl.DataFrame({"id": ["1", "x"]}))
    with pytest.raises(CheckStop, match=r"^load deletes: 1 source key in id couldn't be read as the table's key type "
                                        r"\(for example 'x'\)"):
        e._gone_keys(ent, pl.DataFrame(), None, RunResult("r", ent.name, "ok"))


def test_an_exists_in_check_fails_the_values_its_table_lacks_and_every_row_before_that_table_is_loaded():
    customers = entity("silver.customers")
    ent = entity(spec={"check": [{"column": "customer_id", "exists_in": "silver.customers.customer_id",
                                  "on_fail": "warn"}]})
    df = pl.DataFrame({"customer_id": [1, 2, None]})
    for loaded, failed in ((pl.DataFrame({"customer_id": [1, 5]}), 1), (None, 3)):
        e = bare(project=project(**{customers.name: customers}), run_id="r", load_date=dt.date(2026, 11, 11), _dq=[],
                 _seen=None, _held=[],
                 store=SimpleNamespace(scan=lambda *a, t=loaded: None if t is None else t.lazy()))
        kept, n_bad = e._checks(ent, df, RunResult("r", ent.name, "ok"))
        assert e._dq[0]["failed"] == failed and kept.height == 3 and n_bad == 0


@pytest.mark.parametrize("bounds, failed", [({"min": 0}, 1), ({"max": 10}, 1), ({"min": 0, "max": 10}, 2), ({}, 0)])
def test_a_range_check_fails_values_below_its_min_or_above_its_max(bounds, failed):
    ent = entity(spec={"check": [{"column": "qty", "range": bounds, "on_fail": "warn"}]})
    e = bare(project=project(), run_id="r", load_date=dt.date(2026, 11, 11), _dq=[], _seen=None, _held=[])
    e._checks(ent, pl.DataFrame({"qty": [-1, 0, 10, 11, None]}), RunResult("r", ent.name, "ok"))
    assert e._dq[0]["failed"] == failed


@pytest.mark.parametrize("which, kept", [("newest", "b"), ("oldest", "a")])
def test_a_dedupe_rule_keeps_the_newest_or_the_oldest_row_of_a_key(which, kept):
    df = pl.DataFrame({"id": [1, 1, 2], "v": ["b", "a", "c"], "at": [2, 1, 1]})
    out, changed = eng.apply_clean(df, [{"dedupe_keep": f"{which}:at"}], ["id"])
    assert out.filter(pl.col("id") == 1)["v"].to_list() == [kept] and changed == 1 and out.height == 2


def test_an_unrecorded_run_starts_at_its_first_commit_and_at_1970_when_that_has_no_time():
    run, store = unlogged_store(20)
    newest = store.history()[0]
    for older, started in (({"timestamp": 1_762_858_800_000}, "2025-11-11T11:00:00.000000+00:00"),
                           ({}, "1970-01-01T00:00:00.000000+00:00")):
        store.history = lambda *a, o=older: [newest, {"fabric_desktop_run_id": run, "version": 3} | o]
        store.appended = []
        e = bare(project=project(), store=store, run_id="r-20261112-090000-aaaaaaaaaaaa")
        e._record_unlogged(entity(), RunResult(e.run_id, "silver.orders", "ok"))
        [(_, [row])] = store.appended
        assert row["started_at"] == started and row["delta_version"] == 4


def test_a_lookback_read_says_from_when_it_reads_and_keeps_the_saved_watermark_as_its_floor(monkeypatch):
    src = pl.DataFrame({"id": [1, 2, 3], "modified": [dt.date(2026, 11, 7), dt.date(2026, 11, 9), dt.date(2026, 11, 10)]})
    monkeypatch.setattr(eng, "lazy", lambda *a: src.lazy())
    monkeypatch.setattr(eng.preflight, "estimate", lambda table, keep, schema, after: SimpleNamespace(
        text=lambda: "about 1 MB", after=after))
    ent = entity(mode="incremental", spec={"source": {"table": "abfss://ws/t"},
                                           "load": {"watermark": "modified", "lookback": "2 days"}})
    e = bare(_open_source=lambda ent: ("t", "abfss://ws/t", None), _watermark=lambda ent: "2026-11-10", _wm_floor=None)
    lf, keep, est, detail = e._plan_read(ent, False)
    assert detail == "incremental, modified after 2026-11-08 (the watermark 2026-11-10 less the lookback 2 days); about 1 MB"
    assert lf.collect()["id"].to_list() == [2, 3] and keep == ["id", "modified"]
    assert est.after == ("modified", dt.date(2026, 11, 8)) and e._wm_floor == "2026-11-10"


def test_a_hard_delete_merge_removes_the_gone_keys_rows(tmp_path):
    from test_batches import DAYS, run, setup
    from test_large_tables import rows

    s = setup(tmp_path, "s", False, load='mode = "incremental"\ndeletes = "hard"')
    assert run(s).status == "ok" and s.table().height == 600
    s.source(rows(range(5, 600), DAYS[0]), mode="overwrite")  # keys 0..4 are gone from the source
    res = run(s, day=dt.date(2026, 10, 2))
    assert res.status == "ok", res.message
    table = s.table()
    assert sorted(table["id"].to_list()) == list(range(5, 600)) and table["name"].null_count() == 0
    write = next(st.detail for st in res.steps if st.name == "write")
    assert "merged 595 rows on id" in write and "removed 5 rows deleted at the source" in write


@pytest.mark.parametrize("load, status", [('mode = "full"', "failed"), ('mode = "full"\nmemory_guard = false', "ok")])
def test_the_largest_batch_too_big_for_free_memory_stops_the_load_unless_the_guard_is_off(tmp_path, load, status):
    from test_batches import setup

    from fabric_desktop.meta import Engine, Project

    s = setup(tmp_path, "s", True, load=load)
    engine = Engine(Project(s.root), s.store, dt.date(2026, 10, 1))
    engine.batch_rows, engine.available_memory = 200, lambda: 30_000
    res = engine.run("bronze.events")
    assert res.status == status, res.message
    assert ("the batch 2026-10-01 to 2026-10-02 alone holds 200 rows" in res.message) is (status == "failed")


@pytest.mark.parametrize("values, says", [([1, None], "failed on 1 row,"), ([None, None], "failed on 2 rows,")])
def test_a_stop_check_that_fails_says_on_how_many_rows(values, says):
    ent = entity(spec={"check": [{"name": "has_id", "column": "id", "not_null": True}]})
    e = bare(project=project(), run_id="r", load_date=dt.date(2026, 11, 11), _dq=[], _seen=None, _held=[])
    with pytest.raises(CheckStop, match=f"^check has_id {says} so nothing was written$"):
        e._checks(ent, pl.DataFrame({"id": values}, schema={"id": pl.Int64}), RunResult("r", ent.name, "ok"))
