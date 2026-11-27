"""More DeltaTable-in-an-exercise tests, from a mutation run: SQL text or a Column, the merge source
and its alias, forName, forPath and isDeltaTable, update and delete, every MERGE clause, Delta's
one-match rule and when it is skipped, and what isn't here. The tables are the test's own, written
with delta-rs in a temporary folder; the spark session is a stand-in, so no learner code runs."""

from __future__ import annotations

import types

import deltalake
import duckdb
import pyarrow as pa
import pytest
from deltalake import write_deltalake
from duckdb.experimental.spark.sql.functions import col

from fabric_desktop.academy import pyspark_delta as pd
from fabric_desktop.academy import pyspark_shim

MULTI = "DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE"


@pytest.fixture
def con():
    c = duckdb.connect()
    yield c
    c.close()


@pytest.fixture
def spark(tmp_path, con):
    path = pyspark_shim.table_path(tmp_path, "silver.t")
    path.parent.mkdir(parents=True)
    write_deltalake(str(path), pa.table({"id": [1, 2], "v": [10, 20]}))
    return types.SimpleNamespace(work=tmp_path, conn=con, table=lambda name: f"table {name}")


def rows(spark, name="silver.t"):
    t = deltalake.DeltaTable(str(pyspark_shim.table_path(spark.work, name)))
    return sorted((r["id"], r["v"]) for r in t.to_pyarrow_table().to_pylist())


def source(con, alias=None, **data):
    rel = con.from_arrow(pa.table(data or {"id": [2, 3], "v": [200, 300]}))
    return types.SimpleNamespace(relation=rel.set_alias(alias) if alias else rel)


def table(spark, alias="t"):
    t = pd.DeltaTable.forName(spark, "silver.t")
    return t.alias(alias) if alias else t


# ---- SQL text, Columns and the source

def test_sql_is_text_or_a_columns_expression():
    assert pd._sql("a = 1") == "a = 1"
    doubled = col("v") * 2
    assert pd._sql(doubled) == str(doubled.expr) and "* 2" in pd._sql(doubled)
    with pytest.raises(TypeError, match="^Expected SQL text or a Column, got int.$"):
        pd._sql(5)


def test_the_source_must_be_a_dataframe_and_gets_a_default_alias(con):
    with pytest.raises(TypeError, match="needs a spark DataFrame as its source"):
        pd._source(object())
    data, alias = pd._source(source(con))
    assert alias == "source" and data.num_rows == 2
    assert pd._source(source(con, "s"))[1] == "s"
    assert pd._source(source(con, "unnamed_relation_1"))[1] == "source"
    blank = types.SimpleNamespace(alias="", arrow=lambda: pa.table({"a": [1]}))
    assert pd._source(types.SimpleNamespace(relation=blank)) == (pa.table({"a": [1]}), "source")
    named = types.SimpleNamespace(alias="x", arrow=lambda: pa.table({"a": [2]}), to_arrow_table=None)
    assert pd._source(types.SimpleNamespace(relation=named)) == (pa.table({"a": [2]}), "x")


# ---- finding a table

def test_for_name_finds_the_exercises_table_or_says_there_is_none(spark, monkeypatch):
    t = pd.DeltaTable.forName(spark, "silver.t")
    assert (t._name, t._path, t._alias) == ("silver.t", pyspark_shim.table_path(spark.work, "silver.t"), "")
    with pytest.raises(ValueError, match="^There's no table silver.nope$"):
        pd.DeltaTable.forName(spark, "silver.nope")
    monkeypatch.setattr(pd.DeltaTable, "_session", spark)
    assert pd.DeltaTable.forName(None, "silver.t")._name == "silver.t"


@pytest.mark.parametrize("path", ["Tables/silver/t", "/lakehouse/default/Tables/silver/t", "lakehouse/default/Tables/silver/t/",
                                  "Tables\\silver\\t", "Tables//silver/t"])
def test_for_path_takes_a_tables_path(spark, path):
    assert pd.DeltaTable.forPath(spark, path)._name == "silver.t"


def test_for_path_refuses_anything_else(spark):
    with pytest.raises(ValueError, match=r"^In an exercise, DeltaTable.forPath takes a Tables/ path"):
        pd.DeltaTable.forPath(spark, "Files/silver/t")


def test_is_delta_table_by_name_or_path(spark):
    assert pd.DeltaTable.isDeltaTable(spark, "silver.t") is True
    assert pd.DeltaTable.isDeltaTable(spark, "Tables/silver/t") is True
    assert pd.DeltaTable.isDeltaTable(spark, "silver.nope") is False
    assert pd.DeltaTable.isDeltaTable(spark, "Files/x") is False


def test_alias_keeps_the_table_and_to_df_reads_it(spark, monkeypatch):
    t = table(spark, "x")
    assert (t._name, t._alias) == ("silver.t", "x")
    monkeypatch.setattr(pd.DeltaTable, "_session", spark)
    assert t.toDF() == "table silver.t"


def test_history_lists_each_commit(spark, monkeypatch):
    monkeypatch.setattr(pd.DeltaTable, "_session", spark)
    table(spark).delete("id = 1")
    got = table(spark).history().relation.fetchall()
    assert [r[0] for r in got] == [1, 0] and got[0][2] == "DELETE" and all(r[1] is not None for r in got)
    assert len(table(spark).history(1).relation.fetchall()) == 1


# ---- update and delete

def test_update_sets_columns_where_the_condition_holds(spark):
    with pytest.raises(ValueError, match="^update needs the columns to set"):
        table(spark).update("id = 1", {})
    table(spark).update("id = 1", {"v": "v + 1"})
    assert rows(spark) == [(1, 11), (2, 20)]
    table(spark).updateExpr(set={"v": col("v") * 2})
    assert rows(spark) == [(1, 22), (2, 40)]
    with pytest.raises(ValueError):
        table(spark).updateExpr("id = 1")


