"""Procedures, functions and system procedures in local T-SQL (WR-224): each runs as a Fabric Warehouse
documents it or is refused by its own name, never run as a no-op.

- ``CREATE``, ``ALTER``, ``CREATE OR ALTER`` and ``DROP`` of a ``PROCEDURE`` or ``FUNCTION`` (scalar or table-
  valued), ``EXEC`` of a procedure, and a call to a user-defined function (``dbo.f(1)``, ``FROM dbo.tvf(1)``) are
  refused by name: a procedure or function takes parameters, which are variables, and runs with control flow, so
  they wait on the local interpreter for variables and control flow. ``DROP`` is refused rather than skipped, so a
  script never looks as if it removed one.
- Dynamic SQL (``sp_executesql``, ``EXEC ('...')`` and ``EXEC (@sql)``) is refused by name: the local engine never
  runs text as SQL.
- ``sp_rename`` renames a table (``@objtype`` left out or ``'OBJECT'``, as a warehouse takes it) or a column
  (``'COLUMN'``, which Microsoft's T-SQL surface area page names for renaming a warehouse column). Other object types
  are refused by name; the engine refuses renaming a Delta table, a registered folder's table or a view.
- Any other system procedure (``sp_help``, ``sp_helptext``, ``sp_columns``, ``xp_...``) is refused by name, pointing to
  INFORMATION_SCHEMA.
- Triggers are refused by name: a warehouse has none either.
- An INFORMATION_SCHEMA view the local engine doesn't have (``ROUTINES``, ``PARAMETERS``) is refused by name.
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot.tokens import Token, TokenType

_NOT_YET = "This T-SQL can't be run on the local engine: "
_WAITS = ("Procedures and user-defined functions take parameters and use control flow, which the local engine "
          "doesn't run yet. Run it in a Fabric Warehouse, or run its statements one at a time with values in place of "
          "its parameters.")
_DYNAMIC = ("runs text as SQL (dynamic SQL), which the local engine doesn't do. Write the statement itself in the "
            "script, with values in place of its parameters, or run it in a Fabric Warehouse.")
_CATALOG = ("INFORMATION_SCHEMA.TABLES and INFORMATION_SCHEMA.COLUMNS list the project's tables and columns, and "
            "INFORMATION_SCHEMA.VIEWS its views.")
_KINDS = {"PROCEDURE": "stored procedures", "PROC": "stored procedures", "FUNCTION": "user-defined functions"}
# The INFORMATION_SCHEMA views a warehouse has that the local engine has too (DuckDB's, with a note).
INFORMATION_VIEWS = {"tables", "columns", "views", "schemata", "table_constraints", "key_column_usage",
                     "referential_constraints", "check_constraints", "constraint_column_usage",
                     "constraint_table_usage"}
RENAMED = "kf_sp_rename"


def statement(tree: exp.Expression) -> exp.Expression:
    """``tree`` as it runs here (``sp_rename`` as ALTER TABLE ... RENAME), or a ValueError naming what isn't run."""
    if isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() in ("PROCEDURE", "PROC", "FUNCTION",
                                                                                   "TRIGGER"):
        kind = tree.args["kind"].upper()
        verb = "CREATE OR ALTER" if tree.args.get("replace") else "CREATE"
        _routine(verb, kind, tree.this.this if isinstance(tree.this, exp.Schema) else tree.this)
    if isinstance(tree, exp.Command) and (tree.name or "").upper() == "ALTER":
        words = str(tree.expression.name if isinstance(tree.expression, exp.Literal) else
                    tree.expression or "").split()
        if words and words[0].upper() in ("PROCEDURE", "PROC", "FUNCTION", "TRIGGER"):
            _routine("ALTER", words[0].upper(), words[1] if len(words) > 1 else "")
    if isinstance(tree, exp.Drop) and (tree.args.get("kind") or "").upper() in ("PROCEDURE", "PROC", "FUNCTION",
                                                                                 "TRIGGER"):
        tables = tree.args.get("tables") or [tree.this]
        _routine("DROP", tree.args["kind"].upper(), tables[0] if tables else "")
    if isinstance(tree, exp.Alter) and any(isinstance(a, (exp.AlterRename, exp.RenameColumn))
                                           for a in tree.args.get("actions") or []) \
            and not tree.meta.get(RENAMED):
        raise ValueError(_NOT_YET + "ALTER TABLE ... RENAME isn't T-SQL. A Fabric Warehouse renames a table with "
                         "EXEC sp_rename 'dbo.old_name', 'new_name' and a column with EXEC sp_rename "
                         "'dbo.table.old_name', 'new_name', 'COLUMN'.")
    if isinstance(tree, exp.Execute):
        return _execute(tree)
    _calls(tree)
    return tree


