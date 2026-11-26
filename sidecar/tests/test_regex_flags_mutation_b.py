"""regex_flags.py from a mutation run: a pattern written as text gets m and s put in front as one text, and a
pattern that isn't text (a column, a number) is joined to them with ||, so its value is never read as SQL text."""

import sqlglot
from sqlglot import exp

from fabric_desktop.regex_flags import inline_flags


def _like(pattern: str, flags: str) -> exp.Expr:
    return sqlglot.parse_one(f"SELECT REGEXP_LIKE(x, {pattern}, '{flags}')", read="snowflake").find(exp.RegexpLike)


def test_a_text_pattern_becomes_one_text_with_the_flags_in_front():
    node = _like("'^b$'", "ms")
    inline_flags(node, "flag")
    assert node.expression == exp.Literal.string("(?ms)^b$") and node.args.get("flag") is None


def test_a_column_or_number_pattern_is_joined_to_the_flags():
    for pattern in ("y", "5"):
        node = _like(pattern, "sc")
        inline_flags(node, "flag")
        joined = node.expression
        assert isinstance(joined, exp.DPipe) and joined.this == exp.Literal.string("(?s)")
        assert joined.expression.sql() == pattern
        assert node.args.get("flag") == exp.Literal.string("c")
