"""Column.withField and Column.dropFields, as Spark's: a struct's field added, replaced or dropped, also
inside a nested struct ("e.f"), with a field matched whatever its case, a NULL struct left NULL, and the
column named update_fields(a, WithField(...), dropfield()) as Spark names it.

Which fields a struct has is known only from the frame, so the edit is worked out when the column is
given to select, withColumn or withColumns. Anywhere else (inside another function, a filter) it is
refused by name rather than guessed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Union

import duckdb
from duckdb.experimental.spark.errors import AnalysisException, PySparkTypeError
from duckdb.experimental.spark.sql.column import Column

from .spark_compat import _RELATION, _TYPE_SQL, _col_sql, _duck_lit, _spark_text, spark_type_name

# A nested column as col("a.e") writes it: a.e, or "a"."e" on DuckDB 2.0.
_PATH = re.compile(r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*)(?:\.(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*))+')
_PART = re.compile(r'"((?:[^"]|"")*)"|([A-Za-z_][A-Za-z0-9_]*)')

# What a column stands for until a frame is known: refused by name if it reaches DuckDB as it is.
_REFUSAL = ("withField and dropFields run on this PC only as a whole column of select, withColumn or withColumns, "
            "so nothing ran. Select the edited struct first and use it from there, or run the cell in Fabric.")
_UNRESOLVED = f"CAST(error('{_REFUSAL}') AS VARCHAR)"  # typed, so a function around it runs it rather than passing NULL


@dataclass(frozen=True)
class _Col:
    column: Column


@dataclass(frozen=True)
class _Extract:
    struct: _Spec
    name: str


@dataclass(frozen=True)
class _With:
    name: str
    value: _Spec


@dataclass(frozen=True)
class _Drop:
    name: str


@dataclass(frozen=True)
class _Update:
    struct: _Spec
    ops: tuple[_With | _Drop, ...]


_Spec = Union[_Col, _Extract, _Update]


def _parts(name: str) -> list[str]:
    """A field path as Spark's parseMultipartIdentifier reads it: a.b.c, with `a.b` one name."""
    out = [m.group(1).replace("``", "`") if m.group(1) is not None else m.group(2)
           for m in re.finditer(r"`((?:[^`]|``)*)`|([^.`]+)", name)]
    if not out:
        raise AnalysisException(message=f"[PARSE_SYNTAX_ERROR] Syntax error at or near end of input: {name!r}.")
    return out


def _spec(column: Column) -> _Spec:
    return vars(column).get("_fd_struct") or _Col(column)


def _helper(struct: _Spec, parts: list[str], make: Any) -> _Update:
    """Spark's UpdateFields.updateFieldsHelper: a.e.f is a WithField of e whose value edits a.e."""
    if len(parts) == 1:
        return _Update(struct, (make(parts[0]),))
    return _Update(struct, (_With(parts[0], _helper(_Extract(struct, parts[0]), parts[1:], make)),))


def _combined(old: _Spec, new: _Update) -> _Update:
    """update_fields(update_fields(s, ops1), ops2) as one update_fields(s, ops1 ++ ops2); with only
    WithFields, one per name (the first one's place, the last one's value), as Spark's optimizer names it."""
    if not isinstance(old, _Update):
        return new
    ops = old.ops + new.ops
    if all(isinstance(op, _With) for op in ops):
        last: dict[str, _With | _Drop] = {}
        for op in ops:
            last[op.name.lower()] = op
        ops = tuple(last[k] for k in dict.fromkeys(op.name.lower() for op in ops))
    return _Update(old.struct, ops)


def _text(spec: Any, struct_of: Any = None) -> str:
    """Spark's name for ``spec``. With ``struct_of`` (whether a column is a struct, from the frame), a
    struct's field read as col[key] or col.key is col.key, as select names it."""
    if isinstance(spec, _Col):
        aliased = vars(spec.column).get("_fd_alias_of")
        if aliased is not None:
            return f"{_spark_text(aliased)} AS {_spark_text(spec.column)}"
        field = vars(spec.column).get("_fd_field")
        if field is not None and struct_of is not None and struct_of(field[0]):
            return f"{_spark_text(field[0])}.{field[1]}"
        plain = _RELATION.sub("", str(spec.column.expr))
        if "_fd_name" not in vars(spec.column) and _PATH.fullmatch(plain):  # Spark writes col("a.e") a.e
            return ".".join(m.group(1).replace('""', '"') if m.group(1) is not None else m.group(2)
                            for m in _PART.finditer(plain))
        return _spark_text(spec.column)
    if isinstance(spec, _Extract):
        return f"{_text(spec.struct, struct_of)}.{spec.name}"
    if isinstance(spec, _With):
        return f"WithField({_text(spec.value, struct_of)})"
    if isinstance(spec, _Drop):
        return "dropfield()"
    return f"update_fields({_text(spec.struct, struct_of)}, {', '.join(_text(op, struct_of) for op in spec.ops)})"


def _edited(column: Column, update: _Update) -> Column:
    return _deferred(_combined(_spec(column), update))


def field_of(column: Column, name: str) -> Column:
    """``col.name``, ``col[name]`` or ``col.getField(name)`` of a withField/dropFields column."""
    return _deferred(_Extract(_spec(column), name))


def _deferred(spec: _Spec) -> Column:
    out = Column(duckdb.SQLExpression(_UNRESOLVED))
    out._fd_struct = spec  # type: ignore[attr-defined]
    out._fd_name = _text(spec)  # type: ignore[attr-defined]
    return out


