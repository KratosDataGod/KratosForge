"""spark_udf.py's conversions and forms, pinned so that changes to them fail a test (mutation testing,
batch 88): each return type's fit and its range, the Arrow type each Spark type becomes, numpy and pandas
values as Python values, the forms udf and pandas_udf take, and spark.udf.register's refusals. The UDFs
run on this process's local Spark session; nothing reaches OneLake or Fabric.
"""

from __future__ import annotations

import datetime as dt
import decimal
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from duckdb.experimental.spark.sql import types as T

from fabric_desktop import spark_udf as U


@pytest.fixture
def spark(monkeypatch, tmp_path):
    from fabric_desktop import lakehouse, spark_local
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(lakehouse, "_attached", None)
    monkeypatch.setattr(U, "_registered", {})
    return spark_local.session()


# ---- the declared return type

def test_a_return_type_that_is_neither_a_type_nor_a_string_is_refused():
    with pytest.raises(TypeError, match="returnType should be a DataType"):
        U._data_type(123)


def test_a_boolean_fits_only_a_bool():
    assert U._fits(True, T.BooleanType()) is True
    assert U._fits(False, T.BooleanType()) is False
    assert U._fits(1, T.BooleanType()) is None


@pytest.mark.parametrize("dtype, bits", [(T.ByteType(), 8), (T.ShortType(), 16), (T.IntegerType(), 32), (T.LongType(), 64)])
def test_an_integer_fits_exactly_its_types_range(dtype, bits):
    low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
    assert U._fits(high, dtype) == high and U._fits(low, dtype) == low
    assert U._fits(high + 1, dtype) is None and U._fits(low - 1, dtype) is None
    assert U._fits(True, dtype) is None and U._fits(1.0, dtype) is None


def test_a_decimal_fits_only_a_decimal():
    assert U._fits(decimal.Decimal("1.50"), T.DecimalType(10, 2)) == decimal.Decimal("1.50")
    assert U._fits(1.5, T.DecimalType(10, 2)) is None


def test_any_value_fits_a_string_as_its_text():
    assert U._fits("a", T.StringType()) == "a"
    out = U._fits(5, T.StringType())
    assert out == "5" and isinstance(out, str)


def test_a_date_fits_a_date_but_a_datetime_does_not():
    day = dt.date(2026, 11, 17)
    assert U._fits(day, T.DateType()) == day
    assert U._fits(dt.datetime(2026, 11, 17, 1, 2), T.DateType()) is None
    assert U._fits("2026-11-17", T.DateType()) is None


@pytest.mark.parametrize("dtype", [T.TimestampType(), T.TimestampNTZType()])
def test_a_timestamp_fits_only_a_datetime(dtype):
    when = dt.datetime(2026, 11, 17, 1, 2, 3)
    assert U._fits(when, dtype) == when
    assert U._fits(dt.date(2026, 11, 17), dtype) is None


def test_binary_fits_bytes_and_bytearray_as_bytes():
    out = U._fits(bytearray(b"ab"), T.BinaryType())
    assert out == b"ab" and type(out) is bytes
    assert U._fits("ab", T.BinaryType()) is None


def test_a_map_fits_a_dict_with_its_keys_and_values_fitted():
    assert U._fits({1: "x", "k": 2}, T.MapType(T.StringType(), T.StringType())) == {"1": "x", "k": "2"}
    assert U._fits([("a", 1)], T.MapType(T.StringType(), T.IntegerType())) is None


def test_a_struct_fits_a_dict_or_a_sequence_of_its_length():
    st = T.StructType([T.StructField("a", T.IntegerType()), T.StructField("b", T.StringType())])
    assert U._fits({"a": 1}, st) == {"a": 1, "b": None}
    assert U._fits((1, 2), st) == {"a": 1, "b": "2"}
    assert U._fits([1], st) is None
    assert U._fits([1, 2, 3], st) is None


def test_structs_inside_a_list_arrive_as_rows():
    from duckdb.experimental.spark.sql.types import Row
    out = U._row_value([{"a": 1}, 2])
    assert out == [Row(a=1), 2] and isinstance(out[0], Row)


# ---- the Arrow type of a pandas_udf's result

@pytest.mark.parametrize("dtype, arrow", [
    (T.DecimalType(10, 2), pa.decimal128(10, 2)),
    (T.TimestampNTZType(), pa.timestamp("us")),
    (T.TimestampType(), pa.timestamp("us", tz="UTC")),
    (T.ArrayType(T.IntegerType()), pa.list_(pa.int32())),
    (T.MapType(T.StringType(), T.LongType()), pa.map_(pa.string(), pa.int64())),
    (T.StructType([T.StructField("a", T.DoubleType())]), pa.struct([("a", pa.float64())])),
    (T.NullType(), pa.string()),
])
def test_each_spark_type_has_its_arrow_type(dtype, arrow):
    assert U._arrow_type(dtype) == arrow


# ---- numpy and pandas values

def test_numpy_and_pandas_scalars_become_python_values():
    out = U._plain(np.int64(3))
    assert out == 3 and type(out) is int
    stamp = U._plain(pd.Timestamp("2026-11-17 01:02:03"))
    assert stamp == dt.datetime(2026, 11, 17, 1, 2, 3) and type(stamp) is dt.datetime
    day = dt.date(2026, 11, 17)
    assert U._plain(day) is day


