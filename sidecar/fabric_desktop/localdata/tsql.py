"""T-SQL on the local engine (FD-LDE-004): a T-SQL script read with sqlglot and run as DuckDB SQL,
with the differences that change answers translated, and anything that can't be carried refused.

sqlglot already turns most T-SQL into DuckDB SQL (TOP, brackets, ISNULL, IIF, DATEADD, CROSS
APPLY, SELECT INTO, OFFSET/FETCH). On top of that this module:

- divides integers as integers (``7 / 2`` is 3), and averages integers as integers, as T-SQL
  does, using the project's column types to tell. A division whose types it can't tell is left
  as DuckDB divides and reported in ``notes``. Dividing by zero (``/`` and ``%``) is T-SQL's error,
  ``ROUND`` keeps its value's type, and ``ISNULL`` takes its first argument's type (WR-211);
- gives ``+``, ``-``, ``*``, ``/`` with a decimal side, and ``AVG`` of a decimal, T-SQL's precision and
  scale, and divides decimals exactly, cut at that scale (WR-206). What can't be done exactly here is
  done in floating point and reported in ``notes``;
- reads date parts by T-SQL's names (``y`` is day of year, ``w`` weekday, ``m`` month), counts
  ``DATEDIFF(week, ...)`` in Sundays crossed, starts ``DATETRUNC(week, ...)`` on Sunday, and keeps a
  date a date in ``DATEADD`` and ``DATETRUNC``;
- counts ``LEN`` without trailing spaces, numbers ``DATEPART(weekday)`` from Sunday = 1 and
  ``DATEPART(week)`` from the week of 1 January (Fabric's defaults: DATEFIRST 7, us_english);
- joins strings with ``+`` as T-SQL does when a side is known to be text;
- runs ``BIT`` as a boolean, and ``CHOOSE``, ``ROUND(x, n, 1)``, ``DATALENGTH``, ``STRING_SPLIT`` and
  ``TOP n WITH TIES``, which DuckDB doesn't have (``ISNUMERIC`` is refused for now);
- runs ``QUOTENAME``, ``STR`` and ``FORMAT`` (dates, and ``N``, ``F`` and ``D`` for integers and
  decimals, in en-US; see tsql_format), and refuses by name a function DuckDB doesn't have;
- gives ``POWER`` its first argument's type (an integer base gives an integer; a decimal base is refused);
- matches ``LIKE`` patterns with ``[a-c]`` and ``[^a-c]`` character ranges, as a regular expression;
- compares text without trailing spaces (``'a' = 'a  '``) when both sides are known to be text, matches
  ``LIKE`` without the text's trailing spaces, notes that ``GROUP BY``, ``DISTINCT`` and ``UNION`` keep
  them apart, runs a binary ``COLLATE`` and refuses any other by name (WR-210);
- keeps ``#temp`` tables apart from ordinary tables of the same name (as temporary ``"#name"``);
- returns ``GETDATE()`` and friends as UTC times without a zone, as a Fabric Warehouse does.

It refuses, rather than half-translates: variables, control flow and batches (``DECLARE``,
``SET @x``, ``IF``, ``WHILE``, ``BEGIN ... END``, ``EXEC``, ``PRINT``), transactions (a Delta write
isn't part of one here), procedures and functions, ``USE``, and anything sqlglot reports it can't
write in DuckDB SQL (``FOR JSON``, ``OPTION (...)``). ``GO`` lines split the script.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections.abc import Callable, Container
from typing import Any, cast

import sqlglot
from sqlglot import ErrorLevel, exp, jsonpath
from sqlglot.dialects.tsql import TSQL
from sqlglot.errors import SqlglotError
from sqlglot.helper import seq_get
from sqlglot.tokens import Token, TokenType

from ..sql import KfDuckDB
from . import tsql_ctes, tsql_format, tsql_hints, tsql_routines, tsql_tables, tsql_types
from .. import sql_dialects, tsql_clone

_GO = re.compile(r"^[ \t]*GO[ \t]*(?:--[^\n]*)?$", re.IGNORECASE | re.MULTILINE)
_INTEGERS = exp.DataType.INTEGER_TYPES
_TEXT = exp.DataType.TEXT_TYPES
_NUMBERS = exp.DataType.NUMERIC_TYPES
# Session options the local engine honours, with the values it runs as: a warehouse's defaults. Another value is
# refused by name, never dropped: SET ANSI_NULLS OFF makes "x = NULL" true in T-SQL, and was run as ON.
_SETTINGS = {
    "NOCOUNT": {"ON", "OFF"},  # only the "rows affected" messages
    "XACT_ABORT": {"ON", "OFF"},  # a script stops at its first error either way (sql-compatibility.md)
    "ANSI_NULLS": {"ON"}, "QUOTED_IDENTIFIER": {"ON"}, "ANSI_WARNINGS": {"ON"}, "ANSI_PADDING": {"ON"},
    "ARITHABORT": {"ON"}, "CONCAT_NULL_YIELDS_NULL": {"ON"}, "NUMERIC_ROUNDABORT": {"OFF"},
    "DATEFORMAT": {"MDY"}, "LANGUAGE": {"US_ENGLISH", "ENGLISH"},
}

_REFUSED = {
    exp.Declare: "variables (DECLARE)",
    exp.IfBlock: "IF blocks",
    exp.WhileBlock: "WHILE loops",
    exp.Execute: "EXEC",
    exp.Use: "USE",
    exp.Transaction: "transactions (BEGIN TRAN)",
    exp.Commit: "transactions (COMMIT)",
    exp.Rollback: "transactions (ROLLBACK)",
}
_LATER = ("Run the statements one at a time, with values in place of variables. A batch with variables and "
          "control flow runs in a Fabric Warehouse, not on the local engine yet.")


_GO_TIMES = re.compile(r"^[ \t]*GO[ \t]+(\d+)[ \t]*(?:--[^\n]*)?$", re.IGNORECASE | re.MULTILINE)


def split_batches(sql: str) -> list[str]:
    """The script's batches: the text between ``GO`` lines. ``GO n`` (run the batch n times) is refused by name."""
    if (times := _GO_TIMES.search(sql)) is not None:
        raise ValueError(_NOT_YET + f"GO {times[1]} (run the batch {times[1]} times) isn't mapped yet. Repeat the "
                         "statements, or use GO on its own.")
    return [part for part in _GO.split(sql) if part.strip()]


def translate(sql: str, columns: dict[str, dict[str, dict[str, str]]] | None = None,
              known: Container[str] | None = None) -> tuple[list[str], list[str]]:
    """Each statement of the T-SQL script ``sql`` as DuckDB SQL, and notes on what may differ.

    ``columns`` is {schema: {table: {column: DuckDB type}}} for the tables the script may read; it
    lets divisions and ``+`` follow T-SQL's types. ``known`` is the upper-case names of the functions
    the DuckDB database has; a function call outside it is refused by name rather than left to DuckDB's
    catalog error. Raises ValueError for what isn't carried."""
    try:
        return _translate(sql, columns, known)
    except RecursionError:  # sqlglot reads and writes a tree by recursion, so a deep enough nesting stops it
        raise ValueError(_TOO_DEEP) from None


def _translate(sql: str, columns: dict[str, dict[str, dict[str, str]]] | None,
               known: Container[str] | None) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    notes: list[str] = []
    skipped = False
    schema = _schema(columns or {})
    for batch in split_batches(sql):
        for tree in _parse(batch):
            if _harmless_setting(tree):
                skipped = True
                continue
            tree = tsql_routines.statement(tree)  # WR-224: procedures, functions, sp_rename, sp_help ...
            _refuse(tree)
            _replicate_inputs(tree)  # WR-235: before N'' text and CONVERT's (MAX) are rewritten
            _national_text(tree)
            notes += tsql_ctes.check(tree)  # WR-230: no recursive CTE, no change through a CTE
            notes += tsql_hints.check(tree)  # WR-231: labels and hints that change no rows, with a note
            tsql_format.parses(tree)  # WR-229: before the types are read, so a PARSE is its number type
            _temp_tables(tree)
            notes += tsql_types.check(tree)  # WR-223: types as a warehouse has them, before any rewrite
            _joined_changes(tree)
            tree = _views(tree, columns)
            notes += _names(tree, columns)
            notes += _as_of(tree)
            _table_columns(tree)
            for parts in list(tree.find_all(exp.TimeFromParts)):  # before CONVERT's rewrite reads it again
                parts.replace(_timefromparts(parts))
            tsql_types.round_datetimes(tree)  # before CONVERT's rewrite, which drops the type
            notes += _converts(tree)
            notes += _types(tree, schema)
            _percentiles(tree)
            notes += _likes(tree)
            _patindex(tree)
            notes += _json(tree)
            tsql_tables.rewrite(tree)  # WR-227: before _gaps, which reads TOP ... WITH TIES
            _functions(tree)
            _gaps(tree)
            _string_edges(tree)
            _char_codes(tree)
            tsql_types.expand_datetimes(tree)
            if known is not None:
                _unknown_functions(tree, known)
            tsql_types.delta_times(tree, columns)  # last, so the passes above read the columns' own types
            if isinstance(tree, exp.Alter) and sum(isinstance(a, exp.ColumnDef)
                                                   for a in tree.args.get("actions") or []) > 1:
                raise ValueError(_NOT_YET + "ALTER TABLE ... ADD of more than one column in one statement isn't "
                                 "mapped yet. Add each column in an ALTER TABLE of its own.")
            try:
                text = tree.sql(dialect=KfDuckDB, unsupported_level=ErrorLevel.RAISE)
            except SqlglotError as exc:
                raise ValueError(f"This T-SQL can't be run on the local engine: {_first_line(exc)}") from None
            if len(text) > 50 * len(batch) + 200_000:  # a backstop: each rewrite reads its arguments once
                raise ValueError(_TOO_DEEP)
            if text.strip():
                texts.append(text)
    if not texts:
        raise ValueError("There's nothing to run: SET NOCOUNT, ANSI_NULLS and the other session options change "
                         "nothing on the local engine, so they're skipped." if skipped else "The query is empty.")
    return texts, list(dict.fromkeys(notes))


class KfTSQL(TSQL):
    """T-SQL as SQL Server reads it: ``FLOAT`` is an 8-byte float, ``FLOAT(1)`` to ``FLOAT(24)`` and
    ``REAL`` 4 bytes. sqlglot reads ``FLOAT`` as ``REAL``, which DuckDB runs as 4 bytes, so
    ``CAST(0.1 AS FLOAT)`` came back as 0.10000000149."""

    class Tokenizer(TSQL.Tokenizer):  # sqlglot sets Tokenizer per dialect at run time
        KEYWORDS = {**TSQL.Tokenizer.KEYWORDS, "FLOAT": TokenType.DOUBLE}

    # In T-SQL, W is weekday (as DW), not week: DATEPART(w, d) is 1 to 7.
    DATE_PART_MAPPING = {**TSQL.DATE_PART_MAPPING, "W": "DAYOFWEEK"}

    class Parser(TSQL.Parser):  # type: ignore[misc,valid-type]  # as Tokenizer above
        # FORMAT is kept as written until its value's type is known (tsql_format).
        # SQUARE is a float in T-SQL; sqlglot reads it as POWER(x, 2), which keeps an integer's type and
        # refuses a decimal (_power).
        # JSON_VALUE and JSON_QUERY keep their path as the text T-SQL was given; _json reads it (WR-219).
        # PARSE and TRY_PARSE take "x AS type USING culture", which sqlglot can't read (WR-229).
        FUNCTION_PARSERS = {**TSQL.Parser.FUNCTION_PARSERS, "PARSE": tsql_format.parser(False),
                            "TRY_PARSE": tsql_format.parser(True)}
        FUNCTIONS = {**TSQL.Parser.FUNCTIONS, "FORMAT": tsql_format.build,
                     "SQUARE": lambda args: exp.Pow(this=exp.Cast(this=seq_get(args, 0), to=exp.DataType.build("DOUBLE")),
                                                    expression=exp.Literal.number(2)),
                     "JSON_VALUE": lambda args: exp.JSONExtractScalar(this=seq_get(args, 0), expression=seq_get(args, 1),
                                                                      scalar_only=True),
                     "JSON_QUERY": lambda args: exp.JSONExtract(this=seq_get(args, 0),
                                                                expression=seq_get(args, 1) or exp.Literal.string("$"),
                                                                json_query=True)}


def _refuse_output(batch: str) -> None:
    """T-SQL's OUTPUT clause names the rows a change wrote as ``inserted`` and ``deleted``. sqlglot reads it as
    RETURNING, which can't name either, so DuckDB stopped with its own binder error, or the query didn't read at
    all (DELETE ... OUTPUT). It is refused by name instead. OUTPUT is a reserved word in T-SQL, so a column called
    output is always bracketed or quoted, and isn't refused."""
    if "OUTPUT" not in batch.upper():
        return
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # left to the parser, which says what it can't read
    if any(t.token_type == TokenType.RETURNING and t.text.upper() == "OUTPUT" for t in tokens):
        raise ValueError(_NOT_YET + "OUTPUT isn't mapped yet: run the INSERT, UPDATE, DELETE or MERGE, then SELECT "
                         "the rows it changed.")


def _joined_changes(tree: exp.Expression) -> None:
    """T-SQL names the table an UPDATE or DELETE changes in its FROM: ``UPDATE t SET ... FROM dbo.a AS t JOIN dbo.b
    AS s ON ...``, ``DELETE t FROM dbo.a AS t JOIN ...``. DuckDB names it once, after UPDATE or DELETE FROM, with
    the other tables in FROM (UPDATE) or USING (DELETE): ``UPDATE dbo.a AS t SET ... FROM dbo.b AS s WHERE ...``.
    Each JOIN becomes a filter in WHERE, which gives the same rows for an inner join. Read as written, DuckDB
    stopped (alias not found), or joined the table to itself and changed every row."""
    if isinstance(tree, exp.Update):
        what, named, source = "UPDATE", tree.this, tree.args.get("from_")
    elif isinstance(tree, exp.Delete) and tree.args.get("tables"):
        what, named, source = "DELETE", tree.args["tables"][0], exp.From(this=tree.this)
    else:
        return
    if not isinstance(source, exp.From) or not isinstance(named, exp.Table) or not isinstance(source.this, exp.Table):
        return
    first: exp.Table = source.this
    joins = first.args.get("joins") or []
    items = [first] + [j.this for j in joins]

    def is_target(item: exp.Expression) -> bool:
        if not isinstance(item, exp.Table):
            return False
        if item.alias:
            return not named.db and named.name.lower() == item.alias.lower()
        return named.name.lower() == item.name.lower() and (named.db or "dbo").lower() == (item.db or "dbo").lower()

    found = [i for i, item in enumerate(items) if is_target(item)]
    if not found:
        if what == "DELETE":  # DELETE dbo.a FROM dbo.b AS s WHERE dbo.a.id = s.id: the other tables are its USING
            tree.set("this", named.copy())
            tree.set("tables", None)
            tree.set("using", [first.copy()])
        return  # UPDATE dbo.a SET ... FROM dbo.b AS s WHERE ...: DuckDB reads it as T-SQL does
    if found != [0]:
        raise ValueError(_NOT_YET + f"{what} isn't mapped yet when the table it changes isn't the first one in FROM: "
                         "name it first, as FROM table AS t JOIN ...")
    if any(j.side or (j.kind and j.kind.upper() not in ("INNER", "CROSS")) for j in joins):
        raise ValueError(_NOT_YET + f"{what} isn't mapped yet with an outer join (LEFT, RIGHT or FULL JOIN) in its "
                         "FROM: use WHERE [NOT] EXISTS (...), or a MERGE.")
    filters = [j.args["on"] for j in joins if j.args.get("on") is not None]
    if any(j.args.get("using") for j in joins):
        raise ValueError(_NOT_YET + f"{what} isn't mapped yet with JOIN ... USING in its FROM: use JOIN ... ON.")
    where = tree.args.get("where")
    if where is not None:
        filters.append(where.this)
    target = first.copy()
    target.set("joins", None)
    others = [j.this.copy() for j in joins]
    tree.set("where", exp.Where(this=exp.and_(*[exp.paren(f.copy(), copy=False) for f in filters])) if filters else None)
    if what == "UPDATE":
        tree.set("this", target)
        if others:
            others[0].set("joins", [exp.Join(this=o) for o in others[1:]] or None)
        tree.set("from_", exp.From(this=others[0]) if others else None)
    else:
        tree.set("this", target)
        tree.set("tables", None)
        tree.set("using", others or None)
# T-SQL's reserved words for control flow and cursors: sqlglot read them as a column (RETURN) or a column with an
# alias (GOTO x), which reached DuckDB as a syntax error.
_RESERVED_STATEMENTS = {"RETURN": "RETURN", "BREAK": "BREAK", "CONTINUE": "CONTINUE", "GOTO": "GOTO",
                        "WAITFOR": "WAITFOR", "CHECKPOINT": "CHECKPOINT", "OPEN": "cursors (OPEN)",
                        "CLOSE": "cursors (CLOSE)", "DEALLOCATE": "cursors (DEALLOCATE)"}


_NOT_SCRIPT_ADVICE = {"WAITFOR", "CHECKPOINT", "OPEN", "CLOSE", "DEALLOCATE"}  # nothing to run one at a time


def _refuse_control(batch: str) -> None:
    """Refuse by name the statements sqlglot misreads: the reserved words above anywhere (a reserved word can't be
    an unquoted name), THROW where a statement starts (T-SQL wants a ; before it), SAVE TRANSACTION, SET
    TRANSACTION and labels (``name:``)."""
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # _parse reports it
    routine = any(tok.token_type == TokenType.CREATE and i + 1 < len(tokens)
                  and tokens[i + 1].text.upper() in ("PROCEDURE", "PROC", "FUNCTION", "TRIGGER")
                  for i, tok in enumerate(tokens))
    if routine:
        return  # _refuse names CREATE PROCEDURE and the rest, whose bodies may RETURN
    named = (TokenType.VAR, TokenType.SET)
    for i, tok in enumerate(tokens):
        if tok.token_type not in named:
            continue
        before = tokens[i - 1].token_type if i else None
        after = tokens[i + 1] if i + 1 < len(tokens) else None
        if before in (TokenType.DOT, TokenType.PARAMETER):
            continue  # t.x, @x
        word = tok.text.upper()
        what = None
        if tok.token_type == TokenType.VAR and word in _RESERVED_STATEMENTS:
            what = _RESERVED_STATEMENTS[word]
        elif word == "THROW" and before in (None, TokenType.SEMICOLON):
            what = "THROW"
        elif word in ("SAVE", "SET") and after is not None and after.text.upper() in ("TRAN", "TRANSACTION"):
            what = "transactions (SAVE TRANSACTION)" if word == "SAVE" else "SET TRANSACTION"
        elif tok.token_type == TokenType.VAR and after is not None and after.token_type == TokenType.COLON:
            what = f"labels ({tok.text}:)"
        if what is not None:
            advice = "Run it in a Fabric Warehouse." if word in _NOT_SCRIPT_ADVICE else _LATER
            raise ValueError(f"The local engine doesn't run T-SQL {what} yet. {advice}")


def _refuse_json_clauses(batch: str) -> None:
    """``FOR JSON PATH`` or ``AUTO`` at a query's end, and ``NULL ON NULL`` or ``ABSENT ON NULL`` in JSON_OBJECT and
    JSON_ARRAY, refused by name (WR-219): sqlglot drops the first and can't read the second. Tokens, not text."""
    if "JSON" not in batch.upper():
        return
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # _parse reports it
    words = [t.text.upper() if t.token_type not in (TokenType.STRING, TokenType.IDENTIFIER, TokenType.NATIONAL_STRING)
             else "" for t in tokens]
    for i, word in enumerate(words):
        if word == "FOR" and words[i + 1:i + 2] == ["JSON"]:
            mode = words[i + 2] if i + 2 < len(words) else ""
            raise ValueError(_NOT_YET + f"FOR JSON {mode if mode in ('PATH', 'AUTO') else ''}".rstrip()
                             + " isn't mapped yet. Build each row's JSON with JSON_OBJECT or JSON_ARRAY, or run it "
                             "in a Fabric Warehouse.")
        if word in ("NULL", "ABSENT") and words[i + 1:i + 3] == ["ON", "NULL"]:
            raise ValueError(_NOT_YET + f"{word} ON NULL in JSON_OBJECT or JSON_ARRAY isn't mapped yet. Leave it "
                             "out: JSON_OBJECT keeps a NULL value as null and JSON_ARRAY leaves it out, as T-SQL does "
                             "by default.")


_LOADING_NOTE = ("isn't mapped yet. It loads files from cloud storage (an Azure Storage or OneLake URL), which the "
                 "local engine doesn't read, so nothing was loaded. Put the file in one of this project's folders, "
                 "where it becomes a table such as dbo.orders, and INSERT ... SELECT from that table, or run it in a "
                 "Fabric Warehouse.")


