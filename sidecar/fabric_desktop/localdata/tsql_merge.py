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

- A MERGE after WITH runs with its CTEs moved into USING (WR-259), so it is checked as any other MERGE. A MERGE into
  a CTE, or one that reads a CTE outside USING, is refused by name.

``SET t.v = ...``, with the table's alias, runs as ``SET v = ...``: T-SQL allows it, and DuckDB's parser didn't.
Which clause takes a row (the first whose condition holds) and the check that a table row is changed by one source
row at most (SQL Server's error 8672) are in engine.py, for a database table and a Delta table alike.
"""

from __future__ import annotations

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
    target = tree.this
    if not isinstance(target, exp.Table):
        return
    _ctes_into_using(tree, target)
    using = tree.args.get("using")
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


def _ctes_into_using(tree: exp.Merge, target: exp.Table) -> None:
    """WR-259: ``WITH d AS (...) MERGE t USING d AS s ...`` runs as ``MERGE t USING (WITH d AS (...) SELECT * FROM d
    AS s) AS s ...``, so the MERGE starts with MERGE and gets the column checks and error 8672 on any table. A MERGE
    into a CTE, or one that reads a CTE outside USING, is refused by name."""
    with_ = tree.args.get("with_")
    if not isinstance(with_, exp.With):
        return
    ctes = {c.alias_or_name.lower(): c.alias_or_name for c in with_.expressions}
    if not target.args.get("db") and target.name.lower() in ctes:
        raise ValueError(f"A MERGE into the CTE '{target.name}' can't be run on the local engine yet, because it "
                         "changes tables only. Name the table after MERGE, and put the CTE's filter in ON or in a "
                         "WHEN clause's AND.")
    using = tree.args.get("using")
    for key in ("on", "whens"):
        part = tree.args.get(key)
        for table in part.find_all(exp.Table) if isinstance(part, exp.Expression) else []:
            if not table.args.get("db") and table.name.lower() in ctes:
                raise ValueError(f"This MERGE reads the CTE '{ctes[table.name.lower()]}' outside USING, and the "
                                 "local engine can't run that yet, because it carries a MERGE's CTEs into its USING "
                                 "only. Join what you need into the USING query, or put the CTE's query where it is "
                                 "read.")
    alias = using.alias if using is not None else ""
    if using is None or not (alias or isinstance(using, exp.Table)):
        return  # no source alias: refused by delta_dml with its own words, as a MERGE without WITH is
    query = exp.select("*").from_(using.copy())
    query.set("with_", with_.copy())
    tree.set("using", exp.Subquery(this=query, alias=exp.TableAlias(this=exp.to_identifier(alias or using.name))))
    tree.set("with_", None)


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
    if then.expression is None and not isinstance(then.this, exp.Star):
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