def _routine(verb: str, kind: str, name: object) -> None:
    written = name.sql(dialect="tsql") if isinstance(name, exp.Expression) else str(name)
    if kind == "TRIGGER":
        raise ValueError(f"{verb} TRIGGER {written} can't be run: a Fabric Warehouse has no triggers either. Run the "
                         "change the trigger would make as its own statement.".replace("  ", " "))
    word = "PROCEDURE" if kind == "PROC" else kind
    raise ValueError(_NOT_YET + f"{verb} {word} {written} isn't mapped yet. {_WAITS}".replace("  ", " "))


def _name(table: exp.Expression) -> str:
    return table.sql(dialect="tsql") if isinstance(table, exp.Expression) else ""


def _execute(tree: exp.Execute) -> exp.Expression:
    target = tree.this
    if not isinstance(target, exp.Table) or not isinstance(target.this, exp.Identifier):
        raise ValueError(_NOT_YET + f"EXEC of a string {_DYNAMIC}")
    name = target.name.lower()
    if name == "sp_executesql":
        raise ValueError(_NOT_YET + f"sp_executesql isn't mapped yet. It {_DYNAMIC}")
    if name == "sp_rename" and (target.db or "sys").lower() in ("sys", "dbo"):
        return _rename(tree)
    if name.startswith(("sp_", "xp_")):
        raise ValueError(_NOT_YET + f"the system procedure {target.name} isn't mapped yet. {_CATALOG}")
    raise ValueError(_NOT_YET + f"EXEC {_name(target)} isn't mapped yet. {_WAITS}")


def _calls(tree: exp.Expression) -> None:
    """A user-defined function's call (a schema-qualified call) is refused by its name."""
    # x.value(...) on a table or its alias is a method on a column (XML's value, nodes), not a function in schema x
    tables = {name.lower() for t in tree.find_all(exp.Table) for name in (t.alias, t.name) if name}
    for dot in tree.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Anonymous) and isinstance(dot.this, exp.Identifier) \
                and dot.this.name.lower() not in tables:
            _function(f"{dot.this.name}.{dot.expression.name}")
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Anonymous) and table.db:
            _function(f"{table.db}.{table.this.name}")


def _function(name: str) -> None:
    raise ValueError(_NOT_YET + f"{name} is a user-defined function, which isn't mapped yet. {_WAITS}")


# ---------- sp_rename

_PARAMETERS = ("objname", "newname", "objtype")


