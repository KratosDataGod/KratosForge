"""Local T-SQL's errors for a name that isn't there, in SQL Server's own words (WR-233).

A Fabric Warehouse says "Invalid column name 'x'." (error 207), "Invalid object name 'x'." (208) and
"Ambiguous column name 'x'." (209), where DuckDB says "Binder Error: Referenced column ..." or "Catalog Error:
Table with name ... does not exist! Did you mean "pg_catalog.pg_settings"?". Only the DuckDB messages that
map to one of those with certainty are rewritten. Any other message keeps DuckDB's words, without a suggestion
that names one of DuckDB's own system tables, which a warehouse project never has. The caller logs DuckDB's
original text, so support can still read it.
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
# A suggestion of one of DuckDB's own catalog tables: a warehouse project has none of them.
_SYSTEM_HINT = re.compile(r'\n?Did you mean "(?:pg_catalog\.|pg_|information_schema\.|duckdb_|sqlite_|system\.|temp\.)'
                          r'[^"]*"\?', re.IGNORECASE)
# A statement a warehouse answers with another error than 208, such as DROP's 3701.
_OWN_MISSING_ERROR = {"drop", "alter", "truncate"}


def warehouse(message: str, sql: str, kind: str) -> str | None:
    """SQL Server's message for DuckDB's ``message`` from statement ``sql`` of ``kind``, or None when there isn't
    one that's certain."""
    first = message.strip().split("\n", 1)[0]
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
