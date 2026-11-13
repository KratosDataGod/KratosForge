"""spark_local paths that mutation testing (batch 8, docs/quality/mutation-testing.md) showed no test
checked: createDataFrame of single values or of an Arrow table, NaN detection, a CSV separator,
mode("ignore") file writes, the query progress hint, the cell connection watch and reading DuckDB's
JSON profile in both its 1.x and 2.0 shapes."""

from __future__ import annotations

import importlib
import threading
import weakref

import duckdb
import pyarrow as pa
import pytest

from fabric_desktop import spark_local


@pytest.fixture(autouse=True)
def _local_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture(scope="module")
def T(spark):
    return importlib.import_module("pyspark.sql.types")


def rows(frame):
    return [tuple(r) for r in frame.collect()]


# ---- createDataFrame

def test_create_data_frame_of_single_values(spark, T):
    frame = spark.createDataFrame([1, 2], T.IntegerType())
    assert rows(frame) == [(1,), (2,)]
    assert frame.columns == ["value"]
    assert rows(spark.createDataFrame([[1, 2], [3]], T.ArrayType(T.IntegerType()))) == [([1, 2],), ([3],)]


@pytest.mark.parametrize("item", [(1, 2), [1], {"a": 1}])
def test_create_data_frame_refuses_a_row_where_a_value_is_typed(spark, T, item):
    with pytest.raises(ValueError, match="makes each item one value"):
        spark.createDataFrame([item], T.IntegerType())


def test_create_data_frame_of_an_arrow_table(spark):
    frame = spark.createDataFrame(pa.table({"id": [1, 2], "name": ["a", "b"]}))
    assert rows(frame) == [(1, "a"), (2, "b")]
    assert frame.columns == ["id", "name"]


@pytest.mark.parametrize(("value", "expected"), [
    (float("nan"), True), (1.0, False), (0.0, False), (1, False), ("nan", False), (None, False),
])
def test_nan_is_only_a_float_nan(value, expected):
    assert spark_local._nan(value) is expected


# ---- reading and writing files

