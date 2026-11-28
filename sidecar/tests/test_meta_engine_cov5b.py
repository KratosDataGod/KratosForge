"""The metadata engine's middle (meta/engine.py), run end to end on tmp_path with the small projects the
batch and large-table tests build (no Fabric): the branches those tests miss. An empty source or one that fits in one batch (the ordinary path), a guard turned off, a
soft-delete first load in batches, when batches don't apply at all; the checks each kind of rule makes
(not_null alone, accepted values, a range with one end, an unknown rule, a reference not loaded yet),
strict drift, a lookback that re-read only older rows, migrating nothing,
and a Type 1 dimension keyed on dates."""

import datetime as dt
import json

import polars as pl
from deltalake import write_deltalake
from test_batches import DAYS, full_entity, run, setup
from test_large_tables import DAY1, DAY2, Dims, Setup, entity, rows

from fabric_desktop.meta import Engine, Project


def steps(res):
    return {st.name: st for st in res.steps}


# ---- batched loads


def test_an_empty_source_or_one_batch_takes_the_ordinary_path(tmp_path):
    empty = Setup(tmp_path / "empty", True)
    full_entity(empty, 'batch_by = "modified"')
    empty.source(rows([], DAY1))
    res = run(empty, batch_rows=200)
    assert res.status == "ok", res.message
    assert "plan batches" not in steps(res) and res.rows_written == 0
    one = setup(tmp_path, "one", True)
    res = run(one, batch_rows=10_000)
    assert res.status == "ok" and "plan batches" not in steps(res) and res.rows_written == 600


def test_with_the_guard_off_a_batch_bigger_than_free_memory_still_runs(tmp_path):
    s = setup(tmp_path, "s", False)
    full_entity(s, 'batch_by = "modified"\nmemory_guard = false')
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows, engine.available_memory = 200, lambda: 30_000
    res = engine.run("bronze.events")
    assert res.status == "ok", res.message
    assert "3 batches by modified" in steps(res)["plan batches"].detail and s.table().height == 600


def test_a_soft_delete_first_load_in_batches_marks_every_row_live(tmp_path):
    s = setup(tmp_path, "s", True, load='mode = "incremental"\nwatermark = "modified"\ndeletes = "soft"')
    res = run(s, batch_rows=200)
    assert res.status == "ok", res.message
    assert "plan batches" in steps(res)
    t = s.table()
    assert t.height == 600 and t["is_deleted"].to_list() == [False] * 600 and t["deleted_at"].null_count() == 600


def test_batches_dont_apply_to_a_replay_a_custom_step_or_a_file_source(tmp_path):
    s = setup(tmp_path, "s", True)
    engine = Engine(Project(s.root), s.store, DAY1)
    e = engine.project.entities["bronze.events"]
    assert engine._batched_applies(e, None) is True
    assert engine._batched_applies(e, "replay") is False
    custom = type(e)(e.name, {**e.spec, "step": {"custom": "transforms.x:build"}}, e.path)
    assert engine._batched_applies(custom, None) is False
    files = type(e)(e.name, {**e.spec, "source": {"files": "Files/raw/*.csv"}}, e.path)
    assert engine._batched_applies(files, None) is False


# ---- checks and contract


def test_a_not_null_check_alone_counts_only_empty_values(tmp_path):
    s = setup(tmp_path, "s", False, checks='[[check]]\ncolumn = "modified"\nnot_null = true\non_fail = "quarantine"')
    s.source(rows([900, 901], None))
    res = run(s)
    assert res.status == "ok", res.message
    assert (res.rows_written, res.quarantined) == (600, 2)


def test_accepted_values_and_one_ended_ranges_quarantine_what_falls_outside(tmp_path):
    checks = """
        [[check]]
        name = "known_name"
        column = "name"
        accepted_values = ["a"]
        on_fail = "warn"
        [[check]]
        name = "not_too_big"
        column = "amount"
        range = { max = 4 }
        on_fail = "quarantine"
        [[check]]
        name = "not_negative"
        column = "amount"
        range = { min = 0 }
        on_fail = "quarantine"
        [[check]]
        name = "nothing_to_do"
        column = "amount"
        on_fail = "stop"
    """
    s = setup(tmp_path, "s", False, checks=checks)
    res = run(s)
    assert res.status == "ok", res.message
    # amounts are 0..5 by day, day 3 is -1: day 5 is above 4, day 3 below 0
    assert (res.rows_written, res.quarantined) == (400, 200)
    dq = dict(s.store.meta("dq_results").filter(pl.col("run_id") == res.run_id).select("check", "failed").rows())
    assert dq == {"known_name": 0, "not_too_big": 100, "not_negative": 100}  # a rule with no test isn't run


def test_a_reference_that_isnt_loaded_yet_fails_every_row(tmp_path):
    s = setup(tmp_path, "s", False, checks='[[check]]\ncolumn = "id"\nexists_in = "bronze.ref.id"\non_fail = "stop"')
    entity(s.root, "bronze.ref", f"""
        name = "bronze.ref"
        owner = "Chris"
        [source]
        table = {json.dumps((tmp_path / "ref").as_posix())}
        [load]
        mode = "full"
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
    """)
    res = run(s)
    assert res.status == "failed" and res.message == "check id failed on 600 rows, so nothing was written"


def test_strict_drift_refuses_a_new_source_column(tmp_path):
    s = setup(tmp_path, "s", False)
    path = s.root / "metadata" / "entities" / "bronze.events.toml"
    path.write_text(path.read_text().replace('keys = ["id"]', 'keys = ["id"]\ndrift = "strict"'))
    s.source(rows([999], DAYS[0]).with_columns(pl.lit("x").alias("extra")))
    res = run(s)
    assert res.status == "failed"
    assert res.message == "schema drift: new columns extra (layer bronze does not allow new columns)"


