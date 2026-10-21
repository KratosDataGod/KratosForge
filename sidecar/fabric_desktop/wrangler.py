"""Data Wrangler: no-code cleaning steps on a cell's table output, written as Polars code.

The Clean view on a table output (Fabric's Data Wrangler, Databricks' no-code transforms) builds a
list of steps. Each step becomes one line of Polars code; the preview runs those same lines on the
output's whole result (``frames.py``), so what the preview shows is what the code does. Nothing in
the notebook changes until the code is added as a new cell and run: the original value is never
touched, and the generated cell writes a new ``<name>_clean`` variable.

Every value a person types (column names, fill values, text to find) goes into the code through
``repr()``, and the operations, types and comparisons come from fixed lists, so a step can only
ever produce the Polars call it names. Each line runs with no builtins.
"""

from __future__ import annotations

import keyword
from typing import Any

import pyarrow as pa

WRANGLE_ROWS = 1_000_000  # the preview runs on at most this many rows of the result
PREVIEW_ROWS = 100
MAX_STEPS = 50
MAX_SPLIT = 20

OPS = {
    "drop": "Drop columns",
    "keep": "Keep only columns",
    "rename": "Rename column",
    "cast": "Change column type",
    "fill": "Fill missing values",
    "drop_missing": "Drop rows with missing values",
    "filter": "Filter rows",
    "dedupe": "Remove duplicate rows",
    "trim": "Trim spaces",
    "case": "Change text case",
    "replace": "Find and replace",
    "split": "Split text",
    "sort": "Sort rows",
}

TYPES = {"integer": "pl.Int64", "double": "pl.Float64", "string": "pl.String", "boolean": "pl.Boolean",
         "date": "pl.Date", "timestamp": "pl.Datetime"}
CASES = {"upper": "to_uppercase", "lower": "to_lowercase", "title": "to_titlecase"}
COMPARE = {"eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
TEXT_TESTS = {"contains": "contains", "starts": "starts_with", "ends": "ends_with"}
NULL_TESTS = {"missing": "is_null", "present": "is_not_null"}


def reader(value: Any, name: str) -> str:
    """The Polars expression that turns the variable ``name`` (holding ``value``) into a DataFrame,
    in code that also runs in a Fabric or Databricks notebook."""
    module = type(value).__module__.lstrip("_")  # DuckDB relations live in _duckdb
    if module.startswith("polars"):
        return f"{name}.collect()" if hasattr(value, "collect") else name
    if module.startswith("pandas"):
        return f"pl.from_pandas({name})"
    if module.startswith(("pyspark", "duckdb.experimental.spark")):
        return f"pl.from_pandas({name}.toPandas())"
    if module.startswith("duckdb"):
        return f"{name}.pl()"
    return f"pl.from_arrow({name})"


def _kind(dtype: Any) -> str:
    if dtype.is_numeric():
        return "number"
    if dtype == _pl().String:
        return "text"
    if dtype.is_temporal():
        return "date"
    if dtype == _pl().Boolean:
        return "boolean"
    return "other"


def _pl() -> Any:
    import polars as pl
    return pl


def _columns(df: Any, step: dict[str, Any], key: str = "columns", required: bool = True) -> list[str]:
    cols = step.get(key) or []
    if isinstance(cols, str):
        cols = [cols]
    cols = [str(c) for c in cols]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"There's no column {missing[0]!r} at this step.")
    if required and not cols:
        raise ValueError("Pick at least one column.")
    return cols


def _column(df: Any, step: dict[str, Any]) -> str:
    return _columns(df, {"columns": step.get("column")})[0]


