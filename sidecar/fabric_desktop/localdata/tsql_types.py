"""T-SQL data types on the local engine as a Fabric Warehouse has them (WR-223).

Microsoft's list ("Data types in Fabric Data Warehouse") names the types a table can hold and, for the others,
the type to use instead. Locally:

- A table or view that would keep a column of a type a warehouse table can't hold (``money``, ``datetime``,
  ``nvarchar``, ``tinyint`` and the rest) is refused by name with Microsoft's alternative, as the warehouse refuses
  it: ``CREATE TABLE``, ``ALTER TABLE ... ADD`` / ``ALTER COLUMN``, ``CREATE TABLE AS SELECT``, ``SELECT ... INTO``
  and a ``CREATE VIEW`` column cast to one. ``datetime2`` and ``time`` keep at most 6 digits of a second in a
  table, so ``datetime2``, ``datetime2(7)``, ``time`` and ``time(7)`` are refused there and ``(6)`` named.
- Unsupported types still work in a query, as a warehouse documents ("in-memory use in session"), where the local
  engine gives T-SQL's value: ``CAST(... AS datetime)`` rounds to T-SQL's 1/300 of a second and
  ``smalldatetime`` to the minute, with T-SQL's range errors; ``nvarchar``, ``nchar``, ``text``, ``ntext``,
  ``image`` and ``tinyint`` hold the same values as ``varchar``, ``varbinary`` and a 0 to 255 integer here. The
  types the local engine has no form of (``datetimeoffset``, ``xml``, ``geography``, ``geometry``, ``json``,
  ``sql_variant``, ``rowversion``, ``hierarchyid``, ``vector`` and CLR types) are refused by name, as are
  ``money`` and ``smallmoney``.
- A ``#temp`` table can take ``nvarchar``, ``nchar``, ``text``, ``ntext``, ``image`` and ``tinyint`` with a note,
  since Microsoft's temp table page lists only the warehouse's table types; ``datetime`` and ``smalldatetime``
  columns are refused there, since a stored value isn't rounded as T-SQL rounds it.
- Sizes past T-SQL's limits (``varchar(8001)``, ``nvarchar(4001)``, ``decimal(39, 0)``, ``datetime2(8)``) stop, in
  T-SQL's words where they are known.
- A Delta table's timestamp column, which is written as a UTC timestamp, reads as ``datetime2`` (the UTC clock
  time, without a zone), as a warehouse's SQL analytics endpoint maps Delta's TIMESTAMP to ``datetime2``.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from ..sqlcompat.rules import TYPE_ALTERNATIVES

T = exp.DataType.Type
_NOT_YET = "This T-SQL can't be run on the local engine: "

# T-SQL's name for each type sqlglot reads, for the messages (sqlglot reads NTEXT as TEXT, TINYINT as UTINYINT,
# DATETIMEOFFSET as TIMESTAMPTZ, SQL_VARIANT as VARIANT and TIMESTAMP as ROWVERSION).
_NAMES = {T.MONEY: "money", T.SMALLMONEY: "smallmoney", T.DATETIME: "datetime", T.SMALLDATETIME: "smalldatetime",
          T.TIMESTAMPTZ: "datetimeoffset", T.NCHAR: "nchar", T.NVARCHAR: "nvarchar", T.TEXT: "text or ntext",
          T.IMAGE: "image", T.UTINYINT: "tinyint", T.TINYINT: "tinyint", T.GEOGRAPHY: "geography",
          T.GEOMETRY: "geometry", T.JSON: "json", T.XML: "xml", T.VARIANT: "sql_variant", T.ROWVERSION: "rowversion",
          T.VECTOR: "vector"}
_ALTERNATIVE_KEY = {T.MONEY: "MONEY", T.SMALLMONEY: "SMALLMONEY", T.DATETIME: "DATETIME",
                    T.SMALLDATETIME: "SMALLDATETIME", T.TIMESTAMPTZ: "DATETIMEOFFSET", T.NCHAR: "NCHAR",
                    T.NVARCHAR: "NVARCHAR", T.TEXT: "TEXT", T.IMAGE: "IMAGE", T.UTINYINT: "TINYINT",
                    T.TINYINT: "TINYINT", T.GEOGRAPHY: "GEOGRAPHY", T.GEOMETRY: "GEOMETRY", T.JSON: "JSON",
                    T.XML: "XML", T.VARIANT: "SQL_VARIANT", T.ROWVERSION: "ROWVERSION", T.VECTOR: "VECTOR"}
# Types a query can use that hold the same values here as in T-SQL.
_SAME_HERE = {T.NCHAR, T.NVARCHAR, T.TEXT, T.IMAGE, T.UTINYINT, T.TINYINT}
_ROUNDED = {T.DATETIME, T.SMALLDATETIME}
_FRACTIONS = {T.DATETIME2: "datetime2", T.TIME: "time"}
_TEMP_NOTE = ("A Fabric Warehouse's page on #temp tables lists only the types its tables hold, so it may refuse "
              "{names} in a #temp table that runs here. Use {alternatives} to be sure.")


def _name(kind: exp.DataType) -> str:
    if kind.this == T.USERDEFINED:
        return str(kind.args.get("kind") or "a user-defined type").lower()
    return _NAMES.get(kind.this, kind.this.value.lower())


def _alternative(kind: exp.DataType) -> str:
    if kind.this == T.USERDEFINED:
        key = str(kind.args.get("kind") or "").upper()
        if key == "SYSNAME":
            return "varchar(128) (sysname is nvarchar(128))"
        return TYPE_ALTERNATIVES.get(key, "a built-in type; CLR user-defined types have no equivalent")
    return TYPE_ALTERNATIVES[_ALTERNATIVE_KEY[kind.this]]


def _size(kind: exp.DataType, at: int = 0) -> int | None:
    """The whole number written in the type's parentheses at ``at`` (None for none, or MAX)."""
    if len(kind.expressions) <= at:
        return None
    size = kind.expressions[at]
    size = size.this if isinstance(size, exp.DataTypeParam) else size
    return int(size.name) if isinstance(size, exp.Literal) and size.is_int else None


