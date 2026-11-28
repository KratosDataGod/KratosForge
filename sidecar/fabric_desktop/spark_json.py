"""Spark's to_json on this PC (WR-118): JSON text written by the value's type, as Spark's JacksonGenerator
writes it, where DuckDB's own to_json differs.

Spark leaves out a struct's NULL fields (ignoreNullFields, true by default) but keeps NULL map values and
array elements; keeps a decimal's scale (1.50); writes a double or float as Java does (1.0E10, 1.0E-5) and
NaN and Infinity as quoted strings; a date as yyyy-MM-dd and a timestamp as yyyy-MM-dd'T'HH:mm:ss.SSSXXX in
the session's time zone (the dateFormat and timestampFormat options change these); and binary as base64.

How a value is written depends on its type, which only the bound tables tell, so spark_complex_types
(once spark.sql has bound them) writes each to_json from its argument's type. A to_json whose argument
can't be typed, or with an option Spark reads that this PC doesn't, is refused by name.
"""

from __future__ import annotations

from sqlglot import exp

from . import spark_builtins_sql, spark_datetime

_T = exp.DataType.Type
_INTS = (_T.TINYINT, _T.SMALLINT, _T.INT, _T.BIGINT)
_TEXT = (_T.TEXT, _T.VARCHAR, _T.CHAR, _T.NCHAR, _T.NVARCHAR)
_TIMESTAMPS = (_T.TIMESTAMP, _T.TIMESTAMPTZ, _T.TIMESTAMPLTZ)
_UNTYPED = (_T.UNKNOWN, _T.NULL)
_BINARY = (_T.BINARY, _T.VARBINARY)
DATE_FORMAT = "yyyy-MM-dd"
TIMESTAMP_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"
TIMESTAMP_NTZ_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSS"
OPTIONS = ("ignoreNullFields", "dateFormat", "timestampFormat", "timestampNTZFormat")


class Refused(ValueError):
    """A to_json this PC can't write as Spark does: nothing runs."""


UNTYPED = "the type of what it writes couldn't be worked out here"
UNTYPED_STEP = "Cast the value to a typed struct, array or map, or run the cell in Fabric."


def refused(why: str, step: str = "Run the cell in Fabric.") -> Refused:
    return Refused(f"to_json can't run on this PC the way it runs in Fabric, because {why}, so nothing ran. {step}")


def _string(v: str) -> str:
    return f"TO_JSON(CAST({v} AS VARCHAR))"


def _formatted(v: str, pattern: str) -> str:
    return '\'"\' || ' + spark_datetime.format_sql(pattern).replace("fj_arg0", f"({v})") + ' || \'"\''


