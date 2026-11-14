"""The name Spark 3.5 gives a column of spark.sql that has no AS, worked out from the query as written.

Spark names such a column after its analysed expression (Analyzer.ResolveAliases, toPrettySQL): a column
cast to a type keeps the column's name, a string constant is written without its quotes (upper('a') is
``upper(a)``), each operator sits in parentheses, a function is named as it was called (char_length, not
length) with the arguments Spark fills in (locate(b, s, 1)), and a window names its frame in full. DuckDB
names a column after its own SQL, so every such column is given Spark's name here.

The name a function was called by, and the text of each constant, are read from the query text when it is
parsed (``remember``): sqlglot reads char_length and length into one node, and 1.5d into a cast."""

from __future__ import annotations

import re
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

_NAME = "fd_spark_name"     # meta: the function name as written
_TEXT = "fd_spark_text"     # meta: a constant as written
_BEFORE = "fd_spark_before"  # meta: the word written before a constant (DATE in DATE '2024-01-01')
_IN_FORM = "fd_spark_in"    # meta: position('b' IN s), which Spark names locate(b, s, 1)
_ARGS = "fd_spark_args"     # meta: a call's arguments as written, one text each
_SPECIAL_FORM = "fd_spark_special"  # meta: a call written with FROM, IN, DISTINCT, a lambda...
_FIELD = "fd_spark_field"   # meta: the field of extract(field FROM x) as written

# Functions Spark names in capitals (their own sql), unless called by another name (ceiling, pow, sign).
_UPPER = {"acos", "asin", "atan", "atan2", "acosh", "asinh", "atanh", "cos", "sin", "tan", "cot", "csc", "sec",
          "cosh", "sinh", "tanh", "cbrt", "ceil", "floor", "exp", "expm1", "log2", "log1p", "log10", "signum",
          "degrees", "radians", "hypot", "pi", "e", "sqrt", "power", "rank", "dense_rank", "percent_rank",
          "spark_partition_id", "regexp_like"}

# Called by one name, named by another.
_RENAMED = {"array_agg": "collect_list", "dateadd": "date_add"}

# Arguments Spark fills in when fewer are given: name -> the full list's tail, as Spark writes it.
_DEFAULTS: dict[str, tuple[int, list[str]]] = {
    "locate": (2, ["1"]), "position": (2, ["1"]), "split": (2, ["-1"]), "regexp_replace": (3, ["1"]),
    "regexp_extract": (2, ["1"]), "regexp_extract_all": (2, ["1"]), "regexp_instr": (2, ["0"]),
    "lpad": (2, [" "]), "rpad": (2, [" "]), "round": (1, ["0"]), "bround": (1, ["0"]),
    "substring": (2, ["2147483647"]), "substr": (2, ["2147483647"]), "percentile": (2, ["1"]),
    "percentile_approx": (2, ["10000"]), "approx_percentile": (2, ["10000"]), "replace": (2, [""]),
    "overlay": (3, ["-1"]), "sentences": (1, ["", ""]), "sort_array": (1, ["true"]),
    "months_between": (2, ["true"]), "from_unixtime": (1, ["yyyy-MM-dd HH:mm:ss"]),
    "to_unix_timestamp": (1, ["yyyy-MM-dd HH:mm:ss"]), "str_to_map": (1, [",", ":"]), "lag": (1, ["1", "NULL"]), "lead": (1, ["1", "NULL"]),
}
_SHIFTS = ("lag", "lead")
_RANKS = {"row_number", "rank", "dense_rank", "percent_rank", "ntile"}

_OPERATORS: dict[type, str] = {
    exp.Add: "+", exp.Sub: "-", exp.Mul: "*", exp.Div: "/", exp.Mod: "%", exp.IntDiv: "div", exp.EQ: "=",
    exp.NullSafeEQ: "<=>", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=", exp.And: "AND", exp.Or: "OR",
    exp.BitwiseAnd: "&", exp.BitwiseOr: "|", exp.BitwiseXor: "^",
}
# Calls Spark reads as a cast: string(i) is CAST(i AS STRING), named after i when it is the whole column.
_CASTS = {"string": "STRING", "int": "INT", "bigint": "BIGINT", "smallint": "SMALLINT", "tinyint": "TINYINT",
          "double": "DOUBLE", "float": "FLOAT", "boolean": "BOOLEAN", "date": "DATE", "timestamp": "TIMESTAMP",
          "binary": "BINARY", "decimal": "DECIMAL(10,0)"}