def check(tree: exp.Expression) -> list[str]:
    """Refuse by name what a warehouse refuses or the local engine can't hold; returns notes. Run before the
    rewrites, while the tree has the types as written."""
    notes: list[str] = []
    _limits(tree)
    temporary = False
    for kind, column, temp in _kept(tree):
        temporary = temporary or temp
        if temp:
            continue
        if kind.this in _ALTERNATIVE_KEY or kind.this == T.USERDEFINED:
            raise ValueError(f"The data type {_name(kind)} isn't supported in a Fabric Warehouse table or view "
                             f"(column {column}), so it's refused here as the warehouse refuses it. Use "
                             f"{_alternative(kind)}.")
        if kind.this in _FRACTIONS and (_size(kind) is None or _size(kind) == 7):
            written = _FRACTIONS[kind.this]
            said = f"{written} with no precision is {written}(7)" if not kind.expressions else f"{written}(7)"
            raise ValueError(f"{said}, which keeps 7 digits of a second, and a Fabric Warehouse table keeps at most "
                             f"6 (column {column}), so it's refused here as the warehouse refuses it. Use "
                             f"{written}(6).")
    temp_kinds = [kind for kind, _, temp in _kept(tree) if temp]
    for kind in temp_kinds:
        if kind.this in _ROUNDED:
            raise ValueError(_NOT_YET + f"a {_name(kind)} column in a #temp table isn't mapped yet. Here a stored "
                             "value isn't rounded to T-SQL's steps. Use datetime2(3), or datetime2(0) for "
                             "smalldatetime.")
    noted = [k for k in temp_kinds if k.this in _SAME_HERE]
    if noted:
        names = sorted({_name(k) for k in noted})
        pairs = sorted({f"{_alternative(k)} for {_name(k)}" for k in noted})
        notes.append(_TEMP_NOTE.format(names=", ".join(names), alternatives=" and ".join(pairs)))
    for kind in list(tree.find_all(exp.DataType)):
        if kind.this == T.USERDEFINED and str(kind.args.get("kind") or "").upper() == "SYSNAME":
            kind.replace(exp.DataType.build("VARCHAR(128)"))  # sysname is nvarchar(128)
        elif kind.this == T.IMAGE:
            kind.replace(exp.DataType.build("VARBINARY"))  # image holds what varbinary(max) holds
        elif kind.this in (T.MONEY, T.SMALLMONEY):
            raise ValueError(_NOT_YET + "MONEY and SMALLMONEY aren't carried yet, and neither are their CONVERT "
                             "styles. Use DECIMAL(19, 4) for money and DECIMAL(10, 4) for smallmoney.")
        elif kind.this in _ALTERNATIVE_KEY and kind.this not in _SAME_HERE | _ROUNDED or kind.this == T.USERDEFINED:
            raise ValueError(_NOT_YET + f"the data type {_name(kind)} isn't mapped yet. The local engine has no form "
                             "of it, and a Fabric Warehouse table can't hold it either. Use "
                             f"{_alternative(kind)}, or run it in a Fabric Warehouse.")
    return notes


