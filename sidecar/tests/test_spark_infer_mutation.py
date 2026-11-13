"""spark_infer paths that mutation testing (batch 10, docs/quality/mutation-testing.md) showed no test
checked: nullable flags in the inferred schema, bigint's edges, values handed back to DuckDB, rows of one
shape skipped only when they really infer the same, Spark's decimal(38,18) applied inside arrays and maps,
and the length error's wording."""

from __future__ import annotations

import datetime
import decimal

import pytest
from duckdb.experimental.spark.errors import PySparkTypeError, PySparkValueError
from duckdb.experimental.spark.sql import types as T

from fabric_desktop import spark_infer as si

NAN = decimal.Decimal("NaN")


# ---- every inferred field, element and value may hold NULL, as PySpark's inference says

def test_every_part_of_an_inferred_schema_is_nullable():
    _, schema = si.infer([({"k": 1}, [1], (1,))], None)
    m, a, s = (f.dataType for f in schema.fields)
    assert all(f.nullable for f in schema.fields)
    assert m.valueContainsNull is True
    assert a.containsNull is True
    assert s.fields[0].nullable is True


def test_merged_structs_keep_every_field_nullable():
    _, schema = si.infer([((1, None),), ((None, "x"),)], ["s"])
    s = schema.fields[0].dataType
    assert [f.nullable for f in s.fields] == [True, True]
    assert [type(f.dataType) for f in s.fields] == [T.LongType, T.StringType]


def test_a_field_only_the_second_struct_has_is_added_nullable():
    a = T.StructType([T.StructField("x", T.LongType(), True)])
    b = T.StructType([T.StructField("x", T.LongType(), True), T.StructField("y", T.StringType(), False)])
    merged = si._merge(a, b)
    assert [f.name for f in merged.fields] == ["x", "y"]
    assert merged.fields[1].nullable is True


def test_merged_arrays_may_hold_null():
    merged = si._merge(T.ArrayType(T.NullType(), False), T.ArrayType(T.LongType(), False))
    assert isinstance(merged.elementType, T.LongType) and merged.containsNull is True


# ---- bigint's edges, and a value DuckDB's own inference handles

@pytest.mark.parametrize("value", [2**63 - 1, -2**63])
def test_the_edges_of_bigint_infer_as_long(value):
    rows, schema = si.infer([(value,)], None)
    assert isinstance(schema.fields[0].dataType, T.LongType) and rows == [(value,)]


@pytest.mark.parametrize("value", [2**63, -2**63 - 1])
def test_just_past_bigint_goes_to_duckdbs_own_inference(value):
    assert si.infer([(value,)], None) is None


@pytest.mark.parametrize("first", [1, 2**63 - 1])
def test_a_row_past_bigint_after_one_inside_is_not_skipped_as_the_same_shape(first):
    assert si.infer([(first,), (2**63,)], None) is None
    assert si.infer([(first,), (-2**63 - 1,)], None) is None


def test_a_datetime_goes_to_duckdbs_own_inference_not_to_date():
    assert si.infer([(datetime.datetime(2024, 1, 2, 3, 4),)], None) is None
    _, schema = si.infer([(datetime.date(2024, 1, 2),)], None)
    assert isinstance(schema.fields[0].dataType, T.DateType)


# ---- rows skipped as "the same shape" only when they infer the same

def test_lists_of_different_element_types_are_not_the_same_shape():
    with pytest.raises(PySparkTypeError):
        si.infer([([1],), (["a"],)], None)


def test_maps_of_different_value_types_are_not_the_same_shape():
    with pytest.raises(PySparkTypeError):
        si.infer([({"k": 1},), ({"k": "a"},)], None)


class _List(list):
    """A list subclass: inferred as an array, but with no cheap shape of its own."""


def test_a_map_whose_values_have_no_shape_is_inferred_each_time():
    with pytest.raises(PySparkTypeError):
        si.infer([({"k": _List([1])},), ({"k": _List(["a"])},)], None)


def test_a_map_with_only_null_values_has_no_type():
    with pytest.raises(PySparkValueError) as raised:
        si.infer([({"a": None},)], None)
    assert raised.value.getErrorClass() == "CANNOT_DETERMINE_TYPE"


# ---- Spark's decimal(38,18) inside arrays and maps, and rows that need no reshaping

def test_a_nan_decimal_is_null():
    assert si._spark_decimal(NAN) is None


def test_a_decimal_inside_an_array_is_stored_as_spark_stores_it():
    rows, _ = si.infer([([NAN, decimal.Decimal("1.5")],)], None)
    assert rows[0][0][0] is None
    assert str(rows[0][0][1]) == "1.500000000000000000"


def test_a_decimal_inside_a_map_is_stored_as_spark_stores_it():
    rows, _ = si.infer([({"k": NAN, "j": decimal.Decimal("2")},)], None)
    assert rows[0][0]["k"] is None
    assert str(rows[0][0]["j"]) == "2.000000000000000000"


def test_a_decimal_in_a_row_is_stored_as_spark_stores_it():
    rows, schema = si.infer([(decimal.Decimal("0.1"),)], None)
    assert (schema.fields[0].dataType.precision, schema.fields[0].dataType.scale) == (38, 18)
    assert str(rows[0][0]) == "0.100000000000000000"


def test_flat_rows_of_tuples_go_as_they_are():
    data = [(1, "a"), (2, "b")]
    rows, _ = si.infer(data, None)
    assert rows == data and all(r is d for r, d in zip(rows, data, strict=True))


def test_a_value_with_no_struct_in_it_is_left_as_it_is():
    value = [1, 2]
    assert si._fit(value, T.ArrayType(T.LongType(), True)) is value
    assert si._fit(None, T.DecimalType(38, 18)) is None


# ---- the error for rows of different lengths, as DuckDB's createDataFrame words it

@pytest.mark.parametrize(("data", "params"), [
    ([(1, 2), (1, 2, 3)], {"arg1": "data0", "arg2": "data1", "arg1_length": "2", "arg2_length": "3"}),
    ([(1,), (2,), (3, 4)], {"arg1": "data1", "arg2": "data2", "arg1_length": "1", "arg2_length": "2"}),
])
def test_rows_of_different_lengths_are_refused_naming_the_rows(data, params):
    with pytest.raises(PySparkTypeError) as raised:
        si.infer(data, None)
    assert raised.value.getErrorClass() == "LENGTH_SHOULD_BE_THE_SAME"
    assert raised.value.getMessageParameters() == params


def test_rows_of_one_length_pass_the_length_check():
    rows, schema = si.infer([(1, "a"), (2, "b"), (3, "c")], None)
    assert len(schema.fields) == 2 and len(rows) == 3

