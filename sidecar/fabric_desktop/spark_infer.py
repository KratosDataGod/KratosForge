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


def _fit(value: Any, t: Any) -> Any:
    """``value`` shaped for type ``t``: a struct's values in its fields' order (a Row's by name)."""
    if value is None:
        return None
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


def infer(data: list[Any] | tuple[Any, ...], names: list[str] | None) -> tuple[list[Any], Any] | None:
    """Rows and the schema PySpark infers for them, or None when a value is one this doesn't cover
    (DuckDB's own inference runs then)."""
    try:
        schema = reduce(_merge, (_row_type(row, names) for row in data))
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
    return [_fit(row, schema) for row in data], schema
