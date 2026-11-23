"""Snowflake's and T-SQL's regular expression parameters as RE2 reads them (WR-257).

Both take a parameters string after the pattern: c (case-sensitive) and i (case-insensitive), the last one
winning; m, multi-line mode, where ^ and $ match at each line; and s, where . matches a line break.
DuckDB's own options read m as "newline sensitive" (only . changes) and apply c, i, m, s in order, so
REGEXP_LIKE(x, '^b$', 'm') missed a second line and 'sm' turned s off again. Here m and s become RE2's
(?m) and (?s) at the front of the pattern, and only c, i (and e, Snowflake's extract) stay as options.
"""

from __future__ import annotations

from sqlglot import exp

# A pattern that matches nothing, whatever flags are put in front of it: ^ can't follow an 'a', even in (?m).
NO_MATCH = "a^a"


def inline_flags(node: exp.Expr, key: str) -> None:
    """``node``'s parameters (its arg ``key``, written in the query) with m and s moved into the pattern."""
    flags = node.args.get(key)
    if not (isinstance(flags, exp.Literal) and flags.is_string):
        return
    inline = "".join(c for c in "ms" if c in flags.name)
    if not inline:
        return
    pattern = node.expression
    prefix = exp.Literal.string(f"(?{inline})")
    node.set("expression", exp.Literal.string(prefix.name + pattern.name)
             if isinstance(pattern, exp.Literal) and pattern.is_string
             else exp.DPipe(this=prefix, expression=pattern.copy()))
    kept = "".join(c for c in flags.name if c not in "ms")
    node.set(key, exp.Literal.string(kept) if kept else None)


def case_flag_only_i(node: exp.Expr, key: str = "parameters") -> None:
    """REGEXP_COUNT and REGEXP_INSTR: sqlglot writes their parameters as an inline (?...) group, where c isn't a
    flag, so a 'c' stopped the query. c is the default, so c and i become i alone when i comes last, else nothing."""
    params = node.args.get(key)
    if isinstance(params, exp.Literal) and params.is_string and "c" in params.name:
        case = [c for c in params.name if c in "ci"]
        kept = "".join(c for c in params.name if c not in "ci") + ("i" if case[-1] == "i" else "")
        node.set(key, exp.Literal.string(kept) if kept else None)
