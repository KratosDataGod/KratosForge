"""Local T-SQL scripts with variables and control flow (WR-705).

A script that declares or reads a variable, or uses IF, WHILE, BEGIN ... END, TRY ... CATCH, PRINT, THROW, RAISERROR,
RETURN, BREAK, CONTINUE, @@ROWCOUNT or ERROR_NUMBER() and the like, runs here statement by statement, as a batch
runs in a Fabric Warehouse.

How it works:

- Each batch (the text between GO lines) is read by a small block parser over sqlglot's T-SQL tokens. It finds
  where each statement and each expression ends, and hands every ordinary statement (SELECT, INSERT, UPDATE, DELETE,
  MERGE, CREATE ...) to the translator in tsql.py as the user wrote it.
- Before anything runs, the whole script is checked: each statement is translated with every variable as a typed
  NULL, so a refusal, an undeclared variable (Msg 137) or a variable declared twice (Msg 134) stops the script with
  none of it run, as the translator's own refusals do (WR-238).
- As the script runs, a variable is put into a statement as ``CAST(<literal> AS <declared type>)``, built as sqlglot
  nodes (never as pasted text), and the statement is translated again. An assignment is worked out by running
  ``SELECT CAST(<expression> AS <declared type>)`` through the same translator and DuckDB, so T-SQL's conversions
  (VARCHAR(3) cutting text short, INT dropping a fraction, 245 for text that isn't a number, 8115 for overflow)
  are the translator's, once.
- PRINT and RAISERROR of severity 10 or less add a line to the run's messages; the engine returns them beside the
  result. The result is the last ordinary statement's, as for any script.

See docs/sql-compatibility.md, "Local T-SQL variables and control flow".
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import time
import uuid
from collections.abc import Callable, Container
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

from . import tsql

_T = exp.DataType.Type


class _Tokenizer(tsql.KfTSQL.Tokenizer):
    # sqlglot reads END (and EXEC, FETCH ...) after a ; or BEGIN as a command and swallows the rest of the
    # statement as one string: "BEGIN TRY ...; END TRY BEGIN CATCH ..." lost its CATCH. Here every word is a token.
    COMMANDS = set()


_NOT_WORDS = {getattr(TokenType, n) for n in ("STRING", "NATIONAL_STRING", "IDENTIFIER", "HEX_STRING", "BIT_STRING",
                                               "BYTE_STRING", "RAW_STRING", "NUMBER", "HEREDOC_STRING",
                                               "UNICODE_STRING") if hasattr(TokenType, n)}
# Words that always start a statement of their own: T-SQL reserves them, so none is a name or a part of another
_HARD = {"DECLARE", "WHILE", "BEGIN", "PRINT", "RAISERROR", "RETURN", "BREAK", "CONTINUE", "GOTO", "WAITFOR",
         "EXEC", "EXECUTE", "COMMIT", "ROLLBACK", "SAVE", "USE", "OPEN", "CLOSE", "DEALLOCATE", "CHECKPOINT"}
# Words that start a statement unless what comes before makes them a part of the statement being read
_SOFT = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "SET", "CREATE", "DROP", "ALTER", "TRUNCATE", "WITH", "IF",
         "FETCH"}
# DROP TABLE IF EXISTS: the IF of a DROP follows the kind of object
_OBJECTS = {"TABLE", "VIEW", "SCHEMA", "PROCEDURE", "PROC", "FUNCTION", "INDEX", "STATISTICS", "DATABASE", "TRIGGER",
            "TYPE", "SEQUENCE", "SYNONYM", "ROLE", "USER", "COLUMN", "CONSTRAINT", "EXTERNAL", "LOGIN"}
_COMPOUND = {TokenType.PLUS: "+", TokenType.DASH: "-", TokenType.STAR: "*", TokenType.SLASH: "/", TokenType.MOD: "%",
             TokenType.AMP: "&", TokenType.PIPE: "|", TokenType.CARET: "^"}
_ERROR_TYPES = {"ERROR_NUMBER": "INT", "ERROR_SEVERITY": "INT", "ERROR_STATE": "INT", "ERROR_LINE": "INT",
                "ERROR_MESSAGE": "NVARCHAR(4000)", "ERROR_PROCEDURE": "NVARCHAR(128)"}
# A name that isn't there is found when SQL Server compiles or recompiles the statement, so TRY doesn't catch it at
# the same level (Microsoft's TRY...CATCH page): the batch stops.
_NOT_CAUGHT = {207, 208, 209}
_MSG = re.compile(r"Msg (\d+), Level (\d+): (.*)", re.DOTALL)
_REFUSED_TAIL = "Run it in a Fabric Warehouse."
_VARIABLE_TYPES = {_T.INT, _T.BIGINT, _T.SMALLINT, _T.UTINYINT, _T.TINYINT, _T.BIT, _T.BOOLEAN, _T.DECIMAL,
                   _T.DOUBLE, _T.FLOAT, _T.CHAR, _T.VARCHAR, _T.NCHAR, _T.NVARCHAR, _T.DATE, _T.TIME, _T.DATETIME,
                   _T.DATETIME2, _T.SMALLDATETIME, _T.UUID}
_TEXTS = {_T.CHAR, _T.VARCHAR, _T.NCHAR, _T.NVARCHAR}
_NATIONAL = {_T.NCHAR, _T.NVARCHAR}
_PRINT_MOST = 8000  # PRINT shows at most 8,000 bytes of varchar (4,000 characters of nvarchar)
_RAISERROR_MOST = 2047  # RAISERROR's message, after its arguments are put in


def _refused(what: str, advice: str = _REFUSED_TAIL) -> str:
    return f"The local engine doesn't run T-SQL {what} yet. {advice}"


def _compile_error(number: int, text: str) -> str:
    """A compile error, which stops the batch before it runs: SQL Server grades these 15."""
    return f"Msg {number}, Level 15: {text}"


# The script, read


@dataclass
class Span:
    """Text of the batch, from ``start`` to ``end`` (inclusive), with the first token's line in the user's text."""

    start: int
    end: int
    line: int


@dataclass
class Node:
    place: int  # from 0, in the order the user wrote the statements; -1 for a block
    kind: str  # the statement's first word, such as IF or SELECT
    line: int


@dataclass
class Leaf(Node):
    span: Span
    trees: list[Any] = field(default_factory=list)  # compiled: each a _Statement


@dataclass
class DeclareItem:
    name: str
    written: str  # the name as the user wrote it, with its @
    type_span: Span
    init: Span | None
    to: Any = None  # compiled: the declared exp.DataType
    value: Any = None  # compiled: the initialiser's template


@dataclass
class Declare(Node):
    items: list[DeclareItem] = field(default_factory=list)


@dataclass
class SetVar(Node):
    name: str = ""
    written: str = ""
    op: str = "="
    expr: Span | None = None
    value: Any = None


@dataclass
class Print(Node):
    expr: Span | None = None
    value: Any = None


@dataclass
class Throw(Node):
    args: list[Span] = field(default_factory=list)  # none: THROW; again in CATCH
    values: list[Any] = field(default_factory=list)


@dataclass
class Raiserror(Node):
    args: list[Span] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)


@dataclass
class Jump(Node):
    """RETURN, BREAK or CONTINUE (``kind``)."""


@dataclass
class Refused(Node):
    """A statement that is refused by name; the refusal is raised in its place when the script is checked."""

    message: str = ""


@dataclass
class Block(Node):
    body: list[Node] = field(default_factory=list)


@dataclass
class If(Node):
    cond: Span | None = None
    then: Node | None = None
    otherwise: Node | None = None
    value: Any = None


@dataclass
class While(Node):
    cond: Span | None = None
    body: Node | None = None
    value: Any = None


@dataclass
class Try(Node):
    body: Block | None = None
    catch: Block | None = None


class ScriptSyntaxError(ValueError):
    """The batch can't be read: which statement it is isn't known, so the batch is named."""


class _Parser:
    """Statements and blocks over one batch's tokens. ``text`` is the user's whole script up to and including the
    batch, with every earlier batch blanked out (its line breaks kept), so a token's line is the user's line."""

    def __init__(self, text: str, tokens: list[Token], counter: list[int]) -> None:
        self.text, self.toks, self.counter = text, tokens, counter
        self.words = []
        for i, tok in enumerate(tokens):
            before = tokens[i - 1].token_type if i else None
            plain = tok.token_type not in _NOT_WORDS and before not in (TokenType.DOT, TokenType.PARAMETER)
            self.words.append(tok.text.upper() if plain else "")
        self.i = 0

    # tokens

    def word(self, k: int = 0) -> str:
        j = self.i + k
        return self.words[j] if 0 <= j < len(self.toks) else ""

    def kind(self, k: int = 0) -> TokenType | None:
        j = self.i + k
        return self.toks[j].token_type if 0 <= j < len(self.toks) else None

    def at_end(self) -> bool:
        return self.i >= len(self.toks)

    def line(self, j: int | None = None) -> int:
        j = self.i if j is None else j
        return self.toks[min(j, len(self.toks) - 1)].line if self.toks else 1

    def near(self, j: int | None = None) -> str:
        j = self.i if j is None else j
        return self.toks[j].text if j < len(self.toks) else "the end of the batch"

    def syntax(self, j: int | None = None) -> ScriptSyntaxError:
        j = self.i if j is None else j
        near = self.near(j)
        where = f"'{near}'" if j < len(self.toks) else near
        return ScriptSyntaxError(f"Could not read the query as T-SQL: incorrect syntax near {where} on line "
                                 f"{self.line(j)}.")

    def span(self, a: int, b: int) -> Span:
        """Tokens a to b - 1."""
        if b <= a:
            raise self.syntax(a)
        return Span(self.toks[a].start, self.toks[b - 1].end, self.toks[a].line)

    def place(self) -> int:
        self.counter[0] += 1
        return self.counter[0] - 1

    def skip_semicolons(self) -> None:
        while self.kind() == TokenType.SEMICOLON:
            self.i += 1

    # extents

    def _ends_expression(self, j: int, case: int) -> bool:
        w = self.words[j]
        if w in ("END", "ELSE") and case == 0:
            return True
        if w == "THROW":
            nxt = self.toks[j + 1].token_type if j + 1 < len(self.toks) else None
            return nxt in (None, TokenType.NUMBER, TokenType.PARAMETER, TokenType.SEMICOLON)
        return w in _HARD or (w in _SOFT and w != "FETCH")

    def expression_end(self, j: int, comma: bool = False) -> int:
        """Where the expression that starts at token ``j`` ends (exclusive): at a ``;``, a statement's first word, the
        END or ELSE of a block, or (``comma``) a comma, outside brackets and CASE."""
        depth = case = 0
        start = j
        while j < len(self.toks):
            tok = self.toks[j]
            t = tok.token_type
            if depth == 0 and j > start:
                if t == TokenType.SEMICOLON or (comma and t == TokenType.COMMA) or self._ends_expression(j, case):
                    break
            if t == TokenType.L_PAREN:
                depth += 1
            elif t == TokenType.R_PAREN:
                depth -= 1
                if depth < 0:
                    break
            elif t == TokenType.CASE:
                case += 1
            elif t == TokenType.END and case:
                case -= 1
            elif depth == 0 and j == start and (t == TokenType.SEMICOLON or (comma and t == TokenType.COMMA)):
                break
            j += 1
        return j

    def leaf_end(self, j: int) -> int:
        """Where the ordinary statement that starts at token ``j`` ends (exclusive)."""
        first = self.words[j]
        main = "" if first == "WITH" else first
        seen_query = seen_set = False
        depth = case = 0
        start = j
        while j < len(self.toks):
            tok = self.toks[j]
            t = tok.token_type
            if depth == 0 and j > start:
                if t == TokenType.SEMICOLON:
                    break
                w = self.words[j]
                prev = self.words[j - 1]
                if w in ("END", "ELSE") and case == 0:
                    break
                if w in _HARD:
                    break
                if w in _SOFT and not self._continues(w, prev, first, main, seen_query, seen_set, j):
                    break
                if w == "SELECT":
                    if first == "INSERT" or main == "INSERT":
                        seen_query = True
                    if not main:
                        main = "SELECT"
                elif w == "VALUES":
                    seen_query = True
                elif w in ("INSERT", "UPDATE", "DELETE", "MERGE") and not main:
                    main = w
                elif w == "SET":
                    seen_set = True
            if t == TokenType.L_PAREN:
                depth += 1
            elif t == TokenType.R_PAREN:
                depth -= 1
                if depth < 0:
                    break
            elif t == TokenType.CASE:
                case += 1
            elif t == TokenType.END and case:
                case -= 1
            j += 1
        return j

    @staticmethod
    def _continues(w: str, prev: str, first: str, main: str, seen_query: bool, seen_set: bool, j: int) -> bool:
        """``w`` (at depth 0, after ``prev``) is part of the statement that began with ``first``."""
        if w == "WITH":
            return True  # T-SQL wants a ; before a CTE, so a WITH here is a hint, TIES or a table's options
        if w == "IF":
            return prev in _OBJECTS
        if w == "FETCH":
            return prev in ("ROW", "ROWS")
        if w == "SELECT":
            return prev in ("UNION", "ALL", "EXCEPT", "INTERSECT", "AS", "DISTINCT") or \
                ((first == "INSERT" or main == "INSERT") and not seen_query) or (first == "WITH" and not main)
        if w in ("INSERT", "UPDATE", "DELETE"):
            return prev in ("THEN", "ON", "FOR") or (first == "WITH" and not main)
        if w == "MERGE":
            return first == "WITH" and not main
        if w == "SET":
            return (main == "UPDATE" and not seen_set) or prev == "UPDATE" or first == "ALTER"
        if w in ("DROP", "ALTER"):
            return first == "ALTER"
        return False

    # statements

    def batch(self) -> list[Node]:
        body: list[Node] = []
        while True:
            self.skip_semicolons()
            if self.at_end():
                return body
            if self.word() in ("END", "ELSE"):
                raise self.syntax()
            body.append(self.statement())

    def required(self) -> Node:
        self.skip_semicolons()
        if self.at_end() or self.word() in ("END", "ELSE"):
            raise self.syntax()
        return self.statement()

    def statement(self) -> Node:
        w, line = self.word(), self.line()
        nxt = self.word(1)
        if w == "BEGIN":
            if nxt == "TRY":
                return self.try_block(line)
            if nxt in ("TRAN", "TRANSACTION", "DISTRIBUTED"):
                return self.refused("BEGIN TRAN", _refused("transactions (BEGIN TRAN)"))
            self.i += 1
            block = Block(-1, "BEGIN", line, self.until_end())
            self.i += 1  # END
            return block
        if w == "IF":
            node = If(self.place(), "IF", line)
            self.i += 1
            end = self.expression_end(self.i)
            node.cond = self.span(self.i, end)
            self.i = end
            node.then = self.required()
            self.skip_semicolons()
            if self.word() == "ELSE":
                self.i += 1
                node.otherwise = self.required()
            return node
        if w == "WHILE":
            node2 = While(self.place(), "WHILE", line)
            self.i += 1
            end = self.expression_end(self.i)
            node2.cond = self.span(self.i, end)
            self.i = end
            node2.body = self.required()
            return node2
        if w in ("BREAK", "CONTINUE"):
            self.i += 1
            return Jump(self.place(), w, line)
        if w == "RETURN":
            node3 = Jump(self.place(), w, line)
            self.i += 1
            if not self.at_end() and self.kind() != TokenType.SEMICOLON and self.word() not in _HARD | _SOFT | {
                    "END", "ELSE", "THROW"}:
                return Refused(node3.place, w, line, _compile_error(
                    178, "A RETURN statement with a return value cannot be used in this context."))
            return node3
        if w == "DECLARE":
            return self.declare(line)
        if w == "SET" and self.kind(1) == TokenType.PARAMETER and self.kind(2) != TokenType.PARAMETER:
            return self.set_variable(line)
        if w == "PRINT":
            node4 = Print(self.place(), w, line)
            self.i += 1
            end = self.expression_end(self.i)
            node4.expr = self.span(self.i, end)
            self.i = end
            return node4
        if w == "THROW":
            return self.throw(line)
        if w == "RAISERROR":
            return self.raiserror(line)
        if w == "GOTO":
            return self.refused(w, _refused("GOTO"))
        if self.kind() == TokenType.VAR and self.kind(1) == TokenType.COLON and w:
            return self.refused("LABEL", _refused(f"labels ({self.toks[self.i].text}:)"))
        if w == "WAITFOR":
            return self.refused(w, _refused("WAITFOR"))
        if w in ("OPEN", "CLOSE", "FETCH", "DEALLOCATE"):
            return self.refused(w, _refused(f"cursors ({w})"))
        if w in ("COMMIT", "ROLLBACK", "SAVE"):
            label = "SAVE TRANSACTION" if w == "SAVE" else w
            return self.refused(w, _refused(f"transactions ({label})"))
        if w == "SET" and nxt in ("TRAN", "TRANSACTION"):
            return self.refused(w, _refused("SET TRANSACTION"))
        if w == "CHECKPOINT":
            return self.refused(w, _refused("CHECKPOINT"))
        return self.leaf(line)

    def refused(self, kind: str, message: str) -> Refused:
        node = Refused(self.place(), kind, self.line(), message)
        # past the statement, so the rest of the batch is still counted
        self.i = max(self.leaf_end(self.i), self.i + 1)
        return node

    def leaf(self, line: int) -> Leaf:
        end = self.leaf_end(self.i)
        node = Leaf(self.place(), self.word() or self.near().upper(), line, self.span(self.i, end))
        self.i = end
        return node

    def until_end(self) -> list[Node]:
        body: list[Node] = []
        while True:
            self.skip_semicolons()
            if self.at_end():
                raise self.syntax()
            if self.word() == "END":
                if not body:
                    raise self.syntax()  # BEGIN END with nothing in it doesn't read in T-SQL
                return body
            if self.word() == "ELSE":
                raise self.syntax()
            body.append(self.statement())

    def try_block(self, line: int) -> Try:
        self.i += 2  # BEGIN TRY
        body = Block(-1, "TRY", line, self.until_end())
        if self.word(1) != "TRY":
            raise self.syntax(self.i + 1)
        self.i += 2
        self.skip_semicolons()
        if self.word() != "BEGIN" or self.word(1) != "CATCH":
            raise self.syntax()
        catch_line = self.line()
        self.i += 2
        self.skip_semicolons()
        catch: list[Node] = []
        if self.word() != "END":  # an empty CATCH reads in T-SQL
            catch = self.until_end()
        if self.word(1) != "CATCH":
            raise self.syntax(self.i + 1)
        self.i += 2
        return Try(-1, "TRY", line, body, Block(-1, "CATCH", catch_line, catch))

    def variable(self) -> tuple[str, str]:
        if self.kind() != TokenType.PARAMETER or self.kind(1) == TokenType.PARAMETER or self.i + 1 >= len(self.toks):
            raise self.syntax()
        tok = self.toks[self.i + 1]
        if tok.token_type not in (TokenType.VAR,) and not tok.text:
            raise self.syntax()
        written = self.text[self.toks[self.i].start:tok.end + 1]
        self.i += 2
        return tok.text.lower(), written

    def declare(self, line: int) -> Node:
        node = Declare(self.place(), "DECLARE", line)
        self.i += 1
        while True:
            if self.kind() != TokenType.PARAMETER:
                if self.word(1) in ("CURSOR", "INSENSITIVE", "SCROLL"):
                    return self.refused_here(node, _refused("cursors (DECLARE ... CURSOR)"))
                raise self.syntax()
            name, written = self.variable()
            if self.word() == "AS":
                self.i += 1
            if self.word() == "CURSOR":
                return self.refused_here(node, _refused("cursors (DECLARE ... CURSOR)"))
            if self.word() == "TABLE":
                return self.refused_here(node, _refused(f"table variables (DECLARE {written} TABLE)"))
            start, depth = self.i, 0
            while self.i < len(self.toks):
                t = self.kind()
                if depth == 0 and (t in (TokenType.EQ, TokenType.COMMA, TokenType.SEMICOLON)
                                   or (self.i > start and self.word() in _HARD | _SOFT | {"END", "ELSE", "THROW"})):
                    break
                depth += 1 if t == TokenType.L_PAREN else -1 if t == TokenType.R_PAREN else 0
                self.i += 1
            item = DeclareItem(name, written, self.span(start, self.i), None)
            if self.kind() == TokenType.EQ:
                self.i += 1
                end = self.expression_end(self.i, comma=True)
                item.init = self.span(self.i, end)
                self.i = end
            node.items.append(item)
            if self.kind() != TokenType.COMMA:
                return node
            self.i += 1

    def refused_here(self, node: Node, message: str) -> Refused:
        self.i = max(self.leaf_end(self.i), self.i + 1)
        return Refused(node.place, node.kind, node.line, message)

    def set_variable(self, line: int) -> SetVar:
        node = SetVar(self.place(), "SET", line)
        self.i += 1
        node.name, node.written = self.variable()
        if self.kind() in _COMPOUND and self.kind(1) == TokenType.EQ:
            node.op = _COMPOUND[self.kind()]  # type: ignore[index]
            self.i += 2
        elif self.kind() == TokenType.EQ:
            self.i += 1
        else:
            raise self.syntax()
        end = self.expression_end(self.i)
        node.expr = self.span(self.i, end)
        self.i = end
        return node

    def throw(self, line: int) -> Throw:
        node = Throw(self.place(), "THROW", line)
        self.i += 1
        if self.at_end() or self.kind() == TokenType.SEMICOLON or self.word() in _HARD | _SOFT | {"END", "ELSE"}:
            return node
        for n in range(3):
            end = self.expression_end(self.i, comma=True)
            node.args.append(self.span(self.i, end))
            self.i = end
            if n < 2:
                if self.kind() != TokenType.COMMA:
                    raise self.syntax()
                self.i += 1
        return node

    def raiserror(self, line: int) -> Raiserror:
        node = Raiserror(self.place(), "RAISERROR", line)
        self.i += 1
        if self.kind() != TokenType.L_PAREN:
            raise self.syntax()
        self.i += 1
        while True:
            end = self.expression_end(self.i, comma=True)
            node.args.append(self.span(self.i, end))
            self.i = end
            if self.kind() == TokenType.COMMA:
                self.i += 1
                continue
            if self.kind() != TokenType.R_PAREN:
                raise self.syntax()
            self.i += 1
            break
        if len(node.args) < 3:
            raise self.syntax(self.i - 1)
        if self.word() == "WITH":
            self.i += 1
            while self.word() in ("LOG", "NOWAIT", "SETERROR"):
                node.options.append(self.word())
                self.i += 1
                if self.kind() != TokenType.COMMA:
                    break
                self.i += 1
            if not node.options:
                raise self.syntax()
        return node


# Telling a script from a plain one


_ROUTINE = {"PROCEDURE", "PROC", "FUNCTION", "TRIGGER"}


def is_script(sql: str) -> bool:
    """``sql`` uses a variable or control flow, so it runs here (``run``) rather than through translate()."""
    try:
        batches = tsql.split_batches(sql)
    except ValueError:
        return False  # translate() names the problem
    for batch in batches:
        try:
            tokens = _Tokenizer(dialect=tsql.KfTSQL).tokenize(batch)
        except SqlglotError:
            continue
        p = _Parser(batch, tokens, [0])
        if any(w in ("CREATE", "ALTER") and _ROUTINE.intersection(p.words[i + 1:i + 4]) for i, w in enumerate(p.words)):
            return False  # a procedure's or function's body is its own: translate() refuses it whole (WR-224)
        for i, tok in enumerate(tokens):
            w = p.words[i]
            if tok.token_type == TokenType.PARAMETER:
                return True
            if w in ("DECLARE", "WHILE", "PRINT", "RAISERROR", "THROW", "RETURN", "BREAK", "CONTINUE"):
                return True
            if w == "IF" and (p.words[i - 1] if i else "") not in _OBJECTS:
                return True
            if w == "BEGIN" and (p.words[i + 1] if i + 1 < len(tokens) else "") not in (
                    "TRAN", "TRANSACTION", "DISTRIBUTED"):
                return True
            if w in _ERROR_TYPES and i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN:
                return True
    return False


# Values


def _type_name(to: exp.DataType) -> str:
    return to.sql(dialect=tsql.KfTSQL)


def literal(value: Any, to: exp.DataType) -> exp.Expression:
    """``value`` (as DuckDB gave it back) as T-SQL ``CAST(<literal> AS <to>)``, built from nodes so nothing of the
    value is read as SQL. Each kind is written so that the cast gives back the same value."""
    cast = exp.Cast(this=_bare(value, to), to=to.copy())
    return cast


def _bare(value: Any, to: exp.DataType) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Literal.number(1 if value else 0)
    if isinstance(value, int):
        return _signed(str(abs(value)), value < 0)
    if isinstance(value, decimal.Decimal):
        return _signed(format(abs(value), "f"), value.is_signed() and value != 0)
    if isinstance(value, float):
        text = repr(abs(value))
        if "e" not in text and "E" not in text:
            text += "E0"  # a float literal, read exactly as the float it was, not as a decimal
        return _signed(text, value < 0)
    if isinstance(value, str):
        return exp.National(this=value) if to.this in _NATIONAL else exp.Literal.string(value)
    if isinstance(value, dt.datetime):
        if to.this == _T.SMALLDATETIME:
            text = value.strftime("%Y-%m-%dT%H:%M:%S")
        elif to.this == _T.DATETIME:
            text = value.strftime("%Y-%m-%dT%H:%M:%S") + f".{value.microsecond // 1000:03d}"
        else:
            text = value.replace(tzinfo=None).isoformat(sep="T", timespec="microseconds")
        return exp.Literal.string(text)
    if isinstance(value, dt.date):
        return exp.Literal.string(value.isoformat())
    if isinstance(value, dt.time):
        return exp.Literal.string(value.isoformat(timespec="microseconds"))
    if isinstance(value, uuid.UUID):
        return exp.Literal.string(str(value))
    raise ValueError(tsql._NOT_YET + f"a {type(value).__name__} value in a variable of type {_type_name(to)} "
                     "isn't mapped yet.")


def _signed(digits: str, negative: bool) -> exp.Expression:
    number = exp.Literal.number(digits)
    return exp.Neg(this=number) if negative else number


# The script, checked and compiled


class Runner(Protocol):
    """What runs a script's statements: the local engine (engine.py), or nothing (a check only)."""

    def translate(self, tree: exp.Expression, text: str) -> tuple[str, list[str]]:
        """``tree`` as DuckDB SQL, and its notes; raises ValueError for what isn't carried."""

    def run(self, sql: str, kind: str) -> tuple[Any, int, bool]:
        """Run an ordinary statement: (its result as a pyarrow Table or None, its row count, whether it changed
        data)."""

    def rows(self, sql: str, last_only: bool = False) -> tuple[int, list[tuple[Any, ...]]]:
        """(how many rows, the rows) of a SELECT; ``last_only`` keeps only the last row read."""

    def check(self) -> None:
        """Raises when the run was cancelled or ran out of time."""

    def error(self, exc: Exception, sql: str, kind: str) -> Exception:
        """``exc`` in a warehouse's words, as the engine shows it."""