def _literal(dtype: Any, text: Any) -> str:
    """A typed-in value as a Polars literal for a column of ``dtype``."""
    kind = _kind(dtype)
    if kind == "number":
        try:
            number = float(text)
        except (TypeError, ValueError):
            raise ValueError(f"{text!r} isn't a number.") from None
        if dtype.is_integer():
            if not number.is_integer():
                raise ValueError(f"{text!r} isn't a whole number.")
            return repr(int(number))
        return repr(number)
    if kind == "boolean":
        word = str(text).strip().lower()
        if word not in ("true", "false"):
            raise ValueError("Type true or false.")
        return repr(word == "true")
    if kind == "date":
        cast = "to_date" if dtype == _pl().Date else "to_datetime"
        return f"pl.lit({str(text)!r}).str.{cast}()"
    return repr(str(text))


def describe_step(step: dict[str, Any]) -> str:
    """The step's line in the Steps list and its comment in the code."""
    op = step.get("op")
    label = OPS.get(str(op), str(op))
    cols = step.get("columns") or ([step["column"]] if step.get("column") else [])
    return f"{label}: {', '.join(map(str, cols))}" if cols else label


def code_for(df: Any, step: dict[str, Any]) -> tuple[str, list[str]]:
    """One step as one line of Polars code on ``df``, and the columns it changes or adds."""
    pl = _pl()
    op = step.get("op")
    if op not in OPS:
        raise ValueError(f"Unknown step {op!r}.")
    schema = df.schema
    if op == "drop":
        cols = _columns(df, step)
        return f"df = df.drop({cols!r})", []
    if op == "keep":
        cols = _columns(df, step)
        return f"df = df.select({cols!r})", []
    if op == "rename":
        col = _column(df, step)
        new = str(step.get("to") or "").strip()
        if not new:
            raise ValueError("Type the new name.")
        if new in df.columns and new != col:
            raise ValueError(f"There's already a column {new!r}.")
        return f"df = df.rename({ {col: new}!r})", [new]
    if op == "cast":
        cols = _columns(df, step)
        to = step.get("to")
        if to not in TYPES:
            raise ValueError(f"Pick a type: {', '.join(TYPES)}.")
        exprs = []
        for c in cols:
            if schema[c] == pl.String and to in ("date", "timestamp"):
                exprs.append(f"pl.col({c!r}).str.{'to_date' if to == 'date' else 'to_datetime'}(strict=False)")
            else:
                exprs.append(f"pl.col({c!r}).cast({TYPES[to]}, strict=False)")
        return f"df = df.with_columns({', '.join(exprs)})", cols
    if op == "fill":
        cols = _columns(df, step)
        exprs = []
        for c in cols:
            nan = ".fill_nan(None)" if schema[c].is_float() else ""
            exprs.append(f"pl.col({c!r}){nan}.fill_null({_literal(schema[c], step.get('value', ''))})")
        return f"df = df.with_columns({', '.join(exprs)})", cols
    if op == "drop_missing":
        cols = _columns(df, step, required=False)
        return (f"df = df.drop_nulls({cols!r})" if cols else "df = df.drop_nulls()"), []
    if op == "filter":
        col = _column(df, step)
        test = step.get("test")
        if test in NULL_TESTS:
            return f"df = df.filter(pl.col({col!r}).{NULL_TESTS[test]}())", []
        if test in TEXT_TESTS:
            if schema[col] != pl.String:
                raise ValueError(f"{col!r} isn't text; change its type to string first.")
            return (f"df = df.filter(pl.col({col!r}).str.{TEXT_TESTS[test]}({str(step.get('value', ''))!r}"
                    f"{', literal=True' if test == 'contains' else ''}))"), []
        if test in COMPARE:
            return f"df = df.filter(pl.col({col!r}) {COMPARE[test]} {_literal(schema[col], step.get('value', ''))})", []
        raise ValueError("Pick how to compare.")
    if op == "dedupe":
        cols = _columns(df, step, required=False)
        subset = f"subset={cols!r}, " if cols else ""
        return f"df = df.unique({subset}keep='first', maintain_order=True)", []
    if op in ("trim", "case", "replace"):
        cols = _columns(df, step)
        text = [c for c in cols if schema[c] != pl.String]
        if text:
            raise ValueError(f"{text[0]!r} isn't text; change its type to string first.")
        if op == "trim":
            tail = ".str.strip_chars()"
        elif op == "case":
            if step.get("to") not in CASES:
                raise ValueError("Pick upper, lower or title case.")
            tail = f".str.{CASES[step['to']]}()"
        else:
            find = str(step.get("find") or "")
            if not find:
                raise ValueError("Type the text to find.")
            tail = f".str.replace_all({find!r}, {str(step.get('value') or '')!r}, literal=True)"
        exprs = ", ".join(f"pl.col({c!r}){tail}" for c in cols)
        return f"df = df.with_columns({exprs})", cols
    if op == "split":
        col = _column(df, step)
        if schema[col] != pl.String:
            raise ValueError(f"{col!r} isn't text; change its type to string first.")
        sep = str(step.get("value") or "")
        if not sep:
            raise ValueError("Type the text to split on.")
        try:
            parts = int(step.get("parts") or 2)
        except (TypeError, ValueError):
            raise ValueError("Type how many parts.") from None
        if not 2 <= parts <= MAX_SPLIT:
            raise ValueError(f"Split into 2 to {MAX_SPLIT} parts.")
        names = [f"{col}_{i + 1}" for i in range(parts)]
        clash = [n for n in names if n in df.columns]
        if clash:
            raise ValueError(f"There's already a column {clash[0]!r}.")
        exprs = ", ".join(f"pl.col({col!r}).str.split({sep!r}).list.get({i}, null_on_oob=True).alias({n!r})"
                          for i, n in enumerate(names))
        return f"df = df.with_columns({exprs})", names
    # sort
    col = _column(df, step)
    return f"df = df.sort({col!r}, descending={bool(step.get('desc'))!r}, nulls_last=True)", []


