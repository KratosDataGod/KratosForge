"""spark.createDataFrame(rows) with no types given, inferring the schema as PySpark does: every row's
types merged, a tuple (or Row, or namedtuple) inside a row as a struct (_1, _2, ... when it has no
names), a dict inside a row as a map, a dict row's keys in sorted order, unnamed columns as _1, _2, ...,
an int as bigint and a Decimal as decimal(38,18). Types that can't merge, or a column with no value
to infer from, are refused with PySpark's error. DuckDB's own inference makes a tuple an array, a
dict a struct and names columns col0, col1."""

from __future__ import annotations

import datetime
import decimal
from functools import reduce
from typing import Any

import pyarrow as pa
from duckdb.experimental.spark.errors import PySparkTypeError, PySparkValueError
from duckdb.experimental.spark.sql import types as T


class _Unsupported(Exception):
    """A value this inference doesn't cover: DuckDB's own inference is used instead."""


def _names(obj: Any) -> list[str] | None:
    fields = getattr(obj, "__fields__", None) or getattr(obj, "_fields", None)  # a Row, or a namedtuple
    if isinstance(fields, (list, tuple)) and all(isinstance(f, str) for f in fields):
        return list(fields)
    return None


def _infer(obj: Any) -> Any:
    """PySpark's _infer_type."""
    if obj is None:
        return T.NullType()
    if isinstance(obj, bool):
        return T.BooleanType()
    if isinstance(obj, int):
        if not -2**63 <= obj < 2**63:  # Spark makes it NULL; DuckDB's own inference refuses it
            raise _Unsupported("int beyond bigint")
        return T.LongType()
    if isinstance(obj, float):
        return T.DoubleType()
    if isinstance(obj, str):
        return T.StringType()
    if isinstance(obj, (bytes, bytearray)):
        return T.BinaryType()
    if isinstance(obj, decimal.Decimal):
        return T.DecimalType(38, 18)
    if isinstance(obj, datetime.datetime):  # DuckDB's own inference, which already says timestamp
        raise _Unsupported("datetime")
    if isinstance(obj, datetime.date):
        return T.DateType()
    if isinstance(obj, dict):
        keys = [k for k in obj if k is not None]
        values = [v for v in obj.values() if v is not None]
        return T.MapType(_merged(map(_infer, keys)), _merged(map(_infer, values)), True)
    if isinstance(obj, list):
        return T.ArrayType(_merged(_infer(v) for v in obj if v is not None), True)
    if isinstance(obj, tuple):
        return _struct(obj, _names(obj))
    raise _Unsupported(type(obj).__name__)


def _merged(types_: Any) -> Any:
    try:
        return reduce(_merge, types_, T.NullType())
    except PySparkTypeError:  # inside a field, as PySpark says it
        raise PySparkTypeError(error_class="CANNOT_INFER_TYPE_FOR_FIELD",
                               message_parameters={"field_name": ""}) from None


def _struct(values: Any, names: list[str] | None) -> Any:
    names = names or []
    return T.StructType([T.StructField(names[i] if i < len(names) else f"_{i + 1}", _infer(v), True)
                         for i, v in enumerate(values)])


def _merge(a: Any, b: Any) -> Any:
    """PySpark's _merge_type: NULL gives way to any type; structs gain the fields they lack."""
    if isinstance(a, T.NullType):
        return b
    if isinstance(b, T.NullType):
        return a
    if type(a) is not type(b):
        raise PySparkTypeError(error_class="CANNOT_MERGE_TYPE",
                               message_parameters={"data_type1": type(a).__name__, "data_type2": type(b).__name__})
    if isinstance(a, T.StructType):
        theirs = {f.name: f.dataType for f in b.fields}
        fields = [T.StructField(f.name, _merge(f.dataType, theirs.get(f.name, T.NullType())), True) for f in a.fields]
        mine = {f.name for f in a.fields}
        fields += [T.StructField(f.name, f.dataType, True) for f in b.fields if f.name not in mine]
        return T.StructType(fields)
    if isinstance(a, T.ArrayType):
        return T.ArrayType(_merge(a.elementType, b.elementType), True)
    if isinstance(a, T.MapType):
        return T.MapType(_merge(a.keyType, b.keyType), _merge(a.valueType, b.valueType), True)
    if isinstance(a, T.DecimalType) and (a.precision, a.scale) != (b.precision, b.scale):
        raise PySparkTypeError(error_class="CANNOT_MERGE_TYPE",
                               message_parameters={"data_type1": str(a), "data_type2": str(b)})
    return a


