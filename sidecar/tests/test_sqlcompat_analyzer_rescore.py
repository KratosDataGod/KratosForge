"""Mutation batch 243: a parse error placed on line 0 has no place in the text."""

from __future__ import annotations

from fabric_desktop.sqlcompat import analyzer


def test_a_parse_error_on_line_0_has_no_offset():
    text = "ab\ncd"
    assert analyzer._error_offset(text, {"line": 0, "col": 2, "highlight": "cd"}) is None
    assert analyzer._error_offset(text, {"line": 2, "col": 2, "highlight": "cd"}) == 3
