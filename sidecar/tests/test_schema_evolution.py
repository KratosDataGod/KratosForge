"""Schema evolution (PB-031): what a run may change in a table's schema, and the versioned record of it.

Adding a column goes in place (Delta MERGE or append with merge_schema), inside the Fabric-safe
Delta profile. Widening a column's type rewrites the table: delta-rs can't write Delta's type
widening feature, and the feature would take the table outside the profile Fabric readers are
known to read. Narrowing a type, or changing it to another kind, is refused unless the entity sets
overwrite_schema = true under [contract], as Delta refuses it without overwriteSchema. Each change
is recorded in meta.schema_history with the Delta version that made it and a schema version.
"""

import json
from contextlib import contextmanager

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, TableFeatures, write_deltalake
from test_batches import DAYS, full_entity
from test_large_tables import DAY1, DAY2, DAY3, Setup, entity, rows
from test_meta import ORDER_COLS, Repo, edit_entity, landing

from fabric_desktop import delta_profile
from fabric_desktop.meta import Engine, Project, drift, mesh
from fabric_desktop.meta.engine import polars_type
from fabric_desktop.meta.project import validate


def qty_entity(s: Setup, qty: str, mode: str = "incremental", extra: str = "") -> None:
    (s.root / "metadata" / "entities" / "bronze.amounts.toml").unlink(missing_ok=True)  # reads amount
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = {json.dumps(s.src.as_posix())}
        [load]
        mode = "{mode}"
        watermark = "modified"
        [contract]
        keys = ["id"]
        {extra}
        [contract.columns]
        id = "int64"
        qty = "{qty}"
        modified = "date"
    """)


def qty_rows(ids, day, qty=1) -> pl.DataFrame:
    return pl.DataFrame({"id": list(ids), "qty": [qty] * len(ids), "modified": [day] * len(ids)},
                        schema={"id": pl.Int64, "qty": pl.Int64, "modified": pl.Date})


def history(s: Setup) -> list[dict]:
    h = s.store.meta("schema_history").filter(pl.col("entity") == "bronze.events")
    return [{k: r[k] for k in ("change", "columns", "detail", "breaking", "delta_version", "schema_version")}
            for r in h.iter_rows(named=True)]


def table(s: Setup) -> DeltaTable:
    return DeltaTable(s.store.uri("LH_Bronze", "dbo", "events"))


# --------------------------------------------------------------------------- the reader constraint


def test_delta_rs_cant_write_type_widening_so_it_stays_off(tmp_path):
    """The installed delta-rs has no typeWidening table feature to turn on, and refuses the table
    property. If an upgrade adds it, this fails: revisit PB-031's in-place widening then, and only
    with Fabric's readers proven on such a table (it raises the reader and writer protocol)."""
    assert not any("widening" in name.lower() for name in dir(TableFeatures))
    write_deltalake(str(tmp_path), pa.table({"id": pa.array([1], pa.int32())}))
    with pytest.raises(Exception, match="enableTypeWidening"):
        DeltaTable(str(tmp_path)).alter.set_table_properties({"delta.enableTypeWidening": "true"})
    p = DeltaTable(str(tmp_path)).protocol()
    assert (p.min_reader_version, p.min_writer_version) == (1, 2)


def test_the_history_s_breaking_rule_is_promotes():
    """A contract change promote lets through (mesh.WIDENING) is a widening in the schema history,
    and the reverse is a narrowing: the two records agree on what breaks a reader."""
    def typ(name):
        return pl.Int16 if name == "int16" else polars_type(name)  # int16 isn't a contract type
    for old, new in mesh.WIDENING:
        assert drift.widens(typ(old), typ(new)), (old, new)
        assert not drift.widens(typ(new), typ(old)), (new, old)
    assert drift.widens(pl.Decimal(10, 2), pl.Decimal(12, 2)) and not drift.widens(pl.Decimal(12, 2), pl.Decimal(10, 2))
    assert drift.widens(pl.Int32, pl.Decimal(12, 2)) and not drift.widens(pl.Int64, pl.Decimal(12, 2))
    assert not drift.widens(pl.Int64, pl.Float64)  # not every int64 fits a double exactly
    assert not drift.widens(pl.Int64, pl.String)


