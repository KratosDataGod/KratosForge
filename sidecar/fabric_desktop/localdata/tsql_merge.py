"""Local T-SQL MERGE as a Fabric Warehouse reads it (WR-244), or refused by name before anything runs.

From Microsoft's MERGE (Transact-SQL) page, whose second syntax block is the one for Fabric Data Warehouse:

- A MERGE ends with a semicolon. Without one SQL Server stops with error 10713, and the Fabric syntax block
  says the query returns a syntax error. DuckDB and delta-rs ran it.
- There are at most two WHEN MATCHED clauses. With two, the first has ``AND <condition>``, the second applies to
  a row only when the first doesn't, and one of them UPDATEs while the other DELETEs. WHEN NOT MATCHED BY SOURCE
  follows the same rules, and there is at most one WHEN NOT MATCHED [BY TARGET]. Here a third clause, two
  UPDATEs, or an unconditional first clause ran (DuckDB stopped on some with its parser's words), and two WHEN
  NOT MATCHED clauses inserted different rows on a database table and on a Delta table.
- WHEN NOT MATCHED BY SOURCE has no source row: its condition reads only the table's columns, and an action that
  reads the source's is error 207. WHEN NOT MATCHED [BY TARGET] has no table row to read. A Delta table read the
  missing row's columns as NULL and ran; DuckDB stopped with its binder's words.
- A column is set once in a clause (SQL Server's error 264); a Delta table took the last value.
- Fabric's MERGE inserts with ``INSERT [(columns)] VALUES (...)`` only: no ``DEFAULT VALUES``, which sqlglot read
  as a column called DEFAULT.
- A column named without ``t.`` or ``s.`` in a WHEN clause is placed by the table's and the source's columns
  (WR-253). One both have is SQL Server's error 209, "Ambiguous column name 'x'.", in any WHEN clause. One only the
  table has can't be read in WHEN NOT MATCHED [BY TARGET], and one only the source has can't be read in WHEN NOT
  MATCHED BY SOURCE. A Delta table read the missing row's column as NULL, and DuckDB stopped with its binder's words.
  The source's columns are a known table's, a CTE's or a subquery's (``SELECT *`` and ``x.*`` read from the
  relations they name), or the alias's column list. A source whose columns are known only when it runs (a
  temporary table, a table the script makes, a table-valued function) is placed by :func:`place` too, in engine.py,
  by the columns it has then, before anything is written (WR-257).

``SET t.v = ...``, with the table's alias, runs as ``SET v = ...``: T-SQL allows it, and DuckDB's parser didn't.
Which clause takes a row (the first whose condition holds) and the check that a table row is changed by one source
row at most (SQL Server's error 8672) are in engine.py, for a database table and a Delta table alike.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp
from sqlglot.tokens import Token, TokenType

_NOT_YET = "This T-SQL can't be run on the local engine: "
# Tokens that start a statement where a MERGE can't go on: a MERGE's own INSERT, UPDATE and DELETE follow THEN.
_STARTS = {TokenType.SELECT, TokenType.WITH, TokenType.CREATE, TokenType.DROP, TokenType.ALTER, TokenType.TRUNCATE,
           TokenType.MERGE, TokenType.INSERT, TokenType.UPDATE, TokenType.DELETE}
UNTERMINATED = ("A MERGE must end with a semicolon (;) in T-SQL, and a Fabric Warehouse refuses one without it "
                "(SQL Server's error 10713). Add ; after its last WHEN clause.")


def refuse_unterminated(tokens: list[Token]) -> None:
    """Refuses a MERGE that isn't followed by a semicolon before the batch ends or the next statement starts."""
    depth = 0
    merging = False
    for i, token in enumerate(tokens):
        kind = token.token_type
        if kind == TokenType.L_PAREN:
            depth += 1
        elif kind == TokenType.R_PAREN:
            depth = max(0, depth - 1)
        elif kind == TokenType.SEMICOLON:
            merging, depth = False, 0
        elif depth == 0 and kind in _STARTS:
            after = tokens[i + 1].token_type if i + 1 < len(tokens) else None
            before = tokens[i - 1].token_type if i else None
            if kind == TokenType.MERGE and after == TokenType.JOIN:
                continue  # INNER MERGE JOIN, a join hint
            if merging and before != TokenType.THEN:
                raise ValueError(UNTERMINATED)
            if kind == TokenType.MERGE:
                merging = True
    if merging:
        raise ValueError(UNTERMINATED)