def _arguments(tree: exp.Execute) -> dict[str, str]:
    found: dict[str, str] = {}
    for at, arg in enumerate(tree.expressions):
        key = _PARAMETERS[at] if at < len(_PARAMETERS) else ""
        value = arg
        if isinstance(arg, exp.EQ) and isinstance(arg.this, exp.Parameter):
            key, value = arg.this.name.lower(), arg.expression
        if key not in _PARAMETERS or key in found:
            raise ValueError(_NOT_YET + "this sp_rename isn't mapped yet. Write EXEC sp_rename 'dbo.old_name', "
                             "'new_name' for a table, or EXEC sp_rename 'dbo.table.old_name', 'new_name', 'COLUMN' "
                             "for a column.")
        if not (isinstance(value, (exp.Literal, exp.National)) and (isinstance(value, exp.National) or
                                                                     value.is_string)):
            raise ValueError(_NOT_YET + "sp_rename's names have to be written in quotes here, because variables "
                             "aren't mapped yet on the local engine.")
        found[key] = value.name
    if "objname" not in found or "newname" not in found:
        raise ValueError("Procedure or function 'sp_rename' expects parameters '@objname' and '@newname', which "
                         "were not supplied.")
    return found


def _parts(written: str) -> list[str]:
    """'dbo.t', '[dbo].[t]' or 't' as its parts, brackets and quotes taken off."""
    parts: list[str] = []
    word, quote = "", ""
    for ch in written.strip():
        if quote:
            if ch == quote:
                quote = ""
            else:
                word += ch
        elif ch in "[\"":
            quote = "]" if ch == "[" else '"'
        elif ch == ".":
            parts.append(word)
            word = ""
        else:
            word += ch
    parts.append(word)
    return [p.strip() for p in parts]


def _rename(tree: exp.Execute) -> exp.Expression:
    args = _arguments(tree)
    kind = (args.get("objtype") or "OBJECT").strip().upper()
    parts = _parts(args["objname"])
    new = args["newname"]
    if kind not in ("OBJECT", "COLUMN"):
        raise ValueError(_NOT_YET + f"sp_rename with @objtype '{kind}' isn't mapped yet. A Fabric Warehouse takes "
                         "'OBJECT', and the local engine renames tables and columns only.")
    if not new or "/" in new or "\\" in new or new.endswith("."):
        raise ValueError(f"'{new}' can't be a name in a Fabric Warehouse: a name can't be empty, contain / or \\, or "
                         "end with a period.")
    if any(not p for p in parts) or len(parts) > (3 if kind == "COLUMN" else 2) or \
            kind == "COLUMN" and len(parts) < 2:
        raise ValueError(_NOT_YET + f"sp_rename can't read '{args['objname']}'. Write 'dbo.table' for a table and "
                         "'dbo.table.column' with 'COLUMN' for a column.")
    if kind == "COLUMN":
        column = parts[-1]
        schema, table = (parts[0], parts[1]) if len(parts) == 3 else ("dbo", parts[0])
    else:
        schema, table = (parts[0], parts[1]) if len(parts) == 2 else ("dbo", parts[0])
        if "." in new:
            raise ValueError(_NOT_YET + f"sp_rename's new name is the name alone: '{new}' would name the table "
                             f"[{new}] in T-SQL, with the period in its name. Write '{new.split('.')[-1]}'.")
    if table.startswith("#"):
        raise ValueError(_NOT_YET + "renaming a #temp table isn't mapped yet. Make a new #temp table with SELECT "
                         "... INTO.")
    target = exp.Table(this=exp.to_identifier(table, quoted=True), db=exp.to_identifier(schema, quoted=True))
    if kind == "COLUMN":
        action: exp.Expression = exp.RenameColumn(this=exp.to_identifier(column, quoted=True),
                                                  to=exp.to_identifier(new, quoted=True))
    else:
        action = exp.AlterRename(this=exp.Table(this=exp.to_identifier(new, quoted=True)))
    renamed = exp.Alter(this=target, kind="TABLE", actions=[action])
    renamed.meta[RENAMED] = True
    return renamed


def information_schema(view: str) -> None:
    """Refuse by name an INFORMATION_SCHEMA view the local engine doesn't have."""
    if view.lower() not in INFORMATION_VIEWS:
        raise ValueError(_NOT_YET + f"INFORMATION_SCHEMA.{view.upper()} isn't mapped yet"
                         + (". The local engine has no procedures or functions" if view.lower() in
                            ("routines", "parameters", "routine_columns") else "")
                         + f". {_CATALOG}")