# --------------------------------------------------------------------------- in place: added columns


def test_an_added_column_goes_in_place_and_is_recorded_with_its_version(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(100), DAY1))
    s.run()
    s.source(rows(range(1000, 1010), DAY2))
    s.run(day=DAY2)
    files_before = set(table(s).file_uris())
    s.source(rows([2000], DAY3).with_columns(pl.lit("eu").alias("region")))
    res = s.run(day=DAY3)
    assert "(Delta MERGE rewrote 0 files," in s.write_step(res)
    assert files_before < set(table(s).file_uris())  # every file the table had is still there, as it was
    delta_profile.check_table(table(s))  # no protocol change: still the Fabric-safe profile
    assert history(s)[-1] == {"change": "added", "columns": "region", "detail": "region string", "breaking": False,
                              "delta_version": res.delta_version, "schema_version": 2}


def test_the_first_load_is_schema_version_1_and_a_run_that_changes_nothing_adds_no_row(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    first = s.run()
    s.source(rows([5], DAY2))
    s.run(day=DAY2)
    assert history(s) == [{"change": "created", "columns": "id, name, amount, modified, _run_id, _loaded_at",
                           "detail": "id int64, name string, amount float64, modified date, _run_id string, "
                                     "_loaded_at date", "breaking": False, "delta_version": first.delta_version,
                           "schema_version": 1}]


# --------------------------------------------------------------------------- widening


@pytest.mark.parametrize("merge", [True, False], ids=["merge", "rewrite"])
def test_widening_a_type_rewrites_inside_the_profile_and_is_recorded(tmp_path, merge):
    s = Setup(tmp_path, merge)
    qty_entity(s, "int32")
    s.source(qty_rows(range(3), DAY1))
    s.run()
    qty_entity(s, "int64")
    s.source(qty_rows([7], DAY2, qty=2**40))
    res = s.run(day=DAY2, backfill="none")  # the logic changed: keep the rows already loaded
    assert table(s).schema().to_arrow().field("qty").type == pa.int64()
    assert s.table()["qty"].to_list() == [1, 1, 1, 2**40]
    delta_profile.check_table(table(s))  # the rewrite keeps reader 1 / writer 2, no table features
    if merge:
        assert "rewrote the table because column types changed (qty Int32 to Int64)" in s.write_step(res)
    assert history(s)[-1] == {"change": "widened", "columns": "qty", "detail": "qty int32 to int64",
                              "breaking": False, "delta_version": res.delta_version, "schema_version": 2}


# --------------------------------------------------------------------------- narrowing and other kinds


def test_narrowing_a_type_is_refused_without_overwrite_schema(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64", mode="full")
    s.source(qty_rows(range(3), DAY1))
    s.run()
    version = table(s).version()
    qty_entity(s, "int32", mode="full")
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and res.delta_version is None
    assert "schema change refused: qty int64 to int32 would narrow" in res.message
    assert "add this line under [contract] in its entity file: overwrite_schema = true" in res.message
    assert "overwrite_schema = true" in res.message
    assert table(s).version() == version and table(s).schema().to_arrow().field("qty").type == pa.int64()
    assert [h["change"] for h in history(s)] == ["created"]


def test_a_change_to_another_kind_is_refused_on_an_incremental_rewrite_too(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64")
    s.source(qty_rows(range(3), DAY1))
    s.run()
    version = table(s).version()
    qty_entity(s, "string")
    s.source(qty_rows([7], DAY2))
    res = s.engine(DAY2).run("bronze.events", backfill="none")
    assert res.status == "failed" and "qty int64 to string would narrow a column's type or change its kind" in res.message
    assert table(s).version() == version


def test_overwrite_schema_lets_a_narrowing_through_and_records_it_as_breaking(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64", mode="full")
    s.source(qty_rows(range(3), DAY1))
    s.run()
    qty_entity(s, "int32", mode="full", extra="overwrite_schema = true")
    res = s.run(day=DAY2)
    assert table(s).schema().to_arrow().field("qty").type == pa.int32()
    assert history(s)[-1] == {"change": "narrowed", "columns": "qty", "detail": "qty int64 to int32",
                              "breaking": True, "delta_version": res.delta_version, "schema_version": 2}


def test_a_batched_full_load_refuses_a_narrowing_before_it_writes(tmp_path):
    s = Setup(tmp_path, True)
    full_entity(s, 'batch_by = "modified"')
    for i, day in enumerate(DAYS[:3]):
        s.source(rows(range(i * 100, i * 100 + 100), day, amount=i))
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = 100
    assert engine.run("bronze.events").status == "ok"
    version = table(s).version()
    text = (s.root / "metadata" / "entities" / "bronze.events.toml").read_text(encoding="utf-8")
    (s.root / "metadata" / "entities" / "bronze.events.toml").write_text(
        text.replace('amount = "float64"', 'amount = "float32"'), encoding="utf-8")
    engine = Engine(Project(s.root), s.store, DAY1)
    engine.batch_rows = 100
    res = engine.run("bronze.events")
    assert res.status == "failed" and "amount float64 to float32 would narrow" in res.message, res.message
    assert table(s).version() == version


def test_overwrite_schema_must_be_true_or_false(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64", extra='overwrite_schema = "yes"')
    assert "bronze.events: contract overwrite_schema is true or false" in validate(Project(s.root))


def dim_entity(s: Setup, mode: str, qty: str) -> None:
    (s.root / "metadata" / "entities" / "bronze.amounts.toml").unlink(missing_ok=True)  # reads amount
    scd = 'tracked = ["qty"]' if mode == "scd2" else ""
    entity(s.root, "bronze.events", f"""
        name = "bronze.events"
        owner = "Chris"
        [source]
        table = {json.dumps(s.src.as_posix())}
        [load]
        mode = "{mode}"
        [scd]
        surrogate_key = "event_sk"
        {scd}
        [contract]
        keys = ["id"]
        [contract.columns]
        id = "int64"
        qty = "{qty}"
        modified = "date"
    """)


@pytest.mark.parametrize("merge", [True, False])
@pytest.mark.parametrize("mode", ["scd1", "scd2"])
@pytest.mark.parametrize("qty", ["int32", "string"])
def test_a_dimension_refuses_a_narrowing_with_the_same_message(tmp_path, mode, merge, qty):
    # scd1_merge and scd2_merge join the new rows to the table's: the refusal comes before them.
    s = Setup(tmp_path, merge)
    dim_entity(s, mode, "int64")
    s.source(qty_rows(range(3), DAY1))
    assert s.engine(DAY1).run("bronze.events").status == "ok"
    version = table(s).version()
    dim_entity(s, mode, qty)
    s.source(qty_rows(range(2, 5), DAY2, qty=2))
    res = s.engine(DAY2).run("bronze.events", backfill="none")
    assert res.status == "failed" and f"schema change refused: qty int64 to {qty} would narrow" in res.message, res.message
    assert table(s).version() == version and table(s).schema().to_arrow().field("qty").type == pa.int64()


@pytest.mark.parametrize("value,dtype", [("7", pl.Int64), ("1.5", pl.Float64)])
def test_a_landing_file_that_leaves_a_kept_column_empty_still_loads(tmp_path, value, dtype):
    # A column the drift policy keeps reads as text from a CSV that leaves it empty: the table keeps its type.
    r = Repo(tmp_path / "project")
    edit_entity(r.root, "bronze.orders", 'mode = "full"', 'mode = "incremental"')
    r.reload()
    assert r.engine().run("bronze.orders").status == "ok"
    landing(r.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS},score\n200,2,2026-10-02,30.00,,shipped,2026-10-02,{value}\n")
    assert r.engine(DAY2).run("bronze.orders").status == "ok"
    landing(r.root, "landing/orders/orders_2026-10-03.csv", f"{ORDER_COLS},score\n201,2,2026-10-03,30.00,,shipped,2026-10-03,\n")
    res = r.engine(DAY3).run("bronze.orders")
    assert res.status == "ok", res.message
    out = r.table("bronze.orders")
    assert out.schema["score"] == dtype and out.filter(pl.col("order_id") == 201)["score"].to_list() == [None]


def test_a_landing_file_that_changes_a_kept_column_s_kind_is_still_refused(tmp_path):
    r = Repo(tmp_path / "project")
    edit_entity(r.root, "bronze.orders", 'mode = "full"', 'mode = "incremental"')
    r.reload()
    assert r.engine().run("bronze.orders").status == "ok"
    landing(r.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS},score\n200,2,2026-10-02,30.00,,shipped,2026-10-02,7\n")
    assert r.engine(DAY2).run("bronze.orders").status == "ok"
    landing(r.root, "landing/orders/orders_2026-10-03.csv", f"{ORDER_COLS},score\n201,2,2026-10-03,30.00,,shipped,2026-10-03,x\n")
    res = r.engine(DAY3).run("bronze.orders")
    assert res.status == "failed" and "schema change refused: score int64 to string" in res.message, res.message


def test_an_int32_batch_of_a_column_the_contract_doesnt_declare_goes_into_its_int64_column(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64")
    text = (s.root / "metadata" / "entities" / "bronze.events.toml").read_text(encoding="utf-8")
    (s.root / "metadata" / "entities" / "bronze.events.toml").write_text(text.replace('qty = "int64"\n', ""), encoding="utf-8")
    s.source(qty_rows(range(3), DAY1), mode="overwrite")
    assert s.engine(DAY1).run("bronze.events").status == "ok"
    assert table(s).schema().to_arrow().field("qty").type == pa.int64()
    batch = qty_rows([7], DAY2).with_columns(pl.col("qty").cast(pl.Int32))
    write_deltalake(str(s.src), batch.to_arrow(), mode="overwrite", schema_mode="overwrite")
    assert DeltaTable(str(s.src)).schema().to_arrow().field("qty").type == pa.int32()
    res = s.engine(DAY2).run("bronze.events", backfill="none")
    assert res.status == "ok", res.message
    assert table(s).schema().to_arrow().field("qty").type == pa.int64()
    assert [h["change"] for h in history(s)] == ["created"]


def test_a_full_load_with_no_rows_still_refuses_a_declared_narrowing(tmp_path):
    s = Setup(tmp_path, True)
    qty_entity(s, "int64", mode="full")
    s.source(qty_rows(range(3), DAY1))
    s.run()
    qty_entity(s, "int32", mode="full")
    s.source(qty_rows([], DAY2), mode="overwrite")
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and "qty int64 to int32 would narrow" in res.message, res.message


# --------------------------------------------------------------------------- schema versions (#1008)


def test_a_run_that_takes_the_table_as_soon_as_another_lets_go_numbers_its_change_after_it(tmp_path):
    """The schema version is fixed while the table is still locked: a run that starts the moment the
    lock is let go reads it, so two quick changes never share a version."""
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    s.run()

    @contextmanager
    def then_another_run(e):
        yield
        s.source(rows([20], DAY3).with_columns(pl.lit("eu").alias("region"), pl.lit("web").alias("channel")))
        s.run(day=DAY3)

    s.source(rows([10], DAY2).with_columns(pl.lit("eu").alias("region")))
    first = Engine(Project(s.root), s.store, DAY2, merge_writes=True, table_lock=then_another_run).run("bronze.events")
    assert first.status == "ok", first.message
    assert [(r["change"], r["columns"], r["schema_version"]) for r in history(s)][1:] == [
        ("added", "region", 2), ("added", "channel", 3)]


def test_history_rows_from_before_schema_versions_count_so_the_numbering_continues(tmp_path):
    s = Setup(tmp_path, True)
    s.store.meta_append("schema_history", [{"entity": "bronze.events", "run_id": run, "change": "added", "columns": c}
                                           for run, c in (("old-1", "a"), ("old-1", "b"), ("old-2", "c"))])
    s.source(rows(range(3), DAY1))
    s.run()
    s.source(rows([10], DAY2).with_columns(pl.lit("eu").alias("region")))
    s.run(day=DAY2)
    assert [r["schema_version"] for r in history(s)] == [None, None, None, 3, 4]


def test_a_schema_history_that_cant_be_saved_leaves_the_run_ok_and_says_so(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    append = s.store.meta_append

    def no_history(table, rows, metadata=None):
        if table == "schema_history":
            raise OSError("the meta lakehouse is read only")
        append(table, rows, metadata=metadata)
    monkeypatch.setattr(s.store, "meta_append", no_history)
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", lambda _: None, raising=False)
    res = s.run()
    assert "couldn't save the schema history (OSError: the meta lakehouse is read only)" in res.message
    assert s.table().height == 3


def test_a_failure_after_the_commit_keeps_the_note_that_the_schema_history_wasnt_saved(tmp_path, monkeypatch):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    append = s.store.meta_append

    def no_history(table, rows, metadata=None):
        if table == "schema_history":
            raise OSError("the meta lakehouse is read only")
        append(table, rows, metadata=metadata)
    monkeypatch.setattr(s.store, "meta_append", no_history)
    engine = s.engine()
    monkeypatch.setattr(engine, "_record_files", lambda e, res: (_ for _ in ()).throw(OSError("files not recorded")))
    res = engine.run("bronze.events")
    assert res.status == "failed" and res.delta_version is not None
    assert res.message == ("OSError: files not recorded; "
                           "couldn't save the schema history (OSError: the meta lakehouse is read only)")


# --------------------------------------------------------------------------- reads per run (#1009)


def counted(s: Setup, monkeypatch) -> dict:
    """Counts what a run reads for the schema history: the table's log at a version, and the history."""
    seen = {"versioned scans": 0, "whole history": 0, "history where": []}
    scan, meta, where = s.store.scan, s.store.meta, s.store.read_where

    def scan_(lakehouse, schema, table, version=None):
        if (lakehouse, table, version is not None) == ("LH_Bronze", "events", True):
            seen["versioned scans"] += 1
        return scan(lakehouse, schema, table, version)

    def meta_(table):
        seen["whole history"] += table == "schema_history"
        return meta(table)

    def where_(lakehouse, schema, table, cond, columns=None, version=None):
        if table == "schema_history":
            seen["history where"].append(str(cond))
        return where(lakehouse, schema, table, cond, columns, version)
    monkeypatch.setattr(s.store, "scan", scan_)
    monkeypatch.setattr(s.store, "meta", meta_)
    monkeypatch.setattr(s.store, "read_where", where_)
    return seen


@pytest.mark.parametrize("change", [False, True], ids=["same schema", "added column"])
def test_a_run_reads_the_schema_once_and_only_its_own_history(tmp_path, monkeypatch, change):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1))
    s.run()
    new = rows([10], DAY2)
    s.source(new.with_columns(pl.lit("eu").alias("region")) if change else new)
    seen = counted(s, monkeypatch)
    res = s.run(day=DAY2)
    assert seen["versioned scans"] == 0 and seen["whole history"] == 0
    assert seen["history where"] == ([str(pl.col("entity") == "bronze.events")] if change else [])
    monkeypatch.undo()
    if change:
        assert history(s)[-1] == {"change": "added", "columns": "region", "detail": "region string", "breaking": False,
                                  "delta_version": res.delta_version, "schema_version": 2}
    else:
        assert len(history(s)) == 1


def test_the_kept_schema_is_the_one_the_log_gives(tmp_path):
    """The schema a write keeps is read the same way as one read from the log, so the history
    never records a change that is only a difference in how it was read."""
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1).with_columns(pl.lit("eu").alias("region")))
    res = s.run()
    kept = s.store.written_schema("LH_Bronze", "dbo", "events", res.delta_version)
    assert kept is not None
    assert kept == s.store.scan("LH_Bronze", "dbo", "events", res.delta_version).collect_schema()
    assert s.store.written_schema("LH_Bronze", "dbo", "events", res.delta_version - 1) is None
