"""Snowflake tasks, streams and dynamic tables, written and checked on this PC (SF-008, Preview).

``validate(sql)`` reads one or more CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE statements and
returns, for each, its kind, its name, the properties it sets and what's wrong with it. Nothing is sent
anywhere: this is a local check of the documented syntax and rules, not Snowflake's own, and every
result says so (``LABEL``). Deploying comes later, through Snowflake's own CREATE statements on the
SF-003 engine after a confirm; this module never runs anything.

How each statement is read:

- sqlglot (Snowflake dialect) tokenizes everything. The statement head (CREATE, OR REPLACE or OR ALTER,
  the kind, IF NOT EXISTS, the name) is always read from those tokens, so names are checked the same way
  for every kind.
- CREATE DYNAMIC TABLE is parsed by sqlglot when it can, and its properties and query come from the tree.
- sqlglot reads CREATE TASK and CREATE STREAM (and a dynamic table it can't parse) only as a Command, so
  a small strict parser walks their clauses token by token. A clause it doesn't know is an error that
  names it, never skipped.
- A task's body and a dynamic table's query are parsed as Snowflake SQL with sqlglot. A task's WHEN is
  parsed too, and only the functions and operators Snowflake documents for WHEN are accepted.

The rules come from Snowflake's documentation, read on 2026-11-23 (CREATE TASK, CREATE STREAM,
CREATE DYNAMIC TABLE, Introduction to tasks, Identifier requirements). Across statements, tasks form
graphs through AFTER and FINALIZE: predecessors are resolved within the set (or reported as existing on
Snowflake), cycles are errors, and the documented limits (100 predecessors and 100 children per task,
1000 tasks per graph) are checked.

Messages are plain sentences that say what to do next.
"""

from __future__ import annotations

import functools
import re
import zoneinfo
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.snowflake import Snowflake
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import Token, TokenType

LABEL = ("Local validation on this PC, not Snowflake's own check. Snowflake may still refuse a statement "
         "that passes here, and nothing was sent to Snowflake.")
ERROR, WARNING = "error", "warning"

MAX_IDENTIFIER = 255
MAX_PREDECESSORS = 100
MAX_CHILDREN = 100
MAX_GRAPH_TASKS = 1000
MIN_TARGET_LAG_SECONDS = 60
# SCHEDULE = '<num> { SECONDS | MINUTES | HOURS }': 10 seconds to the equivalent of 8 days.
SCHEDULE_RANGES = {"SECONDS": (10, 691_200), "MINUTES": (1, 11_520), "HOURS": (1, 192)}
# TARGET_COMPLETION_INTERVAL: 10 seconds to the equivalent of 1 day.
COMPLETION_RANGES = {"SECONDS": (10, 86_400), "MINUTES": (1, 1_440), "HOURS": (1, 24)}
LAG_UNITS = {"SECONDS": 1, "MINUTES": 60, "HOURS": 3600, "DAYS": 86_400}
# Numbers a task property takes, with the documented range (None: no upper limit).
TASK_NUMBERS: dict[str, tuple[int, int | None]] = {
    "USER_TASK_TIMEOUT_MS": (0, 604_800_000), "SUSPEND_TASK_AFTER_NUM_FAILURES": (0, None),
    "TASK_AUTO_RETRY_ATTEMPTS": (0, 30), "USER_TASK_MINIMUM_TRIGGER_INTERVAL_IN_SECONDS": (10, 604_800),
}
TASK_PROPERTIES = frozenset({
    "WAREHOUSE", "USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE", "SCHEDULE", "CONFIG", "OVERLAP_POLICY",
    "ALLOW_OVERLAPPING_EXECUTION", "ERROR_INTEGRATION", "SUCCESS_INTEGRATION", "LOG_LEVEL", "TRACE_LEVEL",
    "COMMENT", "FINALIZE", "TARGET_COMPLETION_INTERVAL", "SERVERLESS_TASK_MIN_STATEMENT_SIZE",
    "SERVERLESS_TASK_MAX_STATEMENT_SIZE", *TASK_NUMBERS})
# Session parameters often set on a task; any other unknown name is still accepted, with a warning.
COMMON_SESSION_PARAMETERS = frozenset({
    "TIMEZONE", "QUERY_TAG", "STATEMENT_TIMEOUT_IN_SECONDS", "TIMESTAMP_INPUT_FORMAT", "DATE_INPUT_FORMAT",
    "AUTOCOMMIT", "BINARY_INPUT_FORMAT", "JSON_INDENT", "LOCK_TIMEOUT", "ROWS_PER_RESULTSET", "WEEK_START"})
WAREHOUSE_SIZES = {"XSMALL": "XSMALL", "X-SMALL": "XSMALL", "SMALL": "SMALL", "MEDIUM": "MEDIUM",
                   "LARGE": "LARGE", "XLARGE": "XLARGE", "X-LARGE": "XLARGE", "XXLARGE": "XXLARGE",
                   "X2LARGE": "XXLARGE", "2X-LARGE": "XXLARGE"}
BIGGER_SIZES = frozenset({"XXXLARGE", "X3LARGE", "3X-LARGE", "X4LARGE", "4X-LARGE", "X5LARGE", "5X-LARGE",
                          "X6LARGE", "6X-LARGE"})
# The functions Snowflake documents for a task's WHEN.
WHEN_FUNCTIONS = frozenset({"SYSTEM$STREAM_HAS_DATA", "SYSTEM$GET_PREDECESSOR_RETURN_VALUE",
                            "SYSTEM$PREDECESSORS_STATE", "SYSTEM$TASK_COMPLETION_STATE"})
_WHEN_NODES = (exp.And, exp.Or, exp.Not, exp.Paren, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
               exp.Is, exp.Boolean, exp.Literal, exp.Null, exp.Cast, exp.TryCast, exp.DataType,
               exp.DataTypeParam, exp.Neg, exp.Select, exp.Anonymous)