def _from_text(tokens: list[Token], at: int) -> bool:
    """Whether the next FROM after ``at`` is followed by a string: COPY INTO's FROM 'url'. T-SQL reads no table
    from a string, so ``SELECT 1 copy INTO t FROM x`` (copy as an alias) isn't COPY INTO."""
    following = next((j for j in range(at, len(tokens)) if tokens[j].token_type == TokenType.FROM), None)
    return following is not None and following + 1 < len(tokens) \
        and tokens[following + 1].token_type in (TokenType.STRING, TokenType.NATIONAL_STRING)


def _refuse_loading(batch: str) -> None:
    """COPY INTO, BULK INSERT and OPENROWSET(BULK ...) load or read files from a URL (WR-220). sqlglot read COPY INTO
    as DuckDB's COPY, which reads files on this PC, and couldn't read the other two, so each is refused by its T-SQL
    name. Tokens, not text: a string or a bracketed name that holds the words isn't refused. COPY INTO starts a
    statement, or reads FROM a string (T-SQL needs no ; between statements); BULK and OPENROWSET are reserved words in T-SQL, so neither is an unquoted name."""
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # _parse reports it
    for i, tok in enumerate(tokens):
        word = tok.text.upper()
        after = tokens[i + 1] if i + 1 < len(tokens) else None
        start = i == 0 or tokens[i - 1].token_type == TokenType.SEMICOLON
        what = None
        if tok.token_type == TokenType.COPY and after is not None and after.token_type == TokenType.INTO \
                and (start or _from_text(tokens, i + 2)):
            what = "COPY INTO"
        elif tok.token_type == TokenType.VAR and word == "BULK" and after is not None \
                and after.token_type == TokenType.INSERT:
            what = "BULK INSERT"
        elif tok.token_type == TokenType.VAR and word == "OPENROWSET" and after is not None \
                and after.token_type == TokenType.L_PAREN:
            what = "OPENROWSET"
        if what is not None:
            raise ValueError(_NOT_YET + f"{what} {_LOADING_NOTE}")


def _refuse_dbcc(batch: str) -> None:
    """DBCC (CHECKIDENT, SHOW_STATISTICS, ...) starts a statement; sqlglot reads it as a command it passes through.
    Tokens, not text, so a string that holds "DBCC" isn't refused."""
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # _parse reports it
    starts = [tok for i, tok in enumerate(tokens) if i == 0 or tokens[i - 1].token_type == TokenType.SEMICOLON]
    if any(tok.token_type == TokenType.VAR and tok.text.upper() == "DBCC" for tok in starts):
        raise ValueError(_NOT_YET + "DBCC isn't mapped yet. Run it in a Fabric Warehouse.")


_SECURITY_NOTE = ("isn't applied on this PC, because the local engine has no users, roles or permissions. It's refused "
                  "rather than skipped, so a script never looks as if it set them. Apply it in the Fabric Warehouse.")
_PRINCIPALS = {"USER", "ROLE", "LOGIN", "APPLICATION"}


def _refuse_security(batch: str) -> None:
    """Permissions, impersonation, row-level security, dynamic data masking, users and roles, refused by name (WR-217).
    None of them may run as a no-op. GRANT, DENY, REVOKE and REVERT are reserved words in T-SQL; the rest are read
    where a statement names them. Tokens, not text, so a string or a [bracketed] name that holds the words is
    left alone."""
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return  # _parse reports it
    words = [t.text.upper() if t.token_type not in (TokenType.STRING, TokenType.IDENTIFIER, TokenType.NATIONAL_STRING)
             else "" for t in tokens]
    for i, word in enumerate(words):
        before = words[i - 1] if i else ""
        after = words[i + 1] if i + 1 < len(words) else ""
        if before == "." or (i and tokens[i - 1].token_type == TokenType.PARAMETER):
            continue  # t.user, @role
        what = None
        if word in ("GRANT", "DENY", "REVOKE"):
            what = f"{word} (permissions)"
        elif word in ("EXECUTE", "EXEC") and after == "AS":
            what = f"{word} AS (impersonation)"
        elif word == "REVERT":
            what = "REVERT (impersonation)"
        elif word in ("CREATE", "ALTER", "DROP") and after == "SECURITY":
            what = f"{word} SECURITY POLICY (row-level security)"
        elif word == "MASKED" and (after == "WITH" or before == "DROP"):
            what = "MASKED WITH (dynamic data masking)"
        elif word in ("CREATE", "ALTER", "DROP") and after in _PRINCIPALS:
            what = f"{word} {after}"
        elif word == "ALTER" and after == "AUTHORIZATION":
            what = "ALTER AUTHORIZATION"
        if what is not None:
            raise ValueError(_NOT_YET + f"{what} {_SECURITY_NOTE}")


_COMPOUND = (TokenType.PLUS, TokenType.DASH, TokenType.STAR, TokenType.SLASH, TokenType.MOD, TokenType.PIPE,
             TokenType.AMP, TokenType.CARET)


def _refuse_session_options(batch: str) -> None:
    """``SET option value`` for an option the local engine doesn't run (ROWCOUNT, STATISTICS IO, LOCK_TIMEOUT, ...),
    refused by name. The ones it runs, with their values, are in _SETTINGS. ``SET @x = ...`` is a variable, and
    ``UPDATE t SET col = ...`` (or ``+=``) an assignment: a name followed by =, an operator and =, or a dot isn't an
    option."""
    if "SET" not in batch.upper():
        return
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return
    for i, tok in enumerate(tokens[:-1]):
        if tok.token_type != TokenType.SET:
            continue
        name = tokens[i + 1]
        then = tokens[i + 2].token_type if i + 2 < len(tokens) else None
        if name.token_type != TokenType.VAR or then in (TokenType.EQ, TokenType.DOT):
            continue
        if then in _COMPOUND and i + 3 < len(tokens) and tokens[i + 3].token_type == TokenType.EQ:
            continue  # UPDATE t SET n += 1: an assignment, not an option
        option = name.text.upper()
        if option in _SETTINGS or option in ("DATEFIRST", "TRANSACTION"):
            continue
        if option in ("STATISTICS", "SHOWPLAN_ALL", "SHOWPLAN_TEXT", "SHOWPLAN_XML") and i + 2 < len(tokens):
            option += f" {tokens[i + 2].text.upper()}" if option == "STATISTICS" else ""
        raise ValueError(_NOT_YET + f"SET {option} isn't mapped yet. Remove it, or run it in a Fabric Warehouse.")


def _schema_authorization(batch: str) -> bool:
    """CREATE SCHEMA name AUTHORIZATION owner, read by tokens, so a string that holds the words isn't refused."""
    if "AUTHORIZATION" not in batch.upper():
        return False
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return False  # _parse reports it
    words = [t.text.upper() if t.token_type != TokenType.STRING else "" for t in tokens]
    return any(words[i:i + 2] == ["CREATE", "SCHEMA"] and i + 3 < len(words) and words[i + 3] == "AUTHORIZATION"
               for i in range(len(words)))


# Time travel (WR-222): Fabric's OPTION (FOR TIMESTAMP AS OF '<UTC time>') at the end of a SELECT reads every table
# as it was then. Here each table is read from its Delta history at that time (delta.rewrite_as_of).
_AS_OF_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?")
AS_OF_DEFAULT_DAYS = 30  # a warehouse's history by default
AS_OF_MOST_DAYS = 120  # the most a warehouse keeps
_AS_OF_META = "kf_as_of"
_AS_OF_WORDS = ("OPTION", "(", "FOR", "TIMESTAMP", "AS", "OF")


def _take_as_of(batch: str) -> tuple[str, str | None]:
    """The batch without its OPTION (FOR TIMESTAMP AS OF '...'), and that time, or None when it has none. Tokens, so
    the words in a string or a comment aren't read. A time that isn't written out as T-SQL wants it is refused with
    T-SQL's own words; a variable or a second hint is refused by name."""
    if "TIMESTAMP" not in batch.upper():
        return batch, None
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        return batch, None  # _parse reports it
    found = [i for i in range(len(tokens) - 2) if tokens[i].text.upper() == "OPTION"
             and tokens[i + 1].token_type == TokenType.L_PAREN and tokens[i + 2].text.upper() == "FOR"]
    hinted = any(tokens[i].text.upper() == "FOR" and tokens[i + 1].text.upper() == "TIMESTAMP"
                 for i in range(len(tokens) - 1))
    if not found and hinted:  # beside another hint, OPTION (MAXDOP 1, FOR TIMESTAMP AS OF ...)
        raise ValueError(_NOT_YET + "OPTION (FOR TIMESTAMP AS OF ...) takes a time written out in quotes, such as "
                         "'2026-11-01T09:30:00' (UTC), and nothing else in the OPTION.")
    if not found:
        return batch, None
    if len(found) > 1:
        raise ValueError(_NOT_YET + "FOR TIMESTAMP AS OF can be used only once in a batch here, at the end of its "
                         "one SELECT, and this batch has it more than once. Put GO between the SELECTs.")
    i = found[0]
    words = [t.text.upper() for t in tokens[i:i + 6]]
    if words != list(_AS_OF_WORDS) or i + 7 >= len(tokens) or tokens[i + 6].token_type != TokenType.STRING \
            or tokens[i + 7].token_type != TokenType.R_PAREN:
        raise ValueError(_NOT_YET + "OPTION (FOR TIMESTAMP AS OF ...) takes a time written out in quotes, such as "
                         "'2026-11-01T09:30:00' (UTC), and nothing else in the OPTION. A warehouse doesn't take a "
                         "variable there either.")
    when = tokens[i + 6].text
    try:  # the shape, then the date itself: 2026-02-30 has the shape but isn't a day
        real = bool(_AS_OF_FORMAT.fullmatch(when)) and dt.datetime.fromisoformat(when) is not None
    except ValueError:
        real = False
    if not real:
        raise ValueError(f"FOR TIMESTAMP AS OF '{when}': An error occurred during timestamp conversion. Please "
                         "provide a timestamp in the format yyyy-MM-ddTHH:mm:ss[.fff].")
    return batch[:tokens[i].start] + " " + batch[tokens[i + 7].end + 1:], when


def _parse(batch: str) -> list[exp.Expression]:
    batch, when = _take_as_of(batch)
    batch = tsql_hints.take(batch, KfTSQL().tokenize)  # WR-231: statistics, and hints sqlglot can't read
    cloned = tsql_clone.local_refusal(batch)  # WR-226: never a no-op, never a full copy in a clone's place
    if cloned:
        raise ValueError(_NOT_YET + cloned)
    try:
        tokens = KfTSQL().tokenize(batch)
    except SqlglotError:
        tokens = []  # the parse below reports it
    batch = tsql_routines.bare_call(batch, tokens)  # WR-224: sp_rename 'a', 'b' with no EXEC
    tsql_routines.refuse_routines(batch, tokens)
    _refuse_json_clauses(batch)
    _refuse_security(batch)
    _refuse_session_options(batch)
    if _schema_authorization(batch):
        raise ValueError(_NOT_YET + "CREATE SCHEMA ... AUTHORIZATION isn't mapped yet: the local engine has no "
                         "users to own a schema. Use CREATE SCHEMA name, or run it in a Fabric Warehouse.")
    _refuse_loading(batch)
    _refuse_dbcc(batch)
    _refuse_output(batch)
    _refuse_control(batch)
    unsupported: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if not record.getMessage().startswith("Invalid JSON path syntax"):  # the path stays text; _json reads it
                unsupported.append(record.getMessage())

    handler = Catch(logging.WARNING)
    log = logging.getLogger("sqlglot")
    log.addHandler(handler)
    try:
        trees = [t for t in sql_dialects.parse(batch, read=KfTSQL) if t is not None]
    except SqlglotError as exc:
        raise ValueError(f"Could not read the query as T-SQL: {_first_line(exc)}") from None
    finally:
        log.removeHandler(handler)
    if unsupported:
        m = re.match(r"'(\S+)", unsupported[0])
        word = (m[1] if m else batch.strip().split(None, 1)[0]).upper()
        raise ValueError(f"The local engine doesn't run T-SQL {word} yet. {_LATER}")
    if when is not None:
        reads = (exp.Select, exp.Union, exp.Intersect, exp.Except)
        if len(trees) != 1:
            raise ValueError(_NOT_YET + "FOR TIMESTAMP AS OF is read here only in a batch that holds just that "
                             "SELECT, and this batch has more statements. Put GO before and after the SELECT.")
        if not isinstance(trees[0], reads) or trees[0].find(exp.Into) is not None:
            raise ValueError(_NOT_YET + "FOR TIMESTAMP AS OF on INSERT ... SELECT, SELECT ... INTO or CREATE TABLE "
                             "AS isn't mapped yet. Here it applies only to a plain SELECT. Run the SELECT with it, or "
                             "run it in a Fabric Warehouse.")
        trees[0].meta[_AS_OF_META] = when
    return cast(list[exp.Expression], trees)  # sqlglot types parse() as its Expr base


