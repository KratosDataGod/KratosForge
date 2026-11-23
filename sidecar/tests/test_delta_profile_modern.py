"""The modern Delta profile, the default since 2026-11-17: writes to tables with deletion vectors and
column mapping by name, on real tables that Spark made (tests/fixtures/delta_spark).

Every write is checked for data integrity: the rows read back are exactly the ones expected, the
version moved by one, and the new commit in the Delta log is the operation that ran. A refused write
leaves the version, the log and every file as they were. The rows each fixture starts with are the
ones Spark read back (test_delta_read_spark_tables.py).

The same writes were checked once against Spark itself: after delta-rs appended, updated, merged,
deleted and overwrote by predicate on copies of these tables, Spark 3.5.5 with Delta 3.3.2 read
back the same rows as delta-rs, and could still DELETE from each table. For cm_legacy (reader 2,
writer 5) that was an append, an overwrite and a merge: the protocol stayed reader 2, writer 5, the
new files name the columns by their physical names, and delta-rs read Spark's DELETE back.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_profile as P
from fabric_desktop import delta_read
from fabric_desktop.localdata import LocalData
from fabric_desktop.meta.store import LocalStore

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"
_ALL = [(i, i * 10, f"n{i}", "a" if i < 5 else "b") for i in range(10)]
START = {
    "dv": [r for r in _ALL if r[0] not in (3, 7, 8)],
    "dv_part": [r for r in _ALL if r[0] not in (1, 6)],
    "cm_name": [r[:3] for r in _ALL],
    "dv_cm": [r for r in _ALL if r[0] not in (2, 5)],
    # Reader 2, writer 5: column mapping by name as Spark made it before table features.
    "cm_legacy": _ALL + [(10, 100, "n10", "b")],
}
COLUMNS = {"dv": ["id", "amount", "label", "p"], "dv_part": ["id", "amount", "label", "p"],
           "cm_name": ["id", "total", "the label"], "dv_cm": ["id", "total", "label", "p"],
           "cm_legacy": ["id", "total", "label", "p"]}
WRITABLE = sorted(START)


def _snapshot(folder: Path) -> tuple[int, list[str], dict[str, int]]:
    """The version, the log's file names and every file's size: what a refused write must leave alone."""
    files = {p.relative_to(folder).as_posix(): p.stat().st_size for p in folder.rglob("*") if p.is_file()}
    return DeltaTable(str(folder)).version(), sorted(f for f in files if f.startswith("_delta_log/")), files


def _rows(folder: Path) -> list[tuple]:
    return sorted(tuple(r.values()) for r in delta_read.arrow(DeltaTable(str(folder))).to_pylist())


def _last_operation(folder: Path) -> str:
    commits = sorted((folder / "_delta_log").glob("*.json"))
    for line in commits[-1].read_text(encoding="utf-8").splitlines():
        action = json.loads(line)
        if "commitInfo" in action:
            return str(action["commitInfo"]["operation"])
    raise AssertionError("no commitInfo in the last commit")


def _row(name: str, ident: int) -> dict:
    values = (ident, ident * 10, f"n{ident}", "a")
    return dict(zip(COLUMNS[name], values, strict=False))


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path, "dev")


def _place(store: LocalStore, name: str) -> Path:
    folder = Path(store.uri("LH_Bronze", "dbo", name))
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / name, folder)
    return folder


def _frame(name: str, ids: list[int]) -> pl.DataFrame:
    rows = [_row(name, i) for i in ids]
    schema = {"id": pl.Int64, "amount": pl.Int32, "total": pl.Int32, "label": pl.String, "the label": pl.String,
              "p": pl.String}
    return pl.DataFrame(rows, schema={c: schema[c] for c in COLUMNS[name]})


def test_the_default_is_the_modern_profile_and_reads_for_the_sql_endpoint_and_direct_lake():
    assert P.DEFAULT_PROFILE is P.MODERN
    assert P.DEFAULT_READERS == ("sql_endpoint", "direct_lake")
    assert P.profile(None) is P.MODERN and P.profile("conservative") is P.CONSERVATIVE


@pytest.mark.parametrize("name", WRITABLE)
def test_the_spark_tables_are_inside_the_modern_profile_and_outside_the_conservative_one(name):
    table = DeltaTable(str(FIXTURES / name))
    assert P.problems(table) == []
    P.check_table(table)  # no refusal
    with pytest.raises(P.ProfileViolation, match="outside the Fabric-safe Delta profile"):
        P.check_table(table, "conservative")


@pytest.mark.parametrize("name", WRITABLE)
def test_an_append_through_the_store_adds_exactly_its_rows(store, name):
    folder = _place(store, name)
    version = DeltaTable(str(folder)).version()
    store.write("LH_Bronze", "dbo", name, _frame(name, [100, 101]), mode="append")
    assert DeltaTable(str(folder)).version() == version + 1
    assert _last_operation(folder) == "WRITE"
    added = [tuple(_row(name, i).values()) for i in (100, 101)]
    assert _rows(folder) == sorted(START[name] + added)


@pytest.mark.parametrize("name", WRITABLE)
def test_an_overwrite_through_the_store_replaces_every_row(store, name):
    folder = _place(store, name)
    version = DeltaTable(str(folder)).version()
    store.write("LH_Bronze", "dbo", name, _frame(name, [7]), mode="overwrite")
    assert DeltaTable(str(folder)).version() == version + 1
    assert _rows(folder) == [tuple(_row(name, 7).values())]
    # The deletion vectors of the replaced files don't come back: no deleted row reappears.
    assert delta_read.deleted_rows(DeltaTable(str(folder))) == 0


@pytest.mark.parametrize("name", WRITABLE)
def test_an_upsert_through_the_store_changes_one_row_and_adds_one(store, name):
    folder = _place(store, name)
    version = DeltaTable(str(folder)).version()
    change = _frame(name, [0, 100]).with_columns(pl.col(COLUMNS[name][2]).str.replace("n", "changed-"))
    store.upsert("LH_Bronze", "dbo", name, change, on=["id"])
    assert DeltaTable(str(folder)).version() == version + 1
    assert _last_operation(folder) == "MERGE"
    expected = [r for r in START[name] if r[0] != 0]
    expected += [tuple(change.row(0)), tuple(change.row(1))]
    assert _rows(folder) == sorted(expected)


@pytest.mark.parametrize("name", WRITABLE)
def test_local_sql_inserts_updates_deletes_and_merges_keep_spark_deleted_rows_deleted(tmp_path, name):
    local = LocalData(tmp_path / "app" / "local-data")
    try:
        e = local.engine(local.create("Modern")["id"])
        folder = e.tables_dir / "dbo" / name
        shutil.copytree(FIXTURES / name, folder)
        rows = {r[0]: list(r) for r in START[name]}
        version = DeltaTable(str(folder)).version()
        first, amount = COLUMNS[name][0], COLUMNS[name][1]
        values = ", ".join(repr(v) for v in _row(name, 100).values())
        steps = [
            (f"INSERT INTO dbo.{name} VALUES ({values})", "WRITE"),
            (f"UPDATE dbo.{name} SET {amount} = -1 WHERE {first} = 0", "UPDATE"),
            (f"DELETE FROM dbo.{name} WHERE {first} = 4", "DELETE"),
        ]
        changes = [lambda r: r.__setitem__(100, list(_row(name, 100).values())),
                   lambda r: r[0].__setitem__(1, -1),
                   lambda r: r.pop(4)]
        for (sql, op), change in zip(steps, changes, strict=True):
            e.execute(sql)
            change(rows)
            version += 1
            assert DeltaTable(str(folder)).version() == version, sql
            assert _last_operation(folder) == op, sql
            assert _rows(folder) == sorted(tuple(r) for r in rows.values()), sql
        out = e.execute(f"SELECT {first} FROM dbo.{name} ORDER BY {first}")
        assert [r[0] for r in out["rows"]] == sorted(rows)
    finally:
        local.close_all()


def test_column_mapping_by_id_is_refused_because_the_sql_endpoint_cant_read_it(store):
    folder = _place(store, "cm_id")
    before = _snapshot(folder)
    with pytest.raises(P.ProfileViolation) as refused:
        store.write("LH_Bronze", "dbo", "cm_id", pl.DataFrame({"id": [100]}), mode="append")
    assert "delta.columnMapping.mode=id" in str(refused.value)
    assert "SQL analytics endpoint" in str(refused.value)
    assert _snapshot(folder) == before


@pytest.mark.parametrize("name", WRITABLE)
def test_a_reader_that_cant_read_the_table_refuses_with_why_and_leaves_it_unchanged(name):
    table = DeltaTable(str(FIXTURES / name))
    reasons = P.why(table, ["sql_endpoint", "python_notebooks"])
    assert reasons and all("Fabric Python notebooks can't read" in r for r in reasons)
    assert all("SQL analytics endpoint" not in r for r in reasons)  # it reads both features
    with pytest.raises(P.ProfileViolation, match="^Nothing was written to this table") as refused:
        P.check_table(table, readers=["python_notebooks"])
    assert str(refused.value).endswith("Keep writing this table from Fabric Spark.")
    # After a commit the refusal can't say nothing was written: it says not to run an append again.
    with pytest.raises(P.ProfileViolation, match="^The write committed") as late:
        P.check_table(table, readers=["python_notebooks"], committed=True)
    assert "If it was an append, don't run it again" in str(late.value) and "Nothing was written" not in str(late.value)
    P.check_table(table, readers=list(P.DEFAULT_READERS))


def test_a_refused_store_write_for_a_reader_leaves_everything_unchanged(store, monkeypatch):
    folder = _place(store, "dv_cm")
    before = _snapshot(folder)
    monkeypatch.setattr(P, "DEFAULT_READERS", ("sql_endpoint", "pipelines"))
    with pytest.raises(P.ProfileViolation, match="Fabric pipelines can't read"):
        store.write("LH_Bronze", "dbo", "dv_cm", _frame("dv_cm", [100]), mode="append")
    assert _snapshot(folder) == before


def test_an_unknown_cell_is_refused_too_and_says_no_source_was_found():
    reasons = P.why(DeltaTable(str(FIXTURES / "cm_name")), ["snowflake"])
    assert reasons == [("It uses column mapping by name, and no source we found says whether Snowflake can read it "
                        "(Microsoft's Delta Lake interoperability page).")]


def test_an_unknown_reader_or_profile_name_is_refused():
    table = DeltaTable(str(FIXTURES / "dv"))
    with pytest.raises(ValueError, match="Unknown Delta reader 'excel'"):
        P.why(table, ["excel"])
    with pytest.raises(ValueError, match="Unknown Delta profile 'fast'"):
        P.check_table(table, "fast")


def test_switching_the_default_back_refuses_the_same_write_and_changes_nothing(store, monkeypatch):
    folder = _place(store, "dv")
    before = _snapshot(folder)
    monkeypatch.setattr(P, "DEFAULT_PROFILE", P.CONSERVATIVE)
    with pytest.raises(P.ProfileViolation, match="outside the Fabric-safe Delta profile"):
        store.write("LH_Bronze", "dbo", "dv", _frame("dv", [100]), mode="append")
    assert _snapshot(folder) == before


def test_a_write_that_would_change_a_column_mapped_tables_columns_is_refused_before_anything_is_written(store):
    """delta-rs can't evolve a column-mapped table's schema, so a write that adds a column is refused with the
    reason before the write starts; one with exactly the table's columns goes through (the cases above)."""
    folder = _place(store, "cm_name")
    before = _snapshot(folder)
    wider = _frame("cm_name", [100]).with_columns(pl.lit("x").alias("extra"))
    with pytest.raises(ValueError, match=r"dbo.cm_name uses column mapping, and this write adds extra"):
        store.write("LH_Bronze", "dbo", "cm_name", wider, mode="append")
    with pytest.raises(ValueError, match=r"leaves out the label"):
        store.write("LH_Bronze", "dbo", "cm_name", _frame("cm_name", [100]).drop("the label"), mode="overwrite")
    with pytest.raises(ValueError, match=r"this write names ID where the table has id\. Nothing was written"):
        store.write("LH_Bronze", "dbo", "cm_name", _frame("cm_name", [100]).rename({"id": "ID"}), mode="append")
    assert _snapshot(folder) == before