def check(tree: exp.Expression, columns: dict[str, dict[str, dict[str, str]]] | None = None) -> None:
    """Refuses a MERGE whose clauses a Fabric Warehouse refuses, naming the rule, and writes ``SET t.v`` as
    ``SET v``. ``columns`` ({schema: {table: {column: type}}}) gives the table's columns for ``INSERT VALUES``
    without a column list."""
    if not isinstance(tree, exp.Merge):
        return
    target, using = tree.this, tree.args.get("using")
    if not isinstance(target, exp.Table):
        return
    mine = {(target.alias or target.name).lower()}
    theirs = {using.alias.lower()} if using is not None and using.alias else \
        ({using.name.lower()} if isinstance(using, exp.Table) else set())
    listed = tree.args.get("whens")
    whens = [w for w in (listed.expressions if listed is not None else []) if isinstance(w, exp.When)]
    matched = [w for w in whens if w.args.get("matched")]
    by_source = [w for w in whens if not w.args.get("matched") and w.args.get("source")]
    by_target = [w for w in whens if not w.args.get("matched") and not w.args.get("source")]
    _two_at_most("WHEN MATCHED", matched)
    _two_at_most("WHEN NOT MATCHED BY SOURCE", by_source)
    if len(by_target) > 1:
        raise ValueError(f"A MERGE has at most one WHEN NOT MATCHED [BY TARGET] clause in T-SQL, and this one has "
                         f"{len(by_target)}, so a Fabric Warehouse refuses it. Put them in one: WHEN NOT MATCHED AND "
                         "(...) THEN INSERT, with CASE in VALUES where the inserted values differ.")
    for when in whens:
        then = when.args.get("then")
        if isinstance(then, exp.Update):
            _sets(then, mine, theirs, target.name)
        if isinstance(then, exp.Insert):
            _insert(then, _columns_of(target, columns))
    for when in by_source:
        _reads_only(when, theirs, "WHEN NOT MATCHED BY SOURCE", "the table's columns",
                    "a row the source doesn't have, so there is no source row to read")
    for when in by_target:
        _reads_only(when, mine, "WHEN NOT MATCHED [BY TARGET]", "the source's columns",
                    "a source row the table doesn't have, so there is no table row to read")
    table = _columns_of(target, columns)
    source = _source_columns(tree, using, columns) if using is not None else None
    if table is not None and source is not None:
        place([(_match(w), _reads(w)) for w in whens], table, source)


def _action(when: exp.When) -> str:
    then = when.args.get("then")
    return "UPDATE" if isinstance(then, exp.Update) else "DELETE" if isinstance(then, exp.Var) else "INSERT"


def _two_at_most(kind: str, whens: list[exp.When]) -> None:
    if len(whens) > 2:
        raise ValueError(f"A MERGE has at most two {kind} clauses in T-SQL, and this one has {len(whens)}, so a "
                         "Fabric Warehouse refuses it. Keep one that UPDATEs and one that DELETEs, and fold the "
                         "other conditions into them, with CASE in SET where the new values differ.")
    if len(whens) < 2:
        return
    if whens[0].args.get("condition") is None:
        raise ValueError(f"The first of two {kind} clauses needs AND <condition> in T-SQL, and a Fabric Warehouse "
                         "refuses a MERGE without it, because the second clause takes only the rows the first one leaves, "
                         f"so an unconditional first clause would leave none. Write {kind} AND ... THEN for the "
                         "first one.")
    if _action(whens[0]) == _action(whens[1]):
        raise ValueError(f"Of two {kind} clauses, T-SQL needs one to UPDATE and the other to DELETE, and these "
                         f"both {_action(whens[0])}, so a Fabric Warehouse refuses the MERGE. Put them in one "
                         f"clause, with CASE in SET where the new values differ.")


def _sets(then: exp.Update, mine: set[str], theirs: set[str], table: str) -> None:
    seen: set[str] = set()
    for assignment in then.expressions:
        column = assignment.this if isinstance(assignment, exp.EQ) else None
        if not isinstance(column, exp.Column):
            continue
        if column.table:
            if column.table.lower() not in mine and column.table.lower() != table.lower():
                raise ValueError(f"SET {column.sql(dialect='tsql')} names a column of {column.table}, but a MERGE's "
                                 f"UPDATE sets only the columns of the table it changes. Write SET {column.name} = "
                                 "...")
            for part in ("table", "db", "catalog"):
                column.set(part, None)  # SET t.v = ... is SET v = ...: DuckDB's parser takes only the column
        name = column.name.lower()
        if name in seen:
            raise ValueError(f"The column {column.name} is set more than once in one UPDATE SET of this MERGE. T-SQL "
                             "gives a column one value in a clause (SQL Server's error 264), so a Fabric Warehouse "
                             "refuses it. Set it once.")
        seen.add(name)