def _as_of(tree: exp.Expression, now: dt.datetime | None = None) -> list[str]:
    """Each table the SELECT reads, read as of its FOR TIMESTAMP AS OF time from the table's Delta history; a
    table with no history (a database table or a file) is refused by delta.rewrite_as_of's caller, never read as it
    is now. A #temp table and a CTE are read as they are, as in a warehouse. A time in the future, or further back
    than any warehouse keeps, is refused, so neither is answered from the current data."""
    when = tree.meta.get(_AS_OF_META)
    if when is None:
        return []
    at = dt.datetime.fromisoformat(when).replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if at > now:
        raise ValueError(f"FOR TIMESTAMP AS OF '{when}' is later than now (UTC). A time travel read is answered "
                         "only from a table's history, so a later time is refused rather than read as the tables "
                         "are now.")
    if now - at > dt.timedelta(days=AS_OF_MOST_DAYS):
        raise ValueError(f"FOR TIMESTAMP AS OF '{when}' is more than {AS_OF_MOST_DAYS} days ago, further back "
                         f"than a warehouse keeps history ({AS_OF_DEFAULT_DAYS} days by default, at most "
                         f"{AS_OF_MOST_DAYS}). Pick a later time.")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.name.startswith("#") \
                or (not table.db and table.name.lower() in ctes):
            continue
        table.set("when", exp.HistoricalData(this="AT", kind="TIMESTAMP",
                                             expression=exp.Literal.string(at.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"))))
    if now - at > dt.timedelta(days=AS_OF_DEFAULT_DAYS):
        return [(f"FOR TIMESTAMP AS OF '{when}' is more than {AS_OF_DEFAULT_DAYS} days ago. A warehouse keeps "
                 f"{AS_OF_DEFAULT_DAYS} days of history unless its retention was raised (to at most "
                 f"{AS_OF_MOST_DAYS}), and doesn't answer for a time outside it. Here it's read from each table's "
                 "Delta history.")]
    return []


def _harmless_setting(tree: exp.Expression) -> bool:
    """``SET NOCOUNT ON`` and other session options that change nothing in a result."""
    if not isinstance(tree, exp.Set):
        return False
    items = tree.expressions
    names = [i.this.this.name.upper() if isinstance(i.this, exp.EQ) and isinstance(i.this.this, exp.Column) else ""
             for i in items]
    if names == ["DATEFIRST"]:
        value = items[0].this.expression
        if isinstance(value, exp.Literal) and value.name == "7":
            return True  # Fabric's default, which the translation already follows
        raise ValueError("The local engine counts weeks from Sunday (SET DATEFIRST 7, Fabric's default), and doesn't "
                         "run other DATEFIRST settings yet. Remove the SET DATEFIRST.")
    if not items or not all(n in _SETTINGS for n in names):
        return False  # _refuse names it
    for item, name in zip(items, names, strict=True):
        value = item.this.expression
        written = (value.name if isinstance(value, (exp.Var, exp.Literal, exp.National, exp.Column, exp.Identifier))
                   else value.sql(dialect="tsql")).upper()
        if written not in _SETTINGS[name]:
            runs_as = " or ".join(sorted(_SETTINGS[name]))
            raise ValueError(_NOT_YET + f"SET {name} {written} isn't mapped yet: the local engine runs as SET {name} "
                             f"{runs_as}, a Fabric Warehouse's default. Remove the SET, or run it in a Fabric "
                             "Warehouse.")
    return True


def _refuse(tree: exp.Expression) -> None:
    for kind, what in _REFUSED.items():
        if isinstance(tree, kind):
            raise ValueError(f"The local engine doesn't run T-SQL {what} yet. {_LATER}")
    if isinstance(tree, exp.Command):
        word = (tree.name or "").upper()
        raise ValueError(f"The local engine doesn't run T-SQL {word or 'this statement'} yet. {_LATER}")
    if isinstance(tree, exp.Set):
        raise ValueError(f"The local engine doesn't run T-SQL SET yet. {_LATER}")
    if isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() in ("PROCEDURE", "PROC", "FUNCTION",
                                                                                   "TRIGGER"):
        raise ValueError(f"T-SQL CREATE {tree.args['kind'].upper()} doesn't run on the local engine. "
                         "Procedures and functions run in a Fabric Warehouse.")
    for collate in list(tree.find_all(exp.Collate)):
        name = collate.expression.sql(dialect="tsql").strip("[]\"'")
        if not re.search(r"_BIN2?(_|$)", name, re.IGNORECASE):
            raise ValueError(_NOT_YET + f"COLLATE {name} isn't mapped yet: text here compares as a Fabric Warehouse's "
                             "default collation (Latin1_General_100_BIN2_UTF8) does, by code point and case-sensitive. "
                             "Use UPPER or LOWER on both sides to compare without case, or run it in a Fabric "
                             "Warehouse.")
        collate.replace(collate.this)  # a binary collation is how text compares here already (WR-210)
    for p in tree.find_all(exp.Parameter, exp.SessionParameter):
        name = p.sql(dialect="tsql")
        raise ValueError(f"The local engine doesn't run T-SQL variables ({name}) yet. {_LATER}")


_SYSTEM_SCHEMAS = {"sys"}
_INFORMATION_NOTE = ("INFORMATION_SCHEMA lists this project's local database as DuckDB describes it: TABLE_CATALOG "
                     "names the local database, not the warehouse, and some columns a warehouse has aren't there.")


def _names(tree: exp.Expression, columns: dict[str, dict[str, dict[str, str]]] | None) -> list[str]:
    """Names as a Fabric Warehouse resolves them, or refused by name, so a query never reads the wrong table.

    - ``warehouse.dbo.t`` (another warehouse or a lakehouse's SQL analytics endpoint) is refused: a project on
      this PC is one database. ``server.db.dbo.t`` is refused too; a warehouse has no linked servers.
    - ``sys.objects`` and the other catalog views are refused; INFORMATION_SCHEMA runs, with a note.
    - A name without a schema means dbo in T-SQL. DuckDB also looked in main, so ``SELECT * FROM m`` read main.m
      where a warehouse says "Invalid object name 'm'"; it is refused when dbo has no such table and another
      schema has. (A table the script makes before it reads it isn't known yet, and is left to DuckDB.)"""
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    made = tree.this.this if isinstance(tree, exp.Create) and isinstance(tree.this, exp.Schema) else \
        tree.this if isinstance(tree, exp.Create) else None
    known = {s.lower(): {t.lower() for t in tables} for s, tables in (columns or {}).items()}
    notes: list[str] = []
    listed: list[exp.Table] = []
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Dot):
            parts = table.this
            last = parts.expression.name if isinstance(parts.expression, exp.Identifier) else table.name
            schema_part = parts.this.name if isinstance(parts.this, exp.Identifier) else "dbo"
            raise ValueError(_NOT_YET + f"four-part names ({table.sql(dialect='tsql')}) aren't supported in a Fabric "
                             f"Warehouse either, because it has no linked servers. Write {schema_part}.{last} for this "
                             "project's table.")
        if not isinstance(table.this, exp.Identifier):
            continue  # a table function
        name, schema = table.name, table.db
        if table.catalog:
            raise ValueError(_NOT_YET + f"three-part names ({table.catalog}.{schema}.{name}) aren't mapped yet: on "
                             "this PC a project is one database, so another warehouse or a SQL analytics endpoint "
                             f"can't be named. Write {schema or 'dbo'}.{name} for this project's table, or run it in a "
                             "Fabric Warehouse.")
        if schema.lower() in _SYSTEM_SCHEMAS:
            raise ValueError(_NOT_YET + f"catalog views ({schema}.{name}) aren't mapped yet. "
                             "INFORMATION_SCHEMA.TABLES and INFORMATION_SCHEMA.COLUMNS list the project's tables and "
                             "columns.")
        if schema.lower() == "information_schema":
            tsql_routines.information_schema(name)
            notes.append(_INFORMATION_NOTE)
            listed.append(table)
            continue
        if schema or table is made or name.startswith("#") or name.lower() in ctes or not known \
                or _names_its_alias(tree, table):
            continue
        if name.lower() in known.get("dbo", set()):
            continue
        elsewhere = sorted(s for s, tables in known.items() if name.lower() in tables and s != "information_schema")
        if elsewhere:
            raise ValueError(f"Invalid object name '{name}': a name without a schema is looked for in dbo, as in "
                             f"T-SQL, and there's no dbo.{name}. Write {elsewhere[0]}.{name} for the one in "
                             f"{elsewhere[0]}.")
    for table in listed:
        tsql_routines.project_rows(table)
    return notes


def _names_its_alias(tree: exp.Expression, table: exp.Table) -> bool:
    """True for T-SQL's ``UPDATE x ... FROM dbo.t AS x`` and ``DELETE x FROM dbo.t AS x``: the x after UPDATE or
    DELETE names an alias in the statement's own FROM, not a table. Only that target, with no alias of its own."""
    if table.alias or table.db:
        return False
    if isinstance(tree, exp.Update) and table is tree.this:
        source = tree.args.get("from_")
        first = source.this if isinstance(source, exp.From) else None
    elif isinstance(tree, exp.Delete) and any(table is t for t in tree.args.get("tables") or []):
        first = tree.this
    else:
        return False
    if not isinstance(first, exp.Table):
        return False
    aliases = {t.alias.lower() for t in [first, *(j.this for j in first.args.get("joins") or [])]
               if isinstance(t, exp.Table) and t.alias}
    return table.name.lower() in aliases


def _views(tree: exp.Expression, columns: dict[str, dict[str, dict[str, str]]] | None) -> exp.Expression:
    """``ALTER VIEW v AS ...`` as ``CREATE OR REPLACE VIEW`` (DuckDB has no ALTER VIEW ... AS), once v is known to
    be there. In a view's query a name without a schema is written as dbo.name, as T-SQL binds it when the view is
    made: DuckDB binds a view's names each time it's read, so it could later read another schema's table."""
    if isinstance(tree, exp.Alter) and (tree.args.get("kind") or "").upper() == "VIEW":
        actions = tree.args.get("actions") or []
        if len(actions) != 1 or not isinstance(actions[0], exp.Query):
            raise ValueError(_NOT_YET + "this ALTER VIEW isn't mapped yet. Use ALTER VIEW name AS SELECT ...")
        target = tree.this
        known = {s.lower(): {t.lower() for t in tables} for s, tables in (columns or {}).items()}
        if columns is not None and target.name.lower() not in known.get((target.db or "dbo").lower(), set()):
            raise ValueError(f"Invalid object name '{target.db or 'dbo'}.{target.name}': ALTER VIEW changes a view "
                             "that's there. Use CREATE VIEW, or CREATE OR ALTER VIEW.")
        tree = exp.Create(this=target, kind="VIEW", replace=True, expression=actions[0])
    if not (isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() == "VIEW"):
        return tree
    props = tree.args.get("properties")
    for prop in (props.expressions if isinstance(props, exp.Properties) else []):
        if isinstance(prop, exp.ViewAttributeProperty):
            raise ValueError(_NOT_YET + f"CREATE VIEW ... WITH {prop.name.upper()} isn't mapped yet. Remove it, or "
                             "run it in a Fabric Warehouse.")
    query = tree.expression
    if isinstance(query, exp.Expression):
        ctes = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
        for table in query.find_all(exp.Table):
            if isinstance(table.this, exp.Identifier) and not table.db and not table.name.startswith("#") \
                    and table.name.lower() not in ctes:
                table.set("db", exp.to_identifier("dbo"))
    return tree


def _temp_tables(tree: exp.Expression) -> None:
    """``#t`` reads and makes the temporary table ``"#t"``, not an ordinary table ``t``."""
    for ident in tree.find_all(exp.Identifier):
        if ident.args.get("temporary"):
            ident.set("this", "#" + ident.this)
            ident.set("quoted", True)
            ident.set("temporary", None)
    if isinstance(tree, exp.Create):
        name = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        if isinstance(name, exp.Table) and name.name.startswith("#"):
            props = tree.args.get("properties") or exp.Properties(expressions=[])
            if not any(isinstance(p, exp.TemporaryProperty) for p in props.expressions):
                props.append("expressions", exp.TemporaryProperty())
                tree.set("properties", props)


_SIZED_COLUMNS = {exp.DataType.Type.VARCHAR: "strlen", exp.DataType.Type.CHAR: "strlen",
                  exp.DataType.Type.VARBINARY: "octet_length", exp.DataType.Type.BINARY: "octet_length"}


_TEXT_COLUMNS = (exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR)


def _sized(column: exp.ColumnDef) -> int | None:
    """n for a VARCHAR(n), CHAR(n), VARBINARY(n) or BINARY(n) column; None for any other (VARCHAR(MAX) too)."""
    kind = column.args.get("kind")
    if not (isinstance(kind, exp.DataType) and kind.this in _SIZED_COLUMNS and kind.expressions):
        return None
    size = kind.expressions[0].this
    return int(size.name) if isinstance(size, exp.Literal) and size.is_int else None


def _table_columns(tree: exp.Expression) -> None:
    """``CREATE TABLE`` as a Fabric Warehouse defines it.

    - ``VARCHAR(n)``, ``CHAR(n)``, ``VARBINARY(n)`` and ``BINARY(n)`` hold at most n bytes (a warehouse's text is
      UTF-8). DuckDB ignores the length, so a longer value was kept whole; a CHECK now stops it with T-SQL's
      "String or binary data would be truncated", on INSERT and UPDATE. Trailing blanks past the length pass, as
      in T-SQL (which drops them; locally they're kept). DuckDB can't add a column with a CHECK, so ``ALTER TABLE
      ... ADD`` of a sized column is refused by name.
    - ``IDENTITY`` is refused by name: a warehouse takes only ``BIGINT IDENTITY`` with no seed or increment, and
      the local engine doesn't number rows that way yet.
    - ``##name`` is refused anywhere, since a warehouse has no global temporary tables (sqlglot read it as an
      ordinary table), and ``WITH (DISTRIBUTION = ...)``,
      which a warehouse takes only on a #temp table, is dropped there (it changes how the table is stored, not
      what it holds)."""
    for ident in tree.find_all(exp.Identifier):
        if ident.args.get("global_"):  # sqlglot reads ##g as g: it made and read an ordinary table
            raise ValueError(f"This T-SQL can't be run on the local engine: global temporary tables (##{ident.name}) "
                             "aren't supported in a Fabric Warehouse either. Use a #temp table.")
    if isinstance(tree, exp.Alter) and (tree.args.get("kind") or "").upper() == "TABLE":
        for column in tree.args.get("actions") or []:
            if isinstance(column, exp.ColumnDef) and _sized(column) is not None:
                raise ValueError(_NOT_YET + f"ALTER TABLE ... ADD of a column with a length ({column.name}) isn't "
                                 "mapped yet: the local engine can't hold an added column to its length. Declare "
                                 "the column in CREATE TABLE, or run it in a Fabric Warehouse.")
        return
    if not (isinstance(tree, exp.Create) and (tree.args.get("kind") or "").upper() == "TABLE"):
        return
    name = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
    temporary = isinstance(name, exp.Table) and name.name.startswith("#")
    props = tree.args.get("properties")
    if isinstance(props, exp.Properties):
        for prop in list(props.expressions):
            if isinstance(prop, exp.Property) and prop.name.upper() == "DISTRIBUTION":
                if not temporary:
                    raise ValueError("This T-SQL can't be run on the local engine: WITH (DISTRIBUTION = ...) is only "
                                     "for #temp tables in a Fabric Warehouse. Remove it, or make the table a #temp table.")
                prop.pop()
    if not isinstance(tree.this, exp.Schema):
        return
    for column in tree.this.expressions:
        if not isinstance(column, exp.ColumnDef):
            continue
        constraints = column.args.get("constraints") or []
        identity = [c for c in constraints if isinstance(c.args.get("kind"), (exp.AutoIncrementColumnConstraint,
                                                                              exp.GeneratedAsIdentityColumnConstraint))]
        if identity:
            raise ValueError(_NOT_YET + f"IDENTITY isn't mapped yet (column {column.name}). A Fabric Warehouse takes "
                             "only BIGINT IDENTITY, with no seed or increment; until the local engine numbers rows "
                             "that way, give the column its values in the INSERT.")
        size = _sized(column)
        if size is None:
            continue
        kind = column.args["kind"]
        table = f"{name.db}.{name.name}" if isinstance(name, exp.Table) and name.db else \
            (name.name if isinstance(name, exp.Table) else "")
        value: exp.Expr = exp.column(column.name, quoted=column.this.quoted)
        if kind.this in _TEXT_COLUMNS:  # trailing blanks past the length are dropped, not an error, in T-SQL
            value = _rtrim(value)
        too_long = exp.GT(this=exp.func(_SIZED_COLUMNS[kind.this], value), expression=exp.Literal.number(size))
        message = exp.Literal.string(f"String or binary data would be truncated in table '{table}', column "
                                     f"'{column.name}'.")
        check = exp.Case(ifs=[exp.If(this=too_long, true=exp.func("ERROR", message))], default=exp.true())
        column.append("constraints", exp.ColumnConstraint(kind=exp.CheckColumnConstraint(this=check)))
        if kind.this in (exp.DataType.Type.VARBINARY, exp.DataType.Type.BINARY):
            kind.set("expressions", [])  # DuckDB's BLOB takes no length: VARBINARY(4) stopped CREATE TABLE


def _schema(columns: dict[str, dict[str, dict[str, str]]]) -> Any:
    from sqlglot.schema import MappingSchema
    clean: dict[str, dict[str, dict[str, str]]] = {}
    for schema, tables in columns.items():
        for table, cols in tables.items():
            kept = {}
            for col, kind in cols.items():
                try:
                    exp.DataType.build(kind, dialect="duckdb")
                except SqlglotError:
                    continue  # a nested type sqlglot can't read; that column stays untyped
                kept[col] = kind
            clean.setdefault(schema, {})[table] = kept
    return MappingSchema(cast(dict[str, object], clean), dialect="duckdb", normalize=True)


def _types(tree: exp.Expression, schema: Any) -> list[str]:
    """``_typed`` for a query, and for an UPDATE's SET values and an UPDATE's or DELETE's FROM and WHERE: sqlglot
    types no column of an UPDATE or a DELETE, so ``SET v = v + 'x'`` added text as numbers (a DuckDB binder error)
    and a WHERE compared text with its trailing spaces. They are typed as a SELECT that lends them its scope."""
    lent = _change_as_select(tree)
    if lent is None:
        return _typed(tree, schema)
    proxy, give_back = lent
    try:
        return _typed(proxy, schema)
    finally:
        give_back()


def _change_as_select(tree: exp.Expression) -> tuple[exp.Select, Callable[[], None]] | None:
    """A SELECT of an UPDATE's SET values and WHERE (or a DELETE's WHERE), FROM its tables: the nodes themselves,
    lent, so what ``_typed`` rewrites in them is rewritten in the statement, and a function that gives them back."""
    if isinstance(tree, exp.Update):
        sets = list(tree.expressions)
        if not sets or not all(isinstance(e, exp.EQ) for e in sets):
            return None
        source = tree.args.get("from_")
    elif isinstance(tree, exp.Delete):
        sets, source = [], None
        if tree.args.get("using") not in (None, False) or tree.args.get("tables") and len(tree.args["tables"]) > 1:
            return None
    else:
        return None
    where = tree.args.get("where")
    if not sets and where is None:
        return None
    target = tree.this
    if not isinstance(target, exp.Table):
        return None
    values = [e.expression for e in sets] + ([where.this] if where is not None else [])
    proxy = exp.Select(expressions=values)
    if source is None:
        proxy.set("from_", exp.From(this=target.copy()))
    else:
        proxy.set("from_", source)
        named = {t.alias_or_name.lower() for t in source.find_all(exp.Table)}
        if target.alias_or_name.lower() not in named:  # UPDATE dbo.t SET ... FROM dbo.s: t is read too
            proxy.set("joins", [exp.Join(this=target.copy())])

    def give_back() -> None:
        lent = list(proxy.expressions)
        for eq, value in zip(sets, lent, strict=False):
            eq.set("expression", value)
        if where is not None:
            where.set("this", lent[-1])
        if source is not None:
            tree.set("from_", proxy.args.get("from_"))

    return proxy, give_back


def _typed(tree: exp.Expression, schema: Any) -> list[str]:
    """Make integer division and averages, ``+`` on text, and casts to integers behave as in T-SQL. The types come from
    a qualified copy of the tree (qualifying renames output columns, so the copy isn't run)."""
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify

    kinds = (exp.Div, exp.Avg, exp.Add, exp.Sub, exp.Mul, exp.Cast, exp.TryCast, exp.DateAdd, exp.TimestampTrunc, exp.Pow,
             exp.Ceil, exp.Floor, exp.Degrees, exp.Radians,
             tsql_format.TsqlFormat, exp.Concat, exp.ConcatWs, exp.Mod, exp.Round, exp.Coalesce, exp.WithinGroup)
    here = _in_order(tree, kinds)
    compared = _in_order(tree, _COMPARISONS)
    grouped = any(True for _ in tree.find_all(exp.Group, exp.Distinct, exp.SetOperation))
    if not here and not compared and not grouped:
        return []
    sized = [n for n in here if isinstance(n, (exp.Cast, exp.TryCast))]
    typed = tree.copy()
    try:
        typed = qualify(typed, schema=schema, dialect="duckdb", db="dbo", expand_stars=False,
                        validate_qualify_columns=False, quote_identifiers=False, identify=False)
    except SqlglotError:
        pass  # unknown tables: literals still get their types
    try:
        typed = annotate_types(typed, schema=schema, dialect="duckdb")
    except SqlglotError:
        for cast_node in sized:
            _sized_text(cast_node, None)
        return [("Couldn't tell the column types, so divisions, averages, casts to integers, text comparisons and "
                "floats joined into text are done as DuckDB does them (T-SQL divides and averages integers as "
                "integers, cuts a decimal cast to an integer toward zero, ignores trailing spaces when it compares "
                "text, and writes a float in CONCAT with at most 6 significant digits).")]
    notes = _padded(compared, _in_order(typed, _COMPARISONS)) + _grouped_text(typed)
    there = _in_order(typed, kinds)
    if len(there) != len(here):
        for cast_node in sized:
            _sized_text(cast_node, None)
        return notes + [("Couldn't match the query's columns to their types, so divisions, averages, casts to "
                "integers and floats joined into text are done as DuckDB does them (T-SQL divides and averages "
                "integers as integers, cuts a decimal cast to an integer toward zero, and writes a float in CONCAT "
                "with at most 6 significant digits).")]
    pairs = list(zip(here, there, strict=True))
    done: set[int] = set()
    # Decimal arithmetic first, and inner before outer: a rewrite copies its operands, which by then carry theirs.
    for node, info in reversed(pairs):
        if isinstance(node, tuple(_DECIMAL_OPS)) and (sides := _decimal_operands(info)) is not None:
            if _decimal_math(node, *sides) and _FLOAT_NOTE not in notes:
                notes.append(_FLOAT_NOTE)
            done.add(id(node))
        elif isinstance(node, exp.Avg) and (number := _tsql_number(info.this)) is not None and number[2]:
            # T-SQL's AVG of decimal(p, s) is decimal(38, max(s, 6)); DuckDB's is a double.
            whole = node.parent if isinstance(node.parent, exp.Window) and node.parent.this is node else node
            _cast_in_place(whole, 38, max(number[1], 6))
            if _FLOAT_NOTE not in notes:
                notes.append(_FLOAT_NOTE)
            done.add(id(node))
    for node, info in pairs:
        if id(node) in done:
            continue
        if isinstance(node, exp.Sub | exp.Mul):
            pass  # numbers, as DuckDB does them; only decimals need T-SQL's precision and scale
        elif isinstance(node, exp.WithinGroup):
            ordered = info.expression.expressions if isinstance(info.expression, exp.Order) else []
            if ordered:
                node.meta[_ORDER_TYPE] = ordered[0].this.type  # for _percentiles
        elif isinstance(node, exp.Div):
            a, b = info.this.type, info.expression.type
            if _is(a, _INTEGERS) and _is(b, _INTEGERS):
                _by_zero(node, "//")
            elif _is(a, _NUMBERS) and _is(b, _NUMBERS):
                _by_zero(node, "/")  # a float, or a number with one: an error too, not inf or NULL
            elif _unknown(a) or _unknown(b):
                notes.append(f"Couldn't tell the types in {node.sql(dialect='tsql')}: it divides as DuckDB does, "
                             "and T-SQL divides two integers as integers.")
        elif isinstance(node, exp.Avg):
            if _is(info.this.type, _INTEGERS):
                # AVG(x) OVER (...) is a Window around the AVG: the whole window is what gets cut.
                whole = node.parent if isinstance(node.parent, exp.Window) and node.parent.this is node else node
                whole.replace(exp.Cast(this=exp.Trunc(this=whole.copy()), to=exp.DataType.build("BIGINT")))
        elif isinstance(node, exp.Mod):
            if _is(info.this.type, _NUMBERS) and _is(info.expression.type, _NUMBERS):
                _by_zero(node, "%")
        elif isinstance(node, exp.Round):
            _round_keeps_type(node, info)
        elif isinstance(node, exp.Coalesce):
            if node.args.get("is_null"):
                _isnull_first_type(node, info)
        elif isinstance(node, exp.Pow):
            notes += _power(node, info.this.type)
        elif isinstance(node, (exp.Ceil, exp.Floor, exp.Degrees, exp.Radians)):
            notes += _same_type(node, info.this.type)
        elif isinstance(node, tsql_format.TsqlFormat):
            tsql_format.rewrite(node, info.this.type)
        elif isinstance(node, (exp.Concat, exp.ConcatWs)):
            notes += _floats_joined(node, info)
        elif isinstance(node, (exp.DateAdd, exp.TimestampTrunc)):
            if _is(info.this.type, {exp.DataType.Type.DATE}):
                _stays_a_date(node)
        elif isinstance(node, exp.Add):
            a, b = info.this.type, info.expression.type
            if _is(a, _TEXT) and _is(b, _TEXT):
                node.replace(exp.DPipe(this=node.this, expression=node.expression, safe=False))
            elif _is(a, _TEXT) and _is(b, _NUMBERS):
                # A number outranks text in T-SQL: '1' + 1 is 2 (and 'x' + 1 is an error).
                assert b is not None
                _text_as_number(node, "this", b)
            elif _is(b, _TEXT) and _is(a, _NUMBERS):
                assert a is not None
                _text_as_number(node, "expression", a)
            elif (_is(a, _TEXT) or _is(b, _TEXT)) and (_unknown(a) or _unknown(b)):
                notes.append(f"Couldn't tell the types in {node.sql(dialect='tsql')}: T-SQL adds a number to text "
                             "as numbers and joins two strings, and here it is done as DuckDB does it.")
        else:
            to = node.args.get("to")
            if isinstance(to, exp.DataType) and to.this in _INTEGERS - {exp.DataType.Type.BIT} \
                    and _unknown(info.this.type) and not isinstance(node.this, exp.Null) \
                    and not node.meta.get("kf_digits"):  # NULL, and PARSE's checked digits, convert the same
                notes.append(f"Couldn't tell the type in {node.sql(dialect='tsql')}: it converts as DuckDB does, "
                             "and T-SQL cuts a decimal toward zero and refuses text that isn't a whole number.")
            source = _numeric_style(node, info) or info.this.type
            if (unchecked := node.meta.pop("tsql_unchecked_style", None)) is not None:
                notes.append(_unchecked_style(unchecked))
            _truncated(node, info.this.type)
            _sized_text(node, source)  # a no-op for the integer casts _truncated may replace
    return notes


_BY_ZERO = "Divide by zero error encountered."


def _by_zero(node: exp.Expression, op: str) -> None:
    """``a / b`` or ``a % b`` stops with T-SQL's error when ``b`` is 0, for integers and floats too (WR-211);
    DuckDB gives NULL, or infinity for a float. ``//`` divides integers as integers. A divisor written as a
    number other than 0 can't be 0, and is left as it is."""
    value = _number(node.expression)
    if value is not None and value != 0:
        if op == "//":
            node.replace(exp.IntDiv(this=node.this, expression=node.expression))
        return
    # NULL / 0 is NULL in T-SQL: the error is only for a value divided by zero.
    _fill_in(node, f"CASE WHEN (__b) = 0 AND (__a) IS NOT NULL THEN error('{_BY_ZERO}') ELSE (__a) {op} (__b) END")


_OVERFLOW = "Arithmetic overflow error converting expression to data type {kind}."
_TSQL_INT_NAMES = {exp.DataType.Type.UTINYINT: "tinyint", exp.DataType.Type.TINYINT: "tinyint",
                   exp.DataType.Type.SMALLINT: "smallint", exp.DataType.Type.INT: "int", exp.DataType.Type.BIGINT: "bigint"}


def _round_keeps_type(node: exp.Round, info: exp.Expression) -> None:
    """``ROUND`` keeps its value's type in T-SQL (WR-211): ``ROUND(1234.567, -2)`` is 1200.000, and a result
    that no longer fits the type is an error (``ROUND(999.5, 0)``, a decimal(4,1), would be 1000.0). DuckDB
    gives a decimal of another scale, or a BIGINT for an integer. A float stays as DuckDB rounds it."""
    kind = info.this.type
    number = _tsql_number(info.this)
    if number is not None and number[2]:
        precision, scale = number[0], number[1]
        to, name = f"DECIMAL({precision}, {scale})", "numeric"
        # Rounded at full width: DuckDB rounds a narrow decimal past its digits to 0 (748.58 at -3).
        node.set("this", exp.Cast(this=node.this, to=exp.DataType.build(f"DECIMAL(38, {scale})")))
    elif _is(kind, set(_TSQL_INT_NAMES)) and not _written_decimal(node.this):
        assert kind is not None
        to, name = ("UTINYINT" if kind.this == exp.DataType.Type.TINYINT else kind.this.value), _TSQL_INT_NAMES[kind.this]
        # Rounded in a wider type: DuckDB's ROUND on an integer wraps past its type (ROUND(2147483647, -1)).
        node.set("this", exp.Cast(this=node.this, to=exp.DataType.build("HUGEINT")))
    else:
        return
    message = _OVERFLOW.format(kind=name)
    _fill_in_one(node, f"CASE WHEN (__x) IS NOT NULL AND TRY_CAST((__x) AS {to}) IS NULL "
                       f"THEN CAST(error('{message}') AS {to}) ELSE CAST((__x) AS {to}) END")


def _fill_in_one(node: exp.Expression, text: str) -> None:
    """``node`` replaced by the DuckDB SQL ``text``, with ``__x`` standing for the node itself (kept, so later
    rewrites still find it)."""
    filled = sqlglot.parse_one(text, read="duckdb")
    holders = [c for c in filled.find_all(exp.Column) if c.name == "__x"]
    node.replace(filled)
    for placeholder in holders:
        placeholder.replace(node.copy())


def _isnull_first_type(node: exp.Coalesce, info: exp.Expression) -> None:
    """``ISNULL(a, b)`` has ``a``'s type in T-SQL, so ``b`` is converted to it: ``ISNULL(CAST(NULL AS
    VARCHAR(2)), 'abcd')`` is 'ab' and ``ISNULL(int_column, 2.7)`` is 2. ``COALESCE`` takes the type that
    ranks highest instead, as DuckDB does (WR-211). An ``a`` whose type can't be told is left as it is."""
    first = node.this
    kind = first.args["to"] if isinstance(first, (exp.Cast, exp.TryCast)) else info.this.type
    if len(node.expressions) != 1 or not isinstance(kind, exp.DataType) or _unknown(kind):
        return
    other, other_type = node.expressions[0], info.expressions[0].type
    if kind.this in _SIZED and kind.expressions:
        size = kind.expressions[0].this
        if isinstance(size, exp.Literal) and not size.is_string:
            node.set("expressions", [exp.func("LEFT", exp.cast(other, "VARCHAR"), exp.Literal.number(int(size.name)))])
    elif kind.this in _INTEGERS - {exp.DataType.Type.BIT, exp.DataType.Type.BOOLEAN} and not _is(other_type, _INTEGERS):
        cut = exp.Cast(this=other, to=kind.copy())
        node.set("expressions", [cut])
        _truncated(cut, other_type)  # toward zero, as a CAST to an integer; text has to be a whole number
    elif kind.this == exp.DataType.Type.DECIMAL and kind.expressions:
        node.set("expressions", [exp.Cast(this=other, to=kind.copy())])


# T-SQL converts an integer to a decimal of this precision when it meets one in arithmetic.
_INTEGER_DIGITS = {exp.DataType.Type.TINYINT: 3, exp.DataType.Type.UTINYINT: 3, exp.DataType.Type.SMALLINT: 5,
                   exp.DataType.Type.INT: 10, exp.DataType.Type.BIGINT: 19}
_FLOAT_NOTE = ("A decimal AVG, or decimal arithmetic on a value too wide to work out exactly on the local engine, is "
               "done in floating point and then given T-SQL's precision and scale. About the first 15 significant "
               "digits match the warehouse; later digits can differ.")
_DECIMAL_OPS: dict[type[exp.Expression], str] = {exp.Add: "+", exp.Sub: "-", exp.Mul: "*", exp.Div: "/"}
_HUGEINT_ROOM = 38  # a HUGEINT holds every whole number of up to 38 digits


def _decimal_ps(kind: exp.DataType | None) -> tuple[int, int] | None:
    """A decimal's (precision, scale), or an integer's as T-SQL widens it to a decimal; None for anything else."""
    if kind is None:
        return None
    if kind.this in _INTEGER_DIGITS:
        return _INTEGER_DIGITS[kind.this], 0
    if kind.this != exp.DataType.Type.DECIMAL:
        return None
    params = [int(p.name) for p in kind.expressions if p.name.isdigit()]
    if not params:
        return 18, 3  # DuckDB's DECIMAL with no size
    return params[0], params[1] if len(params) > 1 else 0


def tsql_decimal(op: str, p1: int, s1: int, p2: int, s2: int) -> tuple[int, int]:
    """The precision and scale T-SQL gives ``+``, ``-``, ``*`` or ``/`` of decimal(p1, s1) and decimal(p2, s2),
    with its rules for a result wider than 38 digits (Microsoft Learn, "Precision, scale, and length")."""
    if op in "+-":
        integral = max(p1 - s1, p2 - s2)
        scale = max(s1, s2)
        precision = scale + integral + 1
        if precision > 38:
            precision, scale = 38, max(min(scale, 38 - integral), 0)
        return precision, scale
    if op == "*":
        precision, scale = p1 + p2 + 1, s1 + s2
    else:
        scale = max(6, s1 + p2 + 1)
        precision = p1 - s1 + s2 + scale
    if precision > 38:
        integral = precision - scale
        if integral < 32:
            scale = min(scale, 38 - integral)
        elif scale > 6:
            scale = 6
        precision = 38
    return precision, scale


def _tsql_number(node: exp.Expression) -> tuple[int, int, bool] | None:
    """``(precision, scale, is_decimal)`` of a number as T-SQL types it, from the typed copy of the tree; None when
    it isn't a decimal or an integer. sqlglot types ``1.5`` as a float, ``a / b`` of decimals as DuckDB does, and
    ``SUM`` or ``AVG`` of a decimal as its argument, so those are worked out here by T-SQL's rules."""
    if isinstance(node, (exp.Paren, exp.Neg)):
        return _tsql_number(node.this)
    if isinstance(node, exp.Literal) and node.is_number:
        text = node.name.lstrip("-+")
        if "e" in text.lower():
            return None  # 2e0 is a float
        if "." not in text:
            # A whole number meets a decimal as decimal(its digits, 0), so 1.0 / 3 is 0.333333; it is an int,
            # or a decimal when it is too big for an int.
            return max(len(text.lstrip("0")), 1), 0, int(text) >= 2 ** 31
        whole, fraction = text.split(".", 1)
        return max(len(whole.lstrip("0")) + len(fraction), 1), len(fraction), True
    op = _DECIMAL_OPS.get(type(node))
    if op is not None:
        a, b = _tsql_number(node.this), _tsql_number(node.expression)
        if a is not None and b is not None and (a[2] or b[2]):
            return (*tsql_decimal(op, a[0], a[1], b[0], b[1]), True)
    if isinstance(node, exp.Window) and isinstance(node.this, (exp.Sum, exp.Avg)):
        return _tsql_number(node.this)
    if isinstance(node, (exp.Sum, exp.Avg)):
        inner = _tsql_number(node.this)
        if inner is not None and inner[2]:
            return 38, inner[1] if isinstance(node, exp.Sum) else max(inner[1], 6), True
    kind = node.type
    ps = _decimal_ps(kind)
    if kind is None or ps is None:
        return None
    return ps[0], ps[1], kind.this == exp.DataType.Type.DECIMAL


def _decimal_operands(info: exp.Expression) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Both sides' (precision, scale) when ``info`` is decimal arithmetic: a decimal on one side, and a decimal or
    an integer on the other."""
    a, b = _tsql_number(info.this), _tsql_number(info.expression)
    if a is None or b is None or not (a[2] or b[2]):
        return None
    return a[:2], b[:2]


def _volatile(node: exp.Expression) -> bool:
    """Whether reading ``node`` twice could give two answers (RAND, NEWID)."""
    return any(isinstance(n, (exp.Rand, exp.Uuid)) or (isinstance(n, exp.Anonymous) and n.name.upper() in
                                                         ("NEWID", "RAND")) for n in node.walk())


def _decimal_math(node: exp.Expression, left: tuple[int, int], right: tuple[int, int]) -> bool:
    """``+``, ``-``, ``*`` or ``/`` with a decimal side given T-SQL's precision and scale and T-SQL's value (WR-206).
    DuckDB widens decimals by its own rules, keeps every digit of scale where T-SQL cuts it, and divides decimals
    as doubles. Whether a value can end up worked out in floating point (the caller notes it)."""
    op = _DECIMAL_OPS[type(node)]
    precision, scale = tsql_decimal(op, *left, *right)
    if op == "/":
        return _division(node, left, right, precision, scale)
    kept = left[1] + right[1] if op == "*" else max(left[1], right[1])
    kind = f"DECIMAL({precision}, {scale})"
    if kept > _HUGEINT_ROOM:
        # The sides' scales add up past 38: DuckDB can't type the product at all (decimal(38,20) * decimal(38,20)),
        # where T-SQL cuts the scale to 6. Only floating point is left.
        _fill_in(node, f"CAST(CAST((__a) AS DOUBLE) {op} CAST((__b) AS DOUBLE) AS {kind})")
        return True
    if kept <= scale or _volatile(node):
        _cast_in_place(node, precision, scale)
        return False
    # T-SQL cut the scale to make room for whole digits. DuckDB keeps the scale, so a big value overflows there and
    # not in a warehouse: such a value is worked out in floating point instead, and the rest exactly. The check is
    # in floating point too, a digit short of DuckDB's room, so rounding in it can't let an overflow through.
    size = f"ABS(CAST((__a) AS DOUBLE)) {'*' if op == '*' else '+'} ABS(CAST((__b) AS DOUBLE))"
    text = (f"CASE WHEN {size} < 1e{_HUGEINT_ROOM - 1 - kept} THEN CAST((__a) {op} (__b) AS {kind}) "
            f"ELSE CAST(CAST((__a) AS DOUBLE) {op} CAST((__b) AS DOUBLE) AS {kind}) END")
    _fill_in(node, text)
    return True


def _fill_in(node: exp.Expression, text: str) -> None:
    """``node`` (a op b) replaced by the DuckDB SQL ``text``, with ``__a`` and ``__b`` standing for its sides."""
    filled = sqlglot.parse_one(text, read="duckdb")
    for placeholder in list(filled.find_all(exp.Column)):
        if placeholder.name in ("__a", "__b"):
            placeholder.replace((node.this if placeholder.name == "__a" else node.expression).copy())
    node.replace(filled)


def _unscaled(side: str, scale: int) -> str:
    """A decimal's digits as a whole number (2.50 at scale 2 is 250), in HUGEINT: the value at exactly ``scale``
    places, written out with its point taken away. Arithmetic would have to pass a wide value through a decimal
    that can't hold it."""
    if scale == 0:
        return f"CAST({side} AS HUGEINT)"
    return f"CAST(REPLACE(CAST(CAST({side} AS DECIMAL(38, {scale})) AS VARCHAR), '.', '') AS HUGEINT)"


def _division(node: exp.Expression, left: tuple[int, int], right: tuple[int, int], precision: int,
              scale: int) -> bool:
    """``a / b`` with a decimal side as T-SQL divides: exactly, cut toward zero at the result's scale, and stopping
    on a zero divisor ("Divide by zero error encountered"). Both sides become whole numbers (their digits, in
    HUGEINT), the dividend moved left so the quotient lands at the result's scale. A value whose digits would not
    fit in 38 is divided in floating point instead. Whether that can happen (the caller notes it)."""
    (p1, s1), (p2, s2) = left, right
    shift = scale - s1 + s2  # places the dividend moves left (or, below zero, the divisor)
    if _volatile(node) or abs(shift) > _HUGEINT_ROOM:
        _cast_in_place(node, precision, scale)
        return True
    a_shift, b_shift = max(shift, 0), max(-shift, 0)
    a, b = _unscaled("(__a)", s1), _unscaled("(__b)", s2)
    kind = f"DECIMAL({precision}, {scale})"
    quotient = (f"CAST(CAST(CASE WHEN ((__a) < 0) <> ((__b) < 0) THEN -1 ELSE 1 END * (ABS({a}) * "
                f"CAST('{10 ** a_shift}' AS HUGEINT) // (ABS({b}) * CAST('{10 ** b_shift}' AS HUGEINT))) "
                f"AS DECIMAL(38, 0)) * CAST('0.{'0' * (scale - 1)}1' AS DECIMAL({scale}, {scale})) AS {kind})")
    fits = [f"ABS({digits}) < CAST('{10 ** (_HUGEINT_ROOM - moved)}' AS HUGEINT)"
            for digits, p, moved in ((a, p1, a_shift), (b, p2, b_shift)) if p + moved > _HUGEINT_ROOM]
    zero = "WHEN (__b) = 0 AND (__a) IS NOT NULL THEN error('Divide by zero error encountered.')"  # NULL / 0 is NULL
    if fits:
        text = (f"CASE {zero} WHEN {' AND '.join(fits)} THEN {quotient} "
                f"ELSE CAST(CAST((__a) AS DOUBLE) / CAST((__b) AS DOUBLE) AS {kind}) END")
    else:
        text = f"CASE {zero} ELSE {quotient} END"
    _fill_in(node, text)
    return bool(fits)


def _cast_in_place(node: exp.Expression, precision: int, scale: int) -> None:
    """``node`` cast to DECIMAL(precision, scale), keeping the node itself in the tree (later rewrites find it)."""
    cast = exp.Cast(to=exp.DataType.build(f"DECIMAL({precision}, {scale})"))
    node.replace(cast)
    cast.set("this", node)


def _text_as_number(node: exp.Add, side: str, to: exp.DataType) -> None:
    """The text side of ``+`` converted to the number's type, as T-SQL does: ``'' + 1`` is 1 and ``f + ''`` is f,
    since '' converts to 0 for an integer or a float (DuckDB refuses it); text for an integer has to be a whole
    number, as in a CAST. A decimal refuses '' in both."""
    cast = exp.Cast(this=node.args[side], to=to.copy())
    node.set(side, cast)
    if to.this in _INTEGERS - {exp.DataType.Type.BIT}:
        _whole_text(cast, to)
    elif to.this in _FLOATS:
        v = exp.to_identifier("kf_text")
        blank = exp.EQ(this=exp.func("TRIM", v.copy()), expression=exp.Literal.string(""))
        case = exp.Case(ifs=[exp.If(this=blank, true=exp.Cast(this=exp.Literal.number(0), to=to.copy()))],
                        default=exp.Cast(this=v.copy(), to=to.copy()))
        bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[cast.this]), exp.Lambda(this=case, expressions=[v]))
        cast.replace(exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1))