def _legacy(writer: int, config: dict | None = None, fields: list | None = None):
    """A stand-in table at writer version ``writer`` with no feature lists, the given settings and schema fields."""
    from types import SimpleNamespace
    schema = json.dumps({"type": "struct", "fields": fields or [{"name": "id", "type": "long", "nullable": True,
                                                                  "metadata": {}}]})
    return SimpleNamespace(
        protocol=lambda: SimpleNamespace(min_reader_version=1, min_writer_version=writer, reader_features=None,
                                         writer_features=None),
        metadata=lambda: SimpleNamespace(configuration=config or {}),
        schema=lambda: SimpleNamespace(to_json=lambda: schema))


@pytest.mark.parametrize("writer", [3, 4, 5])
def test_a_legacy_writer_version_3_to_5_is_inside_when_none_of_its_features_is_in_use(writer):
    assert P.problems(_legacy(writer)) == []
    # The conservative profile still refuses it for the version alone.
    assert P.problems(_legacy(writer), "conservative") == [f"writer version {writer} > 2"]


def test_writer_version_6_stays_outside_the_modern_profile_even_with_nothing_in_use():
    found = P.problems(_legacy(6))
    assert found == [("writer version 6, which turns on check constraints, change data feed, generated columns, "
                      "column mapping and identity columns without listing them")]
    with pytest.raises(P.ProfileViolation, match="outside the modern Delta profile: writer version 6"):
        P.check_table(_legacy(6))