def _row_type(row: Any, names: list[str] | None) -> Any:
    """PySpark's _infer_schema for one row: a dict's keys sorted, a Row's or namedtuple's own names,
    else the names given and then _n."""
    if isinstance(row, dict):
        return T.StructType([T.StructField(str(k), _infer(v), True) for k, v in sorted(row.items())])
    if isinstance(row, (tuple, list)):
        own = _names(row)
        if own is not None:
            return _struct(row, own)
        if names and len(names) > len(row):
            raise IndexError("list index out of range")  # as PySpark does when more names than values are given
        return _struct(row, names)
    raise _Unsupported(type(row).__name__)


def _has_null(t: Any) -> bool:
    if isinstance(t, T.NullType):
        return True
    if isinstance(t, T.StructType):
        return any(_has_null(f.dataType) for f in t.fields)
    if isinstance(t, T.ArrayType):
        return _has_null(t.elementType)
    if isinstance(t, T.MapType):
        return _has_null(t.keyType) or _has_null(t.valueType)
    return False


def _has_struct(t: Any) -> bool:
    """Whether values of type ``t`` need reshaping: a struct (its fields' order) or a decimal (Spark's scale)."""
    if isinstance(t, (T.StructType, T.DecimalType)):
        return True
    if isinstance(t, T.ArrayType):
        return _has_struct(t.elementType)
    if isinstance(t, T.MapType):
        return _has_struct(t.keyType) or _has_struct(t.valueType)
    return False


_SCALE = decimal.Decimal(1).scaleb(-18)
_LIMIT = decimal.Decimal(10) ** 20  # decimal(38,18) holds 20 whole digits


def _spark_decimal(value: decimal.Decimal) -> decimal.Decimal | None:
    """A Decimal as Spark stores it in decimal(38,18): rounded half up to 18 places, NaN as NULL; one
    too large for it is an error, as Spark's is when the cell runs."""
    if value.is_nan():
        return None
    if value.is_infinite() or abs(value) >= _LIMIT:
        raise ArithmeticError(f"[NUMERIC_VALUE_OUT_OF_RANGE] {value} cannot be represented as Decimal(38, 18).")
    return value.quantize(_SCALE, rounding=decimal.ROUND_HALF_UP, context=decimal.Context(prec=60))


def _fit(value: Any, t: Any) -> Any:
    """``value`` shaped for type ``t``: a struct's values in its fields' order (a Row's by name). A part
    with no struct in it needs no reshaping and is left as it is."""
    if value is None or not _has_struct(t):
        return value
    if isinstance(t, T.DecimalType):
        return _spark_decimal(value)
    if isinstance(t, T.StructType):
        names = _names(value)
        if isinstance(value, dict) or names is not None:
            by_name = dict(value) if isinstance(value, dict) else dict(zip(names or [], value, strict=False))
            return tuple(_fit(by_name.get(f.name), f.dataType) for f in t.fields)
        values = list(value)
        return tuple(_fit(values[i] if i < len(values) else None, f.dataType) for i, f in enumerate(t.fields))
    if isinstance(t, T.ArrayType):
        return [_fit(v, t.elementType) for v in value]
    if isinstance(t, T.MapType):
        return {_fit(k, t.keyType): _fit(v, t.valueType) for k, v in value.items()}
    return value


_SCALARS = (type(None), bool, int, float, str, bytes, bytearray, decimal.Decimal, datetime.date, datetime.datetime)


