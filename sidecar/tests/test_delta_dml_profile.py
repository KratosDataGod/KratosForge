"""PB-015 criterion 2: a table that spark.sql MERGE INTO, UPDATE or DELETE, or the DeltaTable merge,
update and delete builders, wrote on this PC passes the Fabric Delta profile check.

Each test writes through one path and then runs the profile check (delta_profile.problems and
check_table, with the default profile and readers) on the table it left, and checks what the profile
rests on: the protocol's versions and table features and the table's deletion vector and column
mapping settings are the ones the table had before, and every file the change added has stats
(numRecords, minValues, maxValues, nullCount) named as the table's files name their columns (the
physical names, for a column-mapped table). The tables are delta-rs's own, made on this PC with
saveAsTable under the default (modern) profile, and Spark's (tests/fixtures/delta_spark) with
deletion vectors and column mapping by name. Whether Fabric reads the tables is not run here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_profile as P
from fabric_desktop import delta_read, lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"

# Every table has an id (long) and an integer column; most a label and a partition-like p.
COLUMNS = {
    "plain": ["id", "amount", "label", "p"], "plain_part": ["id", "amount", "label", "p"],
    "made": ["id", "amount", "label", "p"], "made_part": ["id", "amount", "label", "p"],
    "dv": ["id", "amount", "label", "p"], "dv_part": ["id", "amount", "label", "p"],
    "cm_name": ["id", "total", "the label"], "dv_cm": ["id", "total", "label", "p"],
    "cm_legacy": ["id", "total", "label", "p"],
}
SPARK_TABLES = ["dv", "dv_part", "cm_name", "dv_cm", "cm_legacy"]
TABLES = ["plain", "plain_part", "made", "made_part", *SPARK_TABLES]
_ROWS = [(i, i * 10, f"n{i}", "a" if i < 5 else "b") for i in range(10)]
_ARROW = pa.schema([("id", pa.int64()), ("amount", pa.int32()), ("label", pa.string()), ("p", pa.string())])


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield root
    lakehouse.attach(None)


def run(code: str) -> str:
    out = Kernel().run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def make(lake: Path, name: str) -> Path:
    """The table ``name`` in the lakehouse, as its kind makes it."""
    folder = lake / "dbo" / name
    if name in SPARK_TABLES:
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIXTURES / name, folder)
    elif name.startswith("plain"):
        write_deltalake(str(folder), pa.Table.from_pylist([dict(zip(_ARROW.names, r, strict=True)) for r in _ROWS],
                                                          schema=_ARROW),
                        partition_by=["p"] if name.endswith("_part") else None)
    else:  # saveAsTable on this PC, with the default profile (no profile named)
        part = ".partitionBy('p')" if name.endswith("_part") else ""
        run("from pyspark.sql import functions as F\n"
            f"rows = [(i, i * 10, f'n{{i}}', 'a' if i < 5 else 'b') for i in range(10)]\n"
            "df = spark.createDataFrame(rows, ['id', 'amount', 'label', 'p'])"
            ".withColumn('amount', F.col('amount').cast('int'))\n"
            f"df.write.format('delta'){part}.saveAsTable('dbo.{name}')")
    return folder


def _q(column: str) -> str:
    return f"`{column}`"


def source_view(name: str) -> str:
    """A temp view ``src``: id 0 (in every table) with a new value, id 4 (in every table) to delete, id 100 (new)."""
    cols = COLUMNS[name]
    rows = [(0, -1, "n0", "a"), (4, 40, "n4", "a"), (100, 1000, "n100", "a")]
    data = [r[:len(cols)] + (r[0] == 4,) for r in rows]
    return (f"from pyspark.sql import functions as F\nspark.createDataFrame({data!r}, {[*cols, 'gone']!r})"
            f".withColumn({cols[1]!r}, F.col({cols[1]!r}).cast('int')).createOrReplaceTempView('src')\n")


def _insert(name: str) -> str:
    cols = ", ".join(_q(c) for c in COLUMNS[name])
    values = ", ".join(f"s.{_q(c)}" for c in COLUMNS[name])
    return f"INSERT ({cols}) VALUES ({values})"


def sql_code(name: str, op: str) -> str:
    cols = COLUMNS[name]
    key, value = _q(cols[0]), _q(cols[1])
    merge = f"MERGE INTO dbo.{name} AS t USING src AS s ON t.{key} = s.{key} "
    sql = {
        "merge_update": merge + f"WHEN MATCHED AND NOT s.gone THEN UPDATE SET t.{value} = s.{value}",
        "merge_delete": merge + "WHEN MATCHED AND s.gone THEN DELETE",
        "merge_insert": merge + f"WHEN NOT MATCHED THEN {_insert(name)}",
        "merge_all": merge + "WHEN MATCHED AND s.gone THEN DELETE "
                     f"WHEN MATCHED THEN UPDATE SET t.{value} = s.{value} WHEN NOT MATCHED THEN {_insert(name)}",
        "update": f"UPDATE dbo.{name} SET {value} = -1 WHERE {key} = 0",
        "delete": f"DELETE FROM dbo.{name} WHERE {key} = 4",
    }[op]
    return source_view(name) + f"spark.sql({sql!r})"


def api_code(name: str, op: str) -> str:
    cols = COLUMNS[name]
    key, value = _q(cols[0]), _q(cols[1])
    table = f"DeltaTable.forName(spark, 'dbo.{name}')"
    merge = f"{table}.alias('t').merge(spark.table('src').alias('s'), 't.{key} = s.{key}')"
    insert = "{" + ", ".join(f"{_q(c)!r}: 's.{_q(c)}'" for c in cols) + "}"
    code = {
        "merge_update": merge + f".whenMatchedUpdate('NOT s.gone', {{{value!r}: 's.{value}'}}).execute()",
        "merge_delete": merge + ".whenMatchedDelete('s.gone').execute()",
        "merge_insert": merge + f".whenNotMatchedInsert(values={insert}).execute()",
        "merge_all": merge + f".whenMatchedDelete('s.gone').whenMatchedUpdate(set={{{value!r}: 's.{value}'}})"
                     f".whenNotMatchedInsert(values={insert}).execute()",
        "update": f"{table}.update('{key} = 0', {{{value!r}: '-1'}})",
        "delete": f"{table}.delete('{key} = 4')",
    }[op]
    return source_view(name) + "from delta.tables import DeltaTable\n" + code


# The ids each change leaves, from the ids the table had, and the value id 0 ends with.
EFFECT = {"merge_update": (set(), set(), -1), "merge_delete": (set(), {4}, 0), "merge_insert": ({100}, set(), 0),
          "merge_all": ({100}, {4}, -1), "update": (set(), set(), -1), "delete": (set(), {4}, 0)}
OPERATION = {"update": "UPDATE", "delete": "DELETE"}  # every other change is a MERGE


def _state(folder: Path) -> tuple[int, tuple[int, int, tuple[str, ...], tuple[str, ...]], dict[str, str]]:
    t = DeltaTable(str(folder))
    protocol = t.protocol()
    config = {k: v for k, v in (t.metadata().configuration or {}).items()
              if k in ("delta.enableDeletionVectors", "delta.columnMapping.mode")}
    return t.version(), (protocol.min_reader_version, protocol.min_writer_version,
                         tuple(sorted(protocol.reader_features or [])),
                         tuple(sorted(protocol.writer_features or []))), config


def _adds(folder: Path, after: int) -> list[dict]:
    adds = []
    for commit in sorted((folder / "_delta_log").glob("*.json")):
        if int(commit.stem) > after:
            adds += [a["add"] for a in map(json.loads, commit.read_text(encoding="utf-8").splitlines()) if "add" in a]
    return adds


def assert_passes_profile(folder: Path, before: tuple, operation: str) -> None:
    """The profile check on the table a change just wrote, and what it rests on."""
    version, protocol, config = before
    table = DeltaTable(str(folder))
    assert table.version() == version + 1
    assert table.history(1)[0]["operation"] == operation
    assert P.DEFAULT_PROFILE is P.MODERN
    assert P.problems(table) == []
    P.check_table(table)  # the default profile and readers: the SQL analytics endpoint and Direct Lake
    P.check_table(table, committed=True)
    assert P.note_after_write(table) == []
    _version, protocol_now, config_now = _state(folder)
    assert protocol_now == protocol, "the change moved the protocol or its table features"
    assert config_now == config, "the change turned deletion vectors or column mapping on or off"
    if protocol == (1, 2, (), ()):  # a table every Fabric reader reads stays one
        assert P.problems(table, P.CONSERVATIVE) == []
    fields = json.loads(table.schema().to_json())["fields"]
    partitions = set(table.metadata().partition_columns)
    physical = {f["name"]: f["metadata"].get("delta.columnMapping.physicalName", f["name"]) for f in fields}
    expected = {physical[n] for n in physical if n not in partitions}
    for add in _adds(folder, version):
        assert add.get("stats"), f"{add['path']} has no stats"
        stats = json.loads(add["stats"])
        assert stats["numRecords"] > 0, add["path"]
        assert set(stats["nullCount"]) == expected, add["path"]
        assert set(stats["minValues"]) == set(stats["maxValues"]) == expected, add["path"]
        assert set(add.get("partitionValues") or {}) == {physical[n] for n in partitions}, add["path"]


def _ids(folder: Path) -> dict[int, int]:
    rows = delta_read.arrow(DeltaTable(str(folder))).to_pylist()
    return {r["id"]: next(v for k, v in r.items() if k in ("amount", "total")) for r in rows}


def _check(lake: Path, name: str, op: str, code: str) -> None:
    folder = make(lake, name)
    start = _ids(folder)
    before = _state(folder)
    run(code)
    assert_passes_profile(folder, before, OPERATION.get(op, "MERGE"))
    added, removed, zero = EFFECT[op]
    after = _ids(folder)
    assert set(after) == (set(start) | added) - removed
    assert after[0] == zero


OPS = ["merge_update", "merge_delete", "merge_insert", "merge_all", "update", "delete"]


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("name", TABLES)
def test_spark_sql_merge_update_and_delete_write_a_table_inside_the_profile(lake, name, op):
    _check(lake, name, op, sql_code(name, op))


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("name", TABLES)
def test_the_delta_table_builders_write_a_table_inside_the_profile(lake, name, op):
    _check(lake, name, op, api_code(name, op))


def test_a_table_saved_with_the_default_profile_is_fabric_safe_before_and_after_each_change(lake):
    """saveAsTable on this PC with no profile named makes a table every Fabric reader reads, and a run of
    MERGE, UPDATE and DELETE (SQL and builders) one after another keeps it so, commit by commit."""
    folder = make(lake, "made_part")
    assert _state(folder)[1] == (1, 2, (), ())
    for code, op in [(sql_code("made_part", "merge_all"), "MERGE"), (sql_code("made_part", "update"), "UPDATE"),
                     (api_code("made_part", "delete").replace("= 4", "= 9"), "DELETE"),
                     (api_code("made_part", "merge_update"), "MERGE")]:
        before = _state(folder)
        run(code)
        assert_passes_profile(folder, before, op)
        assert P.problems(DeltaTable(str(folder)), P.CONSERVATIVE) == []


@pytest.mark.parametrize("name", ["dv", "dv_part", "dv_cm"])
def test_a_merge_into_a_table_with_deletion_vectors_keeps_sparks_deleted_rows_deleted(lake, name):
    """MERGE checks each table row matches one source row before delta-rs runs it; on a table with
    deletion vectors that check reads the rows through delta_read, which applies them."""
    folder = make(lake, name)
    gone = {r[0] for r in _ROWS} - set(_ids(folder))
    assert gone and delta_read.deleted_rows(DeltaTable(str(folder))) > 0
    before = _state(folder)
    run(sql_code(name, "merge_all"))
    assert_passes_profile(folder, before, "MERGE")
    assert not gone & set(_ids(folder))
