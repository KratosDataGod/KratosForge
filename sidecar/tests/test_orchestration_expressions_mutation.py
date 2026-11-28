"""Mutation-testing follow-up for reading pipeline expressions (orchestration/expressions.py):
whole values and interpolations at the start of a string, an escaped @@{ next to a real one, quotes
inside an interpolation, a value typed as something other than Expression, member chains with ?.,
[..] and a trailing dot, calls with nothing after them, and pipeline() with no member name."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration import expressions as ex
from fabric_desktop.orchestration.expressions import Reference


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def texts(node):
    return [(e.text, e.interpolated, e.problem) for e in ex.scan(node)]


# ---- strings


def test_a_whole_value_expression_is_not_interpolated():
    assert texts("@concat('a', 'b')") == [("concat('a', 'b')", False, "")]


def test_an_interpolation_at_the_very_start_of_a_string_is_read():
    assert texts("@{pipeline().RunId} done") == [("pipeline().RunId", True, "")]


def test_a_real_interpolation_right_after_an_escaped_one_is_read():
    assert texts("x@@{@{b}}") == [("b", True, "")]


def test_a_brace_inside_quotes_at_the_start_of_an_interpolation_is_text():
    assert texts("@{'}'}") == [("'}'", True, "")]


def test_a_quote_closes_and_the_interpolation_after_it_is_read():
    assert texts("@{'a'} b @{c}") == [("'a'", True, ""), ("c", True, "")]


def test_a_quote_at_the_very_end_of_an_open_interpolation():
    assert texts("@{'a'") == [("'a'", True, "an @{ isn't closed with }")]


def test_an_unclosed_interpolation_keeps_its_whole_text():
    assert texts("Run @{abc") == [("abc", True, "an @{ isn't closed with }")]


# ---- Expression objects


def test_only_a_value_typed_expression_is_read_as_one():
    assert texts({"value": "plain", "type": "String"}) == []
    assert texts({"value": "pipeline().RunId", "type": "Expression"}) == [("pipeline().RunId", False, "")]


def test_an_expression_value_that_starts_with_an_interpolation_is_read_as_its_parts():
    assert texts({"type": "Expression", "value": "@{a}-@{b}"}) == [("a", True, ""), ("b", True, "")]


def test_a_whole_expression_value_is_not_interpolated():
    assert texts({"type": "Expression", "value": "@pipeline().RunId"}) == [("pipeline().RunId", False, "")]


# ---- tokens


def test_a_doubled_quote_in_a_string_is_one_quote():
    assert ex.tokens("'it''s'") == ([("string", "it's")], "")
    assert ex.tokens("'a''bc'") == ([("string", "a'bc")], "")
    assert ex.tokens("'a'") == ([("string", "a")], "")
    assert ex.tokens("'a''")[1] == "a quoted string isn't closed"


# ---- member chains


def test_a_member_chain_with_null_safe_steps():
    assert ex.references("activity('A')?.output?['rows']") == [Reference("activity", "A", "output.rows")]


def test_a_member_chain_of_names_and_brackets():
    assert ex.references("activity('A').output") == [Reference("activity", "A", "output")]
    assert ex.references("activity('A')['rows'][0].x") == [Reference("activity", "A", "rows.0.x")]
    assert ex.references("activity('A')['x'].y") == [Reference("activity", "A", "x.y")]
    assert ex.references("activity('A')['x']") == [Reference("activity", "A", "x")]


def test_a_member_chain_that_stops_early():
    assert ex.references("activity('A').") == [Reference("activity", "A", "")]
    assert ex.references("activity('A')?") == [Reference("activity", "A", "")]
    assert ex.references("activity('A')[") == [Reference("activity", "A", "")]


# ---- calls


def test_a_call_with_nothing_after_it():
    assert ex.references("activity('A')") == [Reference("activity", "A", "")]
    assert ex.references("item()") == [Reference("item", "item", "")]


def test_only_pipeline_and_item_have_members_read():
    assert ex.references("utcnow().x") == []


def test_pipeline_parameters_with_no_name_is_a_system_reference():
    assert ex.references("pipeline().parameters") == [Reference("system", "parameters", "")]
    assert ex.references("pipeline().parameters.day") == [Reference("parameter", "day", "")]