def _columns_of(target: exp.Table, columns: dict[str, dict[str, dict[str, str]]] | None) -> list[str] | None:
    schema, name = (target.db or "dbo").lower(), target.name.lower()
    for s, tables in (columns or {}).items():
        if s.lower() == schema:
            for t, found in tables.items():
                if t.lower() == name:
                    return list(found)
    return None


def _insert(then: exp.Insert, table: list[str] | None) -> None:
    columns = then.this.expressions if isinstance(then.this, exp.Tuple) else []
    if len(columns) == 1 and isinstance(columns[0], exp.Column) and not columns[0].this.quoted \
            and columns[0].name.upper() == "DEFAULT" and then.expression is None:
        raise ValueError("A Fabric Warehouse's MERGE doesn't take INSERT DEFAULT VALUES. Its WHEN NOT MATCHED "
                         "inserts with INSERT (columns) VALUES (...). Give the values, or NULL where a column has "
                         "none.")
    if not isinstance(then.expression, exp.Expression) and not isinstance(then.this, exp.Star):  # sqlglot: None or False
        raise ValueError("A MERGE's INSERT needs VALUES (...) in T-SQL, so write INSERT (columns) VALUES (...).")
    values = then.expression.expressions if isinstance(then.expression, exp.Tuple) else []
    if not columns and table is not None and values and len(values) != len(table):
        raise ValueError(f"INSERT VALUES without a column list gives a value for each column of the table in "
                         f"order, and it has {len(table)} ({', '.join(table)}), but VALUES gives {len(values)}. "
                         "Give one for each, or list the columns: INSERT (a, b) VALUES (...).")
    seen: set[str] = set()
    for column in columns:
        name = column.name.lower()
        if name in seen:
            raise ValueError(f"The column {column.name} is named more than once in this MERGE's INSERT. T-SQL "
                             "gives a column one value (SQL Server's error 264), so a Fabric Warehouse refuses it. "
                             "Name it once.")
        seen.add(name)


def _reads_only(when: exp.When, others: set[str], kind: str, allowed: str, why: str) -> None:
    for column in when.find_all(exp.Column):
        if column.table and column.table.lower() in others:
            raise ValueError(f"{kind} reads only {allowed} in T-SQL, because it acts on {why}. This one reads "
                             f"{column.sql(dialect='tsql')}, and a Fabric Warehouse refuses it. Take that out of "
                             "the clause, or move the logic to a WHEN MATCHED clause.")


# WR-253: an unqualified column in a WHEN clause, placed by the table's and the source's columns.
_ONLY = {
    "not_matched": ("WHEN NOT MATCHED [BY TARGET]", "the source's columns", "the table",
                    "a source row the table doesn't have, so there is no table row to read"),
    "not_matched_by_source": ("WHEN NOT MATCHED BY SOURCE", "the table's columns", "the source",
                              "a row the source doesn't have, so there is no source row to read"),
}


def place(whens: list[tuple[str, list[exp.Expression]]], table: list[str], source: list[str]) -> None:
    """Refuses a column named without ``t.`` or ``s.`` in a WHEN clause that both the table and the source have
    (SQL Server's error 209), or that only the side a clause has no row of has. ``whens`` is each clause's kind
    (matched, not_matched or not_matched_by_source) with the expressions it reads; ``table`` and ``source`` are
    their columns. A column neither has is left to the engine, which says Invalid column name."""
    mine, theirs = {c.lower() for c in table}, {c.lower() for c in source}
    for match, reads in whens:
        for node in reads:
            for column in _unqualified(node):
                name = column.name.lower()
                if name in mine and name in theirs:
                    raise ValueError(f"Ambiguous column name '{column.name}'.")
                only = _ONLY.get(match)
                if only is None:
                    continue
                kind, allowed, side, why = only
                wrong = mine if match == "not_matched" else theirs
                right = theirs if match == "not_matched" else mine
                if name in wrong and name not in right:
                    raise ValueError(f"{kind} reads only {allowed} in T-SQL, because it acts on {why}. This one "
                                     f"reads {column.name}, which only {side} has, and a Fabric Warehouse refuses "
                                     "it. Take that out of the clause, or move the logic to a WHEN MATCHED clause.")