# Which options each stream source takes (CREATE STREAM's variants).
STREAM_SOURCES = {
    "TABLE": {"AT", "BEFORE", "APPEND_ONLY", "SHOW_INITIAL_ROWS"},
    "VIEW": {"AT", "BEFORE", "APPEND_ONLY", "SHOW_INITIAL_ROWS"},
    "EXTERNAL TABLE": {"AT", "BEFORE", "INSERT_ONLY"},
    "STAGE": set(),
    "DYNAMIC TABLE": set(),
    "EVENT TABLE": set(),
}
STREAM_PROPERTIES = frozenset({"APPEND_ONLY", "INSERT_ONLY", "SHOW_INITIAL_ROWS", "COMMENT"})
REFRESH_MODES = frozenset({"AUTO", "FULL", "INCREMENTAL", "ADAPTIVE", "CUSTOM_INCREMENTAL"})
INITIALIZE_VALUES = frozenset({"ON_CREATE", "ON_SCHEDULE"})
DT_PROPERTIES = frozenset({
    "TARGET_LAG", "SCHEDULER", "WAREHOUSE", "INITIALIZATION_WAREHOUSE", "REFRESH_MODE", "INITIALIZE",
    "DATA_RETENTION_TIME_IN_DAYS", "MAX_DATA_EXTENSION_TIME_IN_DAYS", "COMMENT", "ROW_TIMESTAMP"})
CRON_FIELDS = (  # name, low, high, names, L allowed
    ("minute", 0, 59, (), False), ("hour", 0, 23, (), False), ("day of month", 1, 31, (), True),
    ("month", 1, 12, ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), False),
    ("day of week", 0, 6, ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"), True),
)
_UNQUOTED = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_STRINGS = (TokenType.STRING, TokenType.RAW_STRING, TokenType.NATIONAL_STRING)
_KIND_LABEL = {"task": "task", "stream": "stream", "dynamic_table": "dynamic table"}


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "message": self.message}


@dataclass
class Result:
    """One statement: its kind (task, stream, dynamic_table or other), its name, what it sets and what's wrong."""
    kind: str
    name: str
    text: str
    findings: list[Finding] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    label: str = LABEL

    @property
    def ok(self) -> bool:
        return not any(f.severity == ERROR for f in self.findings)

    def error(self, message: str) -> None:
        self.findings.append(Finding(ERROR, message))

    def warn(self, message: str) -> None:
        self.findings.append(Finding(WARNING, message))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "ok": self.ok, "label": self.label, "text": self.text,
                "findings": [f.to_dict() for f in self.findings], "properties": self.properties}


@dataclass(frozen=True)
class _Name:
    parts: tuple[str, ...]
    quoted: tuple[bool, ...]

    @property
    def key(self) -> str:
        """How Snowflake resolves it: unquoted parts in upper case, quoted parts as written."""
        return ".".join(p if q else p.upper() for p, q in zip(self.parts, self.quoted, strict=True))

    @property
    def last(self) -> str:
        return self.parts[-1] if self.quoted[-1] else self.parts[-1].upper()

    def __str__(self) -> str:
        return ".".join('"' + p.replace('"', '""') + '"' if q else p for p, q in zip(self.parts, self.quoted, strict=True))


@dataclass(frozen=True)
class _Val:
    kind: str  # string, number, bool, name, group
    text: str
    name: _Name | None = None


class _Stop(Exception):
    """The statement can't be read further; the message says where and what to do."""


class _NotOurs(Exception):
    """Not a CREATE TASK, STREAM or DYNAMIC TABLE statement."""


