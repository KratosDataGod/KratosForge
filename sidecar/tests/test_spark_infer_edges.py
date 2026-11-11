"""createDataFrame's schema inference: values it hands back to DuckDB, map and decimal merges, and a
value with no shared shape."""

import decimal

import pytest
from duckdb.experimental.spark.errors import PySparkTypeError
from duckdb.experimental.spark.sql import types as T

from fabric_desktop import spark_infer as si


@pytest.mark.parametrize("rows", [
    [(2**63,)],          # beyond bigint: Spark makes it NULL, DuckDB refuses it
    [(object(),)],       # a type this inference doesn't cover
    ["just text"],       # a row that isn't a tuple, list or dict
])
def test_values_it_does_not_cover_go_to_duckdbs_own_inference(rows):
    assert si.infer(rows, None) is None


def test_bytes_infer_as_binary():
    _, schema = si.infer([(b"\x00\x01",)], ["b"])
    assert isinstance(schema.fields[0].dataType, T.BinaryType)


def test_maps_merge_their_key_and_value_types():
    rows, schema = si.infer([({"k": None},), ({"k": 1},)], ["m"])
    m = schema.fields[0].dataType
    assert isinstance(m, T.MapType) and isinstance(m.keyType, T.StringType) and isinstance(m.valueType, T.LongType)


def test_two_different_decimal_types_do_not_merge():
    a, b = T.DecimalType(10, 2), T.DecimalType(12, 4)
    with pytest.raises(PySparkTypeError) as raised:
        si._merge(a, b)
    assert raised.value.getErrorClass() == "CANNOT_MERGE_TYPE"


def test_a_value_of_an_unknown_kind_has_no_shared_shape():
    assert si._shape(decimal.Context()) is None
