"""Format SQL for the SQL query page, without changing what it means.

sqlglot parses the SQL in the chosen dialect and prints it back with one clause per line, nested
queries and CTEs indented, and keywords in capitals. Before the page gets the result, the formatted
text is checked against the original:

- it parses to the same tree (same statements, tables, columns, functions, literals);
- it has the same identifiers, string and number literals and function names. Identifier case is
  kept exactly; only function names and date-part words (``'month'``, ``day``) may change case;
- every comment is still there. sqlglot prints comments as ``/* */``; ones that were ``--``
  comments at the end of a line are written back as ``--``.

If any check fails (sqlglot would rename a function, drop a comment, add an alias, or can't read the
SQL), the original is returned unchanged with a warning that says why. Formatting never runs SQL.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import sqlglot
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.tokens import Token, TokenType

from . import sql_dialects

# Date and time parts that sqlglot may print in capitals ('month' -> 'MONTH'); same meaning in every dialect here.
_DATE_PARTS = frozenset("""
year yy yyyy quarter qq q month mm m mon week wk ww dayofyear dy y day dd d weekday dw hour hh minute mi n
second ss s millisecond ms microsecond mcs nanosecond ns iso_week isowk isoww epoch dow doy decade century
millennium
""".split())
_IDENTS = (TokenType.VAR, TokenType.IDENTIFIER)


def format_sql(query: str, dialect: str | None = None) -> dict[str, Any]:
    """``{"sql", "changed", "warning"}``: the formatted SQL, or the original and why it was left alone."""
    name = sql_dialects.sqlglot_name(dialect)
    unchanged = lambda why: {"sql": query, "changed": False, "warning": why}  # noqa: E731
    if not query.strip():
        return {"sql": query, "changed": False, "warning": ""}
    try:
        statements = [s for s in sqlglot.parse(query, read=name) if s is not None]
    except (ParseError, SqlglotError) as exc:
        return unchanged(f"Format SQL left the query as it was: it couldn't be read as "
                         f"{sql_dialects.get(dialect)['label']}. {_first_line(exc)}")
    if not statements:
        return {"sql": query, "changed": False, "warning": ""}  # only comments
    try:
        pretty = ";\n\n".join(s.sql(name, pretty=True) for s in statements)
    except SqlglotError as exc:
        return unchanged(f"Format SQL left the query as it was: {_first_line(exc)}")
    if query.rstrip().endswith(";"):
        pretty += ";"
    pretty = _restore_line_comments(pretty, _line_comments(query))
    problem = _difference(query, pretty, name)
    if problem:
        return unchanged(f"Format SQL left the query as it was, because formatting would {problem}.")
    if query.endswith("\n"):
        pretty += "\n"
    return {"sql": pretty, "changed": pretty != query, "warning": ""}


def _first_line(exc: Exception) -> str:
    """sqlglot's first error in plain words: "Expected table name but got 'where' (line 1, column 17)"."""
    errors = getattr(exc, "errors", None) or []
    if errors and errors[0].get("description"):
        first = errors[0]
        text = re.sub(r"<Token token_type: [^,]+, text: ([^,]*),.*?>", r"'\1'", first["description"])
        return f"{text} (line {first.get('line')}, column {first.get('col')})." if first.get("line") else f"{text}."
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def _difference(before: str, after: str, dialect: str) -> str:
    """What formatting changed besides layout and case, in words; empty when nothing did."""
    try:
        old = [s for s in sqlglot.parse(before, read=dialect) if s is not None]
        new = [s for s in sqlglot.parse(after, read=dialect) if s is not None]
    except (ParseError, SqlglotError):
        return "produce SQL that doesn't parse"
    if old != new:  # sqlglot compares trees by meaning, ignoring layout and comments
        return "change the query's structure"
    a, b = _parts(before, dialect), _parts(after, dialect)
    for kind in ("identifier", "literal", "function", "comment"):
        lost = a[kind] - b[kind]
        added = b[kind] - a[kind]
        if lost or added:
            sample = next(iter(lost or added))
            verb = "remove" if lost else "add"
            return f"{verb} the {kind} {sample.strip()!r}" if kind != "comment" else f"{verb} a comment ({sample.strip()[:40]!r})"
    return ""


def _parts(query: str, dialect: str) -> dict[str, Counter]:
    """The identifiers, literals, function names and comments of ``query``, as multisets."""
    tokens: list[Token] = sqlglot.Dialect.get_or_raise(dialect).tokenize(query)
    parts = {k: Counter() for k in ("identifier", "literal", "function", "comment")}
    for i, tok in enumerate(tokens):
        parts["comment"].update(c.strip() for c in tok.comments)
        follows_paren = i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.L_PAREN
        if tok.token_type in _IDENTS and follows_paren:
            parts["function"][tok.text.upper()] += 1
        elif tok.token_type in _IDENTS:
            text = tok.text.lower() if tok.text.lower() in _DATE_PARTS else tok.text
            parts["identifier"][(tok.token_type == TokenType.IDENTIFIER, text)] += 1
        elif tok.token_type in (TokenType.STRING, TokenType.NATIONAL_STRING, TokenType.NUMBER):
            text = tok.text.lower() if tok.text.lower() in _DATE_PARTS else tok.text
            parts["literal"][text] += 1
    return parts


def _line_comments(query: str) -> set[str]:
    """The text of every ``--`` comment in ``query`` (outside strings and quoted names)."""
    found: set[str] = set()
    i, n = 0, len(query)
    closing = {"'": "'", '"': '"', "`": "`", "[": "]"}
    while i < n:
        ch = query[i]
        if ch in closing:
            end = query.find(closing[ch], i + 1)
            while end != -1 and closing[ch] != "]" and end + 1 < n and query[end + 1] == closing[ch]:
                end = query.find(closing[ch], end + 2)  # a doubled quote inside the literal
            i = n if end == -1 else end + 1
        elif query.startswith("/*", i):
            end = query.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif query.startswith("--", i):
            end = query.find("\n", i)
            end = n if end == -1 else end
            found.add(query[i + 2:end].strip())
            i = end
        else:
            i += 1
    return found


def _restore_line_comments(text: str, line_comments: set[str]) -> str:
    """Write ``/* x */`` back as ``-- x`` where x was a ``--`` comment and nothing follows it on the line."""
    if not line_comments:
        return text

    def swap(m: re.Match) -> str:
        body = m.group(1)
        return f"-- {body.strip()}" if body.strip() in line_comments and "*/" not in body else m.group(0)

    return re.sub(r"/\*(.*?)\*/(?=[ \t]*(?:\n|$))", swap, text)