def _floats_joined(node: exp.Expression, info: exp.Expression) -> list[str]:
    """``CONCAT`` and ``CONCAT_WS`` write a float as text in T-SQL's style 0 (``0.333333``), as a CAST to text does;
    DuckDB writes its shortest exact form. An argument whose type can't be told is left as it is, with a note."""
    notes = []
    for arg, typed in zip(list(node.expressions), info.expressions, strict=True):
        if _is(typed.type, _FLOATS) and not _written_decimal(arg):  # 1.50 is a decimal: '1.50' (WR-210)
            arg.replace(_float_text(arg.copy()))
        elif _is(typed.type, _BITS):
            arg.replace(_bit_text(arg.copy()))
        elif _unknown(typed.type) and not isinstance(arg, (exp.Literal, exp.Null)):
            notes.append(f"Couldn't tell the type of {arg.sql(dialect='tsql')} in {node.sql(dialect='tsql')}: a float "
                         "there is written as DuckDB does (0.3333333333333333), and T-SQL writes at most 6 "
                         "significant digits (0.333333).")
    return notes


_BITS = {exp.DataType.Type.BIT, exp.DataType.Type.BOOLEAN}


def _bit_text(value: exp.Expression) -> exp.Expr:
    """A BIT as text is ``1`` or ``0`` in T-SQL; DuckDB writes its boolean as ``true`` (WR-210)."""
    return exp.cast(exp.cast(value, "INT"), "VARCHAR")


def _power(node: exp.Pow, base: exp.DataType | None) -> list[str]:
    """``POWER(x, y)`` has x's type in T-SQL: an integer base gives an integer, cut toward zero
    (``POWER(2, -1)`` is 0), and a result too big for that type is an error. A decimal base keeps the
    decimal's scale, which isn't carried here, so it is refused rather than answered differently."""
    written_decimal = _written_decimal(node.this)
    if _is(base, _INTEGERS - {exp.DataType.Type.BIT}) and not written_decimal:
        assert base is not None
        big = base.this == exp.DataType.Type.BIGINT  # tinyint and smallint give an int, as T-SQL's POWER does
        node.replace(exp.Cast(this=exp.Trunc(this=node.copy()), to=exp.DataType.build("BIGINT" if big else "INT")))
    elif written_decimal or _is(base, {exp.DataType.Type.DECIMAL, exp.DataType.Type.MONEY,
                                        exp.DataType.Type.SMALLMONEY}):
        raise ValueError(_NOT_YET + "POWER of a decimal keeps the decimal's scale in T-SQL, which isn't mapped "
                         "yet. Cast the first argument to FLOAT, or to the integer type you mean.")
    elif _unknown(base):
        return [(f"Couldn't tell the type in {node.sql(dialect='tsql')}: it raises as DuckDB does, in floating "
                "point, and T-SQL gives the first argument's type (an integer base gives an integer).")]
    return []


def _written_decimal(node: exp.Expression) -> bool:
    """``2.0`` is a decimal in T-SQL and ``2e0`` a float; sqlglot types both as a float."""
    first = node.this if isinstance(node, exp.Neg) else node
    return isinstance(first, exp.Literal) and not first.is_string and "." in first.name \
        and "e" not in first.name.lower()


def _same_type(node: exp.Expression, arg: exp.DataType | None) -> list[str]:
    """``CEILING``, ``FLOOR``, ``DEGREES`` and ``RADIANS`` give their argument's type in T-SQL: ``CEILING(5)`` is the
    integer 5 and ``DEGREES(1)`` the integer 57 (cut toward zero), where DuckDB gives a float. ``DEGREES`` and
    ``RADIANS`` of a decimal keep the decimal's scale, which isn't carried here, so they are refused."""
    name = "DEGREES" if isinstance(node, exp.Degrees) else "RADIANS"
    if isinstance(node, (exp.Degrees, exp.Radians)) and (
            _written_decimal(node.this) or _is(arg, {exp.DataType.Type.DECIMAL, exp.DataType.Type.MONEY,
                                                     exp.DataType.Type.SMALLMONEY})):
        raise ValueError(_NOT_YET + f"{name} of a decimal keeps the decimal's scale in T-SQL, which isn't mapped "
                         "yet. Cast the argument to FLOAT.")
    if _is(arg, _INTEGERS - {exp.DataType.Type.BIT}) and not _written_decimal(node.this):
        assert arg is not None
        inner = node.copy() if isinstance(node, (exp.Ceil, exp.Floor)) else exp.Trunc(this=node.copy())
        node.replace(exp.Cast(this=inner, to=arg.copy()))
    elif _unknown(arg):
        return [(f"Couldn't tell the type in {node.sql(dialect='tsql')}: it gives a float, as DuckDB does, and T-SQL "
                "gives the argument's type (an integer gives an integer).")]
    return []


def _truncated(node: exp.Expression, source: exp.DataType | None) -> None:
    """``CAST(3.7 AS INT)`` is 3 in T-SQL, which cuts a decimal or float toward zero on the way to an
    integer type; DuckDB rounds it to 4. TRUNC first, so -3.7 is -3 as well."""
    to = node.args.get("to")
    if not (isinstance(to, exp.DataType) and to.this in _INTEGERS - {exp.DataType.Type.BIT}):
        return
    if _is(source, _NUMBERS - _INTEGERS):
        node.set("this", exp.Trunc(this=node.this))
    elif _is(source, _TEXT):
        _whole_text(node, to)


def _whole_text(node: exp.Expression, to: exp.DataType) -> None:
    """Text to an integer type: T-SQL takes only a whole number, with spaces around it, and '' as 0;
    ``CAST('3.7' AS INT)`` fails (TRY_CAST gives NULL), where DuckDB rounds it to 4. The text is
    bound once, as LIST_TRANSFORM([text], v -> ...)[1], so a source that changes each time it's read
    is read once."""
    v = exp.to_identifier("kf_text")
    whole = exp.func("REGEXP_FULL_MATCH", v.copy(), exp.Literal.string(" *[+-]?[0-9]+ *"))
    blank = exp.EQ(this=exp.func("TRIM", v.copy()), expression=exp.Literal.string(""))
    message = exp.Literal.string(f"Conversion failed when converting the text to data type {to.sql(dialect='tsql')}.")
    other = exp.Null() if isinstance(node, exp.TryCast) else exp.func("ERROR", message)
    cast = node.copy()
    cast.set("this", v.copy())
    case = exp.Case(ifs=[exp.If(this=whole, true=cast),
                         exp.If(this=blank, true=exp.Cast(this=exp.Literal.number(0), to=to.copy())),
                         exp.If(this=exp.Is(this=v.copy(), expression=exp.Null()),
                                true=exp.Cast(this=exp.Null(), to=to.copy()))],
                    default=other)
    bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[node.this]), exp.Lambda(this=case, expressions=[v]))
    node.replace(exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1))  # DuckDB lists count from 1