def clauses(change: Any) -> list[tuple[str, list[exp.Expression]]]:
    """Each WHEN clause of a ``delta_dml.Change`` MERGE, as :func:`place` takes them."""
    out: list[tuple[str, list[exp.Expression]]] = []
    for when in change.whens:
        reads = [when.condition] if when.condition is not None else []
        out.append((when.match, reads + [value for _, value in when.sets] + list(when.values)))
    return out


def _match(when: exp.When) -> str:
    if when.args.get("matched"):
        return "matched"
    return "not_matched_by_source" if when.args.get("source") else "not_matched"


def _reads(when: exp.When) -> list[exp.Expression]:
    """What a WHEN clause reads: its condition, the values it sets and the values it inserts, not the names of
    the table's columns it sets or inserts into."""
    reads = [when.args["condition"]] if when.args.get("condition") is not None else []
    then = when.args.get("then")
    if isinstance(then, exp.Update):
        reads += [a.expression for a in then.expressions if isinstance(a, exp.EQ)]
    elif isinstance(then, exp.Insert) and isinstance(then.expression, exp.Tuple):
        reads += list(then.expression.expressions)
    return reads


def _unqualified(node: exp.Expression) -> list[exp.Column]:
    """The columns in ``node`` named without a table, outside any subquery (whose own FROM places them)."""
    found = []
    for column in node.find_all(exp.Column):
        if column.table or isinstance(column.this, exp.Star):
            continue
        parent = column.parent
        while parent is not None and parent is not node and not isinstance(parent, (exp.Select, exp.Subquery)):
            parent = parent.parent
        if parent is None or parent is node:
            found.append(column)
    return found


def _ctes(node: exp.Expression) -> dict[str, exp.CTE]:
    with_ = node.args.get("with_") or node.args.get("with")
    return {cte.alias.lower(): cte for cte in (with_.expressions if isinstance(with_, exp.With) else [])}


def _source_columns(tree: exp.Merge, using: exp.Expression,
                    columns: dict[str, dict[str, dict[str, str]]] | None) -> list[str] | None:
    """The MERGE source's columns, or None when they aren't known before it runs (a temporary table, a table the
    script makes, a function)."""
    return _relation(using, _ctes(tree), columns)


def _relation(node: exp.Expression, ctes: dict[str, exp.CTE],
              columns: dict[str, dict[str, dict[str, str]]] | None) -> list[str] | None:
    alias = node.args.get("alias")
    listed = alias.columns if isinstance(alias, exp.TableAlias) else []
    if listed:
        return [c.name for c in listed]
    if isinstance(node, exp.Table):
        if not isinstance(node.this, exp.Identifier) or node.this.args.get("temporary"):
            return None
        if not node.db and node.name.lower() in ctes:
            cte = ctes[node.name.lower()]
            inner = {k: v for k, v in ctes.items() if k != node.name.lower()}  # a CTE doesn't read itself here
            return _relation(cte, inner, columns)
        return _columns_of(node, columns)
    if isinstance(node, (exp.Subquery, exp.CTE)):
        return _query(node.this, ctes, columns)
    return None


def _query(node: exp.Expression, ctes: dict[str, exp.CTE],
           columns: dict[str, dict[str, dict[str, str]]] | None) -> list[str] | None:
    if isinstance(node, exp.SetOperation):
        return _query(node.this, ctes, columns)
    if isinstance(node, exp.Subquery):
        return _query(node.this, ctes, columns)
    if not isinstance(node, exp.Select):
        return None
    ctes = {**ctes, **_ctes(node)}
    from_ = node.args.get("from_") or node.args.get("from")
    relations = ([from_.this] if isinstance(from_, exp.From) else []) + [j.this for j in node.args.get("joins") or []]
    out: list[str] = []
    for projection in node.expressions:
        if isinstance(projection, exp.Star):
            for relation in relations:
                found = _relation(relation, ctes, columns)
                if found is None:
                    return None
                out += found
        elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            named = [r for r in relations if (r.alias or r.name).lower() == projection.table.lower()]
            found = _relation(named[0], ctes, columns) if len(named) == 1 else None
            if found is None:
                return None
            out += found
        elif isinstance(projection, (exp.Alias, exp.Column)):
            out.append(projection.alias_or_name)
        else:
            return None
    return out