_ROUTINES = {"PROCEDURE", "PROC", "FUNCTION", "TRIGGER"}
_NAME_TOKENS = (TokenType.VAR, TokenType.DOT, TokenType.IDENTIFIER)


def refuse_routines(batch: str, tokens: list[Token]) -> None:
    """``CREATE [OR ALTER]``, ``ALTER`` or ``DROP [IF EXISTS]`` of a procedure, function or trigger, and ``EXEC (...)``
    of a string, refused by name from the tokens, before the body is read (sqlglot can't read every body)."""
    for i, tok in enumerate(tokens):
        if i and tokens[i - 1].token_type == TokenType.DOT:
            continue
        if tok.token_type == TokenType.EXECUTE or tok.text.upper() in ("EXEC", "EXECUTE"):
            if i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN:
                raise ValueError(_NOT_YET + f"EXEC of a string {_DYNAMIC}")
            continue
        if tok.token_type not in (TokenType.CREATE, TokenType.ALTER, TokenType.DROP):
            continue
        j, verb = i + 1, tok.text.upper()
        if verb == "CREATE" and j + 1 < len(tokens) and tokens[j].text.upper() == "OR" and \
                tokens[j + 1].text.upper() == "ALTER":
            j, verb = j + 2, "CREATE OR ALTER"
        if j >= len(tokens) or tokens[j].text.upper() not in _ROUTINES:
            continue
        kind = tokens[j].text.upper()
        j += 1
        if verb == "DROP" and j + 1 < len(tokens) and tokens[j].text.upper() == "IF" and \
                tokens[j + 1].text.upper() == "EXISTS":
            j += 2
        name = ""
        if j < len(tokens):  # the name as written: the tokens that follow with nothing between them
            end = j
            while end + 1 < len(tokens) and tokens[end + 1].start == tokens[end].end + 1 and \
                    tokens[end + 1].token_type not in (TokenType.L_PAREN, TokenType.SEMICOLON):
                end += 1
            name = batch[tokens[j].start:tokens[end].end + 1]
        _routine(verb, kind, name)


def bare_call(batch: str, tokens: list[Token]) -> str:
    """A procedure called without EXEC, which T-SQL takes as a batch's first statement (``sp_rename 'a', 'b'``),
    with EXEC written in, so it is read and named as EXEC is."""
    if not tokens or tokens[0].token_type != TokenType.VAR:
        return batch
    j = 1
    while j + 1 < len(tokens) and tokens[j].token_type == TokenType.DOT:
        j += 2
    following = tokens[j].token_type if j < len(tokens) else None
    if following in (TokenType.STRING, TokenType.NATIONAL_STRING, TokenType.PARAMETER) or \
            following is None and tokens[0].text.lower().startswith(("sp_", "xp_")):
        start = tokens[0].start
        return batch[:start] + "EXEC " + batch[start:]
    return batch


_CATALOG_COLUMN = {"tables": "table_catalog", "columns": "table_catalog", "views": "table_catalog",
                   "schemata": "catalog_name"}


def project_rows(table: exp.Table) -> None:
    """INFORMATION_SCHEMA.TABLES, COLUMNS, VIEWS and SCHEMATA list the project's own objects, as a warehouse's list
    its database's: not DuckDB's system views, and not #temp tables, which T-SQL keeps in tempdb."""
    column = _CATALOG_COLUMN.get(table.name.lower())
    if column is None:
        return
    alias = table.args.get("alias")
    inner = table.copy()
    inner.set("alias", None)
    query = exp.select("*").from_(inner).where(
        exp.not_(exp.column(column).isin(exp.Literal.string("system"), exp.Literal.string("temp"))))
    table.replace(exp.Subquery(this=query, alias=alias.copy() if alias else
                               exp.TableAlias(this=exp.to_identifier(table.name))))