# Conversions sqlglot puts around an argument on its own.
_WRAPPERS = tuple(getattr(exp, n) for n in ("TsOrDsToDate", "TimeStrToTime", "TsOrDsToTimestamp", "TimeStrToDate")
                  if hasattr(exp, n))
_SORTED_ARRAY = ("lambdafunction((IF(((namedlambdavariable() IS NULL) AND (namedlambdavariable() IS NULL)), 0, "
                 "(IF((namedlambdavariable() IS NULL), 1, (IF((namedlambdavariable() IS NULL), -1, "
                 "(IF((namedlambdavariable() < namedlambdavariable()), -1, (IF((namedlambdavariable() > "
                 "namedlambdavariable()), 1, 0)))))))))), namedlambdavariable(), namedlambdavariable())")
_TYPED_WORDS = {"DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ"}


def remember(statements: list[Any], text: str) -> None:
    """Keep what the AST loses on each node of ``statements`` read from ``text``: the name each function
    was called by, the text of each of its arguments, and the text of each constant."""
    try:
        tokens = list(sqlglot.tokenize(text, read="spark")) if any(isinstance(s, exp.Expression) for s in statements) else []
    except (TokenError, ParseError):  # silent-ok: unreadable text has no names to keep; the parse said why
        tokens = []
    at = {t.start: i for i, t in enumerate(tokens)}
    for statement in statements:
        if not isinstance(statement, exp.Expression):
            continue
        for node in statement.walk():
            start, end = node.meta.get("start"), node.meta.get("end")
            if isinstance(node, exp.Extract):
                _keep_field(node, text)
            if start is None and isinstance(node, (exp.Func, exp.Binary, exp.Bracket)) and not isinstance(node, exp.Column):
                _recover_call(node, tokens, at, text)
                continue
            if start is None or end is None or not 0 <= start <= end < len(text):
                continue
            written = text[start:end + 1]
            if isinstance(node, exp.Literal):
                node.meta[_TEXT] = written
                before = re.search(r"([A-Za-z_]+)\s*$", text[:start])
                if before:
                    node.meta[_BEFORE] = before.group(1).upper()
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", written) and not isinstance(node, (exp.Identifier, exp.Var)):
                node.meta[_NAME] = written.lower()
                if start in at:
                    _keep_arguments(node, tokens, at[start], text)
                _check_call(node)


def _keep_field(node: exp.Extract, text: str) -> None:
    """Spark names extract(Hour FROM s) with the field as written; sqlglot keeps it in capitals."""
    at = _position(node)
    found = re.search(r"\(\s*([A-Za-z_]+)\s+FROM\s*$", text[:at], re.IGNORECASE) if at is not None else None
    if found and found.group(1).upper() == str(node.this.name).upper():
        node.meta[_FIELD] = found.group(1)


def _recover_call(node: exp.Expr, tokens: list[Any], at: dict[int, int], text: str) -> None:
    """A call sqlglot keeps no place for (ceil(d), mod(i, 3), element_at(a, 1)): its name is the word
    written just before its first argument's opening parenthesis."""
    first = _position(node)
    if first is None:
        return
    found = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*$", text[:first])
    if not found or found.group(1).lower() in ("cast", "try_cast", "in", "over", "filter", "exists", "when", "then",
                                                "else", "and", "or", "not", "select", "where", "on", "as", "by"):
        return
    start = found.start(1)
    if start not in at:
        return
    node.meta[_NAME] = found.group(1).lower()
    _keep_arguments(node, tokens, at[start], text)
    _check_call(node)


def _check_call(node: exp.Expr) -> None:
    """Forget the call on a node that isn't one (upper(i % 3): the % isn't upper): one that isn't a function
    in sqlglot is the call only when the arguments are as many."""
    given = node.meta.get(_ARGS)
    if not isinstance(node, exp.Func) and (given is None or len(given) != len(_arguments(node))):
        for key in (_NAME, _ARGS, _SPECIAL_FORM, _IN_FORM):
            node.meta.pop(key, None)