def test_a_pandas_array_becomes_its_list():
    out = U._plain(pd.array([1, 2]))
    assert out == [1, 2] and type(out) is list


def test_a_numpy_array_becomes_its_list_even_with_one_value():
    for arr, want in ((np.array([1, 2]), [1, 2]), (np.array([5]), [5]), (np.array([[1], [2]]), [[1], [2]])):
        out = U._plain(arr)
        assert out == want and type(out) is list
    assert type(U._plain(np.array([1, 2]))[0]) is int  # its values as Python ones too
    assert U._plain(np.array(4)) == 4 and type(U._plain(np.array(4))) is int  # no dimensions: a scalar


def test_null_is_none_nan_or_nat_and_never_an_array():
    assert U._isnull(None) is True
    assert U._isnull(float("nan")) is True
    assert U._isnull(pd.NaT) is True
    assert U._isnull(np.array([1, 2])) is False
    assert U._isnull([None]) is False


# ---- the forms udf and pandas_udf take

def test_udf_of_a_udf_is_the_same_udf():
    f = U.udf(lambda x: x, "int")
    assert U.udf(f) is f


def test_a_udf_is_a_batched_udf_and_a_pandas_udf_a_scalar_pandas_udf():
    plain = U.UserDefinedFunction(lambda x: x, "int")
    vector = U.UserDefinedFunction(lambda x: x, "int", vectorized=True)
    assert (plain.evalType, vector.evalType) == (100, 200)
    assert vector._iterator is False


def test_pandas_kind_is_scalar_when_the_signature_cant_be_read_or_names_a_series():
    def series(s: pd.Series) -> pd.Series:
        return s
    assert U._pandas_kind(max) == "scalar"
    assert U._pandas_kind(series) == "scalar"


def test_pandas_udf_called_with_the_function_returns_the_udf():
    def double(s: pd.Series) -> pd.Series:
        return s * 2
    out = U.pandas_udf(double, "long")
    assert isinstance(out, U.UserDefinedFunction) and isinstance(out.returnType, T.LongType)


def test_pandas_udf_with_only_a_named_return_type_keeps_it():
    @U.pandas_udf(returnType="long")
    def double(s: pd.Series) -> pd.Series:
        return s * 2
    assert isinstance(double.returnType, T.LongType)


def test_a_number_as_the_return_type_after_a_function_is_refused():
    def double(s: pd.Series) -> pd.Series:
        return s * 2
    with pytest.raises(TypeError, match="returnType should be a DataType"):
        U.pandas_udf(double, 200)


def test_function_type_scalar_wins_over_iterator_hints():
    def gen(it: Iterator[pd.Series]) -> Iterator[pd.Series]:
        yield from it
    assert U.pandas_udf(gen, "long")._iterator is True
    assert U.pandas_udf(gen, "long", 200)._iterator is False


# ---- registration

def test_a_udf_registers_once_per_connection():
    calls = []

    class Conn:
        def remove_function(self, name):
            raise KeyError(name)

        def create_function(self, name, *a, **kw):
            calls.append(name)

    f = U.UserDefinedFunction(lambda x: x, "int")
    conn = Conn()
    assert f._register(conn) == f._register(conn)
    assert len(calls) == 1
    assert f._register(Conn()) and len(calls) == 2


def test_register_returns_the_udf_and_refuses_a_return_type_for_a_udf(spark):
    made = U.udf(lambda x: x + 1, "int")
    with pytest.raises(TypeError, match="without a returnType"):
        U.UDFRegistration().register("plus_one_typed", made, "long")
    fn = U.UDFRegistration().register("plus_one", made)
    assert isinstance(fn, U.UserDefinedFunction) and fn._name == "plus_one"
    assert spark.sql("SELECT plus_one(41) AS v").collect()[0][0] == 42


def test_java_udfs_are_refused():
    with pytest.raises(NotImplementedError, match="no JVM"):
        U.UDFRegistration().registerJavaFunction("f", "com.example.F")


def test_install_gives_spark_2s_pandas_udf_types():
    from duckdb.experimental.spark.sql import functions as F
    U.install()
    P = F.PandasUDFType
    assert (P.SCALAR, P.SCALAR_ITER, P.GROUPED_MAP, P.GROUPED_AGG) == (200, 204, 201, 202)


def test_a_pandas_udf_returning_numpy_arrays_gives_lists(spark):
    df = spark.createDataFrame([(1,), (2,)], ["id"])
    pairs = U.pandas_udf(lambda s: s.apply(lambda v: np.array([v, v + 1])), T.ArrayType(T.LongType()))
    assert [r[0] for r in df.select(pairs("id")).collect()] == [[1, 2], [2, 3]]
    # One element stays an array of one, and one NaN in it stays a value, not a null row.
    single = U.pandas_udf(lambda s: s.apply(lambda v: np.array([v])), T.ArrayType(T.LongType()))
    assert [r[0] for r in df.select(single("id")).collect()] == [[1], [2]]
    nan = U.pandas_udf(lambda s: s.apply(lambda v: np.array([np.nan])), T.ArrayType(T.DoubleType()))
    got = [r[0] for r in df.select(nan("id")).collect()]
    assert len(got) == 2 and all(len(g) == 1 and g[0] != g[0] for g in got)
