"""No SQL text reaches DuckDB holding a NUL character (U+0000) that DuckDB would stop reading at.

DuckDB 1.x reads a query's text only up to a NUL, so the statement it runs isn't the one written
(DuckDB 2.0 reads the whole text). Text from outside the app reaches DuckDB on many paths: SQL cells, saved queries, project
scripts, templates, semantic views, pipeline steps, a cloned repo, a remote reply. sql.KfDuckDB
guards the SQL it translates; ``install()`` guards everything else at the one place all of it
passes, DuckDB's own Python entry points (a connection's execute, sql, query and the like, the same
module-level functions, and a relation's query, filter and project), so a path that calls DuckDB
directly can't skip it. The package installs it on import (fabric_desktop/__init__.py).

A NUL inside a plain quoted string ('a<NUL>b') is the value the text means, so it is written as the
string's pieces joined by CHR(0), as sql.KfDuckDB does. Anywhere else (a name, a keyword, a comment, an
E'' or $$ string) the text is refused before anything runs. Where the NUL sits is decided by DuckDB's
own tokenizer, run on the text with the NUL swapped for a character the text doesn't hold.
"""

from __future__ import annotations

import bisect
import functools
from typing import Any

import duckdb

REFUSAL = ("The SQL wasn't run, because it holds a NUL character (U+0000) outside a quoted string, and "
           "DuckDB on this PC reads a query only up to that character. Remove the character, or write it "
           "in a string as CHR(0).")

_CONNECTION = ("execute", "executemany", "sql", "query", "from_query", "extract_statements", "table", "view")
_MODULE = (*_CONNECTION, "query_df", "filter", "project", "aggregate", "order", "get_table_names")
# A connection method that takes no text: every other one is guarded (get_table_names reads SQL, and a
# method added in a later DuckDB is guarded until it's listed here).
_CONNECTION_NO_TEXT = frozenset({
    "arrow", "begin", "checkpoint", "close", "commit", "cursor", "description", "df", "disable_profiling",
    "duplicate", "enable_profiling", "fetch_arrow_table", "fetch_df", "fetch_df_chunk", "fetch_record_batch",
    "fetchall", "fetchdf", "fetchmany", "fetchnumpy", "fetchone", "get_profiling_information", "interrupt",
    "list_filesystems", "pl", "query_progress", "rollback", "rowcount", "tf", "to_arrow_reader",
    "to_arrow_table", "torch"})
# A relation method that takes no SQL text: everything else on a relation is guarded (join, cross, the
# per-column aggregates like sum and min, which take expression text, and any added in a later DuckDB).
_RELATION_NO_TEXT = frozenset({
    "arrow", "close", "columns", "describe", "description", "df", "dtypes", "execute", "fetch_arrow_reader",
    "fetch_arrow_table", "fetch_df_chunk", "fetch_record_batch", "fetchall", "fetchdf", "fetchmany",
    "fetchnumpy", "fetchone", "pl", "record_batch", "shape", "show", "tf", "to_arrow_reader",
    "to_arrow_table", "to_df", "torch", "type", "types", "fetch_df", "alias", "sql_query"})


def _methods(owner: Any, no_text: frozenset[str]) -> tuple[str, ...]:
    """Every public method of ``owner`` that may take text, so the guard fails closed on a new one."""
    return tuple(n for n in dir(owner) if not n.startswith("_") and n not in no_text and callable(getattr(owner, n, None)))
_VALUES = frozenset({"parameters", "params", "parameter"})  # bound values, never SQL text


def _stand_in(sql: str) -> str | None:
    """A private-use character ``sql`` doesn't hold: the tokenizer reads it where the NUL was."""
    return next((c for c in map(chr, range(0xE000, 0xF900)) if c not in sql), None)


def _literal_end(data: bytes, start: int) -> int:
    """The index of the quote that closes the plain string opening at ``data[start]``, or -1."""
    i = start + 1
    while i < len(data):
        if data[i] == 0x27:
            if i + 1 < len(data) and data[i + 1] == 0x27:
                i += 2
                continue
            return i
        i += 1
    return -1


def guard(sql: str) -> str:
    """``sql`` as DuckDB reads it whole: unchanged without a NUL, a NUL in a plain string written with
    CHR(0), and any other NUL refused (duckdb.ParserException, before anything runs)."""
    if "\x00" not in sql:
        return sql
    mark = _stand_in(sql)
    if mark is None:
        raise duckdb.ParserException(REFUSAL)
    data = sql.replace("\x00", mark).encode("utf-8")
    try:
        tokens = duckdb.tokenize(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - text the tokenizer can't read is refused, never run
        raise duckdb.ParserException(REFUSAL) from None
    starts = [offset for offset, _kind in tokens]
    marker = mark.encode("utf-8")
    cuts: dict[int, int] = {}  # a string literal's start -> its closing quote, for each holding a NUL
    at = data.find(marker)
    while at >= 0:
        index = bisect.bisect_right(starts, at) - 1
        start = starts[index] if index >= 0 else -1
        kind = str(tokens[index][1]) if index >= 0 else ""
        end = _literal_end(data, start) if kind.endswith("string_const") and data[start:start + 1] == b"'" else -1
        if not start < at < end:
            raise duckdb.ParserException(REFUSAL)
        cuts[start] = end
        at = data.find(marker, at + 1)
    out, done = [], 0
    for start, end in sorted(cuts.items()):
        pieces = data[start + 1:end].decode("utf-8").split(mark)
        parts = []
        for i, piece in enumerate(pieces):
            parts += ["CHR(0)"] * bool(i) + [f"'{piece}'"] * bool(piece)
        out += [data[done:start].decode("utf-8"), "(" + " || ".join(parts) + ")"]
        done = end + 1
    out.append(data[done:].decode("utf-8"))
    return "".join(out)


def _guarded(call: Any) -> Any:
    @functools.wraps(call)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        args = tuple(guard(a) if isinstance(a, str) else a for a in args)
        kwargs = {k: guard(v) if isinstance(v, str) and k not in _VALUES else v for k, v in kwargs.items()}
        return call(*args, **kwargs)

    guarded.__kf_nul_guard__ = True  # type: ignore[attr-defined]
    return guarded


def install() -> None:
    """Guard DuckDB's entry points that read SQL text, in this process. Safe to call again."""
    for owner, names in ((duckdb.DuckDBPyConnection, _methods(duckdb.DuckDBPyConnection, _CONNECTION_NO_TEXT)),
                         (duckdb, _MODULE), (duckdb.DuckDBPyRelation, _methods(duckdb.DuckDBPyRelation, _RELATION_NO_TEXT))):
        for name in names:
            call = getattr(owner, name, None)
            if call is not None and not getattr(call, "__kf_nul_guard__", False):
                setattr(owner, name, _guarded(call))