def _kept(tree: exp.Expression) -> list[tuple[exp.DataType, str, bool]]:
    """(type, column, in a #temp table) for each column a table or view would keep with its type as written."""
    out: list[tuple[exp.DataType, str, bool]] = []
    if isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() in ("TABLE", "VIEW"):
        target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        temp = isinstance(target, exp.Table) and target.name.startswith("#")
        if isinstance(tree.this, exp.Schema):
            out += [(c.args["kind"], c.name, temp) for c in tree.this.expressions
                    if isinstance(c, exp.ColumnDef) and isinstance(c.args.get("kind"), exp.DataType)]
        if isinstance(tree.expression, exp.Query):
            out += [(kind, name, temp) for kind, name in _projected(tree.expression)]
    elif isinstance(tree, exp.Alter) and (tree.args.get("kind") or "").upper() == "TABLE":
        temp = tree.this.name.startswith("#")
        for action in tree.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef) and isinstance(action.args.get("kind"), exp.DataType):
                out.append((action.args["kind"], action.name, temp))
            elif isinstance(action, exp.AlterColumn) and isinstance(action.args.get("dtype"), exp.DataType):
                out.append((action.args["dtype"], action.name, temp))
    elif isinstance(tree, exp.Query):
        into = tree.args.get("into") if isinstance(tree, exp.Select) else None
        if isinstance(into, exp.Into) and isinstance(into.this, exp.Table):
            out += [(kind, name, into.this.name.startswith("#")) for kind, name in _projected(tree)]
    return out


def _projected(query: exp.Query) -> list[tuple[exp.DataType, str]]:
    """The type each output column is cast to at the top of a query (every branch of a UNION)."""
    if isinstance(query, exp.SetOperation):
        return _projected(query.this) + _projected(query.expression)
    if isinstance(query, exp.Subquery):
        return _projected(query.this) if isinstance(query.this, exp.Query) else []
    out = []
    for item in query.selects:
        value = item.unalias()
        kind = value.args.get("to") if isinstance(value, exp.Cast) else \
            value.this if isinstance(value, exp.Convert) else None
        if isinstance(kind, exp.DataType):
            out.append((kind, item.alias_or_name or kind.sql(dialect="tsql")))
    return out


_MOST = {T.VARCHAR: 8000, T.CHAR: 8000, T.VARBINARY: 8000, T.BINARY: 8000, T.NVARCHAR: 4000, T.NCHAR: 4000}


def _limits(tree: exp.Expression) -> None:
    """T-SQL's errors for a size it doesn't take."""
    for kind in tree.find_all(exp.DataType):
        size = _size(kind)
        if size is None:
            continue
        name = "decimal" if kind.this == T.DECIMAL else kind.sql(dialect="tsql").split("(")[0].lower()
        column = kind.parent if isinstance(kind.parent, exp.ColumnDef) else None
        where = f"column '{column.name}'" if column is not None else f"type '{name}'"
        if kind.this in _MOST and size > _MOST[kind.this]:
            raise ValueError(f"The size ({size}) given to the {where} exceeds the maximum allowed for any data type "
                             f"({_MOST[kind.this]}).")
        if kind.this in _MOST and size < 1 or kind.this == T.DECIMAL and size < 1:
            raise ValueError(f"Length or precision specification {size} is invalid.")
        if kind.this == T.DECIMAL and size > 38:
            raise ValueError(f"{name}({size}) has more digits than T-SQL's {name} holds: its precision is at most 38.")
        if kind.this == T.DECIMAL and (_size(kind, 1) or 0) > size:
            raise ValueError(f"{name}({size}, {_size(kind, 1)}) has a scale larger than its precision, which T-SQL "
                             "doesn't take: the scale is 0 to the precision.")
        if kind.this in (T.DATETIME2, T.TIME, T.TIMESTAMPTZ) and size > 7:
            raise ValueError(f"Specified scale {size} is invalid.")


