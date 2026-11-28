"""Delta MERGE and writes on this PC, checked as Delta checks them (WR-115).

Every test reads the table back after the write: its rows, its version, and the commit's entry in
the Delta log (operation and row counts). A refused write must leave all three as they were.

Expected behaviour is Delta 3.2.1's source (Fabric Runtime 1.3's Delta):
- MERGE: MergeIntoCommandBase.throwErrorOnMultipleMatches and ClassicMergeExecutor.findTouchedFiles.
  A table row that ON matches with more than one source row stops the MERGE
  (DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE), whatever the clauses' own conditions say,
  unless the MERGE only inserts or its only WHEN MATCHED is an unconditional DELETE.
- replaceWhere: WriteIntoDelta. An overwrite replaces the rows that match it, every row written must
  make it true (a NULL fails, CheckDeltaInvariant), an append ignores it, overwriteSchema doesn't
  apply with it, and partitionOverwriteMode=dynamic as a writer option with it is an error, while the
  session setting gives way to it.
None of this was run on Fabric or Databricks: UNVERIFIED live.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml, lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.localdata import LocalData
from fabric_desktop.notebook import Kernel

TABLE = pa.table({"id": [1, 2, 3], "region": ["EU", "US", "EU"], "amount": [10, 20, 30]})


def _state(path) -> tuple[int, list[dict], int]:
    table = DeltaTable(str(path))
    rows = sorted(table.to_pyarrow_table().to_pylist(), key=lambda r: (r["id"] is None, r["id"] or 0, str(r)))
    return table.version(), rows, len(table.history())


def _unchanged(path, before) -> None:
    assert _state(path) == before, "a refused write changed the table or its log"


def _committed(path, before, operation: str) -> dict:
    """The one commit a write made: the version moved by one, the log has one entry more, and its
    row counts agree with the rows read back. Returns that entry's metrics."""
    version, rows, entries = _state(path)
    assert (version, entries) == (before[0] + 1, before[2] + 1)
    entry = DeltaTable(str(path)).history(1)[0]
    assert entry["operation"] == operation and entry["version"] == version
    metrics = entry.get("operationMetrics") or {}
    if operation == "MERGE":
        assert len(rows) == len(before[1]) + metrics["num_target_rows_inserted"] - metrics["num_target_rows_deleted"]
    return metrics


# ---- MERGE: one table row, several source rows

SOURCE = pa.table({"id": [1, 1, 4], "region": ["EU", "EU", "APAC"], "amount": [100, 200, 400]})

REFUSED = {
    "update": "WHEN MATCHED THEN UPDATE SET *",
    "an update whose condition takes one of the two": "WHEN MATCHED AND s.amount = 100 THEN UPDATE SET *",
    "an update whose condition takes neither": "WHEN MATCHED AND s.amount = 999 THEN UPDATE SET *",
    "a conditional delete": "WHEN MATCHED AND s.amount > 0 THEN DELETE",
    "a delete and an update": "WHEN MATCHED AND s.amount = 100 THEN DELETE WHEN MATCHED THEN UPDATE SET *",
    "not matched by source alone": "WHEN NOT MATCHED BY SOURCE THEN DELETE",
    "insert and not matched by source": "WHEN NOT MATCHED THEN INSERT * WHEN NOT MATCHED BY SOURCE THEN DELETE",
}
ALLOWED = {
    "only inserts": ("WHEN NOT MATCHED THEN INSERT *", [1, 2, 3, 4]),
    "one unconditional delete": ("WHEN MATCHED THEN DELETE", [2, 3]),
    "one unconditional delete and an insert": ("WHEN MATCHED THEN DELETE WHEN NOT MATCHED THEN INSERT *", [2, 3, 4]),
    "one unconditional delete and not matched by source": (
        "WHEN MATCHED THEN DELETE WHEN NOT MATCHED BY SOURCE AND t.region = 'US' THEN DELETE", [3]),
}


@pytest.fixture
def delta(tmp_path):
    path = tmp_path / "sales"
    write_deltalake(str(path), TABLE)
    return path


def _apply(path, clauses: str, on: str = "t.id = s.id", source: pa.Table = SOURCE) -> dict:
    change = delta_dml.parse(f"MERGE INTO sales AS t USING src AS s ON {on} {clauses}")
    return delta_dml.apply(change, DeltaTable(str(path)), source, source.column_names)


@pytest.mark.parametrize("clauses", REFUSED.values(), ids=REFUSED.keys())
def test_a_merge_that_two_source_rows_match_on_one_row_stops_and_writes_nothing(delta, clauses):
    before = _state(delta)
    with pytest.raises(ValueError, match=r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\] More than one "
                                         r"source row matches the same row of sales \(source key 1\)"):
        _apply(delta, clauses)
    _unchanged(delta, before)