_FLOATS = {exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT}


def _float_text(value: exp.Expression) -> exp.Expr:
    """A float as text in T-SQL's style 0: at most 6 significant digits, in scientific notation
    from 1e+006 up and below 0.0001, with a three-digit exponent (``0.333333``, ``1.23457e+006``). That is C's
    ``%g`` with the exponent padded; DuckDB writes the shortest exact form instead."""
    printed = exp.func("PRINTF", exp.Literal.string("%g"), value)  # a float always converts
    return exp.func("REGEXP_REPLACE", printed, exp.Literal.string(r"e([+-])(\d\d)$"), exp.Literal.string(r"e\10\2"))


def _numeric_style(node: exp.Expression, info: exp.Expression) -> exp.DataType | None:
    """sqlglot reads a ``CONVERT`` style as a date style, so ``CONVERT(VARCHAR, x, 1)`` of a number
    became ``STRFTIME(x, '%m/%d/%y')``, which fails. For an integer or a decimal T-SQL ignores the
    style, and so does this; a float's styles 1, 2 and 3 (8, 16 and 17 digits) are refused. Returns
    the number's type when the STRFTIME was taken off."""
    style = node.meta.get("tsql_style")
    if style is None or not isinstance(node.this, exp.TimeToStr) or not isinstance(info.this, exp.TimeToStr):
        return None
    kind = info.this.this.type
    if not _is(kind, _NUMBERS):
        _date_style(node.this, kind, style)
        if style not in CHECKED_DATE_STYLES:
            node.meta["tsql_unchecked_style"] = style
        return None
    if style != "0" and _is(kind, _FLOATS):
        raise ValueError(f"This T-SQL can't be run on the local engine: CONVERT style {style} for a float isn't "
                         "carried yet. Leave the style out, or use 0.")
    node.set("this", node.this.this)
    return kind


def _date_style(text: exp.TimeToStr, kind: exp.DataType | None, style: str) -> None:
    """A date written as text with a style, as T-SQL writes it (WR-208): a ``date`` has no time, so styles
    120, 121 and 126 give only ``yyyy-mm-dd``; and style 126 leaves the fraction off when it is zero
    (``2024-02-29T13:04:05``)."""
    fmt = text.args.get("format")
    if not isinstance(fmt, exp.Literal) or not fmt.name.startswith("%Y-%m-%d"):
        return
    if _is(kind, {exp.DataType.Type.DATE}):
        text.set("format", exp.Literal.string("%Y-%m-%d"))
        return
    if (_is(kind, {exp.DataType.Type.DATETIME, exp.DataType.Type.SMALLDATETIME}) or tsql_types.is_rounded(text.this)) \
            and fmt.name.endswith(".%f"):
        fmt = exp.Literal.string(fmt.name[:-len(".%f")] + ".%g")  # datetime is written to the millisecond (WR-223)
        text.set("format", fmt)
    if style == "126" and fmt.name.endswith((".%f", ".%g")):
        whole = text.copy()
        whole.set("format", exp.Literal.string(fmt.name[:-len(".%f")]))  # .%g is as long
        # DuckDB's MICROSECOND counts the seconds too: the fraction is what's left of a whole second.
        fraction = exp.Mod(this=exp.func("MICROSECOND", text.this.copy()), expression=exp.Literal.number(1_000_000))
        zero = exp.EQ(this=exp.paren(fraction, copy=False), expression=exp.Literal.number(0))
        text.replace(exp.Case(ifs=[exp.If(this=zero, true=whole)], default=text.copy()))


_SIZED = {exp.DataType.Type.VARCHAR, exp.DataType.Type.NVARCHAR, exp.DataType.Type.CHAR, exp.DataType.Type.NCHAR}
_PADDED = {exp.DataType.Type.CHAR, exp.DataType.Type.NCHAR}


def _sized_text(node: exp.Expression, source: exp.DataType | None) -> None:
    """``CAST(x AS VARCHAR(3))`` cuts text to 3 characters in T-SQL (and ``CHAR(3)`` pads it); DuckDB
    ignores the length. With no length a CAST or CONVERT to text is 30 characters long. A number too
    long for the length is an error, not cut."""
    to = node.args.get("to")
    if not isinstance(to, exp.DataType) or to.this not in _SIZED:
        return
    size = to.expressions[0].this if to.expressions else exp.Literal.number(30)
    if not (isinstance(size, exp.Literal) and not size.is_string):
        to.set("expressions", [])  # VARCHAR(MAX): DuckDB's VARCHAR has no limit and takes no MAX
        return
    n = int(size.name)
    text: exp.Expr = exp.Cast(this=node.this, to=exp.DataType.build("VARCHAR"), safe=isinstance(node, exp.TryCast))
    if isinstance(node, exp.TryCast):
        text = exp.TryCast(this=node.this, to=exp.DataType.build("VARCHAR"))
    if _is(source, _BITS):
        text = _bit_text(node.this)
    if source is not None and (digits := _fraction_digits(source)) is not None:
        text = _time_text(node.this, source, digits)
    if _is(source, _NUMBERS):
        # A float in T-SQL's style; a number written with a point (1.50) is a decimal there, and keeps its digits.
        shown = _float_text(node.this) if _is(source, _FLOATS) and not _written_decimal(node.this) else text
        too_long = exp.GT(this=exp.func("LENGTH", shown.copy()), expression=exp.Literal.number(n))
        message = exp.Literal.string(f"The number doesn't fit in {to.sql(dialect='tsql')}.")
        cut: exp.Expr = exp.Case(ifs=[exp.If(this=too_long, true=exp.func("ERROR", message))], default=shown)
    else:
        cut = exp.func("LEFT", text, exp.Literal.number(n))
    if to.this in _PADDED:
        cut = exp.func("RPAD", cut, exp.Literal.number(n), exp.Literal.string(" "))
    node.replace(cut)


def _fraction_digits(source: exp.DataType | None) -> int | None:
    """A datetime2(n) or time(n) with its n written: T-SQL shows n digits of a second as text, zeros too."""
    if not (isinstance(source, exp.DataType) and source.this in (exp.DataType.Type.DATETIME2, exp.DataType.Type.TIME)
            and source.expressions):
        return None
    size = source.expressions[0].this
    return int(size.name) if isinstance(size, exp.Literal) and not size.is_string and size.name.isdigit() else None


def _time_text(value: exp.Expression, source: exp.DataType, digits: int) -> exp.Expr:
    """``CAST(x AS VARCHAR)`` of a datetime2(n) or time(n): its n digits of a second, as a Fabric Warehouse
    answered in live run 11 (``2024-02-29 13:04:05.000000`` for a datetime2(6)), where DuckDB drops zeros."""
    stamp: exp.Expression = value.copy()
    shape = "%Y-%m-%d %H:%M:%S"
    if source.this == exp.DataType.Type.TIME:
        stamp = exp.Add(this=exp.cast(exp.Literal.string("1970-01-01"), "DATE"), expression=stamp)
        shape = "%H:%M:%S"
    whole = exp.Anonymous(this="STRFTIME", expressions=[stamp.copy(), exp.Literal.string(shape)])
    if digits <= 0:
        return whole
    part = exp.func("LEFT", exp.Anonymous(this="STRFTIME", expressions=[stamp, exp.Literal.string("%f")]),
                    exp.Literal.number(min(digits, 6)))
    return exp.DPipe(this=exp.DPipe(this=whole, expression=exp.Literal.string(".")), expression=part)


# The date styles whose values are checked against T-SQL's (WR-208); 0 is the default, as a CAST.
CHECKED_DATE_STYLES = ("0", "101", "103", "112", "120", "121", "126")


def _unchecked_style(style: str) -> str:
    return (f"CONVERT style {style} runs as a translation that hasn't been checked against T-SQL's answers; "
            f"styles {', '.join(CHECKED_DATE_STYLES[1:-1])} and {CHECKED_DATE_STYLES[-1]} have been. "
            "Compare its values in Fabric if they matter.")


def _converts(tree: exp.Expression) -> list[str]:
    """``CONVERT`` fails on a value it can't convert, as ``CAST`` does; sqlglot writes it as TRY_CAST,
    which gives NULL instead. Write it as sqlglot does, then make the outer conversion strict. A date
    style that hasn't been checked against T-SQL runs as sqlglot maps it, and the notes say so."""
    notes: list[str] = []
    for node in list(tree.find_all(exp.Convert)):
        try:
            new = sqlglot.parse_one(node.sql(dialect=KfDuckDB, unsupported_level=ErrorLevel.RAISE), read="duckdb")
        except SqlglotError as exc:
            raise ValueError(f"This T-SQL can't be run on the local engine: {_first_line(exc)}") from None
        if isinstance(new, exp.StrToTime):
            # To a datetime or datetime2 sqlglot writes the bare STRPTIME; wrap it as for a date, so
            # TRY_CONVERT's NULL and the ISO styles' optional parts apply there too (WR-208).
            new = exp.Cast(this=new, to=node.this.copy())
        if isinstance(new, (exp.Cast, exp.TryCast)):
            # Keep CONVERT's own type, length included (the DuckDB text drops it), and TRY_CONVERT's NULL.
            kind = exp.TryCast if node.args.get("safe") else exp.Cast
            new = kind(this=new.this, to=node.this.copy())
            if isinstance(new.this, exp.StrToTime):
                fmt = new.this.args.get("format")
                if isinstance(fmt, exp.Literal) and fmt.name.startswith("%Y-%m-%d"):
                    # Styles 120, 121 and 126 read ISO text, with or without its time and fraction (WR-208),
                    # which a plain cast does; the style's own pattern would demand every part.
                    new.set("this", new.this.this)
                elif kind is exp.TryCast:
                    new.this.set("safe", True)  # TRY_CONVERT gives NULL for a date that doesn't exist, not an error
            style = node.args.get("style")
            if isinstance(new.this, exp.StrToTime) and isinstance(style, exp.Literal) \
                    and style.name not in CHECKED_DATE_STYLES:
                notes.append(_unchecked_style(style.name))  # text read as a date; a date written as text: _types
            if isinstance(new.this, exp.TimeToStr) and isinstance(style, exp.Literal):
                new.meta["tsql_style"] = style.name  # a date style, unless _types finds a number (_numeric_style)
        node.replace(new)
    return notes


_LIKE_NOTE = ("A LIKE pattern that isn't written in the query (such as LIKE other_column) matches as DuckDB's LIKE "
              "does, which takes no [a-c] character ranges.")


def _likes(tree: exp.Expression) -> list[str]:
    """T-SQL's ``LIKE`` takes ``[a-c]`` and ``[^a-c]`` character ranges, which DuckDB's doesn't: there
    ``'b1' LIKE '[a-c]1'`` is false. A pattern with a range is matched as a regular expression instead."""
    notes: list[str] = []
    for like in list(tree.find_all(exp.Like)):
        escape = like.parent if isinstance(like.parent, exp.Escape) else None
        pattern = like.expression
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            notes.append(_LIKE_NOTE)
            continue
        char = escape.expression.name if escape is not None and isinstance(escape.expression, exp.Literal) else ""
        if "[" not in pattern.name or (escape is not None and len(char) != 1):
            continue
        match: exp.Expression = exp.RegexpFullMatch(this=like.this, expression=exp.Literal.string(_like_regex(pattern.name, char)))
        if like.args.get("negate"):
            match = exp.Not(this=match)
        (escape or like).replace(match)
    return notes


def _patindex(tree: exp.Expression) -> None:
    """``PATINDEX(pattern, s)``: where in ``s`` the LIKE pattern first matches, from 1, or 0; DuckDB has
    no PATINDEX. A pattern that starts with ``%`` is looked for anywhere, the rest of it matched from
    there to the end (``PATINDEX('%b%', 'abc')`` is 2); without one it must match from the start, so the
    answer is 1 or 0. NULL in, NULL out. The pattern has to be a literal, or NULL."""
    for node in list(tree.find_all(exp.Anonymous)):
        if node.name.upper() != "PATINDEX":
            continue
        if len(node.expressions) != 2:
            raise ValueError("This T-SQL can't be run on the local engine: PATINDEX takes a pattern and a text.")
        pattern, text = node.expressions
        bare = pattern
        # CAST(NULL AS VARCHAR(10)), which _types has made LEFT(CAST(NULL AS VARCHAR), 10), or (NULL)
        while isinstance(bare, (exp.Cast, exp.Paren, exp.Left)):
            bare = bare.this
        if isinstance(bare, exp.Null):  # a NULL pattern finds nothing: NULL, as in T-SQL
            node.replace(exp.null())
            continue
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            raise ValueError("This T-SQL can't be run on the local engine: PATINDEX needs its pattern written out "
                             "as text ('%b%'), not taken from a column or an expression.")
        core = pattern.name.lstrip("%")
        body = _like_regex(core)[len("(?s)"):]
        if not core:  # '%' alone matches from the first character (UNVERIFIED for an empty text)
            found: exp.Expression = exp.Literal.number(1)
        elif core == pattern.name:  # anchored at the start: the whole text has to match
            found = exp.Case(
                ifs=[exp.If(this=exp.RegexpFullMatch(this=text.copy(), expression=exp.Literal.string("(?s)" + body)),
                            true=exp.Literal.number(1))], default=exp.Literal.number(0))
        else:  # the shortest lead-in after which the rest matches to the end
            before = exp.func("REGEXP_REPLACE", text.copy(), exp.Literal.string(f"(?s)^(.*?){body}$"),
                              exp.Literal.string("\\1"))
            # Anonymous: LENGTH here counts trailing spaces (LEN, which _functions rewrites, doesn't).
            length = exp.Anonymous(this="LENGTH", expressions=[before])
            matches = exp.RegexpFullMatch(this=text.copy(), expression=exp.Literal.string("(?s).*" + body))
            found = exp.Case(ifs=[exp.If(this=matches, true=length + exp.Literal.number(1))],
                             default=exp.Literal.number(0))
        node.replace(exp.Case(ifs=[exp.If(this=exp.Is(this=text.copy(), expression=exp.null()), true=exp.null())],
                              default=found))


def _like_regex(pattern: str, escape: str = "") -> str:
    """A T-SQL LIKE pattern as an RE2 expression for a whole-string match."""
    out, i = ["(?s)"], 0  # % and _ match a line break too
    while i < len(pattern):
        c = pattern[i]
        if escape and c == escape and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "[" and (found := _like_range(pattern, i + 1, escape)) is not None:
            text, i = found
            out.append(text)
            continue
        out.append(".*" if c == "%" else "." if c == "_" else re.escape(c))
        i += 1
    return "".join(out)


_NOTHING, _ANY = "[^\\x{0}-\\x{10FFFF}]", "[\\x{0}-\\x{10FFFF}]"


def _like_range(pattern: str, start: int, escape: str) -> tuple[str, int] | None:
    """The ``[...]`` that opens before ``start`` as an RE2 class, and where the pattern goes on; None
    when it isn't closed (the ``[`` is then an ordinary character). The escape character counts inside
    too, and a range written backwards (``[z-a]``) matches nothing, as in SQL Server."""
    chars: list[tuple[str, bool]] = []  # each character, and whether it was escaped
    i = start
    while i < len(pattern):
        c = pattern[i]
        if escape and c == escape and i + 1 < len(pattern):
            chars.append((pattern[i + 1], True))
            i += 2
            continue
        if c == "]" and chars:
            break
        chars.append((c, False))
        i += 1
    else:
        return None
    negate = len(chars) > 1 and chars[0] == ("^", False)
    if negate:
        chars = chars[1:]
    parts: list[str] = []
    k = 0
    while k < len(chars):
        if k + 2 < len(chars) and chars[k + 1] == ("-", False):
            low, high = chars[k][0], chars[k + 2][0]
            if low <= high:
                parts.append(_in_class(low) + "-" + _in_class(high))
            k += 3
        else:
            parts.append(_in_class(chars[k][0]))
            k += 1
    if not parts:  # only backwards ranges
        return (_ANY if negate else _NOTHING), i + 1
    return "[" + ("^" if negate else "") + "".join(parts) + "]", i + 1


def _in_class(ch: str) -> str:
    return "\\" + ch if ch in "\\[]^-" else ch


_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.In, exp.Like)
_PADDED_NOTE = ("Couldn't tell the types in some comparisons, so they are done as DuckDB does them: T-SQL ignores "
                "trailing spaces when it compares text ('a' = 'a  ', and 'a  ' LIKE 'a'), and here they count.")
_TOP = "\U0010ffff"  # the last character: 'ab ' and then it sorts after 'ab', any spaces, and anything after them


def _padded(here: list[exp.Expression], there: list[exp.Expression]) -> list[str]:
    """T-SQL compares text as if the shorter side were padded with spaces (ANSI padding), so
    ``'a' = 'a  '`` is true, in ``=``, ``<>``, ``<`` and the rest and in ``IN``; DuckDB compares exactly.
    Where both sides are known to be text, trailing spaces are taken off first. ``GROUP BY``,
    ``DISTINCT``, ``UNION`` and ``JOIN ... USING`` still keep 'a' and 'a ' apart (docs/local-data.md)."""
    if len(here) != len(there):
        return [_PADDED_NOTE] if here else []
    unsure = False
    for node, info in zip(here, there, strict=True):
        if isinstance(node, exp.Like):
            if _is(info.this.type, _TEXT):
                _like_padded(node)
            elif _unknown(info.this.type) and _is(info.expression.type, _TEXT):
                unsure = True  # as for =: a text pattern on a value whose type can't be told
            continue
        if isinstance(node, exp.In):
            if info.args.get("query") is not None or not info.expressions:
                continue
            sides = [info.this, *info.expressions]
            if all(_is(v.type, _TEXT) for v in sides):
                node.set("this", _rtrim(node.this))
                node.set("expressions", [_rtrim(v) for v in node.expressions])
            elif any(_is(v.type, _TEXT) for v in sides) and any(_unknown(v.type) for v in sides):
                unsure = True
            continue
        a, b = info.this.type, info.expression.type
        if _is(a, _TEXT) and _is(b, _TEXT):
            if isinstance(node, exp.EQ) and _column_and_text(node):
                _padded_equal(node)
            else:
                node.set("this", _rtrim(node.this))
                node.set("expression", _rtrim(node.expression))
        elif (_is(a, _TEXT) or _is(b, _TEXT)) and (_unknown(a) or _unknown(b)):
            unsure = True
    return [_PADDED_NOTE] if unsure else []


_GROUPED_NOTE = ("T-SQL counts text that differs only in trailing spaces ('a' and 'a  ') as one value in GROUP BY, "
                 "DISTINCT, UNION, INTERSECT and EXCEPT, and here they stay apart. If the text can end in spaces, "
                 "group on RTRIM(...) to get T-SQL's groups.")


def _grouped_text(typed: exp.Expression) -> list[str]:
    """T-SQL's padding also makes ``'a'`` and ``'a  '`` one group, one distinct value and one row of a
    ``UNION``; here they are two. That isn't rewritten (T-SQL shows either one), so a query that groups
    text says so (WR-210). Text already trimmed with RTRIM, and a literal without trailing spaces, can't
    differ."""
    def risky(node: exp.Expression) -> bool:
        if isinstance(node, exp.Alias):
            node = node.this
        if isinstance(node, (exp.Trim, exp.Literal)) or (isinstance(node, exp.Anonymous) and node.name.upper() == "RTRIM"):
            return isinstance(node, exp.Literal) and node.is_string and node.name.endswith(" ")
        return _is(node.type, _TEXT)
    sets: list[list[Any]] = []
    for node in typed.find_all(exp.Group, exp.Distinct, exp.SetOperation):
        if isinstance(node, exp.SetOperation):
            if node.args.get("distinct") is False:
                continue  # UNION ALL keeps every row anyway
            sets += [side.selects for side in (node.left, node.right) if isinstance(side, exp.Query)]
        elif isinstance(node, exp.Distinct) and not node.expressions and isinstance(node.parent, exp.Select):
            sets.append(node.parent.selects)  # SELECT DISTINCT: its columns
        else:
            sets.append(node.expressions)
    return [_GROUPED_NOTE] if any(risky(e) for exprs in sets for e in exprs) else []


def _like_padded(node: exp.Like) -> None:
    """``x LIKE p`` ignores the trailing spaces of ``x`` in T-SQL, any number of them (every character of ``p``
    counts): ``'abc  ' LIKE 'abc'``, ``'abc  ' LIKE 'abc_'`` and ``'abc  ' LIKE 'abc__'`` are all true, as a Fabric
    Warehouse answered in live run 11. So it matches when ``x`` does with none, some or all of its trailing spaces
    taken off (WR-210)."""
    if isinstance(node.this, exp.Literal) and node.this.is_string and not node.this.name.endswith(" "):
        return
    whole: exp.Expression = node.parent if isinstance(node.parent, exp.Escape) else node
    negate = bool(node.args.get("negate"))  # x NOT LIKE p: no form matches
    node.set("negate", None)
    text = node.this
    base = _rtrim(text.copy())
    one = whole.copy()
    like = one.this if isinstance(one, exp.Escape) else one
    like.set("this", exp.DPipe(this=base.copy(), expression=exp.Anonymous(this="REPEAT", expressions=[
        exp.Literal.string(" "), exp.cast(exp.column("kf_n"), "INT")])))
    spaces = exp.Sub(this=exp.Anonymous(this="LENGTH", expressions=[text.copy()]),
                     expression=exp.Anonymous(this="LENGTH", expressions=[base]))
    counts = exp.Anonymous(this="RANGE", expressions=[exp.Literal.number(0), exp.Add(
        this=spaces, expression=exp.Literal.number(1))])
    tried = exp.Anonymous(this="LIST_TRANSFORM", expressions=[
        counts, exp.Lambda(this=one, expressions=[exp.to_identifier("kf_n")])])
    any_form: exp.Expression = exp.Anonymous(this="LIST_BOOL_OR", expressions=[tried])
    whole.replace(exp.not_(any_form) if negate else any_form)