def with_field(self: Column, fieldName: str, col: Column) -> Column:  # noqa: N803 - PySpark's names
    """``Column.withField``: the struct with field ``fieldName`` (a path such as "e.f") set to ``col``."""
    if not isinstance(fieldName, str):
        raise PySparkTypeError(error_class="NOT_STR",
                               message_parameters={"arg_name": "fieldName", "arg_type": type(fieldName).__name__})
    if not isinstance(col, Column):
        raise PySparkTypeError(error_class="NOT_COLUMN",
                               message_parameters={"arg_name": "col", "arg_type": type(col).__name__})
    value = _spec(col)
    return _edited(self, _helper(_spec(self), _parts(fieldName), lambda name: _With(name, value)))


def drop_fields(self: Column, *fieldNames: str) -> Column:  # noqa: N803 - PySpark's names
    """``Column.dropFields``: the struct without the fields named (paths such as "e.g" too)."""
    if not fieldNames:
        raise AnalysisException(message="dropFields needs at least one field name, as in Spark (empty.tail).")
    out = self
    for name in fieldNames:
        if not isinstance(name, str):
            raise PySparkTypeError(error_class="NOT_STR",
                                   message_parameters={"arg_name": "fieldName", "arg_type": type(name).__name__})
        out = _edited(out, _helper(_spec(out), _parts(name), _Drop))
    return out


class _Resolver:
    """The SQL for a spec over one relation, each struct's fields read from the relation's types."""

    def __init__(self, relation: Any) -> None:
        self.relation = relation

    def _type(self, sql: str) -> Any:
        return self.relation.project(sql).types[0]

    def is_struct(self, column: Column) -> bool:
        try:
            return bool(self._type(self.sql(_spec(column))).id == "struct")
        except duckdb.Error:  # silent-ok: the frame can't read it; the select itself says why
            return False

    def text(self, spec: Any) -> str:
        return _text(spec, self.is_struct)

    def _fields(self, sql: str, text: str, update: str) -> list[tuple[str, str]]:
        kind = self._type(sql)
        if kind.id != "struct":
            from duckdb.experimental.spark.sql.type_utils import convert_type
            name = spark_type_name(convert_type(kind))
            raise AnalysisException(message=(
                f'[DATATYPE_MISMATCH.UNEXPECTED_INPUT_TYPE] Cannot resolve "{update}" due to data type mismatch: '
                f'Parameter 1 requires the "STRUCT" type, however "{text}" has the type '
                f'"{_TYPE_SQL.get(name, name.upper())}".;'))
        return [(name, f"({sql})[{_duck_lit(name)}]") for name, _ in kind.children]

    def sql(self, spec: _Spec) -> str:
        if isinstance(spec, _Col):
            inner = vars(spec.column).get("_fd_struct")
            if inner is not None:
                return self.sql(inner)
            text = _col_sql(spec.column)
            # A constant's text can read as another type (1.5 as a decimal, where lit(1.5) is a double).
            kind = self.relation.select(spec.column.expr).types[0]
            return text if kind == self._type(text) or kind.id == "null" else f"CAST({text} AS {kind})"
        if isinstance(spec, _Extract):
            struct = self.sql(spec.struct)
            kind = self._type(struct)
            names = [name for name, _ in kind.children] if kind.id == "struct" else []
            found = [name for name in names if name.lower() == spec.name.lower()]
            if not found:
                listed = ", ".join(f"`{name}`" for name in names)
                raise AnalysisException(message=f"[FIELD_NOT_FOUND] No such struct field `{spec.name}` in {listed}.")
            return f"({struct})[{_duck_lit(found[0])}]"
        struct = self.sql(spec.struct)
        fields = self._fields(struct, self.text(spec.struct), self.text(spec))
        for op in spec.ops:
            at = [i for i, (name, _) in enumerate(fields) if name.lower() == op.name.lower()]
            if isinstance(op, _Drop):
                fields = [f for i, f in enumerate(fields) if i not in at]
            elif at:
                fields = [(op.name, self.sql(op.value)) if i in at else f for i, f in enumerate(fields)]  # named as given
            else:
                fields.append((op.name, self.sql(op.value)))
        if not fields:
            raise AnalysisException(message=f'[DATATYPE_MISMATCH.CANNOT_DROP_ALL_FIELDS] Cannot resolve "{self.text(spec)}" '
                                            "due to data type mismatch: Cannot drop all fields in struct.;")
        packed = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}" := {value}' for name, value in fields)
        return f"CASE WHEN ({struct}) IS NULL THEN NULL ELSE struct_pack({packed}) END"


def refuse_unresolved(cols: list[Any]) -> None:
    """Refuse an edited struct inside another expression up front, before DuckDB's own error about it."""
    if any(isinstance(c, Column) and _REFUSAL in str(c.expr) for c in cols):
        raise NotImplementedError(_REFUSAL)


def resolved(relation: Any, cols: list[Any]) -> list[Any]:
    """``cols`` with each withField/dropFields column worked out over ``relation``, keeping its name."""
    if not any(isinstance(c, Column) and "_fd_struct" in vars(c) for c in cols):
        refuse_unresolved(cols)
        return cols
    resolver = _Resolver(relation)
    out = []
    for c in cols:
        spec = vars(c).get("_fd_struct") if isinstance(c, Column) else None
        if spec is None:
            out.append(c)
            continue
        name = vars(c).get("_fd_struct_alias")
        if name is None or name == vars(c).get("_fd_struct_named"):  # Spark's name, not one given: from the frame
            name = resolver.text(spec)
        out.append(Column(duckdb.SQLExpression(resolver.sql(spec)).alias(name)))
    refuse_unresolved(out)
    return out