@pytest.mark.parametrize("clauses, ids", ALLOWED.values(), ids=ALLOWED.keys())
def test_the_merges_delta_lets_through_with_duplicates_write_one_commit(delta, clauses, ids):
    before = _state(delta)
    _apply(delta, clauses)
    metrics = _committed(delta, before, "MERGE")
    got = _state(delta)[1]
    assert sorted({r["id"] for r in got}) == ids
    if "INSERT" in clauses:
        assert metrics["num_target_rows_inserted"] == 1 and [r["amount"] for r in got if r["id"] == 4] == [400]
    if clauses.startswith("WHEN MATCHED THEN DELETE"):
        assert metrics["num_target_rows_deleted"] == len(before[1]) - len(got) + metrics["num_target_rows_inserted"]


def test_an_insert_only_merge_inserts_every_duplicate(delta):
    before = _state(delta)
    _apply(delta, "WHEN NOT MATCHED THEN INSERT *", source=pa.table({"id": [5, 5], "region": ["EU", "US"],
                                                                     "amount": [1, 2]}))
    assert _committed(delta, before, "MERGE")["num_target_rows_inserted"] == 2


def test_a_merge_with_an_on_that_isnt_only_keys_is_checked_too(delta):
    before = _state(delta)
    with pytest.raises(ValueError, match=r"DELTA_MULTIPLE_SOURCE_ROW_MATCHING.*\(2 source rows\)"):
        _apply(delta, "WHEN MATCHED THEN UPDATE SET amount = s.amount", on="t.id <= s.id AND s.id = 1")
    _unchanged(delta, before)
    # ON narrows the matches to one source row each: Delta lets it through, and so does this PC
    _apply(delta, "WHEN MATCHED THEN UPDATE SET amount = s.amount", on="t.id = s.id AND s.amount > 150")
    _committed(delta, before, "MERGE")
    assert [r["amount"] for r in _state(delta)[1]] == [200, 20, 30]


def test_source_rows_with_a_null_key_match_nothing(delta):
    before = _state(delta)
    source = pa.table({"id": pa.array([None, None], pa.int64()), "region": ["EU", "EU"], "amount": [1, 2]})
    _apply(delta, "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *", source=source)
    metrics = _committed(delta, before, "MERGE")
    assert (metrics["num_target_rows_updated"], metrics["num_target_rows_inserted"]) == (0, 2)


def test_one_source_row_may_change_several_table_rows(tmp_path):
    path = tmp_path / "sales"
    write_deltalake(str(path), pa.table({"id": [1, 1], "region": ["EU", "US"], "amount": [10, 11]}))
    before = _state(path)
    _apply(path, "WHEN MATCHED THEN UPDATE SET amount = s.amount",
           source=pa.table({"id": [1], "region": ["EU"], "amount": [5]}))
    assert _committed(path, before, "MERGE")["num_target_rows_updated"] == 2
    assert [r["amount"] for r in _state(path)[1]] == [5, 5]


def test_a_streamed_source_is_refused_unless_its_caller_checked_it(delta):
    change = delta_dml.parse("MERGE INTO sales AS t USING src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")
    before = _state(delta)
    with pytest.raises(ValueError, match="checked for DELTA_MULTIPLE_SOURCE_ROW_MATCHING"):
        delta_dml.apply(change, DeltaTable(str(delta)), SOURCE.to_reader(), SOURCE.column_names)
    _unchanged(delta, before)


def test_not_matched_by_source_updates_and_deletes_and_the_log_counts_them(delta):
    before = _state(delta)
    _apply(delta, "WHEN MATCHED THEN UPDATE SET amount = s.amount "
                  "WHEN NOT MATCHED BY SOURCE AND t.region = 'US' THEN DELETE "
                  "WHEN NOT MATCHED BY SOURCE THEN UPDATE SET amount = 0",
           source=pa.table({"id": [1], "region": ["EU"], "amount": [100]}))
    metrics = _committed(delta, before, "MERGE")
    assert (metrics["num_target_rows_updated"], metrics["num_target_rows_deleted"]) == (2, 1)
    assert _state(delta)[1] == [{"id": 1, "region": "EU", "amount": 100}, {"id": 3, "region": "EU", "amount": 0}]


# ---- the notebook's spark.sql, DeltaTable builder and writer, on a lakehouse table


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "sales"), TABLE)
    write_deltalake(str(root / "dbo" / "parts"), TABLE, partition_by=["region"])
    yield root
    lakehouse.attach(None)