def test_delete_with_or_without_a_condition(spark):
    table(spark).delete(col("id") == 1)
    assert rows(spark) == [(2, 20)]
    table(spark).delete()
    assert rows(spark) == []


@pytest.mark.parametrize("what", ["optimize", "vacuum", "restoreToVersion", "restoreToTimestamp"])
def test_what_delta_spark_has_and_this_doesnt_says_so(spark, what):
    with pytest.raises(NotImplementedError, match=f"^DeltaTable.{what} isn't in an Academy exercise"):
        getattr(table(spark), what)(1)


# ---- MERGE

def test_merge_updates_and_inserts(spark, con):
    table(spark).merge(source(con), "t.id = source.id").whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
    assert rows(spark) == [(1, 10), (2, 200), (3, 300)]


def test_merge_targets_the_tables_own_name_without_an_alias(spark, con):
    table(spark, None).merge(source(con, "s"), "t.id = s.id").whenMatchedUpdate(set={"v": "s.v + 1"}).execute()
    assert rows(spark) == [(1, 10), (2, 201)]


def test_merge_clauses_with_conditions_and_values(spark, con):
    (table(spark).merge(source(con, "s", id=[1, 2, 3], v=[0, 0, 0]), col("t.id") == col("s.id"))
     .whenMatchedUpdate("t.id = 1", {"v": "99"}).whenMatchedDelete("t.id = 2")
     .whenNotMatchedInsert("s.id = 3", {"id": "s.id", "v": "7"}).execute())
    assert rows(spark) == [(1, 99), (3, 7)]


def test_merge_by_source_clauses(spark, con):
    (table(spark).merge(source(con, "s", id=[1], v=[0]), "t.id = s.id")
     .whenNotMatchedBySourceUpdate("t.id = 2", {"v": "5"}).execute())
    assert rows(spark) == [(1, 10), (2, 5)]
    table(spark).merge(source(con, "s", id=[1], v=[0]), "t.id = s.id").whenNotMatchedBySourceDelete().execute()
    assert rows(spark) == [(1, 10)]


@pytest.mark.parametrize(("method", "args"), [("whenMatchedUpdate", ()), ("whenNotMatchedInsert", ()),
                                              ("whenNotMatchedBySourceUpdate", ())])
def test_a_clause_that_sets_needs_its_columns(spark, con, method, args):
    with pytest.raises(ValueError, match=f"^{method} needs the columns to set"):
        getattr(table(spark).merge(source(con), "t.id = source.id"), method)(*args)


def test_a_merge_without_clauses_or_with_schema_evolution_isnt_run(spark, con):
    with pytest.raises(ValueError, match="no whenMatched or whenNotMatched clause"):
        table(spark).merge(source(con), "t.id = source.id").execute()
    with pytest.raises(NotImplementedError, match="schema evolution"):
        table(spark).merge(source(con), "t.id = source.id").withSchemaEvolution()


def test_the_one_match_check_reads_text_as_spark_and_a_column_as_duckdb(spark, con):
    b = table(spark).merge(source(con), "t.id = source.id")
    assert b._on == "t.id = source.id" and b._check_on is None
    on = col("t.id") == col("source.id")
    c = table(spark).merge(source(con), on)
    assert c._check_on == c._on == str(on.expr)
    assert b.whenMatchedDelete("t.v > 1") is b and b._clauses == [("when_matched_delete", {"predicate": "t.v > 1"})]


@pytest.mark.parametrize("on", ["t.id = s.id", col("t.id") == col("s.id")], ids=["text", "column"])
def test_two_source_rows_for_one_table_row_stop_the_merge(spark, con, on):
    dup = source(con, "s", id=[2, 2], v=[1, 2])
    with pytest.raises(ValueError, match=r"^\[" + MULTI + r"\] Cannot perform Merge"):
        table(spark).merge(dup, on).whenMatchedUpdateAll().execute()
    with pytest.raises(ValueError, match=MULTI):
        table(spark).merge(dup, on).whenMatchedDelete("s.v = 1").execute()
    assert rows(spark) == [(1, 10), (2, 20)]


def test_only_inserting_or_an_unconditional_delete_skips_the_check(spark, con):
    dup = source(con, "s", id=[2, 2, 3], v=[1, 2, 3])
    table(spark).merge(dup, "t.id = s.id").whenNotMatchedInsertAll().execute()
    assert rows(spark) == [(1, 10), (2, 20), (3, 3)]
    table(spark).merge(dup, "t.id = s.id").whenMatchedDelete().execute()
    assert rows(spark) == [(1, 10)]


def test_a_condition_the_check_cant_read_stops_the_merge_in_words(spark, con):
    with pytest.raises(ValueError) as err:
        table(spark).merge(source(con, "s"), "t.id = s.nope").whenMatchedUpdateAll().execute()
    said = str(err.value)
    assert said.startswith("The merge into silver.t isn't run here, because this PC couldn't check")
    assert "The check stopped on: " in said and said.endswith("such as t.key = s.key AND ...")
    problem = said.split("The check stopped on: ")[1].split(". Write the condition")[0]
    assert " " in problem   # the error's own first line, not the bare name of its type
    with pytest.raises(ValueError, match="the condition is more than one statement"):
        table(spark).merge(source(con, "s"), "t.id = s.id; SELECT 1").whenMatchedUpdateAll().execute()
    assert rows(spark) == [(1, 10), (2, 20)]