# Words that make a call's arguments more than a comma-separated list: these are named from the AST.
_SPECIAL = {TokenType.FROM, TokenType.IN, TokenType.FOR, TokenType.DISTINCT, TokenType.ARROW, TokenType.ALL,
            TokenType.STAR, TokenType.ORDER_BY, TokenType.SEPARATOR}


def _keep_arguments(node: exp.Expr, tokens: list[Any], i: int, text: str) -> None:
    if i + 1 >= len(tokens) or tokens[i + 1].token_type != TokenType.L_PAREN:
        return
    depth, pieces, begin, special = 0, [], None, False
    for token in tokens[i + 1:]:
        kind = token.token_type
        if kind in (TokenType.L_PAREN, TokenType.L_BRACKET, TokenType.L_BRACE):
            depth += 1
            if depth == 1:
                continue
        elif kind in (TokenType.R_PAREN, TokenType.R_BRACKET, TokenType.R_BRACE):
            depth -= 1
            if depth == 0:
                if begin is not None:
                    pieces.append(text[begin:token.start].strip())
                break
        elif depth == 1 and kind == TokenType.COMMA:
            pieces.append(text[begin:token.start].strip() if begin is not None else "")
            begin = None
            continue
        if depth == 1 and (kind in _SPECIAL or token.text.upper() in ("PLACING", "BOTH", "LEADING", "TRAILING")):
            special = True
        if begin is None:
            begin = token.start
    else:
        return
    if special or any(not p for p in pieces):
        node.meta[_SPECIAL_FORM] = True
        if node.meta.get(_NAME) == "position" and any(t.token_type == TokenType.IN for t in tokens[i + 2:i + 6]):
            node.meta[_IN_FORM] = True
        return
    node.meta[_ARGS] = pieces


def kept(node: exp.Expr) -> dict[str, Any]:
    """What ``remember`` keeps on ``node``, for a rewrite that replaces it with another node."""
    return {k: v for k, v in node.meta.items() if k.startswith("fd_spark_")}


def column_name(column: exp.Expression, scope: exp.Expression | None = None) -> str | None:
    """Spark's name for ``column``, a selected expression without AS; None for one it names otherwise (a
    column, a star, a generator that makes more than one column)."""
    if isinstance(column, (exp.Alias, exp.Column, exp.Star)):
        return None
    qualifiers = _qualifiers(scope if scope is not None else column.root())
    if column.meta.get(_NAME) in ("window", "session_window"):  # TimeWindowing names the struct it makes
        return str(column.meta[_NAME])
    if isinstance(column, (exp.Cast, exp.TryCast)) and not _typed_literal(column) and isinstance(column.this, exp.Column):
        return _column(column.this, qualifiers, last=True)  # ResolveAliases: Cast(named) keeps the name
    if column.meta.get(_NAME) in _CASTS and isinstance(column, exp.Func):
        given = column.meta.get(_ARGS) or []
        if len(given) == 1 and re.fullmatch(r"`?[A-Za-z_][\w]*`?(\.`?[A-Za-z_][\w]*`?)*", given[0]):
            return given[0].split(".")[-1].strip("`")
    if isinstance(column, (exp.Posexplode, exp.Inline, exp.Stack)) or (
            isinstance(column, exp.Anonymous) and column.name.lower() in ("json_tuple", "stack")):
        return None  # a generator of several columns: each is named by the rewrite that makes it
    if isinstance(column, exp.Explode):  # explode and explode_outer make one column, col
        return "col"
    try:
        return _Printer(qualifiers).pretty(column)
    except _Unknown:
        return None


class _Unknown(Exception):
    """An expression this module can't name as Spark does."""


def _qualifiers(root: exp.Expr) -> set[str]:
    out = set()
    for table in root.find_all(exp.Table):
        out.add(table.name.lower())
        if table.alias:
            out.add(table.alias.lower())
    for sub in root.find_all(exp.Subquery):
        if sub.alias:
            out.add(sub.alias.lower())
    return out


