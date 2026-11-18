"""The modern Delta profile, the default since 2026-11-17: writes to tables with deletion vectors and
column mapping by name, on real tables that Spark made (tests/fixtures/delta_spark).

Every write is checked for data integrity: the rows read back are exactly the ones expected, the
version moved by one, and the new commit in the Delta log is the operation that ran. A refused write
leaves the version, the log and every file as they were. The rows each fixture starts with are the
ones Spark read back (test_delta_read_spark_tables.py).

The same writes were checked once against Spark itself: after delta-rs appended, updated, merged,
deleted and overwrote by predicate on copies of these tables, Spark 3.5.5 with Delta 3.3.2 read
back the same rows as delta-rs, and could still DELETE from each table.
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
}
COLUMNS = {"dv": ["id", "amount", "label", "p"], "dv_part": ["id", "amount", "label", "p"],
           "cm_name": ["id", "total", "the label"], "dv_cm": ["id", "total", "label", "p"]}
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


@pytest.mark.parametrize("writer", [3, 4, 5, 6])
def test_a_legacy_writer_version_that_turns_on_unlisted_features_is_outside_the_modern_profile(writer):
    """Writer versions 3 to 6 imply features (check constraints, change data feed, generated or identity
    columns) that no feature list shows, so the modern profile can't check them and refuses them."""
    from types import SimpleNamespace
    table = SimpleNamespace(
        protocol=lambda: SimpleNamespace(min_reader_version=1, min_writer_version=writer, reader_features=None,
                                         writer_features=None),
        metadata=lambda: SimpleNamespace(configuration={}))
    found = P.problems(table)
    assert len(found) == 1 and found[0].startswith(f"writer version {writer}, which turns on check constraints")
    assert found[0].endswith(" without listing them")
    assert ("identity columns" in found[0]) == (writer == 6) and ("column mapping" in found[0]) == (writer >= 5)
    with pytest.raises(P.ProfileViolation, match=f"outside the modern Delta profile: writer version {writer}"):
        P.check_table(table)


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
