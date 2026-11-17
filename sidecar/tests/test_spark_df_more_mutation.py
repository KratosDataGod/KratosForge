"""spark_df_more.py paths that mutation testing (batch 85, docs/quality/mutation-testing.md) showed no
test checked: freqItems' argument checks and quoting, inputFiles over folders, globs and Delta logs,
to() and withMetadata() refusals, the select and sort wrappers, and writeTo's chained calls. Nothing
reaches OneLake: inputFiles is given the plan's SQL directly, and writeTo's table writer is faked."""

from types import SimpleNamespace

import duckdb
import pytest

from fabric_desktop import lakehouse, spark_df_more as more, spark_local


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


# ---- freqItems


def test_freq_items_takes_one_name_or_a_non_empty_list_of_names(df):
    assert df.freqItems("k", 0.5).first()[0] == [1]  # a single name is a list of one
    for bad in ([], (), 5, None):
        with pytest.raises(TypeError, match="list of column names"):
            df.freqItems(bad)


def test_freq_items_support_runs_from_one_in_ten_thousand_to_one(df):
    assert df.freqItems(["k"], 1).first()[0] == []  # no value is in every row
    assert sorted(df.freqItems(["k"], 1e-4).first()[0]) == [1, 2, 3]
    for bad in (0.99e-4, 1.0001):
        with pytest.raises(ValueError, match="support must be between"):
            df.freqItems(["k"], bad)


def test_freq_items_quotes_a_column_name_with_a_double_quote(spark):
    odd = spark.createDataFrame([(1,), (1,), (2,)], ['a"b'])
    out = odd.freqItems(['a"b'], 0.5)
    assert out.columns == ['a"b_freqItems'] and out.first()[0] == [1]


def test_freq_items_replaces_a_view_left_from_before(df):
    df.relation.create_view(f"fd_freq_{id(df.relation)}", replace=True)
    assert df.freqItems(["s"], 0.5).first()[0] == ["a"]


# ---- inputFiles (the plan's SQL given directly)


def files(sql):
    return more.input_files(SimpleNamespace(relation=SimpleNamespace(sql_query=lambda: sql)))


def test_a_plan_duckdb_cant_write_as_sql_reads_no_files():
    def refuse():
        raise duckdb.Error("no SQL for this relation")
    assert more.input_files(SimpleNamespace(relation=SimpleNamespace(sql_query=refuse))) == []


def test_a_folder_lists_its_files_in_every_subfolder_and_never_a_folder(tmp_path):
    (tmp_path / "y=1").mkdir()
    (tmp_path / "a.parquet").write_bytes(b"x")
    (tmp_path / "y=1" / "b.parquet").write_bytes(b"x")
    assert files(f"SELECT * FROM read_parquet('{tmp_path}')") == [str(tmp_path / "a.parquet"),
                                                                    str(tmp_path / "y=1" / "b.parquet")]


def test_a_delta_folder_lists_only_the_files_its_log_names(tmp_path):
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake
    write_deltalake(str(tmp_path), pa.table({"a": [1]}))
    write_deltalake(str(tmp_path), pa.table({"a": [2]}), mode="overwrite")  # the first file stays on disk
    assert len(list(tmp_path.glob("*.parquet"))) == 2
    assert files(f"SELECT * FROM delta_scan('{tmp_path}')") == DeltaTable(str(tmp_path)).file_uris()


def test_remote_file_uris_in_a_delta_log_are_listed_as_they_are(tmp_path, monkeypatch):
    import deltalake
    (tmp_path / "_delta_log").mkdir()

    class Table:
        def __init__(self, path):
            pass

        def file_uris(self):
            return ["abfss://ws@onelake/lh/Tables/t/part-0.parquet", "part-1.parquet"]
    monkeypatch.setattr(deltalake, "DeltaTable", Table)
    assert files(f"SELECT * FROM delta_scan('{tmp_path}')") == [
        "abfss://ws@onelake/lh/Tables/t/part-0.parquet", str(tmp_path / "part-1.parquet")]


def test_an_unreadable_log_lists_the_data_files_and_not_the_log(tmp_path, monkeypatch):
    import deltalake

    def unreadable(path):
        raise deltalake.exceptions.DeltaError("the log can't be read")
    monkeypatch.setattr(deltalake, "DeltaTable", unreadable)
    (tmp_path / "_delta_log").mkdir()
    (tmp_path / "_delta_log" / "00000000000000000000.json").write_text("{}")
    (tmp_path / "part-0.parquet").write_bytes(b"x")
    assert files(f"SELECT * FROM delta_scan('{tmp_path}')") == [str(tmp_path / "part-0.parquet")]