def _rtrim(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Literal) and node.is_string:
        return exp.Literal.string(node.name.rstrip(" "))
    # T-SQL takes off spaces only; DuckDB's RTRIM(x) also takes off other whitespace, such as a no-break space.
    return exp.func("RTRIM", node, exp.Literal.string(" "))


def _column_and_text(node: exp.EQ) -> bool:
    sides = (node.this, node.expression)
    return any(isinstance(x, exp.Column) for x in sides) and any(isinstance(x, exp.Literal) and x.is_string
                                                                 for x in sides)


def _padded_equal(node: exp.EQ) -> None:
    """``column = 'text'`` as a range on the column and then the exact test, so a file's row groups
    can still be skipped by their min and max (an RTRIM on the column alone reads them all)."""
    column, text = (node.this, node.expression) if isinstance(node.this, exp.Column) else (node.expression, node.this)
    value = text.name.rstrip(" ")
    low, high = exp.Literal.string(value), exp.Literal.string(value + " " + _TOP)
    node.replace(exp.paren(exp.and_(
        exp.GTE(this=column.copy(), expression=low), exp.LTE(this=column.copy(), expression=high),
        exp.EQ(this=_rtrim(column), expression=low.copy())), copy=False))


def _in_order(tree: exp.Expression, kinds: tuple) -> list[exp.Expression]:
    # Depth first: qualifying wraps columns in aliases, which moves nodes between levels but not in this order.
    return [n for n in tree.walk(bfs=False) if isinstance(n, kinds)]


def _is(kind: exp.DataType | None, group: set) -> bool:
    return kind is not None and kind.this in group


def _unknown(kind: exp.DataType | None) -> bool:
    return kind is None or kind.this in (exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL)


_ORDER_TYPE = "kf_order_type"


def _percentiles(tree: exp.Expression) -> None:
    """``PERCENTILE_CONT`` and ``PERCENTILE_DISC`` as T-SQL gives them.

    - ``PERCENTILE_CONT`` is a float in T-SQL. DuckDB kept a decimal's type, so 0.25 of 1.25, 2.50 and
      4.00 came back as 1.88, not 1.875: the values are made floats first.
    - With ``ORDER BY x DESC``, p counts from the largest value. DuckDB's windowed QUANTILE ignores the
      DESC and counted from the smallest, so the 0.25 percentile came back as the 0.75 one. Here the
      values are negated, so ascending order is T-SQL's descending one, and the answer is negated back:
      integers in a wider type, so the smallest one doesn't overflow, then returned to their own type."""
    for group in list(tree.find_all(exp.WithinGroup)):
        window = group.parent
        if not (isinstance(group.this, (exp.PercentileCont, exp.PercentileDisc)) and isinstance(window, exp.Window)
                and window.this is group and isinstance(group.expression, exp.Order)
                and len(group.expression.expressions) == 1):
            continue
        ordered = group.expression.expressions[0]
        cont = isinstance(group.this, exp.PercentileCont)
        if cont:
            ordered.set("this", exp.Cast(this=ordered.this, to=exp.DataType.build("DOUBLE")))
        if not ordered.args.get("desc"):
            continue
        kind: exp.DataType | None = group.meta.get(_ORDER_TYPE)
        if not cont and not _unknown(kind) and (not _is(kind, _NUMBERS) or _is(kind, {exp.DataType.Type.BIT})):
            raise ValueError(_NOT_YET + "PERCENTILE_DISC with ORDER BY ... DESC on a value that isn't a number "
                             "isn't mapped yet. Run it in a Fabric Warehouse.")
        value = ordered.this
        if not cont and _is(kind, _INTEGERS):
            value = exp.Cast(this=value, to=exp.DataType.build("HUGEINT"))
        ordered.set("this", exp.Neg(this=exp.paren(value, copy=False)))
        ordered.set("desc", False)
        ordered.set("nulls_first", True)
        answer: exp.Expression = exp.Neg(this=exp.paren(window.copy(), copy=False))
        if not cont and _is(kind, _INTEGERS):
            assert kind is not None
            answer = exp.Cast(this=answer, to=kind.copy())
        window.replace(answer)


_SPREADS = {"VAR": exp.Variance, "VARP": exp.VariancePop, "STDEVP": exp.StddevPop}


def _functions(tree: exp.Expression) -> None:
    for trim in tree.find_all(exp.Trim):
        if trim.args.get("expression") is None:  # RTRIM, LTRIM and TRIM take off spaces only, as in T-SQL
            trim.set("expression", exp.Literal.string(" "))
    for kind in tree.find_all(exp.DataType):
        if kind.this == exp.DataType.Type.DOUBLE and kind.expressions:
            # FLOAT(n): 4 bytes up to 24 bits of mantissa, 8 from 25 to 53, as SQL Server stores it.
            bits = kind.expressions[0].this
            if not (isinstance(bits, exp.Literal) and bits.is_int and 1 <= int(bits.name) <= 53):
                raise ValueError("FLOAT(n) takes a whole number from 1 to 53.")
            small = int(bits.name) <= 24
            kind.set("this", exp.DataType.Type.FLOAT if small else exp.DataType.Type.DOUBLE)
            kind.set("expressions", [])
        # NVARCHAR(MAX) and VARBINARY(MAX): DuckDB's types have no limit and take no MAX.
        if any(isinstance(p.this, exp.Var) and p.this.name.upper() == "MAX" for p in kind.expressions
               if isinstance(p, exp.DataTypeParam)):
            kind.set("expressions", [])
    for join in tree.find_all(exp.Join):
        # CROSS / OUTER APPLY come out as a LATERAL join, which DuckDB wants an ON for.
        apply = join.this.args.get("cross_apply") if isinstance(join.this, exp.Lateral) else None
        if apply is not None:
            join.this.set("cross_apply", None)
            join.set("side", None if apply else "LEFT")
            join.set("on", exp.true())
    for node in list(tree.find_all(exp.Length, exp.Extract, exp.CurrentTimestamp, exp.Anonymous, exp.DateDiff,
                                   exp.DateAdd, exp.TimestampTrunc)):
        if isinstance(node, exp.DateDiff):
            part = _datepart(node, "DATEDIFF")
            if part == "WEEK":
                _sunday_weeks(node)
            elif part == "ISO_WEEK":
                _sunday_weeks(node, shift=0)
            elif not node.args.get("big_int"):
                _int_datediff(node)
        elif isinstance(node, exp.DateAdd):
            _datepart(node, "DATEADD")
        elif isinstance(node, exp.TimestampTrunc):
            if _datepart(node, "DATETRUNC") == "WEEK":
                _sunday_week_start(node)
        elif isinstance(node, exp.Length):
            # LEN doesn't count trailing spaces.
            node.set("this", _rtrim(node.this))
        elif isinstance(node, exp.Extract):
            unit = node.this.name.upper()
            if isinstance(node.expression, exp.Literal) and node.expression.is_string:
                node.set("expression", exp.cast(node.expression, "TIMESTAMP"))  # T-SQL reads the text as a datetime
            day = node.expression
            if unit in ("MILLISECOND", "MICROSECOND"):  # within the second, as T-SQL counts them (DuckDB adds the seconds)
                node.replace(exp.paren(exp.Mod(this=node.copy(), expression=exp.Literal.number(
                    1000 if unit == "MILLISECOND" else 1_000_000)), copy=False))
            elif unit in ("DAYOFWEEK", "DW", "WEEKDAY"):  # Sunday = 1 (DATEFIRST 7)
                node.replace(exp.paren(exp.func("DAYOFWEEK", day) + 1, copy=False))
            elif unit in ("WEEK", "WK", "WW"):  # 1 January is in week 1; weeks start on Sunday
                sunday_first = exp.EQ(this=exp.func("DAYOFWEEK", exp.func("DATE_TRUNC", exp.Literal.string("year"),
                                                                          day.copy())),
                                      expression=exp.Literal.number(0))
                node.replace(exp.paren(
                    exp.cast(exp.func("STRFTIME", day, exp.Literal.string("%U")), "INT")
                    + exp.Case(ifs=[exp.If(this=sunday_first, true=exp.Literal.number(0))],
                               default=exp.Literal.number(1)), copy=False))
        elif isinstance(node, exp.Anonymous) and node.name.upper() in _SPREADS and len(node.expressions) == 1:
            # VAR is the sample variance, VARP the population's, STDEVP the population's standard deviation
            # (STDEV is read as the sample's already). DuckDB has none of these names.
            node.replace(_SPREADS[node.name.upper()](this=node.expressions[0]))
        elif isinstance(node, exp.CurrentTimestamp) or (
                isinstance(node, exp.Anonymous) and node.name.upper() in ("GETUTCDATE", "SYSUTCDATETIME")):
            # A Fabric Warehouse's clock is UTC, and GETDATE() has no time zone. The project runs in UTC.
            node.replace(exp.cast(exp.CurrentTimestamp(), "TIMESTAMP"))


_NOT_YET = "This T-SQL can't be run on the local engine: "

_TOO_DEEP = (_NOT_YET + "the query nests calls or brackets too deeply to translate. Take the inner part out into a "
             "CTE, a subquery or a variable.")


def _gaps(tree: exp.Expression) -> None:
    """T-SQL functions DuckDB doesn't have, written as DuckDB SQL that gives T-SQL's answer; or, where
    that can't be done faithfully yet, refused with a message that says so (not DuckDB's own error)."""
    for kind in list(tree.find_all(exp.DataType)):
        if kind.this == exp.DataType.Type.BIT:  # DuckDB's BIT is a bit string: CAST(1 AS BIT) was 32 characters
            kind.replace(exp.DataType.build("BOOLEAN"))
    for node in list(tree.find_all(exp.Cast, exp.TryCast)):
        to, value = node.to, node.this
        if to.this == exp.DataType.Type.BOOLEAN and isinstance(value, exp.Literal) and value.is_string:
            text = value.name.strip().upper()
            if text in ("TRUE", "FALSE"):  # T-SQL converts 'true' and 'false', and numbers written as text
                node.replace(exp.Boolean(this=text == "TRUE"))
            else:
                inner = exp.TryCast if isinstance(node, exp.TryCast) else exp.Cast
                node.set("this", inner(this=value, to=exp.DataType.build("DECIMAL(38, 10)")))
    if any(True for _ in tree.find_all(exp.OpenJSON)):
        raise ValueError(_NOT_YET + "OPENJSON isn't mapped yet. JSON_VALUE and JSON_QUERY read one value or object "
                         "from JSON text.")
    _refused_by_name(tree)
    for rounded in list(tree.find_all(exp.Round)):
        mode = rounded.args.get("truncate")
        if mode is None:
            continue
        if not (isinstance(mode, exp.Literal) and not mode.is_string):
            raise ValueError(_NOT_YET + "ROUND's third argument has to be a number written in the query.")
        rounded.set("truncate", None)
        if mode.name not in ("0", "0.0"):  # ROUND(x, n, 1): cut, not round
            rounded.replace(exp.Anonymous(this="TRUNC", expressions=[rounded.this, rounded.args.get("decimals")
                                                                  or exp.Literal.number(0)]))
    if any(True for _ in tree.find_all(tsql_format.TsqlFormat)):  # _types couldn't tell the value's type
        raise ValueError(_NOT_YET + "FORMAT needs to know its value's type, and here it can't be told. "
                         "CAST the value to DATETIME2, DATE or DECIMAL.")
    for limit in list(tree.find_all(exp.Limit)):
        options = limit.args.get("limit_options")
        if options is not None and options.args.get("with_ties"):
            _with_ties(limit)
    for call in list(tree.find_all(exp.Anonymous)):
        name = call.name.upper()
        args = call.expressions
        if name == "CHOOSE" and len(args) >= 2:
            call.replace(exp.Case(this=args[0], ifs=[exp.If(this=exp.Literal.number(i), true=a)
                                                     for i, a in enumerate(args[1:], 1)]))
        elif name == "DATALENGTH" and len(args) == 1:
            call.replace(_datalength(args[0]))
        elif name == "STRING_SPLIT":
            _string_split(call)
        elif name == "QUOTENAME" and 1 <= len(args) <= 2:
            call.replace(_quotename(args))
        elif name == "STR" and 1 <= len(args) <= 3:
            call.replace(_str(args))
        elif name == "ISNUMERIC":
            raise ValueError(_NOT_YET + "ISNUMERIC isn't mapped yet (its rules accept '$', ',' and '-' alone). "
                             "Use TRY_CAST(x AS FLOAT) IS NOT NULL, or TRY_CAST to the type you need.")


_QUOTES = {"[": ("[", "]"), "]": ("[", "]"), '"': ('"', '"'), "'": ("'", "'")}


def _quotename(args: list[exp.Expression]) -> exp.Expression:
    """``QUOTENAME(s [, q])``: s between brackets (or q), the closing character doubled inside, and NULL
    for more than 128 characters, as T-SQL does."""
    quote = "["
    if len(args) == 2:
        q = args[1]
        if not (isinstance(q, (exp.Literal, exp.National)) and q.is_string and q.name in _QUOTES):
            raise ValueError(_NOT_YET + "QUOTENAME runs here with [, ], \" or ' written in the query.")
        quote = q.name
    left, right = _QUOTES[quote]
    text = args[0]
    quoted = exp.DPipe(this=exp.DPipe(this=exp.Literal.string(left), expression=exp.func(
        "REPLACE", text.copy(), exp.Literal.string(right), exp.Literal.string(right * 2)), safe=False),
        expression=exp.Literal.string(right), safe=False)
    return exp.Case(ifs=[exp.If(this=exp.GT(this=exp.Length(this=text.copy()), expression=exp.Literal.number(128)),
                                true=exp.Null())], default=quoted)