# ---------- datetime and smalldatetime: T-SQL's steps

_DAY = "DATE_TRUNC('DAY', kf_t)"
_TICKS = f"FLOOR(((EPOCH_US(kf_t) - EPOCH_US({_DAY})) * 3 + 5000) / 10000.0)"  # 1/300 s steps, half up, from midnight
_ROUND = {T.DATETIME: f"{_DAY} + TO_MILLISECONDS(CAST(FLOOR({_TICKS} * 10.0 / 3.0 + 0.5) AS BIGINT))",
          T.SMALLDATETIME: f"{_DAY} + TO_MINUTES(CAST(FLOOR(({_TICKS} + 9000) / 18000.0) AS BIGINT))"}
_RANGE = {T.DATETIME: ("1753-01-01 00:00:00", "9999-12-31 23:59:59.997"),
          T.SMALLDATETIME: ("1900-01-01 00:00:00", "2079-06-06 23:59:00")}
_TOO_PRECISE = re.compile(r"\.\d{4,}")


def round_datetimes(tree: exp.Expression) -> None:
    """``CAST``, ``CONVERT`` and their ``TRY_`` forms to ``datetime`` give T-SQL's value: rounded to 1/300 of a
    second (.000, .003, .007), ``smalldatetime`` to the minute (29.999 seconds rounds up), and T-SQL's error (or
    NULL, for TRY_) outside the type's range. Text with more than three digits of a second isn't a datetime in
    T-SQL, which stops with its conversion error. ``DATEADD`` of a datetime is a datetime, rounded the same way
    (adding 1 millisecond to .000 leaves .000, 2 gives .003), and ``DATETIMEFROMPARTS`` too.

    ``datetime2(n)`` and ``time(n)`` round to n digits of a second (WR-258): DuckDB has no 1, 2, 4 or 5 digit
    timestamp, nor any time precision, so those are rounded here, and a ``DATEADD`` on one keeps its n. Text with
    more than 7 digits of a second is T-SQL's conversion error, and text whose 7th digit isn't 0 is refused by name
    where T-SQL would keep or round on it: times here keep microseconds.

    Here each one is marked, still typed as written, so the passes that follow read its type and not the
    arithmetic that rounds it; ``expand_datetimes`` writes the rounding in once they've run."""
    for add in reversed(list(tree.find_all(exp.DateAdd, exp.TimestampFromParts))):  # inner first: a DATEADD of one
        kind = exp.DataType.build("DATETIME") if isinstance(add, exp.TimestampFromParts) else written_type(add.this)
        if isinstance(add, exp.TimestampFromParts) and add.args.get("milli") is None:
            continue
        unit = str(getattr(add.args.get("unit"), "name", "") or "").upper()
        if kind is not None and kind.this in _ROUNDED and unit in _BELOW_A_MILLISECOND:
            raise ValueError(f"The datepart {_BELOW_A_MILLISECOND[unit]} is not supported by date function dateadd "
                             f"for data type {_NAMES[kind.this]}.")
        if kind is not None and (kind.this in _ROUNDED or _steps(kind) is not None):
            _mark(add, kind, safe=False, message=f"Adding a value to a '{_NAMES.get(kind.this, 'datetime2')}' "
                  "column caused an overflow.")
    for node in list(tree.find_all(exp.Cast, exp.Convert)):
        if node.meta.get("kf_rounded"):
            continue
        kind = node.args.get("to") if isinstance(node, exp.Cast) else node.this
        if not isinstance(kind, exp.DataType):
            continue
        safe = bool(node.args.get("safe")) or isinstance(node, exp.TryCast)
        source = node.this if isinstance(node, exp.Cast) else node.expression
        text = source.name if isinstance(source, exp.Literal) and source.is_string else None
        if kind.this in _FRACTIONS:
            if text is not None and _PAST_SEVEN.search(text):
                node.replace(exp.null() if safe else _fail("Conversion failed when converting date and/or time from "
                                                             "character string."))
                continue
            if text is not None and _SEVENTH.search(text) and _fraction_size(kind) >= 6:
                raise ValueError(_NOT_YET + f"'{text}' has a 7th digit of a second, which T-SQL's "
                                 f"{_FRACTIONS[kind.this]} keeps or rounds on, and the local engine keeps times to the "
                                 "microsecond, so nothing ran. Write at most 6 digits of a second, or run it in a "
                                 "Fabric Warehouse.")
            if _steps(kind) is not None:
                _mark(node, kind, safe=safe, message="")
            continue
        if kind.this not in _ROUNDED:
            continue
        if text is not None and _TOO_PRECISE.search(text):
            node.replace(exp.null() if safe else _fail("Conversion failed when converting date and/or time from "
                                                         "character string."))
            continue
        target = "datetime" if kind.this == T.DATETIME else "smalldatetime"
        _mark(node, kind, safe=safe, message=f"The conversion of a {'varchar' if text is not None else 'datetime2'} "
              f"data type to a {target} data type resulted in an out-of-range value.")