def valid_name(name: str) -> str:
    name = (name or "").strip() or "df"
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"{name!r} isn't a Python variable name.")
    return name


def run(table: pa.Table, value: Any, steps: list[dict[str, Any]], name: str = "df") -> dict[str, Any]:
    """The steps' code and a preview of their result on ``table`` (the value's whole result).

    A step that fails stops the run there: the answer says which step and why, and shows the data as
    the steps before it left it."""
    pl = _pl()
    name = valid_name(name)
    if len(steps) > MAX_STEPS:
        raise ValueError(f"At most {MAX_STEPS} steps.")
    sampled = table.num_rows > WRANGLE_ROWS
    df = pl.from_arrow(table.slice(0, WRANGLE_ROWS) if sampled else table)
    if not isinstance(df, pl.DataFrame):
        df = df.to_frame()
    before = df.height
    lines: list[str] = []
    touched: list[str] = []
    error = None
    for i, step in enumerate(steps):
        try:
            line, changed = code_for(df, step)
            scope = {"pl": pl, "df": df}
            exec(line, {"__builtins__": {}}, scope)  # noqa: S102 - built from fixed calls and repr() values
            df = scope["df"]
        except Exception as exc:  # noqa: BLE001 - a step's error goes back to the person, with its number
            error = {"step": i, "message": _message(exc)}
            break
        lines.append(f"    # {describe_step(step)}\n    {line}")
        touched = [c for c in touched if c in df.columns] + [c for c in changed if c not in touched]
    body = "\n".join(lines) or "    pass"
    code = (f"import polars as pl\n\n\ndef clean_data(df):\n{body}\n    return df\n\n\n"
            f"{name}_clean = clean_data({reader(value, name)})\ndisplay({name}_clean)\n")
    from .frames import _cell, type_name
    head = df.head(PREVIEW_ROWS)
    return {"code": code, "columns": df.columns, "types": [type_name(f.type) for f in head.to_arrow().schema],
            "rows": [[_cell(v) for v in r] for r in head.rows()], "rows_before": before,
            "rows_after": df.height, "changed": touched, "sampled": sampled, "error": error,
            "steps": [describe_step(s) for s in steps]}


def _message(exc: Exception) -> str:
    from .redact import describe
    return str(exc) if isinstance(exc, ValueError) else describe(exc)
