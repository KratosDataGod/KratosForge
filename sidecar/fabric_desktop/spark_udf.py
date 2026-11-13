"""Python UDFs on this PC's Spark (Fabric notebook parity F1): ``F.udf``, ``@udf``, ``F.pandas_udf``,
``spark.udf.register`` (callable from ``spark.sql`` and ``%%sql`` by its name) and ``F.call_udf``.

A UDF becomes a DuckDB Python function on the session's connection, so it runs in this process, over
the rows DuckDB hands it, as Spark's Python workers do. As in Spark: the declared return type is the
column's type (a string such as ``"int"`` or ``"array<string>"`` works too, and the default is
``string``); ``None`` goes in for a null; a value that doesn't fit the return type comes out null; an
exception in the function fails the query with that exception. ``pandas_udf`` gets a pandas Series for
each argument and returns a Series of the same length (also the iterator form); its grouped-aggregate
form (Series to scalar) runs in Fabric. ``asNondeterministic()`` marks it so DuckDB calls it for every
row. Nothing here reaches the network or Fabric.
"""

from __future__ import annotations

import inspect
import itertools
from collections.abc import Callable
from typing import Any

_ids = itertools.count(1)


def _types() -> Any:
    from duckdb.experimental.spark.sql import types as T
    return T


def _data_type(return_type: Any) -> Any:
    """A Spark DataType from a DataType or a DDL type string ("int", "array<string>")."""
    T = _types()
    if return_type is None:
        return T.StringType()
    if isinstance(return_type, str):
        from .spark_local import _ddl_type
        return _ddl_type(return_type)
    if isinstance(return_type, T.DataType):
        return return_type
    raise TypeError(f"returnType should be a DataType or a type string such as \"int\", not {type(return_type).__name__}.")


def _fits(value: Any, dt: Any) -> Any:
    """``value`` as the declared type, or None when it doesn't fit (Spark's Python UDFs give null then)."""
    import datetime
    import decimal
    T = _types()
    if value is None:
        return None
    if isinstance(dt, T.BooleanType):
        return value if isinstance(value, bool) else None
    if isinstance(dt, (T.ByteType, T.ShortType, T.IntegerType, T.LongType)):
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(dt, (T.FloatType, T.DoubleType)):
        return value if isinstance(value, float) else None
    if isinstance(dt, T.DecimalType):
        return value if isinstance(value, decimal.Decimal) else None
    if isinstance(dt, T.StringType):
        return value if isinstance(value, str) else str(value)  # Spark writes other values as their text
    if isinstance(dt, T.DateType):
        return value if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime) else None
    if isinstance(dt, (T.TimestampType, T.TimestampNTZType)):
        return value if isinstance(value, datetime.datetime) else None
    if isinstance(dt, T.BinaryType):
        return bytes(value) if isinstance(value, (bytes, bytearray)) else None
    if isinstance(dt, T.ArrayType):
        return [_fits(v, dt.elementType) for v in value] if isinstance(value, (list, tuple)) else None
    if isinstance(dt, T.MapType):
        return {_fits(k, dt.keyType): _fits(v, dt.valueType) for k, v in value.items()} if isinstance(value, dict) else None
    if isinstance(dt, T.StructType):
        if isinstance(value, dict):
            return {f.name: _fits(value.get(f.name), f.dataType) for f in dt.fields}
        if isinstance(value, (list, tuple)) and len(value) == len(dt.fields):
            return {f.name: _fits(v, f.dataType) for f, v in zip(dt.fields, value, strict=True)}
        return None
    return value


