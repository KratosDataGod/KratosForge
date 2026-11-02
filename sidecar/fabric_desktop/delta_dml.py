"""MERGE INTO, UPDATE and DELETE on a Delta table, as Spark and Databricks SQL write them (FD-LDE-011).

The statement is read with sqlglot and run by delta-rs (``DeltaTable.merge``, ``.update``,
``.delete``), each in one commit, on any Delta table delta-rs can open: a local folder or OneLake.
What this module doesn't do is find the rows a MERGE reads (the ``USING`` part): the caller runs
that query and passes its rows in, so the same code serves the local engine (DuckDB) and a
notebook's ``spark.sql``.

    MERGE INTO sales AS t USING updates AS s ON t.id = s.id
      WHEN MATCHED AND s.deleted THEN DELETE
      WHEN MATCHED THEN UPDATE SET *              (or SET t.amount = s.amount, ...)
      WHEN NOT MATCHED [BY TARGET] THEN INSERT *  (or INSERT (id, amount) VALUES (s.id, s.amount))
      WHEN NOT MATCHED BY SOURCE THEN DELETE      (or UPDATE SET ...)
    UPDATE sales SET amount = amount * 1.1 WHERE region = 'EU'
    DELETE FROM sales WHERE year < 2020

Column names are matched to the table's and the source's without regard to case, as Spark does.
Conditions and values are SQL expressions delta-rs evaluates (DataFusion); a subquery inside one
isn't supported, so ``UPDATE ... WHERE id IN (SELECT ...)`` is refused with a MERGE to use instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from . import sql_dialects

_START = re.compile(r"^\s*(MERGE|UPDATE|DELETE)\b", re.IGNORECASE)
_LEADING_COMMENTS = re.compile(r"^(\s*(--[^\n]*(\n|$)|/\*.*?\*/))+", re.DOTALL)


@dataclass
class When:
    """One WHEN clause of a MERGE."""
    match: str                      # matched | not_matched | not_matched_by_source
    action: str                     # update | update_all | delete | insert | insert_all
    condition: exp.Expression | None = None
    sets: list[tuple[str, exp.Expression]] = field(default_factory=list)


@dataclass
class Change:
    """A MERGE, UPDATE or DELETE of one Delta table."""
    kind: str                       # merge | update | delete
    schema: str | None
    table: str
    alias: str                      # what the statement calls the table (MERGE)
    source: exp.Expression | None = None   # MERGE: the USING relation (a table or a query), without its alias
    source_alias: str = ""
    on: exp.Expression | None = None
    whens: list[When] = field(default_factory=list)
    sets: list[tuple[str, exp.Expression]] = field(default_factory=list)   # UPDATE
    where: exp.Expression | None = None                                     # UPDATE and DELETE

    @property
    def name(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table

    @property
    def whole_table(self) -> bool:
        """It can change every row with nothing in the statement saying which: an UPDATE or DELETE with
        no WHERE (or one that names no column, such as WHERE true), or a MERGE whose WHEN NOT MATCHED
        BY SOURCE deletes or updates with no condition (with an empty source, that is every row)."""
        if self.kind in ("update", "delete"):
            return self.where is None or not any(True for _ in self.where.find_all(exp.Column))
        return any(w.match == "not_matched_by_source" and (w.condition is None or
                   not any(True for _ in w.condition.find_all(exp.Column))) for w in self.whens)

    def source_sql(self, dialect: Any = "duckdb") -> str:
        """The rows the MERGE reads, as a query in ``dialect``."""
        assert self.source is not None
        if isinstance(self.source, exp.Table):
            return f"SELECT * FROM {self.source.sql(dialect=dialect)}"
        return self.source.sql(dialect=dialect)


def looks_like(statement: str) -> bool:
    """Whether the statement starts with MERGE, UPDATE or DELETE (leading comments aside)."""
    return bool(_START.match(_LEADING_COMMENTS.sub("", statement)))


def parse(statement: str, dialect: str = "spark") -> Change | None:
    """The change ``statement`` makes, or None when it is no MERGE, UPDATE or DELETE."""
    if not looks_like(statement):
        return None
    try:
        tree = sqlglot.parse_one(statement, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"Could not read the statement: {sql_dialects.parse_problem(exc)}") from None
    return parse_tree(tree)


def parse_tree(tree: exp.Expression) -> Change | None:
    if isinstance(tree, exp.Merge):
        return _merge(tree)
    if isinstance(tree, exp.Update):
        target = _target(tree.this, "UPDATE")
        if tree.args.get("from_") or tree.args.get("from"):
            raise ValueError("UPDATE ... FROM isn't supported on a Delta table. Use MERGE INTO "
                             f"{target[1]} USING ... ON ... WHEN MATCHED THEN UPDATE SET ...")
        sets = [_assignment(e) for e in tree.expressions]
        where = tree.args.get("where")
        return Change("update", target[0], target[1], target[2] or target[1], sets=sets,
                      where=where.this if where else None)
    if isinstance(tree, exp.Delete):
        target = _target(tree.this, "DELETE")
        if tree.args.get("using"):
            raise ValueError("DELETE ... USING isn't supported on a Delta table. Use MERGE INTO "
                             f"{target[1]} USING ... ON ... WHEN MATCHED THEN DELETE")
        where = tree.args.get("where")
        return Change("delete", target[0], target[1], target[2] or target[1], where=where.this if where else None)
    return None


def _target(node: Any, what: str) -> tuple[str | None, str, str]:
    if not isinstance(node, exp.Table) or not node.name:
        raise ValueError(f"{what} needs a table to change.")
    if node.catalog:
        raise ValueError(f"{node.sql()}: name the table as schema.table or table.")
    return (node.db or None), node.name, node.alias


def _assignment(node: exp.Expression) -> tuple[str, exp.Expression]:
    if not isinstance(node, exp.EQ) or not isinstance(node.this, exp.Column):
        raise ValueError(f"{node.sql()} isn't column = value.")
    return node.this.name, node.expression


def _merge(tree: exp.Merge) -> Change:
    schema, table, alias = _target(tree.this, "MERGE INTO")
    using = tree.args.get("using")
    if using is None:
        raise ValueError("MERGE INTO needs USING (the rows to merge).")
    source_alias = using.alias or (using.name if isinstance(using, exp.Table) else "")
    if not source_alias:
        raise ValueError("Give the MERGE source an alias: USING (SELECT ...) AS s.")
    source = using.copy()
    source.set("alias", None)
    if isinstance(source, exp.Subquery):
        source = source.this
    on = tree.args.get("on")
    if on is None:
        raise ValueError("MERGE INTO needs ON (how a source row finds its row in the table).")
    whens_node = tree.args.get("whens")
    whens: list[When] = []
    for when in (whens_node.expressions if whens_node else []):
        matched, by_source = bool(when.args.get("matched")), bool(when.args.get("source"))
        match = "matched" if matched else ("not_matched_by_source" if by_source else "not_matched")
        then = when.args.get("then")
        condition = when.args.get("condition")
        if isinstance(then, exp.Var) and then.name.upper() == "DELETE":
            if match == "not_matched":
                raise ValueError("WHEN NOT MATCHED can only INSERT: there is no row in the table to delete.")
            whens.append(When(match, "delete", condition))
        elif isinstance(then, exp.Update):
            if match == "not_matched":
                raise ValueError("WHEN NOT MATCHED can only INSERT: there is no row in the table to update.")
            if len(then.expressions) == 1 and isinstance(then.expressions[0], exp.Star):
                if match != "matched":
                    raise ValueError("UPDATE SET * goes with WHEN MATCHED, where there is a source row to copy.")
                whens.append(When(match, "update_all", condition))
            else:
                whens.append(When(match, "update", condition, [_assignment(e) for e in then.expressions]))
        elif isinstance(then, exp.Insert):
            if match != "not_matched":
                raise ValueError("INSERT goes with WHEN NOT MATCHED, where the source row has no row in the table.")
            if isinstance(then.this, exp.Star):
                whens.append(When(match, "insert_all", condition))
                continue
            columns = then.this.expressions if isinstance(then.this, exp.Tuple) else []
            values = then.expression.expressions if isinstance(then.expression, exp.Tuple) else []
            if not columns or len(columns) != len(values):
                raise ValueError("INSERT (columns) VALUES (values) needs as many values as columns.")
            whens.append(When(match, "insert", condition, [(c.name, v) for c, v in zip(columns, values)]))
        else:
            raise ValueError(f"WHEN ... THEN {then.sql() if then else ''} isn't supported in MERGE.")
    if not whens:
        raise ValueError("MERGE INTO needs at least one WHEN clause.")
    return Change("merge", schema, table, alias or table, source=source, source_alias=source_alias, on=on,
                  whens=whens)


def equi_keys(change: Change) -> list[tuple[str, str]] | None:
    """(table column, source column) pairs when ON is only ``t.a = s.b AND ...``; None otherwise."""
    pairs: list[tuple[str, str]] = []
    parts: list[exp.Expression] = []
    stack = [change.on]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.And):
            stack += [node.this, node.expression]
        elif isinstance(node, exp.Paren):
            stack.append(node.this)
        else:
            parts.append(node)
    for part in parts:
        if not (isinstance(part, exp.EQ) and isinstance(part.this, exp.Column)
                and isinstance(part.expression, exp.Column)):
            return None
        sides = {part.this.table.lower(): part.this.name, part.expression.table.lower(): part.expression.name}
        if set(sides) != {change.alias.lower(), change.source_alias.lower()}:
            return None
        pairs.append((sides[change.alias.lower()], sides[change.source_alias.lower()]))
    return pairs


class _Names:
    """Column references made to name the table's and the source's columns exactly, and quoted, so
    delta-rs (which folds unquoted names to lower case) finds mixed-case columns."""

    def __init__(self, change: Change, target: list[str], source: list[str]):
        self.change = change
        self.target = {n.lower(): n for n in target}
        self.source = {n.lower(): n for n in source}

    def column(self, name: str, table: str | None = None) -> str:
        key = name.lower()
        if table is None or table.lower() == self.change.alias.lower():
            if key in self.target:
                return self.target[key]
        if table is None or table.lower() == self.change.source_alias.lower():
            if key in self.source:
                return self.source[key]
        where = f" in {table}" if table else ""
        raise ValueError(f"There is no column {name}{where}. {self.change.name} has "
                         f"{', '.join(self.target.values())}"
                         + (f"; the source has {', '.join(self.source.values())}" if self.source else "") + ".")

    def render(self, node: exp.Expression) -> str:
        node = node.copy()
        if node.find(exp.Subquery, exp.Select):
            raise ValueError(f"{node.sql()} has a subquery, which delta-rs can't run inside a condition or value. "
                             "Join the rows in with MERGE INTO ... USING (SELECT ...) instead.")
        merge = self.change.kind == "merge"
        for column in list(node.find_all(exp.Column)):
            qualifier = column.table or None
            actual = self.column(column.name, qualifier if merge else None)
            column.set("this", exp.to_identifier(actual, quoted=True))
            # A MERGE tells the table's columns from the source's by alias; UPDATE and DELETE have one table.
            column.set("table", exp.to_identifier(qualifier, quoted=True) if qualifier and merge else None)
        return node.sql(dialect="postgres")

    def key(self, name: str) -> str:
        actual = self.target.get(name.lower())
        if actual is None:
            raise ValueError(f"{self.change.name} has no column {name}.")
        return f'"{actual}"'


def apply(change: Change, table: Any, source: Any = None, source_names: list[str] | None = None,
          commit_properties: Any = None) -> dict[str, Any]:
    """Run ``change`` on ``table`` (a deltalake DeltaTable) in one commit; ``source`` is the MERGE's
    rows (an Arrow table or stream). Returns what changed."""
    target_names = [f.name for f in table.schema().fields]
    names = _Names(change, target_names, source_names or [])
    if change.kind == "delete":
        metrics = table.delete(names.render(change.where) if change.where is not None else None,
                               commit_properties=commit_properties)
        return _result(metrics, deleted="num_deleted_rows")
    if change.kind == "update":
        updates = {names.key(col): names.render(value) for col, value in change.sets}
        metrics = table.update(updates=updates,
                               predicate=names.render(change.where) if change.where is not None else None,
                               commit_properties=commit_properties)
        return _result(metrics, updated="num_updated_rows")
    merger = table.merge(source, names.render(change.on), source_alias=change.source_alias,
                         target_alias=change.alias, commit_properties=commit_properties)
    for when in change.whens:
        condition = names.render(when.condition) if when.condition is not None else None
        sets = {names.key(col): names.render(value) for col, value in when.sets}
        if when.match == "matched":
            if when.action == "delete":
                merger = merger.when_matched_delete(condition)
            elif when.action == "update_all":
                _same_columns(change, target_names, source_names or [])
                merger = merger.when_matched_update_all(condition)
            else:
                merger = merger.when_matched_update(sets, condition)
        elif when.match == "not_matched":
            if when.action == "insert_all":
                _same_columns(change, target_names, source_names or [])
                merger = merger.when_not_matched_insert_all(condition)
            else:
                merger = merger.when_not_matched_insert(sets, condition)
        elif when.action == "delete":
            merger = merger.when_not_matched_by_source_delete(condition)
        else:
            merger = merger.when_not_matched_by_source_update(sets, condition)
    return _result(merger.execute(), inserted="num_target_rows_inserted", updated="num_target_rows_updated",
                   deleted="num_target_rows_deleted", source="num_source_rows")


def _same_columns(change: Change, target: list[str], source: list[str]) -> None:
    """UPDATE SET * and INSERT * copy every table column from the source column of the same name."""
    have = {n.lower() for n in source}
    missing = [n for n in target if n.lower() not in have]
    if missing:
        raise ValueError(f"UPDATE SET * and INSERT * take every column of {change.name} from the source, and the "
                         f"source has no {', '.join(missing)}. Name the columns instead.")


def _result(metrics: dict[str, Any], **keys: str) -> dict[str, Any]:
    out = {"rows_inserted": 0, "rows_updated": 0, "rows_deleted": 0}
    for name, key in keys.items():
        out["source_rows" if name == "source" else f"rows_{name}"] = int(metrics.get(key) or 0)
    out["files_added"] = int(metrics.get("num_added_files", metrics.get("num_target_files_added")) or 0)
    out["files_removed"] = int(metrics.get("num_removed_files", metrics.get("num_target_files_removed")) or 0)
    return out