def _value(v: str, dtype: exp.DataType, options: dict[str, str], depth: int) -> str:
    """DuckDB SQL writing the non-NULL value ``v`` of ``dtype`` as Spark's JSON."""
    t = dtype.this
    if t in _TEXT:
        return _string(v)
    if t in _INTS:
        return f"CAST({v} AS VARCHAR)"
    if t == _T.DECIMAL:
        return _decimal(v, dtype)
    if t == _T.BOOLEAN:
        return f"CASE WHEN {v} THEN 'true' ELSE 'false' END"
    if t in (_T.DOUBLE, _T.FLOAT):
        text = spark_builtins_sql.java_double_text(v)
        return f"CASE WHEN ISNAN({v}) OR ISINF({v}) THEN '\"' || {text} || '\"' ELSE {text} END"
    if t == _T.DATE:
        return _formatted(v, options.get("dateFormat", DATE_FORMAT))
    if t in _TIMESTAMPS:
        return _formatted(v, options.get("timestampFormat", TIMESTAMP_FORMAT))
    if t == _T.TIMESTAMPNTZ:
        return _formatted(v, options.get("timestampNTZFormat", TIMESTAMP_NTZ_FORMAT))
    if t in _BINARY:
        return f"'\"' || TO_BASE64(CAST({v} AS BLOB)) || '\"'"
    e = f"kf_j{depth}"
    if t == _T.ARRAY and len(dtype.expressions) == 1:
        inner = _nullable(e, dtype.expressions[0], options, depth + 1, "'null'")
        return f"'[' || ARRAY_TO_STRING(LIST_TRANSFORM({v}, lambda {e}: {inner}), ',') || ']'"
    if t == _T.MAP and len(dtype.expressions) == 2:
        key_type, value_type = dtype.expressions
        if key_type.this in _TEXT:
            key = f"TO_JSON(CAST({e}.key AS VARCHAR))"
        elif key_type.this in _INTS or key_type.this in (_T.BOOLEAN, _T.DECIMAL):
            key = f"'\"' || {_value(f'{e}.key', key_type, options, depth + 1)} || '\"'"
        elif key_type.this == _T.DATE:  # Spark writes a date key as its days since 1970-01-01
            key = f"'\"' || CAST({e}.key - DATE '1970-01-01' AS VARCHAR) || '\"'"
        else:
            raise refused(f"Spark writes a map with {key_type.sql('spark')} keys in its own text")
        inner = _nullable(f"{e}.value", value_type, options, depth + 1, "'null'")
        return f"'{{' || ARRAY_TO_STRING(LIST_TRANSFORM(MAP_ENTRIES({v}), lambda {e}: {key} || ':' || {inner}), ',') || '}}'"
    if t == _T.STRUCT:
        keep_nulls = options.get("ignoreNullFields", "true").strip().lower() == "false"
        parts = []
        for field in dtype.expressions:
            if not isinstance(field, exp.ColumnDef) or not isinstance(field.args.get("kind"), exp.DataType):
                raise refused("a struct field's type couldn't be worked out here", UNTYPED_STEP)
            name = exp.Literal.string(field.name).sql("duckdb")
            member = f"{v}[{name}]"
            written = _nullable(member, field.args["kind"], options, depth + 1, "'null'" if keep_nulls else "NULL")
            parts.append(f"TO_JSON(CAST({name} AS VARCHAR)) || ':' || {written}")
        if not parts:
            return "'{}'"
        fields = f"LIST_FILTER([{', '.join(parts)}], kf_p -> kf_p IS NOT NULL)"  # a left-out field is NULL
        return f"'{{' || ARRAY_TO_STRING({fields}, ',') || '}}'"
    if t in _UNTYPED:  # array(), whose elements have no type: an error only when there is one to write
        why = refused(UNTYPED, UNTYPED_STEP)
        return f"ERROR({exp.Literal.string(str(why)).sql('duckdb')})"
    raise refused(f"Spark writes a value of type {dtype.sql('spark')} in its own way")


def _decimal(v: str, dtype: exp.DataType) -> str:
    """A decimal as Java's BigDecimal.toString writes it, as Spark's Jackson does: plain, unless its
    adjusted exponent (digits - 1 - scale) is below -6, which only a scale above 6 allows; then in E form
    (0E-8, 1.2E-7, -1.23E-10)."""
    scale = int(dtype.expressions[1].name) if len(dtype.expressions) > 1 else 0
    plain = f"CAST({v} AS VARCHAR)"
    if scale <= 6:
        return plain
    digits = f"COALESCE(NULLIF(LTRIM(REPLACE(REPLACE({plain}, '-', ''), '.', ''), '0'), ''), '0')"
    return (f"(LIST_TRANSFORM([{digits}], kf_dd -> CASE WHEN LENGTH(kf_dd) - 1 - {scale} >= -6 THEN {plain} "
            f"ELSE CASE WHEN {v} < 0 THEN '-' ELSE '' END || LEFT(kf_dd, 1) "
            f"|| CASE WHEN LENGTH(kf_dd) > 1 THEN '.' || SUBSTRING(kf_dd, 2) ELSE '' END "
            f"|| 'E' || CAST(LENGTH(kf_dd) - 1 - {scale} AS VARCHAR) END))[1]")