class _Cursor:
    def __init__(self, text: str, tokens: list[Token]) -> None:
        self.text, self.tokens, self.i = text, tokens, 0

    def done(self) -> bool:
        return self.i >= len(self.tokens)

    def peek(self, ahead: int = 0) -> Token | None:
        j = self.i + ahead
        return self.tokens[j] if j < len(self.tokens) else None

    def word(self, ahead: int = 0) -> str:
        """The token's text in upper case when it is a bare word (a keyword or an unquoted name), else ''."""
        tok = self.peek(ahead)
        if tok is None or tok.token_type in _STRINGS or tok.token_type in (TokenType.IDENTIFIER, TokenType.NUMBER):
            return ""
        return tok.text.upper() if _UNQUOTED.fullmatch(tok.text) else ""

    def take(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def accept(self, *words: str) -> bool:
        if all(self.word(k) == w for k, w in enumerate(words)):
            self.i += len(words)
            return True
        return False

    def expect(self, what: str, *words: str) -> None:
        if not self.accept(*words):
            raise _Stop(f"{what} needs {' '.join(words)} here. Check the clause against Snowflake's reference.")

    def at(self, token_type: TokenType) -> bool:
        tok = self.peek()
        return tok is not None and tok.token_type == token_type

    def group(self, what: str) -> str:
        """A balanced ( ... ) group, returned as written without its outer parentheses."""
        if not self.at(TokenType.L_PAREN):
            raise _Stop(f"{what} needs a list in parentheses here. Add the parentheses.")
        start, depth = self.take(), 1
        while not self.done():
            tok = self.take()
            depth += {TokenType.L_PAREN: 1, TokenType.R_PAREN: -1}.get(tok.token_type, 0)
            if depth == 0:
                return self.text[start.end + 1:tok.start].strip()
        raise _Stop(f"{what} opens a parenthesis that isn't closed. Close it.")

    def rest(self, after: Token) -> str:
        self.i = len(self.tokens)
        return self.text[after.end + 1:].strip()


def _name(cur: _Cursor, what: str) -> _Name:
    """A name of one to three parts (database.schema.object), each a valid Snowflake identifier."""
    parts: list[str] = []
    quoted: list[bool] = []
    while True:
        tok = cur.peek()
        if tok is not None and tok.token_type == TokenType.IDENTIFIER:
            if not tok.text:
                raise _Stop(f"{what} is an empty quoted name. Give it a name between the double quotes.")
            parts.append(tok.text)
            quoted.append(True)
        elif tok is not None and cur.word():
            parts.append(tok.text)
            quoted.append(False)
        else:
            shown = f" {tok.text}" if tok is not None else ""
            raise _Stop(f"{what}{shown} isn't a valid Snowflake identifier. Start an unquoted name with a letter "
                        "or an underscore and use only letters, digits, underscores and dollar signs, or put the "
                        "name in double quotes.")
        cur.take()
        if len(parts[-1]) > MAX_IDENTIFIER:
            raise _Stop(f"{what} has a part longer than {MAX_IDENTIFIER} characters, which Snowflake doesn't allow. "
                        "Shorten it.")
        nxt = cur.peek()
        if nxt is not None and nxt.token_type == TokenType.DOT and len(parts) < 3:
            cur.take()
            continue
        if nxt is not None and nxt.start == tok.end + 1 \
                and nxt.token_type not in (TokenType.COMMA, TokenType.L_PAREN, TokenType.R_PAREN, TokenType.EQ):
            raise _Stop(f"{what} isn't a valid Snowflake identifier. Use at most database.schema.name, and put a "
                        "name with other characters in double quotes.")
        return _Name(tuple(parts), tuple(quoted))


def _value(cur: _Cursor, what: str) -> _Val:
    tok = cur.peek()
    if tok is None or cur.word() == "AS":
        raise _Stop(f"{what} has no value after the equals sign. Give it one.")
    if tok.token_type in _STRINGS:
        cur.take()
        return _Val("string", tok.text)
    if tok.token_type in (TokenType.NUMBER, TokenType.DASH):
        sign = ""
        if tok.token_type == TokenType.DASH:
            cur.take()
            sign = "-"
            if not cur.at(TokenType.NUMBER):
                raise _Stop(f"{what} has a value that isn't a number. Give a number.")
        return _Val("number", sign + cur.take().text)
    if tok.token_type in (TokenType.TRUE, TokenType.FALSE):
        cur.take()
        return _Val("bool", tok.text.upper())
    if tok.token_type == TokenType.L_PAREN:
        return _Val("group", cur.group(what))
    name = _name(cur, what)
    return _Val("name", str(name), name)


def _properties_into(cur: _Cursor, props: dict[str, _Val]) -> None:
    """``NAME = value`` at the cursor."""
    tok = cur.take()
    key = tok.text.upper()
    cur.take()  # the equals sign
    if key in props:
        raise _Stop(f"{key} is given twice. Keep one.")
    props[key] = _value(cur, key)


def _is_property(cur: _Cursor) -> bool:
    nxt = cur.peek(1)
    return bool(cur.word()) and nxt is not None and nxt.token_type == TokenType.EQ


def _tags(cur: _Cursor, clauses: dict[str, str]) -> bool:
    """WITH TAG ( ... ), WITH CONTACT ( ... ) or the same without WITH."""
    ahead = 1 if cur.word() == "WITH" else 0
    if cur.word(ahead) not in ("TAG", "CONTACT"):
        return False
    cur.i += ahead
    word = cur.take().text.upper()
    clauses[word] = cur.group(word)
    return True


# ---------------------------------------------------------------------------- statements


def _split(sql: str) -> list[str]:
    """Statements split on top-level semicolons, keeping a Snowflake Scripting block (BEGIN ... END,
    with a DECLARE section before it) in one piece."""
    tokens = Snowflake().tokenize(sql)
    parts: list[str] = []
    start, depth, declare, has = 0, 0, False, False
    for k, tok in enumerate(tokens):
        nxt = tokens[k + 1] if k + 1 < len(tokens) else None
        word = tok.text.upper() if tok.token_type not in _STRINGS and tok.token_type != TokenType.IDENTIFIER else ""
        prev = tokens[k - 1].text.upper() if k else ""
        if word == "DECLARE" and depth == 0:
            declare = True
        elif word == "BEGIN" and (nxt is None or nxt.text.upper() not in ("TRANSACTION", "WORK", "NAME", ";")):
            depth, declare = depth + 1, False
        elif word == "CASE" and prev != "END":
            depth += 1
        elif word == "END" and depth and (nxt is None or nxt.text.upper() not in ("IF", "LOOP", "FOR", "WHILE", "REPEAT")):
            depth -= 1
        if tok.token_type == TokenType.SEMICOLON and depth == 0 and not declare:
            if has:
                parts.append(sql[start:tok.start])
            start, has = tok.end + 1, False
        elif tok.token_type != TokenType.SEMICOLON:
            has = True
    if has:
        parts.append(sql[start:])
    return [p.strip() for p in parts if p.strip()]


def _head(cur: _Cursor) -> tuple[str, set[str]]:
    if cur.word() != "CREATE":
        raise _NotOurs
    cur.take()
    flags: set[str] = set()
    if cur.accept("OR", "REPLACE"):
        flags.add("OR REPLACE")
    elif cur.accept("OR", "ALTER"):
        flags.add("OR ALTER")
    if cur.accept("TRANSIENT"):
        flags.add("TRANSIENT")
    if cur.accept("TASK"):
        kind = "task"
    elif cur.accept("STREAM"):
        kind = "stream"
    elif cur.accept("DYNAMIC", "TABLE"):
        kind = "dynamic_table"
    else:
        raise _NotOurs
    if cur.accept("IF", "NOT", "EXISTS"):
        flags.add("IF NOT EXISTS")
    return kind, flags


def validate(sql: str) -> list[Result]:
    """Each statement in ``sql`` checked on its own, then the tasks among them checked as graphs."""
    try:
        texts = _split(sql)
    except TokenError:
        return [Result("other", "", sql.strip(), [Finding(ERROR, "This SQL can't be read, often because of an "
                                                          "unclosed quote or comment. Close it and check again.")])]
    results = [_statement(text) for text in texts]
    _graphs([r for r in results if r.kind == "task" and "name_key" in r.properties])
    return results


def _statement(text: str) -> Result:
    cur = _Cursor(text, Snowflake().tokenize(text))
    result = Result("other", "", text)
    try:
        result.kind, flags = _head(cur)
        if "TRANSIENT" in flags and result.kind != "dynamic_table":
            raise _Stop(f"TRANSIENT applies to dynamic tables, not to a {_KIND_LABEL[result.kind]}. Remove it.")
        name = _name(cur, f"The {_KIND_LABEL[result.kind]} name")
    except _NotOurs:
        result.error("This check reads CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE statements only. "
                     "Check other statements in a SQL worksheet, or remove this one from the set.")
        return result
    except _Stop as stop:
        result.error(str(stop))
        return result
    result.name = str(name)
    result.properties.update({"name_key": name.key, "flags": sorted(flags)})
    if {"OR REPLACE", "IF NOT EXISTS"} <= flags:
        result.error("OR REPLACE and IF NOT EXISTS can't be used together. Keep one of them.")
    try:
        if result.kind == "task":
            _task(result, cur)
        elif result.kind == "stream":
            _stream(result, cur)
        else:
            _dynamic_table(result, cur, flags)
    except _Stop as stop:
        result.error(str(stop))
    return result


def _clone(result: Result, cur: _Cursor) -> bool:
    if not cur.accept("CLONE"):
        return False
    result.properties["CLONE"] = str(_name(cur, "The object cloned"))
    if not cur.done():
        result.properties["CLONE_OPTIONS"] = cur.rest(cur.tokens[cur.i - 1])
    result.warn("A clone takes its definition from the object it copies, so only its name was checked here. "
                "Check the source object's definition instead.")
    return True


# ---------------------------------------------------------------------------- tasks


def _task(result: Result, cur: _Cursor) -> None:
    if _clone(result, cur):
        return
    props: dict[str, _Val] = {}
    clauses: dict[str, str] = {}
    after: list[_Name] = []
    when = body = ""
    has_body = False
    while not cur.done():
        word = cur.word()
        if word == "AS":
            has_body, body = True, cur.rest(cur.take())
        elif word == "AFTER":
            if after:
                raise _Stop("AFTER is given twice. List every predecessor in one AFTER, separated by commas.")
            cur.take()
            after.append(_name(cur, "A predecessor in AFTER"))
            while cur.at(TokenType.COMMA):
                cur.take()
                after.append(_name(cur, "A predecessor in AFTER"))
        elif word == "WHEN":
            start = cur.take()
            while not cur.done() and cur.word() != "AS":
                if cur.at(TokenType.L_PAREN):
                    cur.group("WHEN")
                else:
                    cur.take()
            end = cur.peek()
            when = cur.text[start.end + 1:end.start if end is not None else len(cur.text)].strip()
            if not when:
                raise _Stop("WHEN has no condition. Add one, such as SYSTEM$STREAM_HAS_DATA('my_stream'), or remove WHEN.")
        elif word == "EXECUTE":
            cur.take()
            cur.expect("EXECUTE", "AS", "USER")
            clauses["EXECUTE AS USER"] = str(_name(cur, "The user in EXECUTE AS USER"))
        elif _tags(cur, clauses):
            continue
        elif _is_property(cur):
            _properties_into(cur, props)
        else:
            tok = cur.take()
            raise _Stop(f"{tok.text} isn't a clause CREATE TASK takes here. Check its spelling and its place "
                        "against Snowflake's CREATE TASK reference, which puts AFTER and WHEN just before AS.")
    result.properties.update({k: v.text for k, v in props.items()} | clauses)
    if after:
        result.properties["AFTER"] = [str(n) for n in after]
        result.properties["after_keys"] = [n.key for n in after]
    if "FINALIZE" in props:
        result.properties["finalize_key"] = props["FINALIZE"].name.key if props["FINALIZE"].name else props["FINALIZE"].text
    if when:
        result.properties["WHEN"] = when
    if has_body:
        result.properties["AS"] = body
    _task_rules(result, props, after, when)
    if not has_body:
        result.error("A task needs AS followed by the SQL it runs. Add AS and one statement, or a CALL to a procedure.")
    else:
        _task_body(result, body)


def _task_rules(result: Result, props: dict[str, _Val], after: list[_Name], when: str) -> None:
    child, finalizer = bool(after), "FINALIZE" in props
    warehouse, size = props.get("WAREHOUSE"), props.get("USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE")
    if warehouse and size:
        result.error("This task names both a WAREHOUSE and a USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE, and Snowflake "
                     "refuses that. Keep WAREHOUSE for a task on your warehouse, or the size for a serverless task.")
    elif not warehouse and not size:
        result.warn("This task names neither a WAREHOUSE nor a USER_TASK_MANAGED_INITIAL_WAREHOUSE_SIZE, so Snowflake "
                    "runs it serverless, starting at MEDIUM. Name one if that isn't what you want.")
    if warehouse and warehouse.kind != "name":
        result.error("WAREHOUSE takes a warehouse name, not a string or a number. Write the name without quotes, "
                     "or in double quotes.")
    if size:
        _size(result, size)
    if "SCHEDULE" in props:
        _schedule(result, props["SCHEDULE"])
    if child and "SCHEDULE" in props:
        result.error("A child task (one with AFTER) can't have a SCHEDULE, because it runs when its predecessors "
                     "finish. Remove SCHEDULE, or remove AFTER to make it a root task.")
    if finalizer and (child or "SCHEDULE" in props):
        result.error("A finalizer task (one with FINALIZE) can't have a SCHEDULE or AFTER, because it runs when "
                     "its root task's graph finishes. Remove them.")
    if len(after) > MAX_PREDECESSORS:
        result.error(f"This task names {len(after)} predecessors in AFTER, and Snowflake allows {MAX_PREDECESSORS}. "
                     "Split the graph or add an intermediate task.")
    keys = [n.key for n in after]
    for dup in sorted({k for k in keys if keys.count(k) > 1}):
        result.error(f"AFTER names {dup} more than once. Keep one.")
    for key, (low, high) in TASK_NUMBERS.items():
        if key in props:
            _number(result, key, props[key], low, high)
    for key in ("TASK_AUTO_RETRY_ATTEMPTS", "OVERLAP_POLICY"):
        if key in props and (child or finalizer):
            result.error(f"{key} can be set only on a root task, and this is a "
                         f"{'child' if child else 'finalizer'} task. Set it on the root task instead.")
    if "TARGET_COMPLETION_INTERVAL" in props:
        _interval(result, "TARGET_COMPLETION_INTERVAL", props["TARGET_COMPLETION_INTERVAL"], COMPLETION_RANGES)
    root = not child and not finalizer
    if root and "SCHEDULE" not in props and not when:
        result.warn("This root task has no SCHEDULE and no WHEN, so it runs only when started with EXECUTE TASK. "
                    "Add a SCHEDULE, or a WHEN for a task triggered by a stream, if it should run on its own.")
    if root and "SCHEDULE" not in props and when and not warehouse and "TARGET_COMPLETION_INTERVAL" not in props:
        result.error("A serverless triggered task needs a TARGET_COMPLETION_INTERVAL. Add one, such as "
                     "TARGET_COMPLETION_INTERVAL = '5 MINUTES', or name a WAREHOUSE.")
    for key in sorted(set(props) - TASK_PROPERTIES - COMMON_SESSION_PARAMETERS):
        result.warn(f"{key} isn't a CREATE TASK property this check knows, so Snowflake will read it as a session "
                    "parameter. Check its spelling.")
    if when:
        _when(result, when)


def _size(result: Result, size: _Val) -> None:
    text = size.text.upper()
    if text in WAREHOUSE_SIZES:
        result.properties["serverless_size"] = WAREHOUSE_SIZES[text]
    elif text in BIGGER_SIZES:
        result.error(f"A serverless task can start at most at XXLARGE, and {size.text} is bigger. Choose XXLARGE or "
                     "smaller, or name a WAREHOUSE.")
    else:
        result.error(f"{size.text} isn't a warehouse size. Use XSMALL, SMALL, MEDIUM, LARGE, XLARGE or XXLARGE.")


def _number(result: Result, key: str, val: _Val, low: int, high: int | None) -> None:
    if val.kind != "number" or not val.text.lstrip("-").isdigit():
        result.error(f"{key} takes a whole number. Write it without quotes.")
        return
    n = int(val.text)
    if n < low or (high is not None and n > high):
        span = f"from {low:,} to {high:,}" if high is not None else f"of {low} or more"
        result.error(f"{key} is {n:,}, and Snowflake accepts values {span}. Choose a value in that range.")


def _interval(result: Result, key: str, val: _Val, ranges: dict[str, tuple[int, int]]) -> int | None:
    """'<num> { SECONDS | MINUTES | HOURS }' in seconds, or None after an error."""
    units = " or ".join(ranges)
    if val.kind != "string":
        result.error(f"{key} takes a quoted interval such as '5 MINUTES'. Put it in single quotes.")
        return None
    words = val.text.split()
    unit = words[1].upper() if len(words) == 2 else ""
    unit = unit if unit.endswith("S") else unit + "S"
    if len(words) != 2 or not words[0].isdigit() or unit not in ranges:
        result.error(f"{key} '{val.text}' isn't an interval Snowflake reads. Write a whole number and one of "
                     f"{units}, such as '5 MINUTES'.")
        return None
    n, (low, high) = int(words[0]), ranges[unit]
    if not low <= n <= high:
        result.error(f"{key} '{val.text}' is outside Snowflake's documented range of {low:,} to {high:,} "
                     f"{unit.lower()}. Choose a value in that range.")
        return None
    return n * LAG_UNITS[unit]


def _schedule(result: Result, val: _Val) -> None:
    words = val.text.split() if val.kind == "string" else []
    if len(words) >= 2 and words[0].upper() == "USING" and words[1].upper() == "CRON":
        _cron(result, words[2:])
        return
    seconds = _interval(result, "SCHEDULE", val, SCHEDULE_RANGES)
    if seconds is not None:
        result.properties["schedule_seconds"] = seconds


def _cron(result: Result, words: list[str]) -> None:
    if len(words) == 5:
        result.error("SCHEDULE's cron expression has no time zone. Add one after the five fields, such as "
                     "'USING CRON 0 9 * * MON UTC' or America/Los_Angeles.")
        for text, spec in zip(words, CRON_FIELDS, strict=True):
            _cron_field(result, text, *spec)
        return
    if len(words) != 6:
        result.error(f"SCHEDULE's cron expression has {max(len(words) - 1, 0)} fields, and Snowflake's has five "
                     "(minute, hour, day of month, month and day of week) followed by a time zone. Seconds aren't "
                     "a cron field there, so use '<num> SECONDS' for that.")
        return
    for text, spec in zip(words[:5], CRON_FIELDS, strict=True):
        _cron_field(result, text, *spec)
    tz = words[5]
    zones = _zones()
    if zones and tz not in zones:
        result.warn(f"{tz} isn't a time zone this PC knows. Use an IANA time zone name, such as UTC or "
                    "America/Los_Angeles.")
    result.properties["cron"] = {"fields": words[:5], "time_zone": tz}


@functools.lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    """The IANA time zones this PC knows (empty where Python has no time zone data, and then none is checked)."""
    return frozenset(zoneinfo.available_timezones())


def _cron_field(result: Result, text: str, label: str, low: int, high: int, names: tuple[str, ...],
                last: bool) -> None:
    def number(part: str) -> int | None:
        if part.isdigit():
            return int(part)
        return names.index(part.upper()) + (0 if label == "day of week" else 1) if part.upper() in names else None

    for item in text.split(","):
        base, _, step = item.partition("/")
        if step and not step.isdigit() or step == "0":
            result.error(f"The cron {label} field '{text}' has a step that isn't a positive whole number. Fix the "
                         "number after the slash.")
            return
        if base == "*" or (last and base.upper() == "L"):
            continue
        if any(c in base for c in "?#W") or (last and base.upper().endswith("L")):
            result.warn(f"The cron {label} field '{text}' uses a form Snowflake's documentation doesn't list, "
                        "and Snowflake supports only a subset of cron. Check it in Snowflake before relying on it.")
            continue
        ends = base.split("-")
        values = [number(p) for p in ends]
        if len(ends) > 2 or any(v is None for v in values):
            result.error(f"The cron {label} field '{text}' isn't one Snowflake reads. Use numbers from {low} to "
                         f"{high}{', names' if names else ''}, *, ranges with a hyphen, lists with commas or steps "
                         "with a slash.")
            return
        if any(v < low or v > high for v in values if v is not None):
            result.error(f"The cron {label} field '{text}' is outside {low} to {high}. Use values in that range.")
            return


def _when(result: Result, when: str) -> None:
    try:
        tree = sqlglot.parse_one(f"SELECT {when}", read="snowflake")
    except (ParseError, TokenError):
        result.error("WHEN isn't a condition this check can read as Snowflake SQL. Check its syntax.")
        return
    for node in tree.walk():
        if isinstance(node, (exp.Column, exp.Identifier)) and not isinstance(node.parent, (exp.DataType, exp.DataTypeParam)):
            result.error("WHEN refers to a column or a name, and Snowflake's WHEN takes only its system functions, "
                         "constants, casts, comparisons and Boolean operators. Use SYSTEM$STREAM_HAS_DATA('stream_name') "
                         "with the stream's name in quotes.")
            return
        if isinstance(node, exp.Anonymous):
            if node.name.upper() not in WHEN_FUNCTIONS:
                result.error(f"WHEN calls {node.name.upper()}, and Snowflake allows only SYSTEM$STREAM_HAS_DATA, "
                             "SYSTEM$GET_PREDECESSOR_RETURN_VALUE, SYSTEM$PREDECESSORS_STATE and "
                             "SYSTEM$TASK_COMPLETION_STATE there. Move other logic into the task's body.")
                return
        elif isinstance(node, exp.Func) and not isinstance(node, _WHEN_NODES):
            result.error(f"WHEN calls {node.sql_name()}, and Snowflake allows only SYSTEM$STREAM_HAS_DATA, "
                         "SYSTEM$GET_PREDECESSOR_RETURN_VALUE, SYSTEM$PREDECESSORS_STATE and "
                         "SYSTEM$TASK_COMPLETION_STATE there. Move other logic into the task's body.")
            return
        elif not isinstance(node, _WHEN_NODES + (exp.Column, exp.Identifier)):
            result.error("WHEN holds an expression Snowflake doesn't allow there. Use its system functions, "
                         "constants, casts, comparisons and Boolean operators only.")
            return


def _task_body(result: Result, body: str) -> None:
    if not body:
        result.error("AS has no SQL after it. Add the one statement the task runs, or a CALL to a procedure.")
        return
    tokens = Snowflake().tokenize(body)
    if not tokens:
        result.error("AS has no SQL after it. Add the one statement the task runs, or a CALL to a procedure.")
        return
    first = tokens[0]
    if first.token_type == TokenType.RAW_STRING or first.text.upper() in ("BEGIN", "DECLARE"):
        result.properties["body_kind"] = "scripting"
        result.warn("The task's body is a Snowflake Scripting block, which this PC doesn't read. Check the block "
                    "in a Snowflake worksheet before you deploy it.")
        return
    tree = _parse(body)
    if tree is None:
        result.error("The task's body isn't Snowflake SQL this check can read. Check its syntax, and keep it to "
                     "one statement or a CALL to a procedure.")
        return
    result.properties["body_kind"] = "statement"
    if isinstance(tree, exp.Command):
        command = str(tree.this).upper()
        if command == "CALL":
            result.properties["body_kind"] = "call"
        else:
            result.warn(f"This check reads the body's {command} only as a command, so it wasn't checked beyond its "
                        "first word. Check it in a Snowflake worksheet before you deploy it.")


def _parse(sql: str) -> exp.Expr | None:
    try:
        return sqlglot.parse_one(sql, read="snowflake")
    except (ParseError, TokenError):
        return None


# ---------------------------------------------------------------------------- streams


def _stream(result: Result, cur: _Cursor) -> None:
    if _clone(result, cur):
        return
    clauses: dict[str, str] = {}
    if cur.accept("COPY", "GRANTS"):
        clauses["COPY GRANTS"] = "TRUE"
    if not cur.accept("ON"):
        raise _Stop("CREATE STREAM needs ON and its source, such as ON TABLE my_table. Add it after the name.")
    source = next((s for s in sorted(STREAM_SOURCES, key=len, reverse=True) if cur.accept(*s.split())), "")
    if not source:
        tok = cur.peek()
        shown = tok.text if tok is not None else "nothing"
        raise _Stop(f"A stream's source is a TABLE, VIEW, EXTERNAL TABLE, STAGE, DYNAMIC TABLE or EVENT TABLE, and "
                    f"this one says {shown}. Use one of those after ON.")
    source_name = _name(cur, f"The {source.lower()} name")
    props: dict[str, _Val] = {}
    while not cur.done():
        word = cur.word()
        if word in ("AT", "BEFORE"):
            cur.take()
            if "AT" in clauses or "BEFORE" in clauses:
                raise _Stop("A stream takes one AT or BEFORE. Keep one.")
            inner = cur.group(word)
            point = inner.split("=>")[0].strip().upper() if "=>" in inner else ""
            allowed = ("TIMESTAMP", "OFFSET", "STATEMENT", "STREAM") if word == "AT" else ("TIMESTAMP", "OFFSET", "STATEMENT")
            if point not in allowed:
                raise _Stop(f"{word} takes {', '.join(allowed[:-1])} or {allowed[-1]} followed by => and a value. "
                            f"Write it as {word}(OFFSET => -60) or similar.")
            clauses[word] = inner
        elif word == "COPY" and cur.accept("COPY", "GRANTS"):
            clauses["COPY GRANTS"] = "TRUE"
        elif _tags(cur, clauses):
            continue
        elif _is_property(cur):
            _properties_into(cur, props)
        else:
            tok = cur.take()
            raise _Stop(f"{tok.text} isn't a clause CREATE STREAM takes here. Check its spelling and its place "
                        "against Snowflake's CREATE STREAM reference.")
    result.properties.update({"source_kind": source, "source": str(source_name)} | clauses
                             | {k: v.text for k, v in props.items()})
    allowed_opts = STREAM_SOURCES[source]
    for key in sorted(set(props) - STREAM_PROPERTIES):
        result.error(f"{key} isn't a CREATE STREAM option. Use APPEND_ONLY, INSERT_ONLY, SHOW_INITIAL_ROWS or COMMENT.")
    for key in [k for k in ("AT", "BEFORE") if k in clauses] + sorted(set(props) & STREAM_PROPERTIES - {"COMMENT"}):
        if key not in allowed_opts:
            result.error(f"{key} doesn't apply to a stream on {_a(source)}. Remove it.")
    for key in sorted(set(props) & {"APPEND_ONLY", "INSERT_ONLY", "SHOW_INITIAL_ROWS"}):
        if props[key].kind != "bool":
            result.error(f"{key} takes TRUE or FALSE. Write one of them without quotes.")
    if source == "EXTERNAL TABLE" and props.get("INSERT_ONLY", _Val("bool", "FALSE")).text != "TRUE":
        result.error("A stream on an external table needs INSERT_ONLY = TRUE. Add it.")


def _a(source: str) -> str:
    return ("an " if source[0] in "AEIOU" else "a ") + source.lower()


# ---------------------------------------------------------------------------- dynamic tables


def _dynamic_table(result: Result, cur: _Cursor, flags: set[str]) -> None:
    if _clone(result, cur):
        return
    tree = _parse(cur.text)
    props: dict[str, _Val]
    clauses: dict[str, str] = {}
    if isinstance(tree, exp.Create) and isinstance(tree.args.get("properties"), exp.Properties) \
            and any(isinstance(p, exp.DynamicProperty) for p in tree.args["properties"].expressions):
        result.properties["parsed_by"] = "sqlglot"
        props = _dt_from_tree(tree, clauses)
        query = tree.expression
        query_text = query.sql(dialect="snowflake") if query is not None else ""
        has_query = query is not None
    else:
        result.properties["parsed_by"] = "tokens"
        props, query_text, has_query = _dt_from_tokens(cur, clauses)
        query = _parse(query_text) if has_query else None
    result.properties.update(clauses | {k: v.text for k, v in props.items()})
    if has_query:
        result.properties["AS"] = query_text
    _dt_rules(result, props, clauses)
    if "REFRESH USING" in clauses:
        result.warn("REFRESH USING defines a custom incremental refresh, which this PC doesn't check. Check it in "
                    "a Snowflake worksheet before you deploy it.")
        return
    if not has_query:
        result.error("A dynamic table needs AS followed by the query that defines it. Add AS and a SELECT.")
    elif query is None:
        result.error("The dynamic table's query isn't Snowflake SQL this check can read. Check its syntax.")
    elif not isinstance(query, exp.Query):
        result.error("A dynamic table is defined by a query, and this AS holds another kind of statement. Write a "
                     "SELECT after AS.")


def _dt_from_tree(tree: exp.Create, clauses: dict[str, str]) -> dict[str, _Val]:
    props: dict[str, _Val] = {}
    for prop in tree.args["properties"].expressions:
        if isinstance(prop, (exp.DynamicProperty, exp.TransientProperty)):
            continue
        if type(prop) is exp.Property:
            value = prop.args.get("value")
            if isinstance(value, exp.Literal):
                val = _Val("string" if value.is_string else "number", value.name)
            elif isinstance(value, exp.Boolean):
                val = _Val("bool", value.sql().upper())
            elif isinstance(value, (exp.Var, exp.Identifier, exp.Column)):
                val = _Val("name", value.name)
            else:
                val = _Val("group", value.sql(dialect="snowflake") if value is not None else "")
            key = prop.name.upper()
            if key in props:
                raise _Stop(f"{key} is given twice. Keep one.")
            props[key] = val
        elif isinstance(prop, exp.ClusterProperty):
            clauses["CLUSTER BY"] = ", ".join(e.sql(dialect="snowflake") for e in prop.expressions)
        elif isinstance(prop, exp.SchemaCommentProperty):
            props["COMMENT"] = _Val("string", prop.this.name)
        elif isinstance(prop, exp.CopyGrantsProperty):
            clauses["COPY GRANTS"] = "TRUE"
        else:
            clauses[type(prop).__name__] = prop.sql(dialect="snowflake")
    columns = tree.this.expressions if isinstance(tree.this, exp.Schema) else []
    if columns:
        clauses["columns"] = ", ".join(c.sql(dialect="snowflake") for c in columns)
    return props


def _dt_from_tokens(cur: _Cursor, clauses: dict[str, str]) -> tuple[dict[str, _Val], str, bool]:
    props: dict[str, _Val] = {}
    if cur.at(TokenType.L_PAREN):
        clauses["columns"] = cur.group("The column list")
    while not cur.done():
        word = cur.word()
        if word == "AS":
            return props, cur.rest(cur.take()), True
        if cur.accept("CLUSTER", "BY"):
            clauses["CLUSTER BY"] = cur.group("CLUSTER BY")
        elif cur.accept("COPY", "GRANTS"):
            clauses["COPY GRANTS"] = "TRUE"
        elif cur.accept("REQUIRE", "USER"):
            clauses["REQUIRE USER"] = "TRUE"
        elif word in ("FROZEN", "IMMUTABLE") and cur.word(1) == "WHERE":
            cur.i += 2
            clauses[f"{word} WHERE"] = cur.group(f"{word} WHERE")
        elif cur.accept("BACKFILL", "FROM"):
            clauses["BACKFILL FROM"] = str(_name(cur, "The table in BACKFILL FROM"))
        elif cur.accept("EXECUTE", "AS", "USER"):
            clauses["EXECUTE AS USER"] = str(_name(cur, "The user in EXECUTE AS USER"))
        elif cur.accept("REFRESH", "USING"):
            clauses["REFRESH USING"] = cur.group("REFRESH USING")
            return props, "", False
        elif _tags(cur, clauses):
            continue
        elif _is_property(cur):
            props_key = cur.word()
            _properties_into(cur, props)
            if props_key not in DT_PROPERTIES:
                raise _Stop(f"{props_key} isn't a CREATE DYNAMIC TABLE property. Check its spelling against "
                            "Snowflake's CREATE DYNAMIC TABLE reference.")
        else:
            tok = cur.take()
            raise _Stop(f"{tok.text} isn't a clause CREATE DYNAMIC TABLE takes here. Check its spelling and its "
                        "place against Snowflake's CREATE DYNAMIC TABLE reference.")
    return props, "", False


def _dt_rules(result: Result, props: dict[str, _Val], clauses: dict[str, str]) -> None:
    if "WAREHOUSE" not in props:
        result.error("A dynamic table needs a WAREHOUSE for its refreshes. Add WAREHOUSE = followed by the "
                     "warehouse's name.")
    scheduler = props["SCHEDULER"].text.upper() if "SCHEDULER" in props else "ENABLE"
    if scheduler not in ("ENABLE", "DISABLE"):
        result.error("SCHEDULER takes ENABLE or DISABLE. Use one of them.")
    lag = props.get("TARGET_LAG")
    if lag is None and scheduler != "DISABLE":
        result.error("A dynamic table needs a TARGET_LAG, such as TARGET_LAG = '5 minutes' or DOWNSTREAM. Add one.")
    elif lag is not None and scheduler == "DISABLE":
        result.error("TARGET_LAG can't be set with SCHEDULER = DISABLE. Remove one of them.")
    elif lag is not None:
        _target_lag(result, lag)
    for key, allowed in (("REFRESH_MODE", REFRESH_MODES), ("INITIALIZE", INITIALIZE_VALUES)):
        if key in props and props[key].text.upper() not in allowed:
            result.error(f"{key} is {props[key].text}, and Snowflake takes {_either(sorted(allowed))}. Use one of those.")
    if "REQUIRE USER" in clauses and props.get("INITIALIZE", _Val("name", "ON_CREATE")).text.upper() != "ON_SCHEDULE":
        result.error("REQUIRE USER needs INITIALIZE = ON_SCHEDULE. Add it.")
    if props.get("REFRESH_MODE", _Val("name", "")).text.upper() == "CUSTOM_INCREMENTAL" and "REFRESH USING" not in clauses:
        result.error("REFRESH_MODE = CUSTOM_INCREMENTAL needs REFRESH USING instead of AS. Use another refresh "
                     "mode for a query.")


def _either(values: list[str]) -> str:
    return ", ".join(values[:-1]) + " or " + values[-1]


def _target_lag(result: Result, lag: _Val) -> None:
    if lag.kind == "name" and lag.text.upper() == "DOWNSTREAM":
        result.properties["target_lag_seconds"] = None
        return
    words = lag.text.split() if lag.kind == "string" else []
    unit = words[1].upper() if len(words) == 2 else ""
    unit = unit if unit.endswith("S") else unit + "S"
    if len(words) != 2 or not words[0].isdigit() or unit not in LAG_UNITS:
        result.error(f"TARGET_LAG {lag.text} isn't a lag Snowflake reads. Write a quoted whole number and seconds, "
                     "minutes, hours or days, such as '5 minutes', or DOWNSTREAM.")
        return
    seconds = int(words[0]) * LAG_UNITS[unit]
    result.properties["target_lag_seconds"] = seconds
    if seconds < MIN_TARGET_LAG_SECONDS:
        result.error(f"TARGET_LAG '{lag.text}' is under Snowflake's minimum of {MIN_TARGET_LAG_SECONDS} seconds. "
                     "Use '1 minute' or more.")


# ---------------------------------------------------------------------------- task graphs


def _graphs(tasks: list[Result]) -> None:
    """AFTER and FINALIZE across the statements: references resolved, no cycles, documented limits."""
    by_key: dict[str, Result] = {}
    for task in tasks:
        key = task.properties["name_key"]
        if key in by_key:
            task.warn(f"Task {task.name} is created more than once in these statements, and the later one replaces "
                      "the earlier. Keep one definition.")
        by_key[key] = task

    def resolve(ref: str) -> Result | None:
        if ref in by_key:
            return by_key[ref]
        last = ref.split(".")[-1]
        found = [t for k, t in by_key.items() if k.split(".")[-1] == last and ("." not in k or "." not in ref)]
        return found[0] if len(found) == 1 else None

    parents: dict[int, list[Result]] = {id(t): [] for t in by_key.values()}
    children: dict[int, list[Result]] = {id(t): [] for t in by_key.values()}
    for task in by_key.values():
        for ref, shown in zip(task.properties.get("after_keys", []), task.properties.get("AFTER", []), strict=True):
            parent = resolve(ref)
            if parent is None:
                task.warn(f"Predecessor {shown} isn't created in these statements, so this check takes it to exist "
                          "on Snowflake already. Make sure it's in the same schema and has the same owner.")
                continue
            parents[id(task)].append(parent)
            children[id(parent)].append(task)
    finalizers: dict[int, list[Result]] = {}
    for task in by_key.values():
        ref = task.properties.get("finalize_key")
        if ref is None:
            continue
        root = resolve(ref)
        if root is None:
            task.warn(f"Root task {task.properties['FINALIZE']} isn't created in these statements, so this check takes "
                      "it to exist on Snowflake already. Make sure it's a root task in the same schema.")
        elif "AFTER" in root.properties or "finalize_key" in root.properties:
            task.error(f"FINALIZE names {root.name}, which isn't a root task. Name the graph's root task.")
        else:
            finalizers.setdefault(id(root), []).append(task)
    for fins in finalizers.values():
        if len(fins) > 1:
            for fin in fins:
                fin.error(f"Root task {fins[0].properties['FINALIZE']} has {len(fins)} finalizer tasks, and Snowflake "
                          "allows one. Keep one FINALIZE for it.")
    for task in by_key.values():
        if "finalize_key" in task.properties and children[id(task)]:
            task.error(f"Finalizer task {task.name} can't have child tasks. Make the tasks that name it in AFTER "
                       "follow another task.")
        if len(children[id(task)]) > MAX_CHILDREN:
            task.error(f"Task {task.name} has {len(children[id(task)])} child tasks, and Snowflake allows "
                       f"{MAX_CHILDREN}. Add an intermediate task.")
    _cycles(list(by_key.values()), parents)
    _graph_size(list(by_key.values()), parents, children, finalizers)


def _cycles(tasks: list[Result], parents: dict[int, list[Result]]) -> None:
    state: dict[int, int] = {}  # 1 on the current path, 2 finished
    reported: set[int] = set()

    def visit(task: Result, path: list[Result]) -> None:
        state[id(task)] = 1
        path.append(task)
        for parent in parents[id(task)]:
            if state.get(id(parent)) == 1:
                loop = path[path.index(parent):]
                names = [t.name for t in reversed(loop)]
                listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
                for member in loop:
                    if id(member) not in reported:
                        reported.add(id(member))
                        member.error(f"Tasks {listed} form a cycle through AFTER, and a task graph can't loop. "
                                     "Remove one of those AFTER links." if len(names) > 1 else
                                     f"Task {listed} names itself in AFTER, and a task graph can't loop. Remove it "
                                     "from its own AFTER.")
            elif state.get(id(parent)) is None:
                visit(parent, path)
        path.pop()
        state[id(task)] = 2

    for task in tasks:
        if id(task) not in state:
            visit(task, [])


def _graph_size(tasks: list[Result], parents: dict[int, list[Result]], children: dict[int, list[Result]],
                finalizers: dict[int, list[Result]]) -> None:
    seen: set[int] = set()
    for task in tasks:
        if id(task) in seen:
            continue
        group, todo = [], [task]
        seen.add(id(task))
        while todo:
            node = todo.pop()
            group.append(node)
            for other in parents[id(node)] + children[id(node)] + finalizers.get(id(node), []):
                if id(other) not in seen:
                    seen.add(id(other))
                    todo.append(other)
        if len(group) > MAX_GRAPH_TASKS:
            for node in group:
                if not parents[id(node)] and "finalize_key" not in node.properties:
                    node.error(f"The task graph under {node.name} has {len(group)} tasks, and Snowflake allows "
                               f"{MAX_GRAPH_TASKS} in one graph. Split it into smaller graphs.")