@dataclass
class _Statement:
    """One ordinary statement of a Leaf: its parsed tree, or an assignment, or a setting that's skipped."""

    tree: Any = None
    skip: bool = False
    kind: str = ""
    assign: list[tuple[str, Any]] | None = None  # SELECT @x = ...: (name, the expression's template)
    each: bool = False  # SELECT @x = ... with no FROM: each assignment in turn, as SET


@dataclass
class Batch:
    nodes: list[Node]
    before: int  # lines before the batch in the user's text, so ERROR_LINE counts from the batch's first line
    declared: dict[str, exp.DataType]
    first: int
    last: int


@dataclass
class Script:
    """A checked T-SQL script that runs with variables and control flow."""

    batches: list[Batch]
    count: int
    notes: list[str]
    texts: list[str]  # every statement and expression as DuckDB SQL, with each variable a typed NULL (sql_policy)
    text_places: list[int] = field(default_factory=list)  # the place (from 0) of the statement each text is part of
    kinds: dict[int, str] = field(default_factory=dict)  # each statement's place: its first word


def compile_script(sql: str, columns: dict[str, dict[str, dict[str, str]]] | None = None,
                   known: Container[str] | None = None) -> Script:
    """Read and check the whole of ``sql``. Raises tsql.InScript (a statement refused), tsql.InBatch (a batch that
    can't be read) or ValueError (either, in a script of one)."""
    columns = columns or {}
    schema = tsql._schema(columns)

    def translate(tree: exp.Expression, text: str) -> tuple[str, list[str]]:
        return tsql._one(text, tree, columns, known, schema)

    found = tsql._batches(sql)
    counter = [0]
    read: list[tuple[list[Node], int, int, int, str]] = []
    for b, (batch, first, last, before) in enumerate(found):
        text = "\n" * before + batch
        try:
            tokens = _Tokenizer(dialect=tsql.KfTSQL).tokenize(text)
            nodes = _Parser(text, tokens, counter).batch()
        except (SqlglotError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else f"Could not read the query as T-SQL: " \
                                                                  f"{tsql._first_line(exc)}"
            if len(found) < 2:
                raise ValueError(message) from None
            raise tsql.InBatch(message, b, len(found), first, last) from None
        read.append((nodes, before, first, last, text))
    script = Script([], counter[0], [], [])
    checker = _Checker(script, translate)
    for nodes, before, first, last, text in read:
        declared: dict[str, exp.DataType] = {}
        checker.batch(nodes, text, declared)
        # A batch after GO starts with the end of the GO line, which isn't one of its lines: its line 1 follows it.
        script.batches.append(Batch(nodes, before + (before > 0 and text[before:].startswith("\n")), declared, first, last))
    if not script.texts and not any(_runs_something(b.nodes) for b in script.batches):
        raise ValueError("There's nothing to run: SET NOCOUNT, ANSI_NULLS and the other session options change "
                         "nothing on the local engine, so they're skipped.")
    script.notes = list(dict.fromkeys(script.notes))
    return script


def _runs_something(nodes: list[Node]) -> bool:
    for node in nodes:
        if isinstance(node, Leaf):
            if any(not s.skip for s in node.trees):
                return True
        else:
            return True
    return False


def policy_texts(sql: str) -> list[tuple[int, str]]:
    """Every statement and expression of the script as DuckDB SQL, each variable a typed NULL, with the place (from
    0) of the statement it is part of, for sql_policy's check of what the script may do."""
    script = compile_script(sql)
    return list(zip(script.text_places, script.texts, strict=True))


class _Checker:
    """Walks a batch in the order it's written: what's declared where (Msg 137, 134), each statement translated with
    its variables as typed NULLs, so a refusal comes before anything runs."""

    def __init__(self, script: Script, translate: Callable[[exp.Expression, str], tuple[str, list[str]]]) -> None:
        self.script, self.translate_fn = script, translate
        self.place = 0

    def fail(self, node: Node, message: str) -> None:
        kind = self.script.kinds.get(node.place, node.kind)
        if self.script.count < 2:
            raise ValueError(message)
        raise tsql.InScript(message, node.place, self.script.count, kind)

    def batch(self, nodes: list[Node], text: str, declared: dict[str, exp.DataType]) -> None:
        self.text, self.declared, self.loops, self.catches = text, declared, 0, 0
        for node in nodes:
            self.node(node)

    def node(self, node: Node | None) -> None:
        if node is None:
            return
        if node.place >= 0:
            self.script.kinds.setdefault(node.place, node.kind)
            self.place = node.place
        try:
            self._node(node)
        except (tsql.Placed, _Jumped):
            raise
        except ValueError as exc:
            self.fail(node, str(exc))

    def _node(self, node: Node) -> None:
        if isinstance(node, Refused):
            raise ValueError(node.message)
        if isinstance(node, Block):
            for inner in node.body:
                self.node(inner)
        elif isinstance(node, Try):
            self.node(node.body)
            self.catches += 1
            try:
                self.node(node.catch)
            finally:
                self.catches -= 1
        elif isinstance(node, If):
            assert node.cond is not None
            node.value = self.condition(node.cond)
            self.node(node.then)
            self.node(node.otherwise)
        elif isinstance(node, While):
            assert node.cond is not None
            node.value = self.condition(node.cond)
            self.loops += 1
            try:
                self.node(node.body)
            finally:
                self.loops -= 1
        elif isinstance(node, Jump):
            if node.kind == "BREAK" and not self.loops:
                raise ValueError(_compile_error(135, "Cannot use a BREAK statement outside the scope of a WHILE "
                                                     "statement."))
            if node.kind == "CONTINUE" and not self.loops:
                raise ValueError(_compile_error(136, "Cannot use a CONTINUE statement outside the scope of a WHILE "
                                                     "statement."))
        elif isinstance(node, Declare):
            for item in node.items:
                if item.name in self.declared:
                    raise ValueError(_compile_error(134, f"The variable name '{item.written}' has already been "
                                                         "declared. Variable names must be unique within a query "
                                                         "batch or stored procedure."))
                item.to = self.declared_type(item)
                if item.init is not None:
                    item.value = self.expression(item.init, item.to)
                self.declared[item.name] = item.to
        elif isinstance(node, SetVar):
            to = self.known(node.name, node.written)
            assert node.expr is not None
            node.value = self.expression(node.expr, to, (node.written, node.op) if node.op != "=" else None)
        elif isinstance(node, Print):
            assert node.expr is not None
            node.value = self.expression(node.expr, exp.DataType.build("NVARCHAR(MAX)", dialect=tsql.KfTSQL))
        elif isinstance(node, Throw):
            if not node.args and not self.catches:
                raise ValueError(_compile_error(10704, "To rethrow an error, a THROW statement must be used inside a "
                                                       "CATCH block."))
            kinds = ["INT", "NVARCHAR(2048)", "TINYINT"]
            node.values = [self.expression(a, exp.DataType.build(k, dialect=tsql.KfTSQL))
                           for a, k in zip(node.args, kinds, strict=False)]
        elif isinstance(node, Raiserror):
            self.raiserror(node)
        elif isinstance(node, Leaf):
            self.leaf(node)

    def raiserror(self, node: Raiserror) -> None:
        for option in node.options:
            if option in ("LOG", "SETERROR"):
                raise ValueError(_refused(f"RAISERROR ... WITH {option}"))
        message = self.parse_select(node.args[0])
        first = message.expressions[0]
        if isinstance(first, exp.Literal) and not first.is_string or isinstance(first, exp.Neg):
            raise ValueError(_refused("RAISERROR with a message number (from sys.messages)",
                                      "Give the message's text in place of its number, or run it in a Fabric "
                                      "Warehouse."))
        node.values = [self.expression(node.args[0], exp.DataType.build("NVARCHAR(MAX)", dialect=tsql.KfTSQL)),
                       self.expression(node.args[1], exp.DataType.build("INT")),
                       self.expression(node.args[2], exp.DataType.build("INT"))]
        node.values += [self.expression(a, None) for a in node.args[3:]]

    def known(self, name: str, written: str) -> exp.DataType:
        if name not in self.declared:
            raise ValueError(_compile_error(137, f'Must declare the scalar variable "{written}".'))
        return self.declared[name]

    def declared_type(self, item: DeclareItem) -> exp.DataType:
        span = item.type_span
        written = self.text[span.start:span.end + 1]
        tree = self.parse(span, "SELECT CAST(NULL AS ", ")")
        cast = tree.expressions[0] if isinstance(tree, exp.Select) and len(tree.expressions) == 1 else None
        if not isinstance(cast, exp.Cast):
            raise ValueError(f"Could not read the query as T-SQL: {written} isn't a data type, on line {span.line}.")
        to = cast.to
        if to.this in (_T.TEXT, _T.IMAGE) or written.strip().upper() in ("TEXT", "NTEXT", "IMAGE"):
            raise ValueError("Msg 2739, Level 16: The text, ntext, and image data types are invalid for local "
                             "variables.")
        if to.this in (_T.BINARY, _T.VARBINARY):
            raise ValueError(_refused(f"binary variables ({item.written} {written.strip()})",
                                      "Keep the bytes in a table column, or run it in a Fabric Warehouse."))
        if to.this not in _VARIABLE_TYPES:
            # what the translator says about the type itself first (MONEY, DATETIMEOFFSET ...), then this
            self.translate(exp.select(exp.Cast(this=exp.Null(), to=to.copy())), None)
            raise ValueError(_refused(f"variables of type {written.strip()} ({item.written})",
                                      "Declare it as another type, or run it in a Fabric Warehouse."))
        to = to.copy()
        if to.this in _TEXTS and not to.expressions:
            to.set("expressions", [exp.DataTypeParam(this=exp.Literal.number(1))])  # a variable's default length
        if to.this == _T.DECIMAL and not to.expressions:
            to.set("expressions", [exp.DataTypeParam(this=exp.Literal.number(18)),
                                   exp.DataTypeParam(this=exp.Literal.number(0))])
        self.translate(exp.select(exp.Cast(this=exp.Null(), to=to.copy())), None)
        return to

    def parse(self, span: Span, lead: str, tail: str = "") -> exp.Expression:
        """The span's text, after ``lead``, as one parsed T-SQL statement; lines are kept as in the user's text."""
        text = _blank(self.text[:span.start]) + lead + self.text[span.start:span.end + 1] + tail
        trees = [t for t in tsql._parse(text) if not isinstance(t, exp.Semicolon)]
        if len(trees) != 1:
            raise ValueError(f"Could not read the query as T-SQL: incorrect syntax on line {span.line}.")
        return trees[0]

    def parse_select(self, span: Span, lead: str = "SELECT ", tail: str = "") -> exp.Select:
        """The span after ``lead`` as a SELECT of one value with no FROM."""
        tree = self.parse(span, lead, tail)
        if not isinstance(tree, exp.Select) or len(tree.expressions) != 1 or tree.args.get("from_") is not None:
            raise ValueError(f"Could not read the query as T-SQL: incorrect syntax on line {span.line}.")
        return tree

    def expression(self, span: Span, to: exp.DataType | None,
                   compound: tuple[str, str] | None = None) -> exp.Select:
        """``SELECT CAST(<span> AS to)`` (or the bare value with no ``to``), checked; the template to run."""
        if compound is not None:
            written, op = compound
            tree = self.parse_select(span, f"SELECT {written} {op} (", ")")
        else:
            tree = self.parse_select(span)
        value = tree.expressions[0]
        if isinstance(value, exp.Alias):
            raise ValueError(f"Could not read the query as T-SQL: incorrect syntax near '{value.alias}' on line "
                             f"{span.line}.")
        if to is not None:
            value.replace(exp.Cast(this=value.copy(), to=to.copy()))
        self.translate(tree, tree)
        return tree

    def condition(self, span: Span) -> exp.Select:
        tree = self.parse_select(span, "SELECT CASE WHEN ", " THEN 1 ELSE 0 END")
        case = tree.expressions[0]
        if not isinstance(case, exp.Case) or len(case.args.get("ifs") or []) != 1:
            raise ValueError(f"Could not read the query as T-SQL: incorrect syntax on line {span.line}.")
        cond = case.args["ifs"][0].this
        while isinstance(cond, exp.Paren):
            cond = cond.this
        if not isinstance(cond, (exp.Predicate, exp.Connector, exp.Not, exp.Boolean)):
            after = self.text[span.end + 1:].split(None, 1)
            raise ValueError(_compile_error(4145, "An expression of non-boolean type specified in a context where "
                                                  f"a condition is expected, near '{after[0] if after else ''}'."))
        self.translate(tree, tree)
        return tree

    def leaf(self, node: Leaf) -> None:
        text = _blank(self.text[:node.span.start]) + self.text[node.span.start:node.span.end + 1]
        trees = [t for t in tsql._parse(text) if not isinstance(t, exp.Semicolon)]
        if trees:
            node.kind = tsql._statement_kind(trees[0])
            self.script.kinds[node.place] = node.kind
        for tree in trees:
            statement = _Statement(tree=tree, kind=tsql._statement_kind(tree))
            node.trees.append(statement)
            if tsql._harmless_setting(tree):
                statement.skip = True
                continue
            items = tree.expressions if isinstance(tree, exp.Select) else []
            assigned = [i for i in items if isinstance(i, exp.EQ) and _variable(i.this)]
            if assigned and isinstance(tree, exp.Select):
                self.assignment(statement, tree, assigned)
                continue
            _name_results(tree)
            self.translate(tree, text)

    def assignment(self, statement: _Statement, tree: exp.Select, assigned: list[exp.EQ]) -> None:
        if len(assigned) != len(tree.expressions) or tree.find(exp.Into) is not None:
            raise ValueError(_compile_error(141, "A SELECT statement that assigns a value to a variable must not be "
                                                 "combined with data-retrieval operations."))
        names = []
        for item in assigned:
            param = item.this
            name = param.this.name.lower()
            self.known(name, f"@{param.this.name}")
            names.append(name)
        plain = all(not tree.args.get(k) for k in tree.args if k != "expressions")
        statement.each = plain
        statement.assign = []
        if plain:
            for item, name in zip(assigned, names, strict=True):
                value = exp.select(exp.Cast(this=item.expression.copy(), to=self.declared[name].copy()))
                self.translate(value, value)
                statement.assign.append((name, value))
            return
        reads = {p.this.name.lower() for item in assigned for p in item.expression.find_all(exp.Parameter)
                 if isinstance(p.this, exp.Var)}
        if reads & set(names):
            raise ValueError(_refused("row-by-row variable assignment (a SELECT over rows that assigns a variable and "
                                      "reads one it assigns, such as SELECT @s = @s + name FROM ...)",
                                      "A warehouse goes row by row there. Use STRING_AGG, SUM or another aggregate "
                                      "in place of it, or run it in a Fabric Warehouse."))
        query = tree.copy()
        query.set("expressions", [exp.alias_(exp.Cast(this=item.expression.copy(), to=self.declared[name].copy()),
                                             f"kf_v{n}")
                                  for n, (item, name) in enumerate(zip(assigned, names, strict=True))])
        self.translate(query, query)
        statement.assign = [(name, None) for name in names]
        statement.tree = query

    def translate(self, tree: exp.Expression, text: Any) -> None:
        """``tree`` with its variables as typed NULLs, translated: what the translator refuses is refused now."""
        trial = _substitute(tree.copy(), _Placeholders(self))
        sql, notes = self.translate_fn(trial, text if isinstance(text, str) else trial.sql(dialect=tsql.KfTSQL))
        self.script.notes += notes
        if sql.strip():
            self.script.texts.append(sql)
            self.script.text_places.append(self.place)


class _Jumped(Exception):
    pass


def _variable(node: Any) -> bool:
    return isinstance(node, exp.Parameter) and isinstance(node.this, exp.Var)


def _blank(text: str) -> str:
    """``text`` with everything but its line breaks made spaces, so what follows keeps its line and column."""
    return re.sub(r"[^\n]", " ", text)


def _name_results(tree: exp.Expression) -> None:
    """A result column that reads a variable and has no name is named as the user wrote it (``@x``, ``@x + 1``),
    not after the value put in its place. SQL Server shows such a column with no name."""
    selects = [tree] if isinstance(tree, exp.Select) else \
        [s for s in tree.find_all(exp.Select) if s.parent is tree or isinstance(s.parent, exp.SetOperation)] \
        if isinstance(tree, exp.SetOperation) else []
    for select in selects[:1]:
        for item in list(select.expressions):
            if not isinstance(item, exp.Alias) and (item.find(exp.Parameter) is not None or any(
                    call.name.upper() in _ERROR_TYPES for call in item.find_all(exp.Anonymous))):
                item.replace(exp.alias_(item.copy(), item.sql(dialect=tsql.KfTSQL)))


class _Values(Protocol):
    def value(self, name: str) -> exp.Expression: ...

    def rowcount(self) -> exp.Expression: ...

    def error(self, name: str) -> exp.Expression: ...


class _Placeholders:
    def __init__(self, checker: _Checker) -> None:
        self.checker = checker

    def value(self, name: str) -> exp.Expression:
        to = self.checker.known(name.lower(), f"@{name}")
        return exp.Cast(this=exp.Null(), to=to.copy())

    def rowcount(self) -> exp.Expression:
        return literal(0, exp.DataType.build("INT"))

    def error(self, name: str) -> exp.Expression:
        return literal(None, exp.DataType.build(_ERROR_TYPES[name], dialect=tsql.KfTSQL))


def _substitute(tree: exp.Expression, values: _Values) -> exp.Expression:
    """Each variable, @@ROWCOUNT and ERROR_...() in ``tree`` as its typed value."""
    for order in list(tree.find_all(exp.Ordered)):
        if _variable(order.this):
            raise ValueError(_compile_error(1008, "The SELECT item identified by the ORDER BY number 1 contains a "
                                                  "variable as part of the expression identifying a column position. "
                                                  "Variables are only allowed when ordering by an expression "
                                                  "referencing a column name."))
    for param in list(tree.find_all(exp.Parameter)):
        if isinstance(param.parent, exp.Parameter):
            continue
        if isinstance(param.this, exp.Parameter):  # @@name
            if param.this.name.upper() == "ROWCOUNT":
                param.replace(values.rowcount())
            continue  # the translator names any other (@@ERROR, @@VERSION ...)
        if isinstance(param.this, exp.Var):
            replacement = values.value(param.this.name)
            if param is tree:
                return replacement
            param.replace(replacement)
    for call in list(tree.find_all(exp.Anonymous)):
        name = call.name.upper()
        if name in _ERROR_TYPES:
            if call.expressions:
                raise ValueError(f"Msg 174, Level 15: The {name.lower()} function requires 0 argument(s).")
            call.replace(values.error(name))
    return tree


# Running


class TsqlError(Exception):
    """An error a T-SQL TRY ... CATCH can catch, with what ERROR_NUMBER() and the rest give back in CATCH."""

    def __init__(self, shown: str, number: int | None, severity: int | None, state: int | None, text: str,
                 line: int, original: Exception | None = None) -> None:
        super().__init__(shown)
        self.shown, self.number, self.severity, self.state, self.text, self.line = (
            shown, number, severity, state, text, line)
        self.original = original


class Stopped(ValueError):
    """The script stopped: an error nothing caught, or a refusal while it ran. The message names the statement."""


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    pass


@dataclass
class Outcome:
    result: Any  # a pyarrow Table, or None when no statement returned one
    messages: list[str]
    notes: list[str]
    runs: dict[int, list[Any]]  # each statement's place: [kind, how many times it ran, seconds]


class _Run:
    def __init__(self, script: Script, runner: Runner) -> None:
        self.script, self.runner = script, runner
        self.messages: list[str] = []
        self.notes: list[str] = []
        self.result: Any = None
        self.changed: list[tuple[str, int]] = []  # (kind, line) of each statement that changed data, in order
        self.runs: dict[int, list[Any]] = {}

    # values in place of variables

    def value(self, name: str) -> exp.Expression:
        key = name.lower()
        if key not in self.vars:  # compile_script checked this; kept for safety
            raise ValueError(_compile_error(137, f'Must declare the scalar variable "@{name}".'))
        return literal(self.vars[key], self.types[key])

    def rowcount(self) -> exp.Expression:
        return literal(self.count, exp.DataType.build("INT"))

    def error(self, name: str) -> exp.Expression:
        to = exp.DataType.build(_ERROR_TYPES[name], dialect=tsql.KfTSQL)
        if not self.caught:
            return literal(None, to)
        e = self.caught[-1]
        found = {"ERROR_NUMBER": e.number, "ERROR_SEVERITY": e.severity, "ERROR_STATE": e.state,
                 "ERROR_LINE": e.line, "ERROR_MESSAGE": e.text, "ERROR_PROCEDURE": None}[name]
        return literal(found, to)

    # the run

    def run(self) -> Outcome:
        for batch in self.script.batches:
            self.batch = batch
            self.vars: dict[str, Any] = {name: None for name in batch.declared}
            self.types = batch.declared
            self.count = 0
            self.caught: list[TsqlError] = []
            self.trying = 0  # how many TRY blocks the running statement is in
            try:
                for node in batch.nodes:
                    self.node(node)
            except _Return:
                pass
            except TsqlError as exc:
                raise self.stopped(exc.shown, self.failed_at) from None
        return Outcome(self.result, self.messages, list(dict.fromkeys(self.notes)), self.runs)

    def stopped(self, message: str, node: Node, refused: bool = False) -> Stopped:
        if self.script.count < 2:
            return Stopped(message)
        if not message.endswith((".", "!", "?")):
            message += "."
        kind = self.script.kinds.get(node.place, node.kind)
        where = f"Statement {node.place + 1} of {self.script.count} ({kind}, line {node.line})"
        return Stopped(f"{where} {'was refused' if refused else 'failed'}: {message} {self.what_changed()}")

    def what_changed(self) -> str:
        if not self.changed:
            return "Nothing before it changed data."
        counted: dict[tuple[str, int], int] = {}
        for item in self.changed:
            counted[item] = counted.get(item, 0) + 1
        names = [f"the {kind} on line {line}" + (f" ({n} times)" if n > 1 else "")
                 for (kind, line), n in counted.items()]
        if len(names) > 5:
            names = names[:5] + [f"{len(names) - 5} more"]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"Before it, {listed} ran, and what {'it' if len(self.changed) == 1 else 'they'} changed is kept."

    def node(self, node: Node | None) -> None:
        if node is None:
            return
        self.runner.check()
        started = time.monotonic() if node.place >= 0 else None
        try:
            self._node(node)
        finally:
            if started is not None:
                entry = self.runs.setdefault(node.place, [self.script.kinds.get(node.place, node.kind), 0, 0.0])
                entry[1] += 1
                entry[2] += time.monotonic() - started

    def _node(self, node: Node) -> None:
        if isinstance(node, Block):
            for inner in node.body:
                self.node(inner)
        elif isinstance(node, Try):
            depth = len(self.caught)
            caught: TsqlError | None = None
            self.trying += 1
            try:
                self.node(node.body)
            except TsqlError as error:
                caught = error
            finally:  # also when BREAK, CONTINUE or RETURN leaves the TRY
                self.trying -= 1
            if caught is not None:
                exc = caught
                del self.caught[depth:]
                if exc.number is None:
                    self.notes.append("ERROR_NUMBER(), ERROR_SEVERITY() and ERROR_STATE() were NULL in a CATCH "
                                      "block, because the local engine has no SQL Server number for the error it "
                                      f"caught: {exc.text}")
                self.caught.append(exc)
                self.count = 0
                try:
                    self.node(node.catch)
                finally:
                    self.caught.pop()
        elif isinstance(node, If):
            if self.truth(node, node.value):
                self.node(node.then)
            else:
                self.node(node.otherwise)
        elif isinstance(node, While):
            while self.truth(node, node.value):
                self.runner.check()
                try:
                    self.node(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
        elif isinstance(node, Jump):
            if node.kind == "RETURN":
                self.count = 1
                raise _Return()
            raise _Break() if node.kind == "BREAK" else _Continue()
        elif isinstance(node, Declare):
            for item in node.items:
                if item.value is not None:
                    self.vars[item.name] = self.evaluate(node, item.value)
                    self.count = 1
        elif isinstance(node, SetVar):
            self.vars[node.name] = self.evaluate(node, node.value)
            self.count = 1
        elif isinstance(node, Print):
            shown = self.evaluate(node, node.value)
            self.messages.append("" if shown is None else str(shown)[:_PRINT_MOST])
            self.count = 0
        elif isinstance(node, Throw):
            self.throw(node)
        elif isinstance(node, Raiserror):
            self.raiserror(node)
        elif isinstance(node, Leaf):
            self.leaf(node)

    def translate(self, node: Node, template: exp.Expression) -> str:
        tree = _substitute(template.copy(), self)
        try:
            sql, notes = self.runner.translate(tree, tree.sql(dialect=tsql.KfTSQL))
        except ValueError as exc:
            raise self.stopped(str(exc), node, refused=True) from None
        self.notes += notes
        return sql

    def fail(self, node: Node, exc: Exception, sql: str, kind: str) -> TsqlError:
        said = self.runner.error(exc, sql, kind)
        text = str(said).strip().split("\n\nLINE")[0]
        line = node.line - self.batch.before
        found = _MSG.fullmatch(text)
        if found is not None:
            number, level = int(found[1]), int(found[2])
            if number in _NOT_CAUGHT:
                raise self.stopped(text, node) from None
            # State 1, which the errors the local engine numbers (8134, 245, 8115, 512 ...) carry in SQL Server.
            error = TsqlError(text, number, level, 1, found[3], line, said)
        else:
            import duckdb
            if not isinstance(exc, duckdb.Error):
                raise self.stopped(text, node, refused=True) from None  # the local engine's own refusal
            error = TsqlError(text, None, None, None, text, line, said)
        self.failed_at = node
        return error

    def evaluate(self, node: Node, template: exp.Expression) -> Any:
        rows = self.select(node, template)
        return rows[0][0] if rows else None

    def select(self, node: Node, template: exp.Expression, last_only: bool = False) -> list[tuple[Any, ...]]:
        sql = self.translate(node, template)
        try:
            _count, rows = self.runner.rows(sql, last_only)
        except Exception as exc:  # noqa: BLE001 - a DuckDB error is the statement's error, as T-SQL raises it
            if _interrupted(exc):
                raise
            raise self.fail(node, exc, sql, "select") from None
        self.last_rows = _count
        return rows

    def truth(self, node: Node, template: exp.Expression) -> bool:
        value = self.evaluate(node, template)
        self.count = 0  # IF and WHILE set @@ROWCOUNT to 0
        return value == 1

    def raise_error(self, node: Node, shown: str, number: int | None, severity: int | None, state: int | None,
                    text: str) -> None:
        self.failed_at = node
        raise TsqlError(shown, number, severity, state, text, node.line - self.batch.before)

    def throw(self, node: Throw) -> None:
        if not node.values:
            e = self.caught[-1]
            self.failed_at = node
            raise TsqlError(e.shown, e.number, e.severity, e.state, e.text, e.line, e.original)
        number, message, state = (self.evaluate(node, v) for v in node.values)
        if number is None or message is None or state is None:
            self.raise_error(node, "THROW was given NULL, which a warehouse refuses.", None, None, None,
                             "THROW was given NULL, which a warehouse refuses.")
        if number < 50000:
            text = (f"Error number {number} in the THROW statement is outside the valid range. Specify an error "
                    "number in the valid range of 50000 to 2147483647.")
            self.raise_error(node, f"Msg 35100, Level 16: {text}", 35100, 16, None, text)
        self.raise_error(node, f"Msg {number}, Level 16: {message}", number, 16, state, message)

    def raiserror(self, node: Raiserror) -> None:
        message, severity, state = (self.evaluate(node, v) for v in node.values[:3])
        args = [self.evaluate(node, v) for v in node.values[3:]]
        self.count = 0
        severity = max(0, severity or 0)
        if state is None or state < 0:
            state = 1
        if state > 255:
            text = "RAISERROR's state is more than 255, which a warehouse refuses."
            self.raise_error(node, text, None, None, None, text)
        if severity > 18:
            text = ("Error severity levels greater than 18 can only be specified by members of the sysadmin role, "
                    "using the WITH LOG option.")
            self.raise_error(node, f"Msg 2754, Level 16: {text}", 2754, 16, None, text)
        try:
            text = _formatted("" if message is None else str(message), args)
        except ValueError as exc:
            found = _MSG.fullmatch(str(exc))
            assert found is not None
            self.raise_error(node, str(exc), int(found[1]), 16, None, found[3])
        if len(text) > _RAISERROR_MOST:
            text = text[:_RAISERROR_MOST - 3] + "..."
        if severity <= 10:
            self.messages.append(text)
            return
        if not self.trying:
            # A warehouse sends the error to the client and goes on with the batch: RAISERROR doesn't end it.
            line = node.line - self.batch.before
            self.messages.append(f"Msg 50000, Level {severity}, State {state}, Line {line}\n{text}")
            return
        self.raise_error(node, f"Msg 50000, Level {severity}: {text}", 50000, severity, state, text)

    def leaf(self, node: Leaf) -> None:
        for statement in node.trees:
            if statement.skip:
                self.count = 0
                continue
            if statement.assign is not None:
                self.assign(node, statement)
                continue
            sql = self.translate(node, statement.tree)
            if not sql.strip():
                continue
            try:
                result, rows, changed = self.runner.run(sql, statement.kind)
            except Exception as exc:  # noqa: BLE001 - as select()
                if _interrupted(exc):
                    raise
                raise self.fail(node, exc, sql, statement.kind.lower()) from None
            self.count = rows
            if result is not None:
                self.result = result
            if changed:
                self.changed.append((node.kind, node.line))

    def assign(self, node: Leaf, statement: _Statement) -> None:
        assert statement.assign is not None
        if statement.each:
            for name, template in statement.assign:
                self.vars[name] = self.evaluate(node, template)
            self.count = 1
            return
        rows = self.select(node, statement.tree, last_only=True)
        if rows:
            for (name, _), value in zip(statement.assign, rows[-1], strict=True):
                self.vars[name] = value
        self.count = self.last_rows


def _interrupted(exc: BaseException) -> bool:
    import duckdb
    return isinstance(exc, duckdb.InterruptException) or (
        isinstance(exc, OSError) and str(exc).startswith("INTERRUPT Error"))


_SPEC = re.compile(r"%%|%(?P<flags>[-+ 0#]*)(?P<width>\*|\d+)?(?:\.(?P<precision>\*|\d+))?"
                   r"(?P<size>h|l|I64)?(?P<kind>[diosuxX])")


def _formatted(message: str, args: list[Any]) -> str:
    """RAISERROR's message with its arguments put in, as printf does: %d %i %u %o %x %X take an integer, %s text;
    a missing or NULL argument shows (null)."""
    queue = list(args)
    count = [0]

    def take() -> Any:
        count[0] += 1
        return queue.pop(0) if queue else None

    def put(found: re.Match[str]) -> str:
        if found[0] == "%%":
            return "%"
        width = found["width"]
        precision = found["precision"]
        if width == "*":
            width = str(take() or 0)
        if precision == "*":
            precision = str(take() or 0)
        value = take()
        kind = found["kind"]
        if value is None:
            return "(null)"
        if kind == "s":
            if not isinstance(value, str):
                raise _mismatch(count[0])
        elif isinstance(value, bool) or not isinstance(value, int):
            raise _mismatch(count[0])
        elif found["size"] != "I64" and not -2 ** 31 <= value < 2 ** 32:
            raise _mismatch(count[0])
        if kind == "u" and value < 0:
            value += 2 ** 32
        spec = "%" + (found["flags"] or "") + (width or "") + (f".{precision}" if precision is not None else "") + \
               {"i": "d", "u": "d"}.get(kind, kind)
        return spec % value

    return _SPEC.sub(put, message)


def _mismatch(n: int) -> ValueError:
    return ValueError(f"Msg 2786, Level 16: The data type of substitution parameter {n} does not match the expected "
                      "type of the format specification.")


def run(script: Script, runner: Runner) -> Outcome:
    """Run a checked script. Raises Stopped for an error nothing caught (or a refusal as it ran), naming the
    statement and what had changed; the runner's own cancel and time limit errors pass through."""
    return _Run(script, runner).run()


__all__ = ["Outcome", "Runner", "Script", "Stopped", "TsqlError", "compile_script", "is_script", "literal",
           "policy_texts", "run"]