def _nullable(v: str, dtype: exp.DataType, options: dict[str, str], depth: int, null: str) -> str:
    return f"CASE WHEN {v} IS NULL THEN {null} ELSE {_value(v, dtype, options, depth)} END"


def options_of(node: exp.Expr) -> dict[str, str]:
    """to_json's options map, written out as text; anything else is refused."""
    given = node.args.get("options")
    if given is None:
        return {}
    keys = given.args.get("keys")
    values = given.args.get("values")
    pairs = list(zip(keys.expressions, values.expressions, strict=True)) \
        if isinstance(keys, exp.Array) and isinstance(values, exp.Array) else None
    if pairs is None or not all(isinstance(p, exp.Literal) and p.is_string for pair in pairs for p in pair):
        raise Refused("to_json's options must be written out as map('name', 'value', ...), so nothing ran.")
    out = {}
    for key, value in pairs:
        name = next((o for o in OPTIONS if o.lower() == key.name.lower()), None)
        if name is None:
            raise Refused(f"to_json's {key.name!r} option isn't read on this PC, so nothing ran. Leave it out, or run "
                          "the cell in Fabric.")
        out[name] = value.name
    return out


def to_json_sql(v: str, dtype: exp.DataType, options: dict[str, str]) -> str:
    """DuckDB SQL for Spark's to_json of ``v`` (DuckDB SQL) of ``dtype``: NULL for NULL."""
    if dtype.this not in (_T.STRUCT, _T.ARRAY, _T.MAP):
        raise Refused(f"to_json writes a struct, an array or a map, as in Spark, not a {dtype.sql('spark')}, so "
                      "nothing ran. Wrap the value in struct(...) or array(...).")
    return f"CASE WHEN {v} IS NULL THEN NULL ELSE {_value(v, dtype, options, 0)} END"


_NAMES = {_T.TINYINT: "tinyint", _T.SMALLINT: "smallint", _T.INT: "int", _T.BIGINT: "bigint", _T.BOOLEAN: "boolean",
          _T.DOUBLE: "double", _T.FLOAT: "float", _T.DATE: "date", **{t: "string" for t in _TEXT},
          **{t: "binary" for t in _BINARY}}


def type_name(dtype: exp.DataType) -> str | None:
    """typeof's name for a struct or map (or an array of one), as Spark writes it (struct<a:int>); None for
    any other type, which typeof names from DuckDB's own name, and for a type whose Spark name isn't sure."""
    def name(d: exp.DataType) -> str | None:
        t = d.this
        if t in _NAMES:
            return _NAMES[t]
        if t == _T.DECIMAL:
            p = [int(x.name) for x in d.expressions] or [10, 0]
            return f"decimal({p[0]},{p[1] if len(p) > 1 else 0})"
        if t == _T.ARRAY and len(d.expressions) == 1:
            inner = name(d.expressions[0])
            return None if inner is None else f"array<{inner}>"
        if t == _T.MAP and len(d.expressions) == 2:
            k, v = name(d.expressions[0]), name(d.expressions[1])
            return None if k is None or v is None else f"map<{k},{v}>"
        if t == _T.STRUCT:
            parts = []
            for f in d.expressions:
                kind = f.args.get("kind") if isinstance(f, exp.ColumnDef) else None
                inner = name(kind) if isinstance(kind, exp.DataType) else None
                if inner is None or not f.name:  # a field whose name the typing lost isn't named here
                    return None
                parts.append(f"{f.name}:{inner}")
            return f"struct<{','.join(parts)}>"
        return None
    def nested(d: exp.DataType) -> bool:
        return d.this in (_T.STRUCT, _T.MAP) or any(nested(x) for x in d.expressions if isinstance(x, exp.DataType))
    return name(dtype) if nested(dtype) else None