def test_csv_with_a_separator(spark, tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("a,b;c\n1,x;y\n2,z;w\n", encoding="utf-8")  # DuckDB alone would split at the commas
    frame = spark.read.csv(str(path), header=True, sep=";")
    assert frame.columns == ["a,b", "c"]
    assert rows(frame) == [("1,x", "y"), ("2,z", "w")]


def test_mode_ignore_writes_a_new_file_and_keeps_an_old_one(spark, tmp_path, capsys):
    path = tmp_path / "out.parquet"
    spark.createDataFrame([(1,)], ["id"]).write.mode("ignore").parquet(str(path))
    assert path.exists()
    assert rows(spark.read.parquet(str(path))) == [(1,)]
    spark.createDataFrame([(2,)], ["id"]).write.mode("ignore").parquet(str(path))
    assert "already exists, so nothing was written" in capsys.readouterr().out
    assert rows(spark.read.parquet(str(path))) == [(1,)]


# ---- the query progress hint

class _Conn:
    def __init__(self, value):
        self.value = value

    def query_progress(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _Session:
    def __init__(self, value):
        self.conn = _Conn(value)


@pytest.mark.parametrize(("value", "expected"), [
    (50.0, 50.0), (100.0, 100.0), (12.345, 12.3), (0.04, None), (0.0, None), (-1.0, None), (150.0, None),
    (None, None), (duckdb.ConnectionException("closed"), None),
])
def test_progress_shows_a_running_query_between_0_and_100(monkeypatch, value, expected):
    monkeypatch.setattr(spark_local, "_session", _Session(value))
    assert spark_local.progress() == expected


def test_progress_with_no_session(monkeypatch):
    monkeypatch.setattr(spark_local, "_session", None)
    assert spark_local.progress() is None


# ---- the cell connection watch

def test_watch_connections_records_and_stops(monkeypatch):
    monkeypatch.setattr(duckdb, "connect", spark_local._duckdb_connect)
    monkeypatch.setattr(spark_local, "_cell_connections", {})
    ident = threading.get_ident()
    seen = weakref.WeakSet()
    spark_local.watch_connections(ident, seen)
    assert duckdb.connect is spark_local._connect
    assert spark_local._cell_connections[ident] is seen
    spark_local.watch_connections(ident, None)
    assert ident not in spark_local._cell_connections


# ---- reading DuckDB's JSON profile

def test_read_profile_of_duckdb_1():
    root = {"operator_name": "EXPLAIN_ANALYZE", "latency": 0.5, "cumulative_rows_scanned": 0,
            "system_peak_buffer_memory": 1024,
            "children": [{"operator_name": "PROJECTION", "operator_timing": 0.1, "operator_cardinality": 3,
                          "children": [{"operator_name": "SEQ_SCAN", "operator_timing": 0.3,
                                        "operator_cardinality": 7, "extra_info": {"Table": "t", "Empty": ""},
                                        "children": []}]}]}
    got = spark_local._read_profile(root)
    assert [s["name"] for s in got["steps"]] == ["PROJECTION", "SEQ_SCAN"]
    assert [s["depth"] for s in got["steps"]] == [0, 1]
    assert [s["rows"] for s in got["steps"]] == [3, 7]
    assert [s["share"] for s in got["steps"]] == [0.25, 0.75]
    assert got["steps"][1]["details"] == {"Table": "t"}
    assert got["rows"] == 3
    assert got["rows_scanned"] == 7  # no rows scanned reported: the leaf's rows
    assert got["seconds"] == 0.5 and got["peak_memory"] == 1024 and got["more_steps"] == 0


def test_read_profile_of_duckdb_2():
    root = {"query": {"total_time": 0.2, "total_rows_scanned": 0}, "system": {"peak_buffer_memory": 10},
            "operator": [{"type": "RESULT_COLLECTOR", "timing": 0.0, "children": [
                {"type": "TABLE_SCAN", "timing": 0.2, "intermediate_rows": 4,
                 "extra_info": {"Function": "ARROW_SCAN"}, "children": []}]}]}
    got = spark_local._read_profile(root)
    assert [s["name"] for s in got["steps"]] == ["ARROW_SCAN"]
    assert got["steps"][0]["share"] == 1.0
    assert got["rows"] == 4 and got["rows_scanned"] == 4
    assert got["seconds"] == 0.2 and got["peak_memory"] == 10


def test_query_profile_of_a_spark_frame(spark):
    got = spark_local.query_profile(spark.range(5))
    assert got["rows"] == 5
    assert got["steps"] and all(s["name"] not in ("EXPLAIN_ANALYZE", "RESULT_COLLECTOR") for s in got["steps"])


# ---- a read schema matches Parquet and JSON columns by name

def _schema(T, *fields):
    return T.StructType([T.StructField(n, t, True) for n, t in fields])


@pytest.fixture
def files(spark, tmp_path):
    spark.createDataFrame([(1, "x")], ["a", "b"]).write.parquet(str(tmp_path / "p.parquet"))
    (tmp_path / "j.json").write_text('{"a": 1, "b": "x"}\n', encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("kind", ["parquet", "json"])
def test_a_read_schema_matches_columns_by_name(spark, T, files, kind):
    path = str(files / ("p.parquet" if kind == "parquet" else "j.json"))
    reader = spark.read.schema(_schema(T, ("b", T.StringType()), ("a", T.IntegerType())))
    frame = getattr(reader, kind)(path)
    assert frame.columns == ["b", "a"]
    assert rows(frame) == [("x", 1)]
    fewer = getattr(spark.read.schema(_schema(T, ("a", T.IntegerType()))), kind)(path)
    assert rows(fewer) == [(1,)]


def test_failfast_names_a_column_with_a_quote(spark, T, tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("it's\nx\n", encoding="utf-8")
    reader = spark.read.schema(_schema(T, ("it's", T.IntegerType()))).option("header", True).option("mode", "FAILFAST")
    with pytest.raises(Exception, match=r"Malformed CSV value x for column it's \(INTEGER\)"):
        reader.csv(str(path)).collect()


# ---- schemas, USE and write options

def test_ddl_decimal_defaults_as_spark():
    fields = spark_local.ddl_schema("d decimal, e decimal(5)").fields
    assert [(f.dataType.precision, f.dataType.scale) for f in fields] == [(10, 0), (5, 0)]


def test_ddl_comment_with_an_escaped_quote_and_a_comma():
    assert [f.name for f in spark_local.ddl_schema("a int COMMENT 'it\\'s, odd', b string").fields] == ["a", "b"]


def test_use_refuses_three_parts(spark):
    spark.sql("USE spark_catalog.dbo")
    with pytest.raises(ValueError, match="USE spark_catalog.spark_catalog.dbo"):
        spark.sql("USE spark_catalog.spark_catalog.dbo")


def test_a_refused_write_option_says_why():
    assert spark_local.writer_option_refusal("replaceWhere", None) == (
        "uses the replaceWhere write option: replaceWhere (replace only matching rows) isn't supported on this PC, "
        "and ignoring it would replace the whole table")