_MARK = "KF_TSQL_DATETIME"
_PAST_SEVEN = re.compile(r"\.\d{8,}")  # more digits of a second than datetime2 and time hold
_SEVENTH = re.compile(r"\.\d{6}[1-9]")
_BELOW_A_MILLISECOND = {"MICROSECOND": "microsecond", "MCS": "microsecond", "NANOSECOND": "nanosecond",
                        "NS": "nanosecond"}


def _fraction_size(kind: exp.DataType) -> int:
    size = _size(kind)
    return 7 if size is None else size


def _steps(kind: exp.DataType) -> int | None:
    """The digits of a second a datetime2(n) or time(n) with n written below 6 rounds to: DuckDB has TIMESTAMP_S
    and TIMESTAMP_MS but no 1, 2, 4 or 5 digit timestamp, its TIME takes no precision, and DATEADD on its
    TIMESTAMP_MS gives a TIMESTAMP."""
    if kind.this not in _FRACTIONS or _size(kind) is None:
        return None
    size = _fraction_size(kind)
    return size if size < 6 else None


def written_type(value: exp.Expression) -> exp.DataType | None:
    """The type a value is cast or converted to as written, under parentheses."""
    while isinstance(value, exp.Paren):
        value = value.this
    if isinstance(value, exp.Cast):
        kind = value.args.get("to")
    elif isinstance(value, exp.Convert):
        kind = value.this
    else:
        return None
    return kind if isinstance(kind, exp.DataType) else None


def _mark(node: exp.Expr, kind: exp.DataType, safe: bool, message: str) -> None:
    """Wrap ``node`` in the mark ``expand_datetimes`` rounds to ``kind``. What the rounding needs rides in the
    mark's arguments, which survive the rewrites that rebuild the tree around it (CONVERT's styles make a new
    node; meta would be lost). A datetime2(n) or time(n) cast inside is made the plain type, so DuckDB doesn't
    round it first."""
    kind = kind.copy()
    if kind.this in _ROUNDED:
        target = "datetime" if kind.this == T.DATETIME else "smalldatetime"
    else:
        target = f"{_FRACTIONS[kind.this]}:{_steps(kind)}"
        inner = node.args.get("to") if isinstance(node, exp.Cast) else node.this if isinstance(node, exp.Convert) \
            else None
        if isinstance(inner, exp.DataType):
            inner.set("expressions", [])
    mark = exp.Anonymous(this=_MARK, expressions=[exp.Literal.string(target), exp.Literal.string(
        "safe" if safe else "strict"), exp.Literal.string(message)])
    outer = exp.Cast(this=mark, to=kind.copy())
    outer.meta["kf_rounded"] = True
    node.replace(outer)
    mark.set("expressions", [node, *mark.expressions])


# A datetime2(n) value to n digits of a second, half up, within its second (so never negative); a time on a day.
_STEPPED = ("DATE_TRUNC('SECOND', kf_t) + TO_MICROSECONDS((EPOCH_US(kf_t) - EPOCH_US(DATE_TRUNC('SECOND', kf_t)) "
            "+ {half}) // {step} * {step})")