GENERATED = {"name": "y", "type": "integer", "nullable": True, "metadata": {"delta.generationExpression": "id + 1"}}
NESTED_GENERATED = {"name": "s", "type": {"type": "struct", "fields": [GENERATED]}, "nullable": True, "metadata": {}}


@pytest.mark.parametrize(("writer", "config", "fields", "named"), [
    (3, {"delta.constraints.id_positive": "id > 0"}, None, "check constraints (delta.constraints.id_positive)"),
    (5, {"delta.constraints.a": "id > 0", "delta.constraints.b": "id < 9"}, None,
     "check constraints (delta.constraints.a, delta.constraints.b)"),
    (4, {"delta.enableChangeDataFeed": "true"}, None, "change data feed (delta.enableChangeDataFeed is true)"),
    (5, {"delta.enableChangeDataFeed": "TRUE"}, None, "change data feed (delta.enableChangeDataFeed is true)"),
    (4, None, [GENERATED], "generated columns (a column has delta.generationExpression)"),
    (5, None, [NESTED_GENERATED], "generated columns (a column has delta.generationExpression)"),
], ids=["constraint-w3", "constraints-w5", "cdf-w4", "cdf-w5", "generated-w4", "nested-generated-w5"])
def test_a_legacy_writer_version_using_one_of_its_features_is_refused_and_names_it(writer, config, fields, named):
    found = P.problems(_legacy(writer, config, fields))
    assert len(found) == 1 and found[0].startswith(f"writer version {writer}, which turns on check constraints")
    assert found[0].endswith(f"without listing them, and the table uses {named}, which the modern Delta profile "
                             "doesn't include")
    with pytest.raises(P.ProfileViolation, match=f"outside the modern Delta profile: writer version {writer}"):
        P.check_table(_legacy(writer, config, fields))