def _row_value(value: Any) -> Any:
    """A struct argument reaches the function as a Row, as in Spark."""
    if isinstance(value, dict):
        from duckdb.experimental.spark.sql.types import Row
        return Row(**{k: _row_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_row_value(v) for v in value]
    return value


def _column(expr: Any) -> Any:
    from duckdb.experimental.spark.sql.column import Column
    return Column(expr)


def _arg_expr(c: Any) -> Any:
    """A Column, or a column name, as a DuckDB expression."""
    from duckdb.experimental.spark.sql import functions as F
    from duckdb.experimental.spark.sql.column import Column
    if isinstance(c, Column):
        return c.expr
    if isinstance(c, str):
        return F.col(c).expr
    raise TypeError(f"A UDF takes columns or column names, not {type(c).__name__}. Use lit() for a constant.")


def _spark_name(col: Any, name: str, cols: Any) -> Any:
    """The column named as Spark names a UDF's result: ``clean(email)``, ``<lambda>(id)``."""
    from duckdb.experimental.spark.sql.column import Column

    from .spark_compat import _named, _spark_text
    args = ", ".join(c if isinstance(c, str) else _spark_text(c) if isinstance(c, Column) else str(c) for c in cols)
    return _named(col, f"{name}({args})")


def _conn() -> Any:
    from .spark_local import session
    return session().conn


class UserDefinedFunction:
    """What ``udf`` and ``pandas_udf`` return: call it with columns to get a column."""

    def __init__(self, func: Callable[..., Any], return_type: Any, *, vectorized: bool = False,
                 iterator: bool = False, name: str | None = None) -> None:
        self.func = func
        self.returnType = _data_type(return_type)  # noqa: N815 (Spark's name)
        self.evalType = 200 if vectorized else 100  # noqa: N815 (Spark's SQL_SCALAR_PANDAS_UDF / SQL_BATCHED_UDF)
        self.deterministic = True
        self._vectorized = vectorized
        self._iterator = iterator
        self._name = name or f"_fd_udf_{next(_ids)}_{getattr(func, '__name__', 'f')}"
        self._registered_on: Any = None
        self.__name__ = getattr(func, "__name__", "udf")
        self.__doc__ = getattr(func, "__doc__", None)

    def asNondeterministic(self) -> UserDefinedFunction:  # noqa: N802
        self.deterministic = False
        self._registered_on = None  # registered again with side effects, so it runs for every row
        return self

    def _native(self) -> Callable[..., Any]:
        func, dt = self.func, self.returnType

        def call(*args: Any) -> Any:
            return _fits(func(*(_row_value(a) for a in args)), dt)
        return call

    def _arrow(self) -> Callable[..., Any]:
        import pyarrow as pa
        func, dt, iterator = self.func, self.returnType, self._iterator

        def call(*arrays: Any) -> Any:
            series = [a.to_pandas() for a in arrays]
            if iterator:
                parts = list(func(iter([series[0] if len(series) == 1 else tuple(series)])))
                import pandas as pd
                out = pd.concat(parts, ignore_index=True) if parts else pd.Series([], dtype=object)
            else:
                out = func(*series)
            n = len(arrays[0]) if arrays else 0
            if not hasattr(out, "__len__") or isinstance(out, (str, bytes)) or len(out) != n:
                raise RuntimeError(f"The pandas_udf {self.__name__} returned {len(out) if hasattr(out, '__len__') else 'one value'} "
                                   f"for {n} rows. A scalar pandas_udf returns a Series as long as its input.")
            values = [_fits(None if _isnull(v) else _plain(v), dt) for v in list(out)]
            return pa.array(values, type=_arrow_type(dt))
        return call

    def _register(self, conn: Any, name: str | None = None) -> str:
        from duckdb import func as duck_func
        target = name or self._name
        if name is None and self._registered_on is conn:
            return target
        try:
            conn.remove_function(target)
        except Exception:  # noqa: BLE001  # silent-ok: not registered yet, so there is nothing to remove
            pass
        conn.create_function(target, self._arrow() if self._vectorized else self._native(), parameters=None,
                             return_type=self.returnType.duckdb_type,
                             type=duck_func.PythonUDFType.ARROW if self._vectorized else duck_func.PythonUDFType.NATIVE,
                             null_handling=duck_func.FunctionNullHandling.SPECIAL,
                             side_effects=not self.deterministic)
        if name is None:
            self._registered_on = conn
        return target

    def __call__(self, *cols: Any) -> Any:
        import duckdb
        name = self._register(_conn())
        return _spark_name(_column(duckdb.FunctionExpression(name, *(_arg_expr(c) for c in cols))),
                           self.__name__, cols)


def _isnull(v: Any) -> bool:
    try:
        import pandas as pd
        return v is None or (not isinstance(v, (list, tuple, dict)) and bool(pd.isna(v)))
    except (TypeError, ValueError):
        return False


def _plain(v: Any) -> Any:
    """numpy and pandas scalars as Python values."""
    import datetime
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime()
    if hasattr(v, "item") and not isinstance(v, (list, tuple, dict, datetime.date)):
        try:
            return v.item()
        except (ValueError, AttributeError):
            return v
    if hasattr(v, "tolist"):
        return v.tolist()
    return v


def _arrow_type(dt: Any) -> Any:
    import pyarrow as pa
    T = _types()
    simple = {T.BooleanType: pa.bool_(), T.ByteType: pa.int8(), T.ShortType: pa.int16(), T.IntegerType: pa.int32(),
              T.LongType: pa.int64(), T.FloatType: pa.float32(), T.DoubleType: pa.float64(), T.StringType: pa.string(),
              T.DateType: pa.date32(), T.BinaryType: pa.binary()}
    for k, v in simple.items():
        if isinstance(dt, k):
            return v
    if isinstance(dt, T.DecimalType):
        return pa.decimal128(dt.precision, dt.scale)
    if isinstance(dt, T.TimestampNTZType):
        return pa.timestamp("us")
    if isinstance(dt, T.TimestampType):
        return pa.timestamp("us", tz="UTC")
    if isinstance(dt, T.ArrayType):
        return pa.list_(_arrow_type(dt.elementType))
    if isinstance(dt, T.MapType):
        return pa.map_(_arrow_type(dt.keyType), _arrow_type(dt.valueType))
    if isinstance(dt, T.StructType):
        return pa.struct([(f.name, _arrow_type(f.dataType)) for f in dt.fields])
    return pa.string()


def udf(f: Any = None, returnType: Any = None, *, useArrow: bool | None = None) -> Any:  # noqa: N803 (Spark's names)
    """``udf(f, returnType)``, ``@udf``, ``@udf(returnType=...)`` or ``@udf("int")``."""
    if f is None or isinstance(f, (str, _types().DataType)):
        rt = f if returnType is None else returnType
        return lambda g: UserDefinedFunction(g, rt)
    if isinstance(f, UserDefinedFunction):
        return f
    return UserDefinedFunction(f, returnType)


def _pandas_kind(func: Callable[..., Any]) -> str:
    """"scalar", "iterator" or "aggregate", from the type hints as Spark 3 reads them."""
    try:
        hints = inspect.signature(func)
    except (TypeError, ValueError):
        return "scalar"
    ret = hints.return_annotation
    text = ret if isinstance(ret, str) else getattr(ret, "__name__", "") or str(ret)
    params = list(hints.parameters.values())
    first = params[0].annotation if params else None
    first_text = first if isinstance(first, str) else str(first)
    if "Iterator" in first_text:
        return "iterator"
    if ret is not inspect.Signature.empty and "Series" not in text and "DataFrame" not in text and "Iterator" not in text:
        return "aggregate"
    return "scalar"


def pandas_udf(f: Any = None, returnType: Any = None, functionType: Any = None) -> Any:  # noqa: N803
    """``pandas_udf(f, returnType)``, ``@pandas_udf("double")`` or ``@pandas_udf(returnType=...)``."""
    def make(g: Callable[..., Any], rt: Any) -> UserDefinedFunction:
        kind = _pandas_kind(g)
        if kind == "aggregate":
            raise NotImplementedError(
                f"{getattr(g, '__name__', 'This pandas_udf')} is a grouped-aggregate pandas_udf (Series to a single value), "
                "which runs in Fabric. On this PC, use a built-in aggregate (F.sum, F.avg, F.percentile_approx...) "
                "or toPandas() and pandas.")
        return UserDefinedFunction(g, rt, vectorized=True, iterator=kind == "iterator")
    if f is None or isinstance(f, (str, _types().DataType)):
        rt = f if returnType is None else returnType
        return lambda g: make(g, rt)
    return make(f, returnType)


class UDFRegistration:
    """``spark.udf``: register a Python function under a name for SQL, as Spark does."""

    def register(self, name: str, f: Any, returnType: Any = None) -> UserDefinedFunction:  # noqa: N803
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"A UDF's name is a plain name such as clean_email, not {name!r}.")
        if isinstance(f, UserDefinedFunction):
            if returnType is not None:
                raise TypeError("Register a UDF made with udf() without a returnType; its own return type is used.")
            fn = UserDefinedFunction(f.func, f.returnType, vectorized=f._vectorized, iterator=f._iterator, name=name)
            fn.deterministic = f.deterministic
        else:
            fn = UserDefinedFunction(f, returnType, name=name)
        fn._register(_conn())
        fn._registered_on = _conn()
        _registered[name.lower()] = fn
        return fn

    def registerJavaFunction(self, *_a: Any, **_kw: Any) -> None:  # noqa: N802
        raise NotImplementedError("Java UDFs run in Fabric: this PC has no JVM.")