# ---- watermarks and migrating


def test_a_lookback_that_reads_only_older_rows_never_moves_the_watermark_back(tmp_path):
    s = setup(tmp_path, "s", False, load='mode = "incremental"\nwatermark = "modified"\nlookback = "10 days"')
    assert run(s).status == "ok"
    assert s.store.meta("watermarks")["value"][-1] == DAYS[5].isoformat()
    # The source was rewritten with only older rows: the lookback reads them, and nothing as new as the watermark.
    s.source(rows([700, 701], DAYS[4], amount=9), mode="overwrite")
    res = run(s, day=DAY2)
    assert res.status == "ok", res.message
    assert res.rows_read == 2
    assert s.store.meta("watermarks")["value"][-1] == DAYS[5].isoformat()
    assert s.table().filter(pl.col("id").is_in([700, 701])).height == 2


def test_migrating_a_table_that_was_never_loaded_does_nothing(tmp_path):
    d = Dims(tmp_path, True)
    res = Engine(Project(d.root), d.store, DAY1).run("bronze.dim2", backfill="migrate")
    assert steps(res)["migrate"].detail == "nothing to migrate"


# ---- Type 1 merges


def _dates_dim(tmp_path):
    root, src = tmp_path / "project", tmp_path / "days"
    from fabric_desktop.meta.templates import init_project
    init_project(root, "3", "Chris", sample=False)
    entity(root, "bronze.dim_day", f"""
        name = "bronze.dim_day"
        owner = "Chris"
        [source]
        table = {json.dumps(src.as_posix())}
        [load]
        mode = "scd1"
        [scd]
        surrogate_key = "day_sk"
        [contract]
        keys = ["day"]
        [contract.columns]
        day_sk = "int64"
        day = "date"
        label = "string"
    """)
    return root, src


def test_a_type_1_dimension_keyed_on_dates_merges_only_the_days_that_arrive(tmp_path):
    from fabric_desktop.meta import LocalStore
    root, src = _dates_dim(tmp_path)
    store = LocalStore(root / ".local" / "dev", "dev")
    days = [dt.date(2026, 1, d) for d in range(1, 4)]
    write_deltalake(str(src), pl.DataFrame({"day": days, "label": ["a", "b", "c"]}).to_arrow())
    assert Engine(Project(root), store, DAY1).run("bronze.dim_day").status == "ok"
    write_deltalake(str(src), pl.DataFrame({"day": [days[1], dt.date(2026, 1, 9)], "label": ["B", "z"]}).to_arrow(),
                    mode="overwrite")
    res = Engine(Project(root), store, DAY2).run("bronze.dim_day")
    assert res.status == "ok", res.message
    assert "1 new, 1 updated" in steps(res)["write"].detail
    t = Engine(Project(root), store).read_entity("bronze.dim_day").sort("day_sk")
    assert t.select("day_sk", "label").rows() == [(-1, "Unknown"), (1, "a"), (2, "B"), (3, "c"), (4, "z")]




# ---- lookups and Type 1 type changes


def test_a_lookup_into_a_dimension_not_loaded_yet_stops_the_fact(tmp_path):
    d = Dims(tmp_path, True)
    write_deltalake(str(d.src), pl.DataFrame({"customer_id": [1], "city": ["Oslo"], "email": ["a@x"]}).to_arrow())
    entity(d.root, "bronze.fact_visits", f"""
        name = "bronze.fact_visits"
        owner = "Chris"
        [source]
        table = {json.dumps(d.src.as_posix())}
        [load]
        mode = "full"
        [contract]
        [contract.columns]
        customer_id = "int64"
        city = "string"
        cust_sk = "int64"
        [[lookup]]
        dim = "bronze.dim1"
        on = "customer_id"
        as = "cust_sk"
    """)
    res = Engine(Project(d.root), d.store, DAY1).run("bronze.fact_visits")
    assert res.status == "failed" and res.message == "bronze.dim1 has not been loaded in dev yet"


def test_a_type_1_dimension_whose_column_type_changed_is_rewritten_and_says_why(tmp_path):
    d = Dims(tmp_path, True)
    d.load([(1, "Oslo", "a@x"), (2, "Rome", "b@x"), (3, "Kyiv", "c@x")], DAY1)
    path = d.root / "metadata" / "entities" / "bronze.dim1.toml"
    path.write_text(path.read_text().replace('email = "string"', 'email = "int64"')
                    .replace('keys = ["customer_id"]', 'keys = ["customer_id"]\noverwrite_schema = true'))
    write_deltalake(str(d.src), pl.DataFrame({"customer_id": [1], "city": ["Lima"], "email": [7]}).to_arrow(),
                    mode="overwrite", schema_mode="overwrite")
    res = Engine(Project(d.root), d.store, DAY2).run("bronze.dim1", backfill="none")
    assert res.status == "ok", res.message
    assert steps(res)["write"].detail.startswith("0 new, 1 updated; rewrote the table because column types changed "
                                                 "(email String to Int64)")
    # The rows already there keep their text, so the rewritten column holds both as text.
    t = d.table("bronze.dim1")
    assert t.select("cust_sk", "city", "email").rows() == [(-1, "Unknown", "Unknown"), (1, "Lima", "7"), (2, "Rome", "b@x"),
                                                          (3, "Kyiv", "c@x")]