def test_a_writer_version_3_table_ignores_settings_only_a_later_version_turns_on():
    """Version 3 turns on only check constraints, so change data feed settings and generation expressions
    can't be in force there; problems() names only what the version turns on."""
    assert P.problems(_legacy(3, {"delta.enableChangeDataFeed": "false"}, [GENERATED])) == []
    assert P.problems(_legacy(4, {"delta.enableChangeDataFeed": "false"})) == []


@pytest.mark.parametrize("writer", [1, 2, 7])
def test_writer_versions_2_and_7_stay_inside_the_modern_profile(writer):
    from types import SimpleNamespace
    table = SimpleNamespace(
        protocol=lambda: SimpleNamespace(min_reader_version=1, min_writer_version=writer, reader_features=None,
                                         writer_features=["appendOnly"] if writer == 7 else None),
        metadata=lambda: SimpleNamespace(configuration={}))
    assert P.problems(table) == []


def test_a_refused_append_to_a_writer_version_4_table_leaves_it_unchanged(store):
    from deltalake import write_deltalake
    folder = Path(store.uri("LH_Bronze", "dbo", "legacy"))
    folder.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(folder), pl.DataFrame({"id": [1]}).to_arrow(),
                    configuration={"delta.enableChangeDataFeed": "true"})
    if DeltaTable(str(folder)).protocol().min_writer_version != 4:
        pytest.skip("this delta-rs doesn't make a writer version 4 table for change data feed")
    before = _snapshot(folder)
    with pytest.raises(P.ProfileViolation, match="writer version 4"):
        store.write("LH_Bronze", "dbo", "legacy", pl.DataFrame({"id": [2]}), mode="append")
    assert _snapshot(folder) == before