def _column(column: exp.Column, qualifiers: set[str], last: bool = False) -> str:
    parts = [p.name for p in column.parts]
    if last:
        return parts[-1]
    if len(parts) > 1 and parts[0].lower() in qualifiers:
        parts = parts[1:]
    return ".".join(parts)


def _typed_literal(node: exp.Expression) -> str | None:
    """The name Spark gives a typed constant read as a cast (DATE '2024-01-01', 1.5d, 0.1BD), else None."""
    if not isinstance(node, (exp.Cast, exp.TryCast)) or not isinstance(node.this, exp.Literal):
        return None
    literal = node.this
    written = literal.meta.get(_TEXT)
    if written is None:
        # 0.1BD reaches here as CAST('0.1' AS DECIMAL(1,1)) with no position in the query
        if literal.is_string and node.to.this == exp.DataType.Type.DECIMAL and re.fullmatch(r"-?\d+(\.\d*)?", literal.this):
            return str(literal.this)
        return None
    if not literal.is_string and re.search(r"[A-Za-z]$", written):  # 1.5d, 10L, 10Y, 10S
        return str(literal.this)
    before = literal.meta.get(_BEFORE)
    if literal.is_string and before in _TYPED_WORDS and not written.startswith('"'):
        return f"{'TIMESTAMP' if before.startswith('TIMESTAMP') else before} '{literal.this}'"
    return None


def _called(node: exp.Expression) -> str:
    """The name ``node`` was called by, lower case."""
    written = node.meta.get(_NAME)
    if written:
        return str(written)
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower() if isinstance(node, exp.Func) else type(node).__name__.lower()


def _position(node: exp.Expr) -> int | None:
    best = None
    for sub in node.walk():
        start = sub.meta.get("start")
        if start is not None and (best is None or start < best):
            best = start
    return best


def _arguments(node: exp.Expr) -> list[exp.Expression]:
    """``node``'s arguments in the order they were written (sqlglot keeps its own order: locate('b', s)
    is StrPosition(s, 'b')); one sqlglot added (no place in the query) keeps its place after the one before."""
    given: list[exp.Expression] = []
    for key in node.arg_types:
        value = node.args.get(key)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, exp.Expression):
                given.append(item)
    keyed, last = [], -1
    for i, item in enumerate(given):
        at = _position(item)
        last = at if at is not None else last
        keyed.append((last, i, item))
    return [item for _, _, item in sorted(keyed, key=lambda k: (k[0], k[1]))]