def _timefromparts(node: exp.TimeFromParts) -> exp.Expression:
    """``TIMEFROMPARTS(hour, minute, seconds, fractions, precision)``: fractions are tenths, hundredths and so
    on of a second, by the precision, which T-SQL needs written in the query. DuckDB's time keeps
    microseconds, so precision 7 runs here only with fractions written in the query that end in 0."""
    precision = node.args.get("precision")
    if not (isinstance(precision, exp.Literal) and not precision.is_string and precision.name.isdigit()
            and int(precision.name) <= 7):
        raise ValueError(_NOT_YET + "TIMEFROMPARTS's precision has to be a whole number from 0 to 7 written in the query.")
    places = int(precision.name)
    fractions = node.args.get("fractions") or exp.Literal.number(0)
    written = isinstance(fractions, exp.Literal) and not fractions.is_string and fractions.name.isdigit()
    if written and int(fractions.name) >= 10 ** places:
        raise ValueError(f"TIMEFROMPARTS's fractions ({fractions.name}) don't fit a precision of {places}.")
    if places == 7 and not (written and int(fractions.name) % 10 == 0):
        raise ValueError(_NOT_YET + "TIMEFROMPARTS with precision 7 needs fractions written in the query that end "
                         "in 0: times here keep microseconds.")
    micros: exp.Expression = (exp.Literal.number(int(fractions.name) // 10) if places == 7 else
                              exp.Mul(this=exp.Cast(this=fractions.copy(), to=exp.DataType.build("BIGINT")),
                                      expression=exp.Literal.number(10 ** (6 - places))))
    if not written:  # fractions from a column or an expression: stop as T-SQL does, never carry into the seconds
        f = exp.Cast(this=fractions.copy(), to=exp.DataType.build("BIGINT"))
        micros = exp.Case(ifs=[exp.If(this=exp.or_(exp.LT(this=f, expression=exp.Literal.number(0)),
                                                   exp.GTE(this=f.copy(), expression=exp.Literal.number(10 ** places))),
                                      true=exp.Anonymous(this="ERROR", expressions=[exp.Literal.string(
                                          f"TIMEFROMPARTS's fractions don't fit a precision of {places}.")]))],
                          default=micros)
    seconds = exp.Add(this=exp.Cast(this=node.args["sec"].copy(), to=exp.DataType.build("DOUBLE")),
                      expression=exp.Div(this=exp.paren(micros), expression=exp.Literal.number(1000000.0)))
    return exp.Anonymous(this="MAKE_TIME", expressions=[node.args["hour"].copy(), node.args["min"].copy(), exp.paren(seconds)])


def _whole_literal(node: exp.Expression, what: str, low: int, high: int) -> int:
    if not (isinstance(node, exp.Literal) and not node.is_string and node.name.isdigit()
            and low <= int(node.name) <= high):
        raise ValueError(_NOT_YET + f"STR's {what} has to be a whole number from {low} to {high} written in the query.")
    return int(node.name)


def _str(args: list[exp.Expression]) -> exp.Expression:
    """``STR(x [, length [, decimals]])``: x as right-aligned text of ``length`` characters (10) with
    ``decimals`` places (0, at most 16), rounded; fewer places when they don't fit, and ``length``
    asterisks when the whole number doesn't, as T-SQL does."""
    length = _whole_literal(args[1], "length", 1, 8000) if len(args) > 1 else 10
    places = min(_whole_literal(args[2], "decimals", 0, 16), 16) if len(args) > 2 else 0
    value = exp.Cast(this=args[0].copy(), to=exp.DataType.build("DOUBLE"))
    width = exp.Literal.number(length)
    ifs = []
    for d in range(places, -1, -1):
        text = exp.func("PRINTF", exp.Literal.string(f"%.{d}f"), exp.Round(this=value.copy(),
                                                                         decimals=exp.Literal.number(d)))
        ifs.append(exp.If(this=exp.LTE(this=exp.Length(this=text.copy()), expression=width),
                          true=exp.func("LPAD", text, width.copy(), exp.Literal.string(" "))))
    return exp.Case(ifs=[exp.If(this=exp.Is(this=value.copy(), expression=exp.Null()), true=exp.Null()), *ifs],
                    default=exp.Literal.string("*" * length))


_JSON_NOTE = ("JSON_VALUE gives a JSON number in the local engine's form, which can differ from the text: 1.50 is "
              "1.5, and 1e2 is 100.0. T-SQL gives the number as written.")
_JSON_QUERY_NOTE = ("JSON_QUERY gives the object or array without the spaces between its parts here ([1,2] for "
                    "[1, 2]). It hasn't been checked whether a warehouse keeps them.")
_JSON_BUILD_NOTE = ("JSON_OBJECT and JSON_ARRAY write a decimal in its shortest form here (1.50 is 1.5). T-SQL keeps "
                    "its scale.")
_ISJSON_NOTE = ("ISJSON counts JSON with a trailing comma, or NaN, as valid here. T-SQL counts only standard JSON.")
_ISJSON_TYPES = {"VALUE": None, "ARRAY": ("ARRAY",), "OBJECT": ("OBJECT",),
                 "SCALAR": ("BIGINT", "UBIGINT", "HUGEINT", "DOUBLE", "VARCHAR")}


def _json_path(node: exp.Expression, name: str) -> tuple[str, bool]:
    """A JSON_VALUE or JSON_QUERY path as DuckDB reads it, and whether it's strict. T-SQL's path may start with
    ``lax`` (the default) or ``strict``, then ``$`` and keys and array positions. The path is written again from
    its parts, so a key with a quote or a space reaches DuckDB as that key. A wildcard, slice or filter, which
    T-SQL's paths don't take, and a path that isn't written out, are refused by name."""
    path = node.expression
    if isinstance(path, exp.Literal) and path.is_string:
        m = re.fullmatch(r"\s*(?:(lax|strict)\s+)?(\$.*)", path.name, re.IGNORECASE | re.DOTALL)
        try:
            parts = jsonpath.parse(m.group(2).strip()).expressions if m else []
        except (SqlglotError, ValueError):
            parts = []
        if not parts:
            raise ValueError(f"{name}: '{path.name}' isn't a JSON path. A path starts with $, or with lax or strict "
                             "and then $, such as '$.customer.id'.")
        strict = bool(m and (m.group(1) or "").lower() == "strict")
    else:
        raise ValueError(_NOT_YET + f"{name} with a path that isn't written out as text isn't mapped yet. Write the "
                         "path as text, such as '$.customer.id', or run it in a Fabric Warehouse.")
    text = ""
    for part in parts:
        if isinstance(part, exp.JSONPathRoot):
            text += "$"
        elif isinstance(part, exp.JSONPathKey) and isinstance(part.this, str):
            key = part.this
            text += f".{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else \
                '."' + key.replace('"', '\\"') + '"'  # jsonpath.parse unescapes only an escaped quote; a backslash stays as written
        elif isinstance(part, exp.JSONPathSubscript) and isinstance(part.this, int):
            text += f"[{part.this}]"
        else:
            raise ValueError(_NOT_YET + f"{name} with a wildcard, slice or filter in its path isn't mapped yet. "
                             "T-SQL's paths take keys and array positions, such as '$.items[0].id'.")
    if not text.startswith("$"):
        raise ValueError(f"{name}: a JSON path starts with $, such as '$.customer.id'.")
    return text, strict


def _fail(message: str) -> exp.Expression:
    return exp.Anonymous(this="error", expressions=[exp.Literal.string(message)])


def _json(tree: exp.Expression) -> list[str]:
    """T-SQL's JSON functions with T-SQL's answers (WR-219):

    - ``ISJSON(x)`` is 1 for an object or array, 0 for anything else, and NULL for NULL. With ``VALUE``, ``ARRAY``,
      ``OBJECT`` or ``SCALAR`` (a number or a string) it tests for that kind.
    - ``JSON_VALUE`` gives a scalar's text. In lax mode (the default) a path that isn't there, an object or array, or
      text past 4000 characters gives NULL; in strict mode each is an error, as in T-SQL.
    - ``JSON_QUERY`` gives an object or array. In lax mode a scalar or a path that isn't there gives NULL; in strict
      mode an error.
    - ``JSON_ARRAY`` leaves NULLs out, T-SQL's default (DuckDB's keeps them). ``JSON_OBJECT`` keeps them as null,
      which is T-SQL's default and DuckDB's.
    """
    notes: list[str] = []
    if any(True for _ in tree.find_all(exp.JSONArrayAgg, exp.JSONObjectAgg)):
        raise ValueError(_NOT_YET + "JSON_ARRAYAGG and JSON_OBJECTAGG aren't mapped yet. JSON_ARRAY and JSON_OBJECT "
                         "build one row's JSON, or run it in a Fabric Warehouse.")
    if any(True for _ in tree.find_all(exp.JSONObject)):
        notes.append(_JSON_BUILD_NOTE)
    for node in reversed(list(tree.find_all(exp.Anonymous))):  # innermost first, so a nested call is replaced too
        name = node.name.upper()
        if name == "ISJSON":
            args = node.expressions
            if not 1 <= len(args) <= 2:
                raise ValueError("ISJSON takes the text, and optionally VALUE, ARRAY, OBJECT or SCALAR.")
            wanted = "" if len(args) == 1 else (args[1].name.upper() if isinstance(args[1], exp.Column) else "?")
            if wanted and wanted not in _ISJSON_TYPES:
                raise ValueError(f"ISJSON's second argument is VALUE, ARRAY, OBJECT or SCALAR, not {args[1].sql()}.")
            text = args[0]
            types = ("OBJECT", "ARRAY") if not wanted else _ISJSON_TYPES[wanted]
            test: exp.Expression = exp.true() if types is None else exp.In(
                this=exp.Anonymous(this="json_type", expressions=[text.copy()]),
                expressions=[exp.Literal.string(t) for t in types])
            node.replace(exp.Case(ifs=[
                exp.If(this=exp.Is(this=text.copy(), expression=exp.Null()), true=exp.Null()),
                exp.If(this=exp.Not(this=exp.Anonymous(this="json_valid", expressions=[text.copy()])),
                       true=exp.Literal.number(0)),
                exp.If(this=test, true=exp.Literal.number(1))], default=exp.Literal.number(0)))
            notes.append(_ISJSON_NOTE)
        elif name == "JSON_ARRAY":
            parts = [exp.Anonymous(this="to_json", expressions=[a.copy()]) for a in node.expressions]
            inner = exp.Anonymous(this="concat_ws", expressions=[exp.Literal.string(","), *parts]) if parts \
                else exp.Literal.string("")
            notes.append(_JSON_BUILD_NOTE)
            node.replace(exp.Cast(this=exp.DPipe(this=exp.DPipe(this=exp.Literal.string("["), expression=inner),
                                                 expression=exp.Literal.string("]")),
                                  to=exp.DataType.build("JSON")))  # JSON, so an outer JSON_ARRAY or JSON_OBJECT nests it
    for value in reversed(list(tree.find_all(exp.JSONExtractScalar))):
        path, strict = _json_path(value, "JSON_VALUE")
        doc, p = value.this, exp.Literal.string(path)
        kind = exp.Anonymous(this="json_type", expressions=[doc.copy(), p.copy()])
        text = exp.Anonymous(this="json_extract_string", expressions=[doc.copy(), p.copy()])
        long = exp.GT(this=exp.Anonymous(this="length", expressions=[text.copy()]), expression=exp.Literal.number(4000))
        nested = exp.In(this=kind.copy(), expressions=[exp.Literal.string("OBJECT"), exp.Literal.string("ARRAY")])
        if strict:
            ifs = [exp.If(this=exp.Is(this=doc.copy(), expression=exp.Null()), true=exp.Null()),
                   exp.If(this=exp.Is(this=kind.copy(), expression=exp.Null()),
                          true=_fail(f"JSON_VALUE: the property at strict path '{path}' doesn't exist.")),
                   exp.If(this=nested, true=_fail(f"JSON_VALUE: the value at strict path '{path}' isn't a scalar. "
                                                  "Use JSON_QUERY instead.")),
                   exp.If(this=long, true=_fail(f"JSON_VALUE: the value at strict path '{path}' is longer than "
                                                "4000 characters."))]
        else:
            ifs = [exp.If(this=nested, true=exp.Null()), exp.If(this=long, true=exp.Null())]
        value.replace(exp.Case(ifs=ifs, default=text))
        notes.append(_JSON_NOTE)
    for query in reversed(list(tree.find_all(exp.JSONExtract))):
        if not query.args.get("json_query"):
            continue
        path, strict = _json_path(query, "JSON_QUERY")
        doc, p = query.this, exp.Literal.string(path)
        kind = exp.Anonymous(this="json_type", expressions=[doc.copy(), p.copy()])
        nested = exp.In(this=kind.copy(), expressions=[exp.Literal.string("OBJECT"), exp.Literal.string("ARRAY")])
        got = exp.Anonymous(this="json_extract", expressions=[doc.copy(), p.copy()])  # JSON, so it nests as JSON
        if strict:
            ifs = [exp.If(this=exp.Is(this=doc.copy(), expression=exp.Null()), true=exp.Null()),
                   exp.If(this=exp.Is(this=kind.copy(), expression=exp.Null()),
                          true=_fail(f"JSON_QUERY: the property at strict path '{path}' doesn't exist.")),
                   exp.If(this=nested, true=got)]
            default: exp.Expression = _fail(f"JSON_QUERY: the value at strict path '{path}' isn't an object or "
                                            "array. Use JSON_VALUE instead.")
        else:
            ifs, default = [exp.If(this=nested, true=got)], exp.Null()
        query.replace(exp.Case(ifs=ifs, default=default))
        notes.append(_JSON_QUERY_NOTE)
    return notes


def _refused_by_name(tree: exp.Expression) -> None:
    """Functions DuckDB has under the same name with another meaning (WR-203): each gave a different
    answer (HASHBYTES' hex text, the engine's user name) or DuckDB's own error. Refused by T-SQL's name."""
    if any(True for _ in tree.find_all(exp.MD5, exp.SHA, exp.SHA2)):  # T-SQL reads these only from HASHBYTES
        raise ValueError(_NOT_YET + "HASHBYTES isn't mapped yet: T-SQL gives the hash as bytes (VARBINARY), and "
                         "here it would be hex text, so comparisons and lengths would differ. Run it in a Fabric Warehouse.")
    user = any(True for _ in tree.find_all(exp.CurrentUser, exp.SessionUser)) or any(
        c.name.upper() == "USER" and not c.table and not c.this.args.get("quoted") for c in tree.find_all(exp.Column))
    if user:
        raise ValueError(_NOT_YET + "CURRENT_USER isn't mapped yet, and neither are USER, SESSION_USER, SYSTEM_USER, "
                         "SUSER_NAME and SUSER_SNAME: here they would give the local engine's user, not yours. "
                         "Run it in a Fabric Warehouse.")
    if any(True for _ in tree.find_all(exp.ObjectId)):
        raise ValueError(_NOT_YET + "OBJECT_ID isn't mapped yet: the local engine has no object ids. "
                         "Look a table up in INFORMATION_SCHEMA.TABLES.")
    if any(named.expressions or named.this for named in tree.find_all(exp.CurrentSchema)):
        raise ValueError(_NOT_YET + "SCHEMA_NAME of an id isn't mapped yet: the local engine has no schema ids. "
                         "SCHEMA_NAME() with no argument runs.")
    if any(True for _ in tree.find_all(exp.RegexpSubstr)):  # T-SQL's start and occurrence arguments differ
        raise ValueError(_NOT_YET + "REGEXP_SUBSTR isn't mapped yet. REGEXP_LIKE, REGEXP_REPLACE and REGEXP_COUNT run.")
    # DuckDB's GET_BIT and SET_BIT work on bit strings, not numbers
    if any(True for _ in tree.find_all(exp.Getbit)):
        raise ValueError(_NOT_YET + "GET_BIT isn't mapped yet. Use & with a power of 2, such as x & 4.")
    if any(a.name.upper() == "SET_BIT" for a in tree.find_all(exp.Anonymous)):
        raise ValueError(_NOT_YET + "SET_BIT isn't mapped yet. Use | with a power of 2, such as x | 4.")


def _unknown_functions(tree: exp.Expression, known: Container[str]) -> None:
    """A function DuckDB doesn't have is refused by its T-SQL name, not with DuckDB's catalog error."""
    for call in tree.find_all(exp.Anonymous):
        name = call.name.upper()
        if name and name not in known:
            raise ValueError(_NOT_YET + f"{name} isn't mapped yet.")
    if any(True for _ in tree.find_all(exp.CurrentTimestampLTZ)):
        raise ValueError(_NOT_YET + "SYSDATETIMEOFFSET isn't mapped yet. A warehouse's clock is UTC: "
                         "SYSUTCDATETIME() gives the same time without the offset.")


def _national_text(tree: exp.Expression) -> None:
    """An N'...' literal is nvarchar text. sqlglot reads it as exp.National, which the passes that type text
    (comparisons with ANSI padding, LIKE's [a-c] ranges, + joining two strings) didn't take for text. So
    ``WHERE col = N'abc'`` missed a row whose col is 'abc  ', ``LIKE N'[a-c]%'`` never matched, and
    ``N'a' + N'b'`` stopped with DuckDB's binder error. A warehouse keeps text as UTF-8 varchar, so here it is a
    plain string. DATALENGTH's own argument keeps its N, since it counts two bytes a character (_datalength)."""
    for node in list(tree.find_all(exp.National)):
        parent = node.parent
        if isinstance(parent, exp.Anonymous) and parent.name.upper() == "DATALENGTH":
            continue
        node.replace(exp.Literal.string(node.name))


def _datalength(value: exp.Expression) -> exp.Expr:
    """``DATALENGTH``: the bytes of the text as UTF-8, as a warehouse's varchar holds it; an N'...'
    literal is nvarchar, two bytes a character (UTF-16)."""
    if isinstance(value, exp.National):
        return exp.Literal.number(len(value.name.encode("utf-16-le")))
    if isinstance(value, exp.Literal) and not value.is_string:
        raise ValueError(_NOT_YET + "DATALENGTH runs here only on text: a number's size depends on its T-SQL type.")
    return exp.func("OCTET_LENGTH", exp.Anonymous(this="ENCODE", expressions=[value]))


def _with_ties(limit: exp.Limit) -> None:
    """``TOP n WITH TIES``: the first n rows by the ORDER BY and every row tied with the last of them,
    as a QUALIFY on their rank (DuckDB has no WITH TIES)."""
    select = limit.parent
    order = select.args.get("order") if isinstance(select, exp.Select) else None
    options = limit.args["limit_options"]
    if not isinstance(select, exp.Select) or order is None or options.args.get("percent") or select.args.get("distinct") or select.args.get("qualify"):
        raise ValueError(_NOT_YET + "TOP ... WITH TIES runs here only with an ORDER BY (as in T-SQL, which needs "
                         "one) and without DISTINCT or QUALIFY.")
    rank = exp.Window(this=exp.Anonymous(this="RANK", expressions=[]), order=order.copy())
    select.set("limit", None)
    select.set("qualify", exp.Qualify(this=exp.LTE(this=rank, expression=limit.expression)))


def _string_split(node: exp.Anonymous) -> None:
    """``STRING_SPLIT(text, separator)`` as a table of one ``value`` column, one row per part."""
    if len(node.expressions) == 3:  # enable_ordinal (WR-227): 0 or NULL leave the ordinal column off, as in T-SQL
        flag = node.expressions[2]
        if isinstance(flag, exp.Null) or (isinstance(flag, exp.Literal) and not flag.is_string and flag.name == "0"):
            node.set("expressions", node.expressions[:2])
        elif isinstance(flag, exp.Literal) and not flag.is_string and flag.name == "1":
            raise ValueError(_NOT_YET + "STRING_SPLIT with enable_ordinal 1 isn't mapped yet. Microsoft's page lists "
                             "the ordinal column for Azure SQL and SQL Server, not for a Fabric Warehouse. Leave "
                             "it out, or run it in a Fabric Warehouse to see whether it's there.")
        else:
            raise ValueError("STRING_SPLIT's enable_ordinal must be a constant 0 or 1, as in T-SQL.")
    if len(node.expressions) != 2:
        raise ValueError("STRING_SPLIT takes text, a separator of one character and, optionally, enable_ordinal.")
    separator = node.expressions[1]
    if isinstance(separator, exp.Null):
        raise ValueError(_NOT_YET + "STRING_SPLIT with a NULL separator isn't mapped yet. Microsoft's page doesn't "
                         "say what a warehouse returns. Give it a separator of one character.")
    if isinstance(separator, (exp.Literal, exp.National)) and len(separator.name) != 1:
        raise ValueError("STRING_SPLIT takes a separator of one character, as in T-SQL.")
    parts = exp.Unnest(expressions=[exp.func("STRING_SPLIT", *node.expressions)])
    query = exp.select(exp.alias_(parts, "value")).subquery()
    holder = node.parent
    if isinstance(holder, exp.Table):  # FROM STRING_SPLIT(...) [AS s]
        alias = holder.args.get("alias")
        query.set("alias", alias.copy() if alias else exp.TableAlias(this=exp.to_identifier("string_split")))
        holder.replace(query)
    elif isinstance(holder, exp.Lateral):  # CROSS / OUTER APPLY STRING_SPLIT(...) AS s
        node.replace(query)
    else:
        raise ValueError(_NOT_YET + "STRING_SPLIT runs here only in FROM or CROSS APPLY.")


def _whole(node: exp.Expression | None, low: int) -> bool:
    """A whole-number literal of at least ``low``: then the edge rules can't apply."""
    if not (isinstance(node, exp.Literal) and not node.is_string and node.name.isdigit()):
        return False
    return len(node.name.lstrip("0")) > 20 or int(node.name) >= low  # past 20 digits is past any int, and int()
    # of 4,300 digits or more would stop with Python's own error


def _number(node: exp.Expression | None) -> float | None:
    """The value of a number literal such as ``2``, ``-1`` or ``-0.5``; None for anything else."""
    sign = 1.0
    if isinstance(node, exp.Neg):
        node, sign = node.this, -1.0
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return sign * float(node.name)
        except ValueError:
            return None
    return None


def _string_edges(tree: exp.Expression) -> None:
    """Where DuckDB's string functions answer differently from T-SQL's at the edges, as T-SQL does:

    - ``SUBSTRING(s, start, n)`` with a start below 1 counts the positions before 1 and returns only
      what falls from 1 on (``SUBSTRING('abc', 0, 2)`` is ``'a'``); DuckDB counts a negative start
      from the end. A negative length is an error.
    - ``STUFF(s, start, n, x)`` is NULL when the start is below 1 or past the end, or the length is
      negative; a NULL ``x`` deletes without inserting.
    - ``REPLICATE(s, n)`` and ``SPACE(n)`` are NULL when ``n`` is negative, and a count with a fraction is
      cut toward zero first (``SPACE(1.7)`` is one space, ``SPACE(-0.5)`` is ''); DuckDB gave '' for a
      negative count, rounded ``SPACE``'s and didn't bind ``REPLICATE``'s.
    - ``LEFT`` and ``RIGHT`` with a negative length are an error (DuckDB drops from the other end).
    - ``TRANSLATE(s, a, b)`` with ``a`` and ``b`` of different lengths is an error (DuckDB drops the
      characters of ``a`` that have no partner in ``b``).

    - ``CHARINDEX(x, s, start)`` searches from the start when ``start`` is 0 or negative (DuckDB counted
      from the end), and is 0 for an empty ``x`` (DuckDB found it at 1) (WR-210).

    Literal starts and lengths that can't hit an edge are left as they are. The calls are rewritten innermost first,
    so a call inside another (``REPLICATE(REPLICATE(s, n), m)``) is rewritten too, not only copied by the outer one.
    Each argument the rewrite reads more than once is bound once (see _once), so nested calls grow the SQL in step,
    not twice or more a level."""
    for node in reversed(list(tree.find_all(exp.Substring, exp.Stuff, exp.Repeat, exp.Space, exp.Left, exp.Right,
                                            exp.Translate, exp.StrPosition))):
        if isinstance(node, exp.StrPosition):
            _charindex(node)
        elif isinstance(node, (exp.Left, exp.Right)):
            _left_right(node)
        elif isinstance(node, exp.Translate):
            _translate_lists(node)
        elif isinstance(node, exp.Substring):
            _substring(node)
        elif isinstance(node, exp.Stuff):
            _stuff(node)
        elif isinstance(node, (exp.Repeat, exp.Space)):
            _repeat(node)


# what a rewrite may copy freely: a column, a literal, or a cast or sign of one
_SMALL = (exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean, exp.National, exp.Neg, exp.Paren, exp.Cast,
          exp.Trunc, exp.DataType, exp.DataTypeParam, exp.Var)
_BOUND = "__kf_bound_args"  # a lambda parameter no user column is likely to share


def _small(node: exp.Expression | None) -> bool:
    if node is None:
        return True
    nodes = list(node.walk())
    return len(nodes) <= 12 and all(isinstance(n, _SMALL) for n in nodes)


def _once(values: list[exp.Expression | None], build: Callable[..., exp.Expression]) -> exp.Expression:
    """``build(*values)``, with each value that isn't small read once: when any isn't, all of those are bound in one
    struct, as LIST_TRANSFORM([STRUCT_PACK(a := ..., ...)], __kf_bound_args -> ...)[1], and ``build`` gets its .a and so
    on in their place. So a rewrite can name an argument twice without copying a call nested in it, and the lambda's
    body holds no subquery, which DuckDB doesn't allow there. ``build`` may copy what it gets freely."""
    if all(_small(v) for v in values):
        return build(*(v.copy() if v is not None else None for v in values))
    fields: list[exp.Expression] = []
    args: list[exp.Expression | None] = []
    for i, value in enumerate(values):
        if _small(value):
            args.append(value.copy() if value is not None else None)
            continue
        name = "abcdefgh"[i]
        assert value is not None
        fields.append(exp.PropertyEQ(this=exp.to_identifier(name), expression=value.copy()))
        args.append(exp.column(name, table=_BOUND))
    pack = exp.Anonymous(this="STRUCT_PACK", expressions=fields)
    bound = exp.func("LIST_TRANSFORM", exp.Array(expressions=[pack]),
                     exp.Lambda(this=build(*args), expressions=[exp.to_identifier(_BOUND)]))
    return exp.Bracket(this=bound, expressions=[exp.Literal.number(1)], offset=1)  # DuckDB lists count from 1


def _whole_count(count: exp.Expression) -> exp.Expression:
    """A count or length as T-SQL makes it an int: toward zero."""
    return count.copy() if _whole(count, 0) else exp.cast(exp.Trunc(this=count.copy()), "BIGINT")


def _left_right(node: exp.Left | exp.Right) -> None:
    """``LEFT`` and ``RIGHT``: a negative length is an error (see _string_edges)."""
    name = "LEFT" if isinstance(node, exp.Left) else "RIGHT"
    length = node.expression
    message = f"Invalid length parameter passed to the {name} function."
    value = _number(length)
    if value is not None:
        # T-SQL makes the length an integer first, toward zero: LEFT(s, -0.5) is ''.
        if int(value) < 0:
            raise ValueError(f"This T-SQL can't be run on the local engine: {message} T-SQL gives the same error.")
        if not _whole(length, 0):  # 2.0, 1e1, 2.7: DuckDB rounds or won't bind them
            node.set("expression", exp.Literal.number(int(value)))
        return
    if _whole(length, 0):
        return

    def build(text: exp.Expression, n: exp.Expression) -> exp.Expression:
        whole = _whole_count(n)
        call = type(node)(this=text, expression=whole.copy())
        negative = exp.LT(this=whole, expression=exp.Literal.number(0))
        return exp.Case(ifs=[exp.If(this=negative, true=exp.func("ERROR", exp.Literal.string(message)))], default=call)
    node.replace(_once([node.this, length], build))


def _translate_lists(node: exp.Translate) -> None:
    """``TRANSLATE``: lists of different lengths are an error (see _string_edges)."""
    source, target = node.args["from_"], node.args["to"]
    message = ("The second and third arguments of the TRANSLATE function must contain an equal number "
               "of characters.")
    if all(isinstance(x, exp.Literal) and x.is_string for x in (source, target)):
        if len(source.name) != len(target.name):
            raise ValueError(f"This T-SQL can't be run on the local engine: {message} T-SQL gives the same error.")
        return

    def build(text: exp.Expression, a: exp.Expression, b: exp.Expression) -> exp.Expression:
        differ = exp.NEQ(this=exp.func("LENGTH", a.copy()), expression=exp.func("LENGTH", b.copy()))
        return exp.Case(ifs=[exp.If(this=differ, true=exp.func("ERROR", exp.Literal.string(message)))],
                        default=exp.Translate(this=text, from_=a, to=b))
    node.replace(_once([node.this, source, target], build))


def _substring(node: exp.Substring) -> None:
    """``SUBSTRING`` at a start below 1 or with a negative length (see _string_edges)."""
    start, length = node.args.get("start"), node.args.get("length")
    if start is None or length is None or (_whole(start, 1) and _whole(length, 0)):
        return

    def build(text: exp.Expression, st: exp.Expression, n: exp.Expression) -> exp.Expression:
        cut: exp.Expression = exp.Substring(this=text, start=st.copy(), length=n.copy())
        if not _whole(start, 1):
            first = exp.func("GREATEST", st.copy(), exp.Literal.number(1))
            count = exp.func("GREATEST", exp.paren(st.copy() + n.copy() - first.copy(), copy=False),
                             exp.Literal.number(0))
            # GREATEST skips NULLs, so a NULL start or length is kept NULL first, as in T-SQL.
            unknown = exp.or_(exp.Is(this=st.copy(), expression=exp.null()),
                              exp.Is(this=n.copy(), expression=exp.null()))
            cut = exp.Case(ifs=[exp.If(this=unknown, true=exp.null())],
                           default=exp.Substring(this=text.copy(), start=first, length=count))
        if not _whole(length, 0):
            negative = exp.LT(this=n.copy(), expression=exp.Literal.number(0))
            error = exp.func("ERROR", exp.Literal.string("Invalid length parameter passed to the SUBSTRING function."))
            cut = exp.Case(ifs=[exp.If(this=negative, true=error)], default=cut)
        return cut
    node.replace(_once([node.this, start, length], build))


def _stuff(node: exp.Stuff) -> None:
    """``STUFF`` past the text's edges, or with a negative length or a NULL insert (see _string_edges)."""
    def build(text: exp.Expression, st: exp.Expression, n: exp.Expression, put: exp.Expression) -> exp.Expression:
        past = exp.or_(exp.LT(this=st.copy(), expression=exp.Literal.number(1)),
                       exp.GT(this=st.copy(), expression=exp.func("LENGTH", text.copy())),
                       exp.LT(this=n.copy(), expression=exp.Literal.number(0)))
        call = exp.Stuff(this=text, start=st, length=n,
                         expression=exp.func("COALESCE", put, exp.Literal.string("")))
        return exp.Case(ifs=[exp.If(this=past, true=exp.null())], default=call)
    node.replace(_once([node.this, node.args["start"], node.args["length"], node.expression], build))


_REPLICATE_CUT = ("REPLICATE gives more than {most} here, and a Fabric Warehouse cuts the result there, because "
                  "its input isn't {kind}(MAX). Cast the input to {kind}(MAX) to keep the whole result, as the "
                  "warehouse needs too, or use LEFT to cut it on purpose.")


def _replicate_inputs(tree: exp.Expression) -> None:
    """Mark each REPLICATE by its input as written: "max" for a cast or CONVERT to VARCHAR(MAX) or NVARCHAR(MAX),
    "national" for N'' text or a cast to NVARCHAR or NCHAR, else "text" (see _repeat). An outer REPLICATE of an
    inner one takes the inner one's kind, as a warehouse types the inner result."""
    for node in reversed(list(tree.find_all(exp.Repeat))):  # innermost first
        value = node.this
        if isinstance(value, exp.Repeat) and "kf_replicate" in value.meta:
            node.meta["kf_replicate"] = value.meta["kf_replicate"]
            continue
        kind = value.args.get("to") if isinstance(value, exp.Cast) else \
            value.this if isinstance(value, exp.Convert) else None
        if isinstance(kind, exp.DataType) and any(isinstance(p, exp.DataTypeParam) and p.name.upper() == "MAX"
                                                  for p in kind.expressions):
            node.meta["kf_replicate"] = "max"
        elif isinstance(value, exp.National) or (isinstance(kind, exp.DataType) and kind.this in (
                exp.DataType.Type.NVARCHAR, exp.DataType.Type.NCHAR)):
            node.meta["kf_replicate"] = "national"


def _repeat(node: exp.Repeat | exp.Space) -> None:
    """``REPLICATE(s, n)`` and ``SPACE(n)``: the count cut toward zero and NULL when negative (#2978), and REPLICATE
    refused at the row where T-SQL would cut the result at 8,000 bytes (4,000 characters of N'' text) because ``s``
    isn't VARCHAR(MAX) or NVARCHAR(MAX) (WR-235)."""
    replicate = isinstance(node, exp.Repeat)
    key = "times" if replicate else "this"
    count = node.args.get(key)
    if count is None:
        return
    # REPLICATE takes any integer type, bigint included; SPACE takes an int (functions/replicate-transact-sql,
    # functions/space-transact-sql). A negative count is NULL for both, as SQL Server answers it.
    width = "bigint" if replicate else "int"
    value = _number(count)
    if not _whole(count, 0) and value is not None and math.isfinite(value) and int(value) >= 0:
        count = exp.Literal.number(_in_range(int(value), width))  # 1.7 is 1 and -0.5 is 0: toward zero, as T-SQL
        node.set(key, count)
    elif _whole(count, 0):
        _in_range(_digits(count.name, width), width)
    elif value is not None and not math.isfinite(value):  # 1e400, or more digits than a float holds
        _in_range(_INFINITE, width)
    kind = node.meta.get("kf_replicate", "text") if replicate else "max"
    if _whole(count, 0) and kind == "max":
        return
    national = kind == "national"
    most = 4000 if national else 8000

    if not _whole(count, 0):
        # #3007: text is a whole number or an error, a number is cut toward zero. _int_of names its value several
        # times, so the count is read once first: RAND() is one value, and a nested call isn't copied.
        count = _once([count], lambda c: _int_of(c, width))

    def build(n: exp.Expression, text: exp.Expression | None) -> exp.Expression:
        whole = n
        ifs = []
        if not _whole(n, 0):
            if not replicate:  # a bigint past an int; a REPLICATE count past a bigint stops in _int_of's cast
                ifs.append(_past(whole, width))
            ifs.append(exp.If(this=exp.LT(this=whole.copy(), expression=exp.Literal.number(0)), true=exp.null()))
        if text is not None and kind != "max" and not _known_within(text, n, national, most):
            one = exp.func("LENGTH", text.copy()) if national else \
                exp.Anonymous(this="STRLEN", expressions=[exp.cast(text.copy(), "VARCHAR")])
            size = exp.Mul(this=exp.cast(one, "DOUBLE"), expression=whole.copy())  # DOUBLE: a huge count can't overflow
            message = _REPLICATE_CUT.format(most=f"{most:,} {'characters' if national else 'bytes'}",
                                            kind="NVARCHAR" if national else "VARCHAR")
            ifs.append(exp.If(this=exp.GT(this=size, expression=exp.Literal.number(most)),
                              true=exp.func("ERROR", exp.Literal.string(_NOT_YET + message))))
        call = exp.Repeat(this=text, times=whole) if text is not None else exp.Space(this=whole)
        return exp.Case(ifs=ifs, default=call) if ifs else call
    node.replace(_once([count, node.this if replicate else None], build))


def _known_within(text: exp.Expression, n: exp.Expression, national: bool, most: int) -> bool:
    """A literal input repeated a literal number of times, within the limit: no check at run time is needed."""
    literal = text.this if isinstance(text, exp.National) else text
    if not (isinstance(literal, exp.Literal) and literal.is_string and _whole(n, 0)):
        return False
    size = len(literal.name) if national else len(literal.name.encode("utf-8"))
    return size * int(n.name) <= most


_INT_TEXT = re.compile(r"\s*([+-]?[0-9]+)?\s*")  # '' and spaces alone are 0, as T-SQL converts them
_INFINITE = 2 ** 64  # a literal past a float: past any int too
_RANGES = {"int": (-2 ** 31, 2 ** 31 - 1), "bigint": (-2 ** 63, 2 ** 63 - 1)}


def _digits(text: str, size: str) -> int:
    """A whole number's text as an int; T-SQL's overflow error past 20 digits, before Python's int() reads it
    (it stops at 4,300 digits with an error of its own)."""
    body = text.strip().lstrip("+-").lstrip("0")
    if len(body) > 20:
        raise ValueError(f"{_NOT_YET}{_OVERFLOW.format(kind=size)} T-SQL gives the same error.")
    return int(text.strip() or "0")


def _in_range(value: int, size: str) -> int:
    """``value``, or T-SQL's overflow error when an int or a bigint (``size``) can't hold it."""
    low, high = _RANGES[size]
    if not low <= value <= high:
        raise ValueError(f"{_NOT_YET}{_OVERFLOW.format(kind=size)} T-SQL gives the same error.")
    return value


def _past(value: exp.Expression, size: str) -> exp.If:
    """The CASE branch that stops with T-SQL's overflow error when ``value`` is past an int or a bigint."""
    low, high = _RANGES[size]
    outside = exp.Not(this=exp.Between(this=value.copy(), low=exp.Literal.number(low), high=exp.Literal.number(high)))
    return exp.If(this=outside, true=exp.func("ERROR", exp.Literal.string(_OVERFLOW.format(kind=size))))


def _int_of(value: exp.Expression, size: str = "int") -> exp.Expression:
    """``value`` made an int as T-SQL makes a count or code one: a number is cut toward zero, and text
    must be a whole number (``'66'`` is 66, ``'6.5'`` is a conversion error, as in T-SQL; DuckDB's cast
    would round it). A column's type isn't known here, so both ways are written and DuckDB keeps the
    one its type takes; each reads the value through VARCHAR so that both bind whatever the type."""
    if isinstance(value, exp.Literal) and value.is_string:
        if not _INT_TEXT.fullmatch(value.name):
            raise ValueError(f"{_NOT_YET}{_not_whole(value.name, size)} T-SQL gives the same error.")
        return exp.Literal.number(_in_range(_digits(value.name, size), size))
    text = exp.cast(value.copy(), "VARCHAR")
    if size == "int":
        message: exp.Expression = exp.DPipe(
            this=exp.DPipe(this=exp.Literal.string("Conversion failed when converting the varchar value '"),
                           expression=text.copy()),
            expression=exp.Literal.string("' to data type int."))
    else:
        message = exp.Literal.string(_not_whole("", size))
    whole = exp.func("COALESCE", exp.Nullif(this=exp.func("TRIM", text.copy()), expression=exp.Literal.string("")),
                     exp.Literal.string("0"))
    strict = exp.Case(ifs=[exp.If(this=exp.func("REGEXP_FULL_MATCH", text.copy(), exp.Literal.string(_INT_TEXT.pattern)),
                                  true=exp.cast(whole, "BIGINT"))],
                      default=exp.func("ERROR", message))
    is_text = exp.EQ(this=exp.func("TYPEOF", value.copy()), expression=exp.Literal.string("VARCHAR"))
    cut = exp.cast(exp.Trunc(this=exp.cast(text.copy(), "DOUBLE")), "BIGINT")
    return exp.Case(ifs=[exp.If(this=is_text, true=strict)], default=cut)


def _not_whole(text: str, size: str) -> str:
    """T-SQL's error for text that isn't a whole number, made an int or a bigint."""
    if size == "int":
        return f"Conversion failed when converting the varchar value '{text}' to data type int."
    return f"Error converting data type varchar to {size}."


def _char_codes(tree: exp.Expression) -> None:
    """ASCII, UNICODE and CHAR at their edges, as T-SQL answers them:

    - ``ASCII('')`` and ``UNICODE('')`` are NULL (DuckDB gave 0 and -1).
    - ``CHAR(n)`` is NULL outside 0 to 255 ("CHAR returns a NULL value for integer expressions outside this
      input range", functions/char-transact-sql), and a count with a fraction is made an int first, toward
      zero (DuckDB gave the code point, stopped below 0, and didn't bind a decimal)."""
    for node in reversed(list(tree.find_all(exp.Ascii, exp.Unicode, exp.Chr))):  # deepest first: see _once
        if isinstance(node, (exp.Ascii, exp.Unicode)):
            text = node.this
            if not (isinstance(text, exp.Literal) and text.is_string and text.name):
                node.set("this", exp.Nullif(this=text.copy(), expression=exp.Literal.string("")))
            continue
        if len(node.expressions) != 1:
            continue
        code = node.expressions[0]
        value = _number(code)
        if value is not None:
            whole = _in_range(int(value) if math.isfinite(value) else _INFINITE, "int")
            node.replace(exp.Chr(expressions=[exp.Literal.number(whole)]) if 0 <= whole <= 255
                         else exp.cast(exp.null(), "TEXT"))
            continue
        # The code is read once, and then its int once, so CHAR(RAND() * 300) draws one number, checks and
        # converts that one, and a call nested in the code is written once.
        node.replace(_once([code], lambda c: _once([_int_of(c)], _char_of)))


def _char_of(v: exp.Expression) -> exp.Expression:
    """``CHAR`` of the int ``v``: T-SQL's overflow error past an int, NULL outside 0 to 255."""
    inside = exp.Between(this=v.copy(), low=exp.Literal.number(0), high=exp.Literal.number(255))
    return exp.Case(ifs=[_past(v, "int"), exp.If(this=inside, true=exp.Chr(expressions=[exp.cast(v.copy(), "INT")]))])


def _charindex(node: exp.StrPosition) -> None:
    """``CHARINDEX`` as T-SQL answers it at its edges (see _string_edges)."""
    start, find, text = node.args.get("position"), node.args["substr"], node.this
    value = _number(start)
    if start is not None and value is not None and value <= 1:
        node.set("position", None)  # 0, negative or 1: from the start
        start = None
    plain_find = isinstance(find, exp.Literal) and find.is_string and find.name
    if plain_find and (start is None or value is not None or isinstance(start, exp.Null)):
        return

    def build(t: exp.Expression, f: exp.Expression, st: exp.Expression | None) -> exp.Expression:
        position = st
        if st is not None and value is None and not isinstance(start, exp.Null):
            # A NULL start keeps the answer NULL, as GREATEST alone (which skips NULLs) wouldn't.
            position = exp.Case(ifs=[exp.If(this=exp.Is(this=st.copy(), expression=exp.null()), true=exp.null())],
                                default=exp.func("GREATEST", st.copy(), exp.Literal.number(1)))
        call = exp.StrPosition(this=t.copy(), substr=f.copy(), position=position)
        if plain_find:
            return call
        empty = exp.EQ(this=exp.func("LENGTH", f.copy()), expression=exp.Literal.number(0))
        unknown = exp.Is(this=t.copy(), expression=exp.null())
        return exp.Case(ifs=[exp.If(this=unknown, true=exp.null()), exp.If(this=empty, true=exp.Literal.number(0))],
                        default=call)
    node.replace(_once([text, find, start], build))


_DATEDIFF_OVERFLOW = ("The datediff function resulted in an overflow. The number of dateparts separating two "
                      "date/time instances is too large. Try to use datediff with a less precise datepart.")


def _int_datediff(node: exp.DateDiff) -> None:
    """``DATEDIFF`` is an int in T-SQL, and a count past an int's range is an error there (WR-208:
    ``DATEDIFF(millisecond, ...)`` over more than 24 days), where DuckDB gives a BIGINT. ``DATEDIFF_BIG``
    keeps the BIGINT."""
    fits = exp.Between(this=node.copy(), low=exp.Literal.number(-(2 ** 31)), high=exp.Literal.number(2 ** 31 - 1))
    whole = exp.Case(ifs=[exp.If(this=fits, true=exp.cast(node.copy(), "INT"))],
                     default=exp.cast(exp.Anonymous(this="error", expressions=[exp.Literal.string(_DATEDIFF_OVERFLOW)]),
                                      "INT"))
    node.replace(whole)


def _sunday_weeks(node: exp.DateDiff, shift: int = 1) -> None:
    """``DATEDIFF(week, a, b)`` counts the Sundays crossed from ``a`` to ``b``, whatever the time of day
    (DATEFIRST doesn't change it); DuckDB's counts whole weeks, so Saturday to Sunday was 0, not 1. A
    day later each Sunday is a Monday, which starts DuckDB's weeks: count the days between those.
    ``shift=0`` counts Mondays, for ``iso_week``."""
    def monday(day: exp.Expression) -> exp.Expression:
        shifted = exp.Add(this=exp.cast(day.copy(), "DATE"), expression=exp.Literal.number(shift))
        return exp.Anonymous(this="DATE_TRUNC", expressions=[exp.Literal.string("week"), shifted])

    days = exp.Anonymous(this="DATE_DIFF", expressions=[exp.Literal.string("day"), monday(node.expression),
                                                        monday(node.this)])
    node.replace(exp.paren(exp.IntDiv(this=days, expression=exp.Literal.number(7)), copy=False))


# T-SQL's date parts by every name it takes (sqlglot reads some as other dialects do: Y as year,
# W as week, M in DATETRUNC as minute), and the functions each may be used with.
_DATEPARTS = {name: part for part, names in {
    "YEAR": ("YEAR", "YY", "YYYY"), "QUARTER": ("QUARTER", "QQ", "Q"), "MONTH": ("MONTH", "MM", "M"),
    "DAYOFYEAR": ("DAYOFYEAR", "DY", "Y"), "DAY": ("DAY", "DD", "D"), "WEEK": ("WEEK", "WK", "WW"),
    "WEEKDAY": ("WEEKDAY", "DW", "W"), "HOUR": ("HOUR", "HH"), "MINUTE": ("MINUTE", "MI", "N"),
    "SECOND": ("SECOND", "SS", "S"), "MILLISECOND": ("MILLISECOND", "MS"), "MICROSECOND": ("MICROSECOND", "MCS"),
    "NANOSECOND": ("NANOSECOND", "NS"), "ISO_WEEK": ("ISO_WEEK", "ISOWK", "ISOWW"),
}.items() for name in names}
_TIME_PARTS = {"HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND", "NANOSECOND"}
_TAKES = {
    "DATEADD": set(_DATEPARTS.values()) - {"ISO_WEEK", "NANOSECOND"},
    "DATEDIFF": set(_DATEPARTS.values()) - {"NANOSECOND"},
    "DATETRUNC": set(_DATEPARTS.values()) - {"WEEKDAY", "NANOSECOND"},
}
# What each T-SQL part is in DuckDB: day of year and weekday count in days.
_DUCKDB_PART = {"DAYOFYEAR": "DAY", "WEEKDAY": "DAY", "ISO_WEEK": "WEEK"}


def _datepart(node: exp.Expression, function: str) -> str:
    """The T-SQL date part of ``node``, which is set to DuckDB's name for it. Raises ValueError for one T-SQL
    doesn't take there, and for nanoseconds, which the local engine doesn't keep."""
    unit = node.args.get("unit")
    name = unit.name.upper() if unit is not None else "DAY"
    part = _DATEPARTS.get(name)
    if part == "NANOSECOND":
        raise ValueError(f"{function} with nanosecond can't be run on the local engine: its times are kept to the "
                         "microsecond.")
    if part not in _TAKES[function]:
        raise ValueError(f"{function} doesn't take the date part {name.lower()}.")
    node.set("unit", exp.var(_DUCKDB_PART.get(part, part)))
    return part


def _stays_a_date(node: exp.Expression) -> None:
    """``DATEADD`` and ``DATETRUNC`` on a date give a date, as in T-SQL (DuckDB gives a timestamp). A time
    part on a date is an error there too."""
    unit = node.args.get("unit")
    name = unit.name.upper() if unit is not None else "DAY"
    if _DATEPARTS.get(name) in _TIME_PARTS:
        function = "DATEADD" if isinstance(node, exp.DateAdd) else "DATETRUNC"
        raise ValueError(f"{function} doesn't take the date part {name.lower()} for a date.")
    wrapper = exp.Cast(to=exp.DataType.build("DATE"))
    node.replace(wrapper)
    wrapper.set("this", node)  # the same node, so a later step still finds what's inside it


def _sunday_week_start(node: exp.TimestampTrunc) -> None:
    """``DATETRUNC(week, d)`` starts weeks on Sunday (DATEFIRST 7); DuckDB's start on Monday."""
    day = exp.Interval(this=exp.Literal.string("1"), unit=exp.var("DAY"))
    node.set("this", exp.Add(this=node.this, expression=day))
    wrapper = exp.Sub(expression=day.copy())
    node.replace(wrapper)
    wrapper.set("this", node)


def _first_line(exc: Any) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
