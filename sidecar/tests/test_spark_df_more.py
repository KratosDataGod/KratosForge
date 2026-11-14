"""freqItems, inputFiles, to, withMetadata and writeTo on this PC's Spark (spark_df_more.py, Fabric
notebook parity F3). writeTo is checked against the table writer it calls; nothing reaches OneLake."""

import pytest

from fabric_desktop import lakehouse, spark_local


@pytest.fixture(autouse=True)
def no_lakehouse(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", None)


@pytest.fixture
def spark():
    ns = {}
    spark_local.install(ns)
    return ns["spark"]


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "a"), (1, "b"), (2, "a"), (3, None)], ["k", "s"])


def test_freq_items_lists_values_at_or_above_support(df):
    row = df.freqItems(["k", "s"], 0.4).collect()[0]
    assert row.k_freqItems == [1] and row.s_freqItems == ["a"]
    assert df.stat.freqItems(["k"]).columns == ["k_freqItems"]
    assert sorted(df.freqItems(["k"]).first()[0]) == [1, 2, 3]  # 1% default: every value
    with pytest.raises(ValueError, match="UNRESOLVED_COLUMN"):
        df.freqItems(["nope"])
    with pytest.raises(ValueError, match="support"):
        df.freqItems(["k"], 0)


def test_input_files_names_what_a_read_scans(spark, df, tmp_path):
    path = tmp_path / "x.parquet"
    df.write.parquet(str(path))
    assert spark.read.parquet(str(path)).inputFiles() == [str(path)]
    assert df.inputFiles() == []  # made from Python data


def test_to_reorders_and_casts_by_name(df):
    out = df.to("S string, k int")
    assert out.dtypes == [("S", "string"), ("k", "int")]
    assert [tuple(r) for r in out.orderBy("k", "S").collect()][0] == ("a", 1)
    with pytest.raises(ValueError, match="UNRESOLVED_COLUMN"):
        df.to("z int")


def test_with_metadata_shows_in_the_schema_and_leaves_the_source_alone(df):
    m = df.withMetadata("k", {"comment": "business key"})
    assert m.schema["k"].metadata == {"comment": "business key"}
    assert df.schema["k"].metadata == {}
    assert m.count() == 4
    with pytest.raises(ValueError, match="UNRESOLVED_COLUMN"):
        df.withMetadata("nope", {})


@pytest.fixture
def writes(monkeypatch):
    calls, existing = [], set()

    def save(self, name, *a, **kw):
        calls.append((name, self._mode, dict(self._options)))
        existing.add(name)
    monkeypatch.setattr(spark_local.LakehouseWriter, "saveAsTable", save)
    monkeypatch.setattr(lakehouse, "table_exists", lambda name: name in existing)
    return calls, existing


def test_write_to_create_replace_append_follow_spark(df, writes):
    calls, existing = writes
    with pytest.raises(ValueError, match="TABLE_OR_VIEW_NOT_FOUND"):
        df.writeTo("dbo.sales").append()
    with pytest.raises(ValueError, match="TABLE_OR_VIEW_NOT_FOUND"):
        df.writeTo("dbo.sales").replace()
    df.writeTo("dbo.sales").using("delta").create()
    with pytest.raises(ValueError, match="TABLE_OR_VIEW_ALREADY_EXISTS"):
        df.writeTo("dbo.sales").create()
    df.writeTo("dbo.sales").option("mergeSchema", "true").append()
    df.writeTo("dbo.sales").replace()
    df.writeTo("dbo.other").createOrReplace()
    assert calls == [("dbo.sales", "errorifexists", {}), ("dbo.sales", "append", {"mergeschema": "true"}),
                     ("dbo.sales", "overwrite", {"overwriteschema": "true"}),
                     ("dbo.other", "overwrite", {"overwriteschema": "true"})]


def test_write_to_refuses_what_runs_in_fabric(df, writes):
    with pytest.raises(ValueError, match="Delta tables"):
        df.writeTo("t").using("parquet")
    with pytest.raises(NotImplementedError, match="runs in Fabric"):
        df.writeTo("t").overwrite(df.k > 1)
    with pytest.raises(NotImplementedError, match="runs in Fabric"):
        df.writeTo("t").overwritePartitions()
    with pytest.raises(ValueError, match="table name"):
        df.writeTo("  ")