class _Printer:
    def __init__(self, qualifiers: set[str]) -> None:
        self.qualifiers = qualifiers
        self.lambda_vars: set[str] = set()

    def args(self, items: list[exp.Expression]) -> str:
        return ", ".join(self.pretty(a) for a in items)

    def pretty(self, node: exp.Expression) -> str:  # noqa: C901 - one case per kind of expression
        typed = _typed_literal(node)
        if typed is not None:
            return typed
        if node.meta.get(_NAME) and not isinstance(node, (exp.Window, exp.Trim, exp.Extract)) \
                and not node.meta.get(_SPECIAL_FORM) or isinstance(node, exp.StrPosition):
            return self.function(node)
        if isinstance(node, exp.Literal):
            return str(node.this) if node.is_string else _number(node)
        if isinstance(node, exp.Boolean):
            return "true" if node.this else "false"
        if isinstance(node, exp.Null):
            return "NULL"
        if isinstance(node, exp.HexString):
            return f"X'{str(node.this).upper()}'"
        if isinstance(node, exp.Column):
            if not node.table and node.name in self.lambda_vars:
                return "namedlambdavariable()"
            return _column(node, self.qualifiers)
        if isinstance(node, exp.Identifier):
            return "namedlambdavariable()" if node.name in self.lambda_vars else str(node.name)
        if isinstance(node, exp.Var):
            return str(node.this)
        if isinstance(node, exp.Star):
            return "*"
        if isinstance(node, exp.Paren):
            return self.pretty(node.this)
        if isinstance(node, exp.Alias):  # struct(s AS a)
            return f"{self.pretty(node.this)} AS {node.alias}"
        if isinstance(node, exp.Neg):
            if isinstance(node.this, exp.Literal) and not node.this.is_string:
                return f"-{node.this.this}"
            return f"(- {self.pretty(node.this)})"
        if isinstance(node, exp.BitwiseNot):
            return f"~{self.pretty(node.this)}"
        if isinstance(node, exp.NEQ):
            return f"(NOT ({self.pretty(node.this)} = {self.pretty(node.expression)}))"
        if isinstance(node, exp.DPipe):
            return f"concat({self.pretty(node.this)}, {self.pretty(node.expression)})"
        for kind, symbol in _OPERATORS.items():
            if type(node) is kind:
                return f"({self.pretty(node.this)} {symbol} {self.pretty(node.expression)})"
        if isinstance(node, exp.Not):
            inner = node.this.unnest()
            if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                return f"({self.pretty(inner.this)} IS NOT NULL)"
            if isinstance(inner, exp.Like):
                return f"(NOT {self.pretty(inner)})"
            return f"(NOT {self.pretty(inner)})"
        if isinstance(node, exp.Is):
            right = node.expression
            return f"({self.pretty(node.this)} IS {self.pretty(right)})"
        if isinstance(node, exp.In):
            if node.args.get("query") is not None or node.args.get("unnest") is not None:
                raise _Unknown
            return f"({self.pretty(node.this)} IN ({self.args(node.expressions)}))"
        if isinstance(node, exp.Between):
            value = self.pretty(node.this)
            return f"(({value} >= {self.pretty(node.args['low'])}) AND ({value} <= {self.pretty(node.args['high'])}))"
        if isinstance(node, exp.Like):
            text = f"{self.pretty(node.this)} LIKE {self.pretty(node.expression)}"
            return f"(NOT {text})" if node.args.get("negate") else text
        if isinstance(node, exp.Escape):
            return self.pretty(node.this)
        if isinstance(node, exp.ILike):
            return f"ilike({self.pretty(node.this)}, {self.pretty(node.expression)})"
        if isinstance(node, exp.RegexpLike):
            name = "REGEXP_LIKE" if node.meta.get(_NAME) == "regexp_like" else "RLIKE"
            return f"{name}({self.pretty(node.this)}, {self.pretty(node.expression)})"
        if isinstance(node, exp.If) and not isinstance(node.parent, exp.Case):
            return (f"(IF({self.pretty(node.this)}, {self.pretty(node.args['true'])}, "
                    f"{self.pretty(node.args['false']) if node.args.get('false') is not None else 'NULL'}))")
        if isinstance(node, exp.Case):
            subject = node.this
            whens = []
            for when in node.args.get("ifs") or []:
                test = self.pretty(when.this)
                if subject is not None:
                    test = f"({self.pretty(subject)} = {test})"
                whens.append(f"WHEN {test} THEN {self.pretty(when.args['true'])}")
            default = node.args.get("default")
            return "CASE " + " ".join(whens) + (f" ELSE {self.pretty(default)}" if default is not None else "") + " END"
        if isinstance(node, (exp.Cast, exp.TryCast)):
            return f"CAST({self.pretty(node.this)} AS {_type(node.to)})"
        if isinstance(node, exp.Interval):
            unit = node.args.get("unit")
            if unit is None or not isinstance(node.this, exp.Literal):
                raise _Unknown
            return f"INTERVAL '{node.this.this}' {str(unit.name).upper()}"
        if isinstance(node, exp.Bracket):
            return f"{self.pretty(node.this)}[{self.args(node.expressions)}]"
        if isinstance(node, exp.Dot):
            return f"{self.pretty(node.this)}.{node.expression.name}"
        if isinstance(node, exp.Struct):
            if _called(node) == "named_struct":
                return "named_struct(" + ", ".join(
                    f"{self.pretty(p.this)}, {self.pretty(p.expression)}" for p in node.expressions) + ")"
            items = []
            for item in node.expressions:
                if isinstance(item, (exp.PropertyEQ, exp.Alias)) and isinstance(item, exp.PropertyEQ):
                    value = self.pretty(item.expression)
                    named = item.this.name
                    shown = item.expression.name if isinstance(item.expression, exp.Column) else None
                    items.append(value if named == shown else f"{value} AS {named}")
                else:
                    items.append(self.pretty(item))
            return f"struct({', '.join(items)})"
        if isinstance(node, exp.VarMap):
            keys, values = node.args["keys"].expressions, node.args["values"].expressions
            return "map(" + ", ".join(f"{self.pretty(k)}, {self.pretty(v)}" for k, v in zip(keys, values, strict=False)) + ")"
        if isinstance(node, exp.Array):
            return f"array({self.args(node.expressions)})"
        if isinstance(node, exp.Extract):
            return f"extract({node.meta.get(_FIELD) or str(node.this.name).lower()} FROM {self.pretty(node.expression)})"
        if isinstance(node, exp.Trim):
            position = node.args.get("position")
            chars = node.args.get("expression")
            if position:
                return f"TRIM({str(position).upper()} {self.pretty(chars) if chars is not None else ''} FROM {self.pretty(node.this)})".replace("  ", " ")
            if chars is not None and _called(node) in ("trim", "btrim"):
                return f"{_called(node)}({self.pretty(node.this)}, {self.pretty(chars)})"
        if isinstance(node, exp.Window):
            return self.window(node)
        if isinstance(node, exp.Filter):
            return f"{self.pretty(node.this)} FILTER (WHERE {self.pretty(node.expression.this)})"
        if isinstance(node, exp.IgnoreNulls):
            return self.pretty(node.this)
        if isinstance(node, exp.Distinct):
            return "DISTINCT " + self.args(node.expressions)
        if isinstance(node, exp.Lambda):
            names = [p.name for p in node.expressions]
            saved = set(self.lambda_vars)
            self.lambda_vars |= set(names)
            body = self.pretty(node.this)
            self.lambda_vars = saved
            return f"lambdafunction({body}, {', '.join('namedlambdavariable()' for _ in names)})"
        if isinstance(node, (exp.Func, exp.Anonymous)):
            return self.function(node)
        raise _Unknown

    def reread(self, text: str) -> exp.Expression:
        """One argument as written, read on its own (sqlglot reads a call's arguments into its own shape:
        to_date's format as %Y, element_at as a subscript, its own default arguments added)."""
        try:
            read = sqlglot.parse_one(f"SELECT {text}", read="spark")
        except (TokenError, ParseError):  # silent-ok: named from the AST instead (_Unknown)
            raise _Unknown from None
        remember([read], f"SELECT {text}")
        items = read.expressions if isinstance(read, exp.Select) else []
        if len(items) != 1:
            raise _Unknown
        return cast(exp.Expression, items[0])

    def function(self, node: exp.Expression) -> str:  # noqa: C901 - Spark's own spellings, one by one
        name = _called(node)
        if isinstance(node, exp.Count) and isinstance(node.this, exp.Star):
            return "count(1)"
        if name in ("current_date", "current_timestamp") and not node.meta.get(_ARGS):
            return f"{name}()"
        written = node.meta.get(_ARGS)
        if written is not None:
            args: list[exp.Expression] = [self.reread(a) for a in written]
        elif isinstance(node, _WRAPPERS) and node.meta.get(_NAME) is None:
            return self.pretty(node.this)  # a wrapper sqlglot added (ts_or_ds_to_date)
        else:
            args = list(node.expressions) if isinstance(node, exp.Anonymous) else _arguments(node)
        if isinstance(node, exp.StrPosition) and node.meta.get(_IN_FORM):
            name = "locate"
        if name in ("isnull", "isnotnull") and len(args) == 1:
            return f"({self.pretty(args[0])} IS {'NOT ' if name == 'isnotnull' else ''}NULL)"
        if name == "if" and len(args) == 3:
            return f"(IF({self.args(args)}))"
        if name == "positive" and len(args) == 1:
            return f"(+ {self.pretty(args[0])})"
        if name == "log":
            return f"LOG({'E(), ' if len(args) == 1 else ''}{self.args(args)})"
        if name in _CASTS and len(args) == 1:
            return self.pretty(args[0])  # Spark writes a cast it made (double(x)) as its value; CAST is one written
        if name in ("first", "last", "first_value", "last_value") and len(args) == 2 and isinstance(args[1], exp.Boolean):
            args = args[:1]
        if name == "from_json":
            args = args[:1]
        if name == "array_sort" and len(args) == 1:
            return f"array_sort({self.pretty(args[0])}, {_SORTED_ARRAY})"
        if name == "aggregate" and len(args) == 3:
            return f"aggregate({self.args(args)}, lambdafunction(namedlambdavariable(), namedlambdavariable()))"
        if name in ("unix_timestamp", "to_unix_timestamp") and len(args) <= 1:
            first = self.pretty(args[0]) if args else "current_timestamp()"
            return f"{name}({first}, yyyy-MM-dd HH:mm:ss)"
        if name in ("datediff", "date_diff") and len(args) == 3:
            name = "timestampdiff"
        shown = [self.pretty(a) for a in args]
        if name in _DEFAULTS:
            given, tail = _DEFAULTS[name]
            if given <= len(shown) < given + len(tail):
                shown += tail[len(shown) - given:]
        name = _RENAMED.get(name, name)
        if name in _UPPER:
            name = name.upper()
        return f"{name}({', '.join(shown)})"

    def window(self, node: exp.Window) -> str:
        func = node.this
        inner = func.this if isinstance(func, (exp.IgnoreNulls, exp.RespectNulls)) else func
        called = _called(inner)
        parts = []
        partition = node.args.get("partition_by") or []
        if partition:
            parts.append("PARTITION BY " + self.args(partition))
        order = node.args.get("order")
        if order is not None:
            parts.append("ORDER BY " + ", ".join(self.ordered(o) for o in order.expressions))
        spec = node.args.get("spec")
        if spec is not None:
            parts.append(f"{str(spec.args.get('kind') or 'ROWS').upper()} BETWEEN {self.bound(spec, 'start')} AND {self.bound(spec, 'end')}")
        elif called in _SHIFTS:
            offset = inner.args.get("offset")
            n = int(offset.this) if isinstance(offset, exp.Literal) and not offset.is_string else 1
            n = -n if called == "lag" else n
            parts.append(f"ROWS BETWEEN {n} FOLLOWING AND {n} FOLLOWING")
        elif called in _RANKS:
            parts.append("ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")
        elif order is not None:
            parts.append("RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")
        else:
            parts.append("ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING")
        return f"{self.pretty(func)} OVER ({' '.join(parts)})"

    def ordered(self, item: exp.Expression) -> str:
        if not isinstance(item, exp.Ordered):
            return f"{self.pretty(item)} ASC NULLS FIRST"
        desc = bool(item.args.get("desc"))
        nulls_first = item.args.get("nulls_first")
        if nulls_first is None:
            nulls_first = not desc
        return f"{self.pretty(item.this)} {'DESC' if desc else 'ASC'} NULLS {'FIRST' if nulls_first else 'LAST'}"

    def bound(self, spec: exp.Expression, which: str) -> str:
        value = spec.args.get(which)
        side = spec.args.get(f"{which}_side")
        text = str(value.name if isinstance(value, exp.Expression) else value or "").upper()
        if text in ("UNBOUNDED", "CURRENT ROW"):
            return f"UNBOUNDED {str(side).upper()}" if text == "UNBOUNDED" else "CURRENT ROW"
        return f"{self.pretty(value) if isinstance(value, exp.Expression) else text} {str(side).upper()}"


def _type(kind: exp.DataType) -> str:
    """A type as Spark's DataType.sql writes it: INT, STRING, DECIMAL(10,2), ARRAY<INT>."""
    return str(kind.sql("spark")).upper().replace(", ", ",")


def _number(literal: exp.Literal) -> str:
    """A number as Spark writes its constant: 1e3 is a double, written as Java writes one (1000.0, 1.0E10)."""
    text = str(literal.this)
    if not re.search(r"[eE]", text):
        return text
    value = float(text)
    if value == 0 or 1e-3 <= abs(value) < 1e7:
        return repr(value)
    mantissa, power = f"{value:.17E}".split("E")
    digits = repr(float(mantissa))
    return f"{digits}E{int(power)}"