def expand_datetimes(tree: exp.Expression) -> None:
    """The rounding ``round_datetimes`` marked, written as DuckDB SQL around the value, which is read once."""
    marks = [m for m in tree.find_all(exp.Anonymous) if m.name == _MARK]
    for mark in reversed(marks):  # the innermost first, so an outer one copies its value already rounded
        value, target, safe, message = mark.expressions
        if ":" in target.name:
            name, digits = target.name.split(":")
            step = 10 ** (6 - int(digits))
            body = _STEPPED.format(half=step // 2, step=step)
            if name == "time":
                body = f"CAST({body} AS TIME)"
                value = exp.Add(this=exp.cast(exp.Literal.string("1970-01-01"), "DATE"), expression=value)
            sql = f"LIST_TRANSFORM([X], kf_t -> {body})[1]"
        else:
            kind = T.DATETIME if target.name == "datetime" else T.SMALLDATETIME
            low, high = _RANGE[kind]
            rounded = _ROUND[kind]
            sql = (f"LIST_TRANSFORM([X], kf_t -> CASE WHEN ({rounded}) < CAST('{low}' AS TIMESTAMP) OR ({rounded}) > "
                   f"CAST('{high}' AS TIMESTAMP) THEN {'NULL' if safe.name == 'safe' else 'KF_FAIL'} ELSE ({rounded}) "
                   "END)[1]")
        wrapper = sqlglot.parse_one(sql, read="duckdb")
        for spot in list(wrapper.find_all(exp.Column)):
            if spot.name == "X":
                spot.replace(value.copy())
            elif spot.name == "KF_FAIL":
                spot.replace(_fail(message.name))
        mark.replace(wrapper)


def _fail(message: str) -> exp.Expression:
    return exp.Anonymous(this="error", expressions=[exp.Literal.string(message)])


# ---------- Delta timestamps read as datetime2

_AWARE = "TIMESTAMP WITH TIME ZONE"


def delta_times(tree: exp.Expression, columns: dict[str, dict[str, dict[str, str]]] | None) -> None:
    """A table's UTC timestamp columns read as ``datetime2``: the UTC clock time without a zone, as a warehouse's
    SQL analytics endpoint reads a Delta TIMESTAMP. Only where a query reads a table (FROM and JOIN), so a
    statement's target is left as it is."""
    if not columns or isinstance(tree, (exp.Update, exp.Delete, exp.Merge)):
        return
    known = {s.lower(): {t.lower(): cols for t, cols in tables.items()} for s, tables in columns.items()}
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    qualified = {(c.args["db"].name.lower(), c.table.lower()) for c in tree.find_all(exp.Column) if c.args.get("db")}
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.parent, (exp.From, exp.Join)) or not isinstance(table.this, exp.Identifier):
            continue
        name, schema = table.name.lower(), (table.db or "dbo").lower()
        if table.catalog or name.startswith("#") or (not table.db and name in ctes) or (schema, name) in qualified:
            continue
        aware = [c for c, kind in known.get(schema, {}).get(name, {}).items() if kind.upper() == _AWARE]
        if not aware:
            continue
        star = exp.Star(replace=[exp.alias_(exp.cast(exp.column(c, quoted=True), "TIMESTAMP"), c, quoted=True)
                                 for c in aware])
        alias = table.args.get("alias")
        inner = table.copy()
        inner.set("alias", None)
        query = exp.select(star).from_(inner)
        table.replace(exp.Subquery(this=query, alias=alias.copy() if alias else
                                   exp.TableAlias(this=exp.to_identifier(table.name, quoted=True))))


def is_rounded(value: exp.Expression) -> bool:
    """``value`` is a datetime or smalldatetime that ``round_datetimes`` marked, under the casts that the CONVERT
    rewrite wraps it in (that rewrite goes through SQL text, so the type as written is lost there)."""
    while isinstance(value, (exp.Cast, exp.Paren)):
        value = value.this
    return isinstance(value, exp.Anonymous) and value.name.upper() == _MARK \
        and value.expressions[1].name in ("datetime", "smalldatetime")