def _shape(value: Any) -> Any:
    """A key that two values share only when _infer gives them the same type, cheap to make: the value's
    Python type, a tuple's names and shapes; None for a list or dict (inferred each time)."""
    kind = type(value)
    if kind is int:
        return int if -2**63 <= value < 2**63 else "big"
    if kind in _SCALARS:
        return kind
    if isinstance(value, tuple):
        inner = tuple(_shape(v) for v in value)
        return None if None in inner else ("tuple", tuple(_names(value) or ()), inner)
    if kind is list:
        items = frozenset(_shape(v) for v in value if v is not None)
        return None if None in items else ("list", items)
    if kind is dict:
        keys = frozenset(_shape(k) for k in value if k is not None)
        values = frozenset(_shape(v) for v in value.values() if v is not None)
        return None if None in keys or None in values else ("dict", keys, values)
    return None


def _same_lengths(data: Any) -> None:
    """Rows of values (not dicts) all of one length, with the error DuckDB's createDataFrame gives."""
    rows = [row for row in data if isinstance(row, (tuple, list))]
    for i, row in enumerate(rows[1:]):
        if len(row) != len(rows[0]):
            raise PySparkTypeError(error_class="LENGTH_SHOULD_BE_THE_SAME",
                                   message_parameters={"arg1": f"data{i}", "arg2": f"data{i + 1}",
                                                       "arg1_length": str(len(rows[0])), "arg2_length": str(len(row))})


def infer(data: list[Any] | tuple[Any, ...], names: list[str] | None) -> tuple[list[Any], Any] | None:
    """Rows and the schema PySpark infers for them, or None when a value is one this doesn't cover
    (DuckDB's own inference runs then). Rows of one shape are inferred once, and each new type merged
    once, in the order rows first show it (merging a type again changes nothing)."""
    _same_lengths(data)
    seen: set[Any] = set()
    schema: Any = None
    try:
        for row in data:
            shape = _shape(row) if isinstance(row, tuple) else None
            if shape is not None:
                if shape in seen:
                    continue
                seen.add(shape)
            row_type = _row_type(row, names)
            schema = row_type if schema is None else _merge(schema, row_type)
    except _Unsupported:
        return None
    except PySparkTypeError as e:
        if e.getErrorClass() == "CANNOT_MERGE_TYPE":
            raise
        raise PySparkTypeError(error_class="CANNOT_INFER_TYPE_FOR_FIELD",
                               message_parameters={"field_name": ""}) from None
    if _has_null(schema):
        raise PySparkValueError(error_class="CANNOT_DETERMINE_TYPE",
                                message_parameters={})
    flat = not any(_has_struct(f.dataType) for f in schema.fields)
    if flat and all(type(row) is tuple for row in data):  # nothing to reshape: the rows go as they are
        return list(data), schema
    return [_fit(row, schema) for row in data], schema


def _arrow_type(t: Any) -> Any:
    if isinstance(t, T.StructType):
        return pa.struct([(f.name, _arrow_type(f.dataType)) for f in t.fields])
    if isinstance(t, T.ArrayType):
        return pa.list_(_arrow_type(t.elementType))
    if isinstance(t, T.MapType):
        return pa.map_(_arrow_type(t.keyType), _arrow_type(t.valueType))
    if isinstance(t, T.DecimalType):
        return pa.decimal128(t.precision, t.scale)
    return {T.LongType: pa.int64(), T.DoubleType: pa.float64(), T.StringType: pa.string(), T.BooleanType: pa.bool_(),
            T.BinaryType: pa.binary(), T.DateType: pa.date32()}[type(t)]


def arrow_table(rows: list[Any], schema: Any) -> pa.Table:
    """The rows (as infer gives them) as an Arrow table of the schema's types: DuckDB reads it in one go,
    where a schema given to createDataFrame converts each value on its own."""
    columns = list(zip(*rows, strict=True)) if rows else [() for _ in schema.fields]
    return pa.Table.from_arrays([pa.array(list(c), _arrow_type(f.dataType)) for c, f in zip(columns, schema.fields, strict=True)],
                                names=[f.name for f in schema.fields])
