"""Delta's ``replaceWhere`` overwrite, as this PC runs it: shared by the notebook runner (spark_local.py)
and the Academy learner's ``spark`` (academy/pyspark_shim.py, copied into ``kf_learner``).

An overwrite with ``.option("replaceWhere", "day = '2026-03-02'")`` replaces only the rows that match
the condition, and every row written must match it too (Delta's DELTA_REPLACE_WHERE_MISMATCH). This
module only turns the condition into the predicate delta-rs replaces by and checks the rows against it;
the caller writes. It imports only sqlglot, duckdb and duckdb_rows, so the learner package can hold it.
"""

from __future__ import annotations

import re
from typing import Any

from .duckdb_rows import first_row

_ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")   # as sql_dialects.parse_problem strips them


def predicate(condition: str, name: str, columns: list[str]) -> str:
    """replaceWhere's condition, Spark SQL, as the predicate delta-rs replaces by: ``columns`` (the
    table's) quoted as the table spells them. A subquery, more than one statement, or a column the table
    hasn't, is refused with a ValueError that says nothing was written."""
    import sqlglot
    from sqlglot import exp
    try:
        nodes = [n for n in sqlglot.parse(condition, read="spark") if n is not None]
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"replaceWhere {condition!r} isn't a condition this PC reads "
                         f"({_ESCAPES.sub('', str(exc))}). Nothing was written.") from None
    if len(nodes) != 1 or not isinstance(nodes[0], exp.Condition):  # "dt = 1; DROP TABLE t" is two statements
        raise ValueError(f"replaceWhere {condition!r} must be a single condition, such as region = 'EU'. Nothing was written.")
    node = nodes[0]
    if node.find(exp.Subquery, exp.Select) or not node.find(exp.Column):
        raise ValueError(f"replaceWhere {condition!r} must compare the table's columns, with no subquery. Nothing "
                         "was written.")
    cased = {c.lower(): c for c in columns}
    for column in node.find_all(exp.Column):
        actual = cased.get(column.name.lower())
        if actual is None or column.table:
            raise ValueError(f"replaceWhere {condition!r}: table {name} has no column {column.sql('spark')}. Its "
                             f"columns are {', '.join(cased.values())}. Nothing was written.")
        column.set("this", exp.to_identifier(actual, quoted=True))
    return node.sql(dialect="postgres")


def mismatch(replace: str, name: str, detail: str = "") -> ValueError:
    """Delta's error when a row written doesn't match replaceWhere."""
    return ValueError(f"[DELTA_REPLACE_WHERE_MISMATCH] Written data does not conform to partial table overwrite "
                      f"condition or constraint '{replace}'.{detail} Every row written must match replaceWhere, so "
                      f"nothing was written to {name}. Filter the rows to the condition first, or widen replaceWhere.")


def check_rows(data: Any, where: str, replace: str, name: str) -> None:
    """Delta's check, made before the write: every row of ``data`` (a pyarrow Table) must make the
    predicate ``where`` true (a NULL fails it, as in Delta's CheckDeltaInvariant). Fails closed: a
    predicate DuckDB can't evaluate, or one that tries to read a file, stops the write. delta-rs checks again as it writes."""
    import duckdb
    # No files or network: a condition such as read_text('C:/x') = region can't read anything here.
    con = duckdb.connect(config={"enable_external_access": False})
    try:
        con.register("kf_rows", data)
        try:
            row = first_row(con.execute(f"SELECT count(*) FROM kf_rows WHERE ({where}) IS NOT TRUE"))
        except duckdb.Error as exc:
            raise ValueError(f"replaceWhere '{replace}' isn't run here, because this PC couldn't check the rows "
                             f"against it. The check stopped on: {str(exc).splitlines()[0]}. Nothing was written to "
                             f"{name}. Write the condition as column comparisons, such as region = 'EU'.") from None
    finally:
        con.close()
    bad = int(row[0]) if row else 0
    if bad:
        verb = "doesn't" if bad == 1 else "don't"
        raise mismatch(replace, name, f" {bad} of the {data.num_rows} rows {verb} match it.")
