"""Local T-SQL's errors in SQL Server's own words (WR-233), with its error number and severity (WR-234).

A Fabric Warehouse says "Invalid column name 'x'." (error 207), "Invalid object name 'x'." (208) and
"Ambiguous column name 'x'." (209), where DuckDB says "Binder Error: Referenced column ..." or "Catalog Error:
Table with name ... does not exist! Did you mean "pg_catalog.pg_settings"?". Only the DuckDB messages that
map to one of those with certainty are rewritten. Any other message keeps DuckDB's words, without a suggestion
that names one of DuckDB's own system tables, which a warehouse project never has. The caller logs DuckDB's
original text, so support can still read it.

A message that has a SQL Server number with certainty is shown as SQL Server prints it, "Msg 207, Level 16:
Invalid column name 'x'.", alone or in a script. Others show no number, because none is guessed.

WR-247: arithmetic and conversion errors too. An int, bigint or decimal that overflows in + - * or ABS is 8115,
"Arithmetic overflow error converting expression to data type int."; text that isn't a number for an integer is
245, for a decimal or float 8114, and for a date or time 241. The translator (tsql.py) raises some of these
itself with SQL Server's words (DuckDB adds "Invalid Input Error: "), such as 8115 for SUM of ints and 245 with
the value. A message whose SQL Server words depend on what DuckDB's text doesn't say (a constant, or the type
of a value DuckDB names only by its value) keeps DuckDB's words.
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
_RAISED = "Invalid Input Error: "  # how DuckDB's error() gives a message the translator wrote in SQL Server's words
_INTEGERS = {"INT32": "int", "INT64": "bigint", "INT16": "smallint", "UINT8": "tinyint"}
_ARITHMETIC = re.compile(r"^Out of Range Error: Overflow in (?:addition|subtraction|multiplication) of "
                         r"(?P<kind>INT32|INT64|DECIMAL\(\d+\)) \(")
_ABS = re.compile(r"^Out of Range Error: Overflow on abs\((?P<value>-\d+)\)$")
_ABS_KINDS = {str(-2 ** 31): "int", str(-2 ** 63): "bigint"}  # the one value of each whose ABS overflows
# A column's value that doesn't fit an integer type; a constant has its own words in SQL Server (220).
_COLUMN_CAST = re.compile(r"^Conversion Error: Type (?:U?INT\d+|DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT) with value \S+ "
                          r"can't be cast because the value is out of range for the destination type "
                          r"(?P<kind>INT32|INT64|INT16|UINT8) when casting from source column ")
_TEXT_TO = re.compile(r"""^Conversion Error: Could not convert string (?P<q>["'])(?P<value>.*)(?P=q) to """
                      r"(?P<kind>INT32|INT64|INT16|UINT8|DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT)$")
_A_NUMBER = re.compile(r"^\s*[+-]?(?:\d+\.?\d*|\.\d+)\s*$")
_DATE_TIME = re.compile(r"^Conversion Error: (?:invalid (?:date|time|timestamp) field format|"
                        r"(?P<range>(?:date|time|timestamp) field value out of range)): \"")
_DATE_TIME_TEXT = "Conversion failed when converting date and/or time from character string."
# datetime and smalldatetime say 242 for a date that isn't there; the translator checks their range in these words
_OLD_DATE_TIMES = re.compile(r"data type to a (?:small)?datetime data type resulted in an out-of-range value")
# SQL Server's number and severity for each message this module or the local engine gives in its words
_NUMBERS = [
    (re.compile(r"^Invalid column name '"), 207),
    (re.compile(r"^Invalid object name '"), 208),
    (re.compile(r"^Ambiguous column name '"), 209),
    (re.compile(r"^Duplicate common table expression name '.*' was specified\.$"), 239),
    (re.compile(r"^Divide by zero error encountered\.$"), 8134),
    (re.compile(r"^Operand data type bit is invalid for (?:sum|avg|min|max) operator\.$"), 8117),
    (re.compile(r"^Arithmetic overflow error converting expression to data type \w+\.$"), 8115),
    (re.compile(r"^Arithmetic overflow error for type int, value = -?\d+\.\d{6}\.$"), 232),
    (re.compile(r"^Conversion failed when converting the n?varchar value '.*' to data type \w+\.$", re.DOTALL), 245),
    (re.compile(r"^The conversion of the n?varchar value '.*' overflowed an int column\.$", re.DOTALL), 248),
    (re.compile(r"^Error converting data type varchar to (?:numeric|float|real)\.$"), 8114),
    (re.compile(r"^Conversion failed when converting date and/or time from character string\.$"), 241),
    (re.compile(r"^The conversion of a varchar data type to a (?:small)?datetime data type resulted in an "
                r"out-of-range value\.$"), 242),
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
    if first.startswith(_RAISED) and numbered(own := message.strip()[len(_RAISED):]) != own:
        return own  # the translator's own words, such as "Divide by zero error encountered."
    if (arithmetic := _arithmetic(first, sql)) is not None:
        return arithmetic
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


def _arithmetic(first: str, sql: str) -> str | None:
    """WR-247: SQL Server's words for an overflow or a failed conversion, where they are certain."""
    overflow = "Arithmetic overflow error converting expression to data type {}."
    if found := _ARITHMETIC.match(first):
        return overflow.format(_INTEGERS.get(found["kind"], "numeric"))
    if (found := _ABS.fullmatch(first)) and found["value"] in _ABS_KINDS:
        return overflow.format(_ABS_KINDS[found["value"]])
    if found := _COLUMN_CAST.match(first):
        return overflow.format(_INTEGERS[found["kind"]])
    if found := _TEXT_TO.fullmatch(first):
        kind, value = found["kind"], found["value"]
        if kind in _INTEGERS:
            # A whole number too big for the type has other words (248 and 244), and '' is 0 in SQL Server
            if re.fullmatch(r"\s*(?:[+-]?\d+)?\s*", value):
                return None
            return f"Conversion failed when converting the varchar value '{value}' to data type {_INTEGERS[kind]}."
        if _A_NUMBER.fullmatch(value) or (not kind.startswith("DECIMAL") and not value.strip()):
            return None  # too big for the decimal (8115 in other words), or '' that a float takes as 0
        return "Error converting data type varchar to {}.".format(
            "numeric" if kind.startswith("DECIMAL") else "float" if kind == "DOUBLE" else "real")
    if (found := _DATE_TIME.match(first)) and not (found["range"] and _OLD_DATE_TIMES.search(sql)):
        return _DATE_TIME_TEXT
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
        if (pattern.fullmatch if pattern.pattern.endswith("$") else pattern.match)(message):
            return f"Msg {number}, Level {LEVEL}: {message}"
    return message
