"""Local T-SQL's errors in SQL Server's own words (WR-233), with its error number and severity (WR-234).

A Fabric Warehouse says "Invalid column name 'x'." (error 207), "Invalid object name 'x'." (208) and
"Ambiguous column name 'x'." (209), where DuckDB says "Binder Error: Referenced column ..." or "Catalog Error:
Table with name ... does not exist! Did you mean "pg_catalog.pg_settings"?". Only the DuckDB messages that
map to one of those with certainty are rewritten. Any other message keeps DuckDB's words, without a suggestion
that names one of DuckDB's own system tables, which a warehouse project never has. The caller logs DuckDB's
original text, so support can still read it.

A message that has a SQL Server number with certainty (207, 208, 209, 239 and 8134 for now) is shown as SQL
Server prints it, "Msg 207, Level 16: Invalid column name 'x'.", alone or in a script. Others show no number:
none is guessed.
"""

from __future__ import annotations

import re

_COLUMN = [
    re.compile(r'^Binder Error: Referenced column "(?P<name>[^"]+)" not found in FROM clause!'),
    re.compile(r'^Binder Error: Table "[^"]+" does not have a column named "(?P<name>[^"]+)"'),
    re.compile(r'^Binder Error: Table "[^"]+" does not have a column with name "(?P<name>[^"]+)"'),
    re.compile(r"^Binder Error: Referenced update column (?P<name>.+?) not found in table!"),
]
_AMBIGUOUS = re.compile(r'^Binder Error: Ambiguous reference to column name "(?P<name>[^"]+)"')
_OBJECT = [
    re.compile(r'^Catalog Error: Table with name "(?P<name>[^"]+)" does not exist because schema "[^"]+" '
               r"does not exist\."),
    re.compile(r"^Catalog Error: Table with name (?P<name>\S+) does not exist!"),
]
_BY_ZERO = "Invalid Input Error: Divide by zero error encountered."
# WR-258: a + whose side is text of a type the translator couldn't work out reaches DuckDB as a +, which DuckDB
# refuses for text. The first line is the same on DuckDB 1.5.6 and 2.0.0.
_PLUS = re.compile(r"^Binder Error: No function matches the given name and argument types "
                   r"'\+\((?P<a>[A-Z_]+), (?P<b>[A-Z_]+)\)'")
_TEXT_KINDS = {"VARCHAR", "STRING_LITERAL"}
PLUS_UNTYPED = ("Couldn't tell whether + adds numbers or joins text here, because a value's type can't be worked out "
                "on the local engine (T-SQL adds a number to text as numbers and joins two strings). CAST the value "
                "to VARCHAR to join it, or to a number to add it.")
# SQL Server's number and severity for each message this module or the local engine gives in its words
_NUMBERS = [
    (re.compile(r"^Invalid column name '"), 207),
    (re.compile(r"^Invalid object name '"), 208),
    (re.compile(r"^Ambiguous column name '"), 209),
    (re.compile(r"^Duplicate common table expression name '.*' was specified\.$"), 239),
    (re.compile(r"^Divide by zero error encountered\.$"), 8134),
]
LEVEL = 16  # each of these is a user error that stops the statement, as SQL Server grades it
# A suggestion of one of DuckDB's own catalog tables: a warehouse project has none of them.
_SYSTEM_HINT = re.compile(r'\n?Did you mean "(?:pg_catalog\.|pg_|information_schema\.|duckdb_|sqlite_|system\.|temp\.)'
                          r'[^"]*"\?', re.IGNORECASE)
# A statement a warehouse answers with another error than 208, such as DROP's 3701.
_OWN_MISSING_ERROR = {"drop", "alter", "truncate"}


def warehouse(message: str, sql: str, kind: str) -> str | None:
    """SQL Server's message for DuckDB's ``message`` from statement ``sql`` of ``kind``, or None when there isn't
    one that's certain."""
    first = message.strip().split("\n", 1)[0]
    if first == _BY_ZERO:
        return first.split(": ", 1)[1]
    if (found := _PLUS.match(first)) and {found["a"], found["b"]} & _TEXT_KINDS:
        return PLUS_UNTYPED
    for pattern in _COLUMN:
        if found := pattern.match(first):
            return f"Invalid column name '{found['name']}'."
    if found := _AMBIGUOUS.match(first):
        return f"Ambiguous column name '{found['name']}'."
    if kind in _OWN_MISSING_ERROR:
        return None
    for pattern in _OBJECT:
        if found := pattern.match(first):
            return f"Invalid object name '{_as_written(found['name'], sql)}'."
    return None


def without_system_hint(message: str) -> str:
    """``message`` without a "Did you mean" that names one of DuckDB's own system tables."""
    return _SYSTEM_HINT.sub("", message)


def _as_written(name: str, sql: str) -> str:
    """``name`` with its schema, when DuckDB left the schema out and the statement names it in one way only."""
    if "." in name:
        return name
    part = r'(?:"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
    word = r'(?:"' + re.escape(name) + r'"|\[' + re.escape(name) + r'\]|' + re.escape(name) + r'(?![\w$]))'
    found = {m.group(1).strip('"[]').lower(): m.group(1).strip('"[]')
             for m in re.finditer(rf"(?<![\w$.\"\]])({part})\s*\.\s*{word}", sql, re.IGNORECASE)}
    return f"{next(iter(found.values()))}.{name}" if len(found) == 1 else name


def numbered(message: str) -> str:
    """``message`` with SQL Server's number and severity in front, as SQL Server prints them, when it is one of
    the messages above; otherwise as it is."""
    for pattern, number in _NUMBERS:
        if pattern.match(message):
            return f"Msg {number}, Level {LEVEL}: {message}"
    return message