def run(code: str) -> tuple[str, str]:
    out = Kernel().run("df = spark.createDataFrame([(1, 'EU', 100), (1, 'EU', 200), (4, 'APAC', 400)], "
                       "['id', 'region', 'amount'])\ndf.createOrReplaceTempView('src')\n" + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


@pytest.mark.parametrize("code", [
    "spark.sql('MERGE INTO dbo.sales t USING src s ON t.id = s.id WHEN MATCHED AND s.amount = 100 THEN UPDATE SET *')",
    "spark.sql('MERGE INTO dbo.sales t USING src s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN DELETE')",
    ("from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').alias('t').merge(df.alias('s'), "
     "'t.id = s.id').whenMatchedUpdateAll().execute()"),
], ids=["spark.sql", "spark.sql by source", "DeltaTable builder"])
def test_the_notebook_merge_paths_stop_on_two_source_rows(lake, code):
    before = _state(lake / "dbo" / "sales")
    status, text = run(code)
    assert status == "error" and "DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE" in text, text
    _unchanged(lake / "dbo" / "sales", before)


def test_auto_merge_adds_the_new_column_in_the_merges_one_commit(lake):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run("spark.conf.set('spark.databricks.delta.schema.autoMerge.enabled', 'true')\n"
                       "spark.createDataFrame([(2, 'US', 25, 'x'), (5, 'US', 50, 'y')], ['id', 'region', 'amount', "
                       "'note']).createOrReplaceTempView('n')\n"
                       "spark.sql('MERGE INTO dbo.sales t USING n s ON t.id = s.id WHEN MATCHED THEN UPDATE SET * "
                       "WHEN NOT MATCHED THEN INSERT *')")
    assert status == "ok", text
    metrics = _committed(path, before, "MERGE")
    assert (metrics["num_target_rows_updated"], metrics["num_target_rows_inserted"]) == (1, 1)
    assert [(r["id"], r["note"]) for r in _state(path)[1]] == [(1, None), (2, "x"), (3, None), (5, "y")]


def test_replace_where_replaces_only_the_matching_rows_in_one_commit(lake):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run("spark.createDataFrame([(7, 'EU', 70)], ['id', 'region', 'amount']).write.mode('overwrite')"
                       ".option('replaceWhere', \"REGION = 'EU'\").saveAsTable('dbo.sales')")
    assert status == "ok", text
    metrics = _committed(path, before, "WRITE")
    assert (metrics["num_added_rows"], metrics["num_removed_files"]) == (1, 1)
    assert _state(path)[1] == [{"id": 2, "region": "US", "amount": 20}, {"id": 7, "region": "EU", "amount": 70}]


@pytest.mark.parametrize("rows", ["[(7, 'US', 70)]", "[(7, None, 70)]"], ids=["another region", "a NULL"])
def test_replace_where_stops_when_a_row_written_doesnt_match(lake, rows):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run(f"spark.createDataFrame({rows}, 'id long, region string, amount long').write"
                       ".mode('overwrite').option('replaceWhere', \"region = 'EU'\").saveAsTable('dbo.sales')")
    assert status == "error" and "[DELTA_REPLACE_WHERE_MISMATCH]" in text and "1 of the 1 rows doesn't match it" in text, text
    _unchanged(path, before)


def test_replace_where_on_a_partition_column_replaces_that_partition(lake):
    path = lake / "dbo" / "parts"
    before = _state(path)
    status, text = run("spark.createDataFrame([(8, 'US', 80), (9, 'US', 90)], ['id', 'region', 'amount']).write"
                       ".mode('overwrite').option('replaceWhere', \"region = 'US'\").saveAsTable('dbo.parts')")
    assert status == "ok", text
    assert _committed(path, before, "WRITE")["num_added_rows"] == 2
    assert [r["id"] for r in _state(path)[1]] == [1, 3, 8, 9]


def test_replace_where_on_an_append_is_ignored_with_a_note(lake):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run("spark.createDataFrame([(7, 'US', 70)], ['id', 'region', 'amount']).write.mode('append')"
                       ".option('replaceWhere', \"region = 'EU'\").saveAsTable('dbo.sales')")
    assert status == "ok" and "replaceWhere only applies to an overwrite" in text, text
    assert _committed(path, before, "WRITE")["num_added_rows"] == 1 and len(_state(path)[1]) == 4


def test_replace_where_and_a_dynamic_overwrite_option_stop_together(lake):
    path = lake / "dbo" / "parts"
    before = _state(path)
    status, text = run("df.write.mode('overwrite').option('partitionOverwriteMode', 'dynamic')"
                       ".option('replaceWhere', \"region = 'EU'\").saveAsTable('dbo.parts')")
    assert status == "error" and "[DELTA_REPLACE_WHERE_WITH_DYNAMIC_PARTITION_OVERWRITE]" in text, text
    _unchanged(path, before)


def test_on_a_table_without_partitions_the_dynamic_option_gives_way_to_replace_where(lake):
    """Delta ignores dynamic partition overwrite on a table without partitions, before it looks at replaceWhere."""
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run("spark.createDataFrame([(7, 'EU', 70)], ['id', 'region', 'amount']).write.mode('overwrite')"
                       ".option('partitionOverwriteMode', 'dynamic').option('replaceWhere', \"region = 'EU'\")"
                       ".saveAsTable('dbo.sales')")
    assert status == "ok", text
    _committed(path, before, "WRITE")
    assert [r["id"] for r in _state(path)[1]] == [2, 7]


def test_replace_where_wins_over_the_sessions_dynamic_overwrite(lake):
    path = lake / "dbo" / "parts"
    before = _state(path)
    status, text = run("spark.conf.set('spark.sql.sources.partitionOverwriteMode', 'dynamic')\n"
                       "spark.createDataFrame([(7, 'EU', 70)], ['id', 'region', 'amount']).write.mode('overwrite')"
                       ".option('replaceWhere', 'amount >= 10').saveAsTable('dbo.parts')")
    assert status == "ok", text
    _committed(path, before, "WRITE")
    assert _state(path)[1] == [{"id": 7, "region": "EU", "amount": 70}]  # every row matched, in every partition


@pytest.mark.parametrize("condition, reason", [
    ("nope = 1", "has no column nope"),
    ("id IN (SELECT id FROM src)", "no subquery"),
    ("1 = 1", "must compare the table's columns"),
    ("region = 'EU'; DROP TABLE dbo.sales", "must be a single condition"),
    ("amount > 0;", None),
    ("no_such_fn(region) = 1", "couldn't check the rows against it"),
])
def test_a_replace_where_this_pc_cant_honor_is_refused_and_writes_nothing(lake, condition, reason):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run(f"df.write.mode('overwrite').option('replaceWhere', {condition!r}).saveAsTable('dbo.sales')")
    if reason is None:  # one statement with a trailing ";" is one condition: here every row matches
        assert status == "ok", text
        return
    assert status == "error" and reason in text, text
    _unchanged(path, before)


def test_replace_where_keeps_the_schema_with_overwrite_schema(lake):
    path = lake / "dbo" / "sales"
    before = _state(path)
    status, text = run("spark.createDataFrame([(7, 'EU', 70)], ['id', 'region', 'amount']).write.mode('overwrite')"
                       ".option('overwriteSchema', 'true').option('replaceWhere', \"region = 'EU'\")"
                       ".saveAsTable('dbo.sales')")
    assert status == "ok" and "overwriteSchema doesn't apply with replaceWhere" in text, text
    _committed(path, before, "WRITE")


def test_replace_where_on_a_new_table_writes_every_row(lake):
    status, text = run("df.write.mode('overwrite').option('replaceWhere', \"region = 'EU'\").saveAsTable('dbo.fresh')")
    assert status == "ok", text
    version, rows, entries = _state(lake / "dbo" / "fresh")
    assert (version, len(rows), entries) == (0, 3, 1)


def test_a_dynamic_overwrite_replaces_only_the_partitions_written(lake):
    path = lake / "dbo" / "parts"
    before = _state(path)
    status, text = run("spark.createDataFrame([(9, 'US', 90)], ['id', 'region', 'amount']).write.mode('overwrite')"
                       ".option('partitionOverwriteMode', 'dynamic').saveAsTable('dbo.parts')")
    assert status == "ok", text
    metrics = _committed(path, before, "WRITE")
    assert (metrics["num_added_rows"], metrics["num_removed_files"]) == (1, 1)
    assert [(r["id"], r["region"]) for r in _state(path)[1]] == [(1, "EU"), (3, "EU"), (9, "US")]


# ---- the local engine's MERGE (SQL on a project's Delta tables)


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("W")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT * FROM (VALUES (1, 'EU', 10), (2, 'US', 20)) t(id, region, amount)")
    e.execute("CREATE TABLE src AS SELECT * FROM (VALUES (1, 100), (1, 200)) t(id, amount)")
    yield e
    local.close_all()


@pytest.mark.parametrize("clauses, refused", [
    ("WHEN MATCHED AND s.amount = 100 THEN UPDATE SET amount = s.amount", True),
    ("WHEN NOT MATCHED BY SOURCE AND t.region = 'US' THEN DELETE", True),
    ("WHEN MATCHED THEN DELETE", False),
])
def test_the_local_engines_merge_follows_deltas_rule(engine, clauses, refused):
    path = engine.tables_dir / "dbo" / "sales"
    before = _state(path)
    statement = f"MERGE INTO sales AS t USING src AS s ON t.id = s.id {clauses}"
    if refused:
        with pytest.raises(ValueError, match="DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE"):
            engine.execute(statement, dialect="spark")
        _unchanged(path, before)
    else:
        engine.execute(statement, dialect="spark")
        assert _committed(path, before, "MERGE")["num_target_rows_deleted"] == 1
        assert [r["id"] for r in _state(path)[1]] == [2]


# ---- an Academy exercise's DeltaTable builder (delta-rs in the learner's process)


@pytest.mark.parametrize("clauses, refused", [
    (lambda m: m.whenMatchedUpdateAll("s.amount = 100"), True),
    (lambda m: m.whenNotMatchedBySourceDelete(), True),
    (lambda m: m.whenMatchedDelete(), False),
    (lambda m: m.whenNotMatchedInsertAll(), False),
], ids=["conditional update", "by source", "unconditional delete", "insert only"])
def test_the_academy_merge_builder_follows_deltas_rule(delta, clauses, refused):
    import duckdb

    from fabric_desktop.academy import pyspark_delta

    class Source:
        relation = duckdb.connect().from_arrow(SOURCE).set_alias("s")
    builder = clauses(pyspark_delta.DeltaTable("sales", delta, "t").merge(Source(), "t.id = s.id"))
    before = _state(delta)
    if refused:
        with pytest.raises(ValueError, match="DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE"):
            builder.execute()
        _unchanged(delta, before)
    else:
        builder.execute()
        _committed(delta, before, "MERGE")


@pytest.mark.parametrize("on, updated", [("t.id = s.id AND nvl(t.amount, 0) >= 0", 1), ("t.id <=> s.id", 1)])
def test_the_academy_builder_reads_a_spark_on(tmp_path, on, updated):
    """Spark SQL in ON (nvl, <=>) is read as Spark's for the check, as delta-rs runs it for the MERGE."""
    import duckdb

    from fabric_desktop.academy import pyspark_delta
    path = tmp_path / "sales"
    write_deltalake(str(path), TABLE)

    class Source:
        relation = duckdb.connect().from_arrow(pa.table({"id": [2], "region": ["US"], "amount": [25]})).set_alias("s")
    before = _state(path)
    pyspark_delta.DeltaTable("sales", path, "t").merge(Source(), on).whenMatchedUpdateAll().execute()
    assert _committed(path, before, "MERGE")["num_target_rows_updated"] == updated


def test_the_academy_builder_refuses_an_on_it_cant_check(delta):
    import duckdb

    from fabric_desktop.academy import pyspark_delta

    class Source:
        relation = duckdb.connect().from_arrow(SOURCE).set_alias("s")
    before = _state(delta)
    with pytest.raises(ValueError, match="couldn't check that each of its rows matches at most one source row"):
        pyspark_delta.DeltaTable("sales", delta, "t").merge(Source(), "t.id = no_such_fn(s.id)") \
            .whenMatchedUpdateAll().execute()
    _unchanged(delta, before)


@pytest.mark.parametrize("on", ["t.id = s.id AND nvl(t.amount, 0) >= 0", "t.id <=> s.id"])
def test_a_spark_on_is_checked_on_the_sql_path_too(delta, on):
    before = _state(delta)
    with pytest.raises(ValueError, match="DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE"):
        _apply(delta, "WHEN MATCHED THEN UPDATE SET *", on=on)
    _unchanged(delta, before)
    _apply(delta, "WHEN MATCHED THEN UPDATE SET *", on=on, source=SOURCE.slice(1))
    assert _committed(delta, before, "MERGE")["num_target_rows_updated"] == 1


def test_the_academy_builder_refuses_an_on_of_two_statements(delta):
    import duckdb

    from fabric_desktop.academy import pyspark_delta

    class Source:
        relation = duckdb.connect().from_arrow(SOURCE.slice(2)).set_alias("s")
    before = _state(delta)
    with pytest.raises(ValueError, match="couldn't check"):
        pyspark_delta.DeltaTable("sales", delta, "t").merge(Source(), "t.id = s.id; SELECT 1") \
            .whenMatchedUpdateAll().execute()
    _unchanged(delta, before)