def test_a_refused_append_to_a_table_with_a_check_constraint_leaves_it_unchanged(store):
    from deltalake import write_deltalake
    folder = Path(store.uri("LH_Bronze", "dbo", "checked"))
    folder.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(folder), pl.DataFrame({"id": [1]}).to_arrow())
    DeltaTable(str(folder)).alter.add_constraint({"id_positive": "id > 0"})
    assert DeltaTable(str(folder)).protocol().min_writer_version == 3
    before = _snapshot(folder)
    with pytest.raises(P.ProfileViolation, match="writer version 3, which turns on check constraints without"):
        store.write("LH_Bronze", "dbo", "checked", pl.DataFrame({"id": [2]}), mode="append")
    assert _snapshot(folder) == before


def _add_commit(folder: Path, configuration: dict[str, str] | None = None, generated: str | None = None) -> None:
    """Commit a new metaData action to a copy of a fixture, as Spark's ALTER TABLE does: settings added to
    the table's, and optionally a generation expression on column ``generated``."""
    log = folder / "_delta_log"
    meta = None
    for commit in sorted(log.glob("*.json")):
        for line in commit.read_text(encoding="utf-8").splitlines():
            action = json.loads(line)
            if "metaData" in action:
                meta = action["metaData"]
    assert meta is not None
    meta["configuration"] = {**meta["configuration"], **(configuration or {})}
    if generated:
        schema = json.loads(meta["schemaString"])
        for field in schema["fields"]:
            if field["name"] == generated:
                field["metadata"]["delta.generationExpression"] = "id * 10"
        meta["schemaString"] = json.dumps(schema)
    version = DeltaTable(str(folder)).version() + 1
    info = {"commitInfo": {"timestamp": 1795096200000, "operation": "SET TBLPROPERTIES", "operationParameters": {}}}
    (log / f"{version:020d}.json").write_text(json.dumps(info) + "\n" + json.dumps({"metaData": meta}) + "\n",
                                              encoding="utf-8")


@pytest.mark.parametrize(("change", "named"), [
    ({"configuration": {"delta.constraints.total_positive": "total >= 0"}},
     "check constraints (delta.constraints.total_positive)"),
    ({"configuration": {"delta.enableChangeDataFeed": "true"}}, "change data feed (delta.enableChangeDataFeed is true)"),
    ({"generated": "total"}, "generated columns (a column has delta.generationExpression)"),
], ids=["constraint", "change-data-feed", "generated-column"])
def test_a_writer_version_5_table_that_uses_an_implied_feature_refuses_every_write_and_changes_nothing(store, change,
                                                                                                       named):
    folder = _place(store, "cm_legacy")
    _add_commit(folder, **change)
    assert DeltaTable(str(folder)).protocol().min_writer_version == 5
    before = _snapshot(folder)
    for write in (lambda: store.write("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [100]), mode="append"),
                  lambda: store.write("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [7]), mode="overwrite"),
                  lambda: store.upsert("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [0, 100]), on=["id"])):
        with pytest.raises(P.ProfileViolation) as refused:
            write()
        text = str(refused.value)
        assert text.startswith("Table is outside the modern Delta profile: writer version 5, which turns on")
        assert f"the table uses {named}, which the modern Delta profile doesn't include" in text
        assert _snapshot(folder) == before


def test_the_legacy_column_mapped_table_keeps_its_protocol_after_a_write(store):
    folder = _place(store, "cm_legacy")
    store.write("LH_Bronze", "dbo", "cm_legacy", _frame("cm_legacy", [100]), mode="append")
    protocol = DeltaTable(str(folder)).protocol()
    assert (protocol.min_reader_version, protocol.min_writer_version) == (2, 5)
    assert not protocol.reader_features and not protocol.writer_features
    assert P.problems(DeltaTable(str(folder))) == []


def test_a_legacy_column_mapped_table_by_id_is_still_refused():
    table = _legacy(5, {"delta.columnMapping.mode": "id"})
    assert P.problems(table) == ["delta.columnMapping.mode=id"]