def test_a_folder_whose_name_contains_delta_log_still_lists_its_files(tmp_path):
    folder = tmp_path / "orders_delta_log_archive"  # a plain folder, not a Delta table
    folder.mkdir()
    (folder / "a.parquet").write_bytes(b"x")
    assert files(f"SELECT * FROM read_parquet('{folder}')") == [str(folder / "a.parquet")]


def test_a_glob_lists_what_it_matches_across_subfolders(tmp_path):
    (tmp_path / "d" / "e").mkdir(parents=True)
    (tmp_path / "b.csv").write_text("x")
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "d" / "e" / "c.csv").write_text("x")
    assert files(f"SELECT * FROM read_csv_auto('{tmp_path}/*.csv')") == [str(tmp_path / "a.csv"),
                                                                       str(tmp_path / "b.csv")]
    # ** reaches any depth, as in Spark, not one folder only
    assert files(f"SELECT * FROM read_csv_auto('{tmp_path}/**/c.csv')") == [str(tmp_path / "d" / "e" / "c.csv")]


def test_a_plain_path_is_listed_once_even_when_scanned_twice(tmp_path):
    p = str(tmp_path / "it's.parquet")  # not on disk: listed as named, with its quote unescaped
    q = p.replace("'", "''")
    assert files(f"SELECT * FROM read_parquet(['{q}', '{q}']) UNION ALL SELECT * FROM parquet_scan('{q}')") == [p]


# ---- to and withMetadata


def test_to_takes_a_struct_type_or_a_ddl_string_only(df):
    with pytest.raises(TypeError, match="StructType or a DDL string"):
        df.to(5)


def test_with_metadata_takes_a_dict_only(df):
    with pytest.raises(TypeError, match="metadata is a dict"):
        df.withMetadata("k", [("comment", "x")])


# ---- the select and sort wrappers


def test_select_keeps_alias_metadata_when_the_columns_come_as_a_list(df):
    from duckdb.experimental.spark.sql import functions as F
    out = df.select([F.col("k").alias("key", metadata={"max": 3}), "s"])
    assert out.schema["key"].metadata == {"max": 3} and out.schema["s"].metadata == {}
    one = df.select(F.col("k").alias("key", metadata={"max": 3}))
    assert one.schema["key"].metadata == {"max": 3}


def test_a_sort_by_a_column_nothing_has_raises_duckdbs_error(df):
    with pytest.raises(duckdb.BinderException):
        df.sort("nope")
    with pytest.raises(duckdb.BinderException):
        df.select("k").sort("nope")


def test_the_wrappers_are_installed_once_and_keep_the_docs():
    def select(self):
        """Select columns."""
    def sort(self):
        """Sort rows."""
    s, o = more._select_keeping(select), more._sort_through_select(sort)
    assert s._fd_keeping is True and o._fd_through is True
    assert s.__doc__ == "Select columns." and o.__doc__ == "Sort rows."
    assert more._select_keeping(s) is s and more._sort_through_select(o) is o


def test_installing_again_leaves_the_schema_property_as_it_was(spark):
    DataFrame = more._df_class()  # noqa: N806
    before = DataFrame.schema
    assert before.fget._fd_metadata is True
    more.install()
    assert DataFrame.schema is before


# ---- writeTo's chained calls


def test_write_to_chains_through_every_setting(df, monkeypatch, capsys):
    calls = []

    def save(self, name, *a, **kw):
        calls.append((name, self._mode, dict(self._options)))
    monkeypatch.setattr(spark_local.LakehouseWriter, "saveAsTable", save)
    monkeypatch.setattr(lakehouse, "table_exists", lambda name: False)
    (df.writeTo(" dbo.t ").tableProperty("delta.appendOnly", "true").partitionedBy("k").clusterBy("k")
     .options(mergeSchema="true").create())
    assert calls == [("dbo.t", "errorifexists", {"mergeschema": "true"})]
    printed = capsys.readouterr().out
    assert "delta.appendOnly is set in Fabric" in printed and "partitionedBy is ignored" in printed
    assert "clusterBy only changes file layout" in printed