_registered: dict[str, UserDefinedFunction] = {}


def reregister(conn: Any) -> None:
    """After a new connection (a restarted session), the named UDFs are there again."""
    for name, fn in _registered.items():
        fn._register(conn, name)


def call_udf(udfName: str, *cols: Any) -> Any:  # noqa: N803
    import duckdb
    fn = _registered.get(str(udfName).lower())
    if fn is None:
        raise ValueError(f"No UDF is registered as {udfName}. Register it first with spark.udf.register(\"{udfName}\", ...).")
    return _spark_name(_column(duckdb.FunctionExpression(fn._register(_conn(), udfName), *(_arg_expr(c) for c in cols))),
                       str(udfName), cols)


def install() -> None:
    """``F.udf``, ``F.pandas_udf`` and ``F.call_udf`` on the Spark functions module (once per process)."""
    from duckdb.experimental.spark.sql import functions as F
    vars(F).update(udf=udf, pandas_udf=pandas_udf, call_udf=call_udf)
    if not hasattr(F, "PandasUDFType"):
        class PandasUDFType:  # Spark 2's names, still accepted by pandas_udf (functionType)
            SCALAR, SCALAR_ITER, GROUPED_MAP, GROUPED_AGG = 200, 204, 201, 202
        F.PandasUDFType = PandasUDFType  # type: ignore[attr-defined]
