"""Pipeline expressions in a local run (orchestration/evaluate.py), the paths the main tests don't
reach: escapes and nesting inside @{...}, malformed expressions, reading members of the wrong kind of
value, and each function refusing an argument it can't use. Each refusal names what was wrong."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration import evaluate as ev
from fabric_desktop.orchestration.evaluate import ExpressionError, Scope


def run(text, scope=None):
    return ev.evaluate(text, scope or Scope(parameters={"Region": "east"}))


@pytest.mark.parametrize("text, expected", [
    ("cost @@{x} and @{pipeline().parameters.Region}", "cost @{x} and east"),   # @@{ keeps an @{ as text
    ("@{concat('it''s', ' } ')}", "it's } "),                                  # '' and } inside a quoted string
    ("@concat(true, null, 1)", "True1"),
    ("@json('null')?.a", None),
    ("@createArray(1)?[5]", None),
    ("@json('{\"a\":1}')?.b", None),
    ("@json('{\"Name\":1}').name", 1),                                         # property names ignore case
    ("@int('1')?.a", None),
    ("@bool(1)", True),
    ("@bool('FALSE')", False),
    ("@contains(json('{\"a\":1}'), 'a')", True),
    ("@greater('b', 'a')", True),
    ("@substring('abc', 1)", "bc"),
    ("@first('')", None),
    ("@last(createArray())", None),
    ("@union(json('{\"a\":1}'), json('{\"b\":2}'))", {"a": 1, "b": 2}),
    ("@intersection(createArray(1, 2, 2), createArray(2, 3))", [2]),
    ("@if(true, 'yes', 'no')", "yes"),
    ("@formatDateTime('2026-11-09T10:05:07.123Z', '''at'' HH:mm fff')", "at 10:05 123"),
    ("@formatDateTime('2026-11-09T10:05:07.1234567Z', 'fffffffK')", "1234560Z"),
    ("@formatDateTime('03/15/2018 12:00:00', 'yyyy-MM-dd')", "2018-03-15"),
])
def test_expressions_that_work(text, expected):
    assert run(text) == expected


@pytest.mark.parametrize("text, says", [
    ("@{concat('a'", "isn't closed"),
    ("@{a{b}c}", "isn't expected"),
    ("@concat('a') 'b'", "after the expression"),
    ("@concat('a'", "isn't closed"),
    ("@createArray(1)[0 1]", "expected ']'"),
    ("@concat('a' 'b')", "expected , or )"),
    ("@region", "did you mean 'region'"),
    ("@", "missing"),
    ("@json('null').a", "of null"),
    ("@createArray(1)[true]", "whole number"),
    ("@createArray(1)[5]", "outside an array of 1"),
    ("@json('{\"a\":1}').b", "no property 'b'"),
    ("@int('1').a", "of a int"),
    ("@add('a', 1)", "needs numbers"),
    ("@int(true)", "boolean"),
    ("@int('x')", "can't convert"),
    ("@float('x')", "can't convert"),
    ("@bool('maybe')", "can't convert"),
    ("@length(1)", "a string or an array"),
    ("@contains(1, 1)", "a string, an array or an object"),
    ("@div(1, 0)", "divide by zero"),
    ("@mod(1, 0)", "divide by zero"),
    ("@greater('a', 1)", "two numbers or two strings"),
    ("@substring('abc', 5)", "start is outside"),
    ("@substring('abc', 1, 5)", "past the end"),
    ("@union(1, 2)", "needs an array"),
    ("@first(1)", "a string or an array"),
    ("@last(1)", "a string or an array"),
    ("@json('{')", "isn't JSON"),
    ("@formatDateTime('2026-11-09', 'M')", "standard date format 'M'"),
    ("@formatDateTime('2026-11-09', 'yyyy Q')", "uses 'Q'"),
    ("@formatDateTime('the ninth')", "isn't a date and time"),
    ("@if('x', 1, 2)", "true or false"),
    ("@length()", "argument(s)"),
    ("@eval('1')", "isn't supported"),
    ("@convertTimeZone('2026-11-09T10:00:00Z', 'UTC', 'Mars Standard Time')", "Windows time zone id"),
])
def test_expressions_that_are_refused_say_why(text, says):
    with pytest.raises(ExpressionError, match=says.replace("(", r"\(").replace(")", r"\)")):
        run(text)


def test_a_join_too_big_is_refused_before_it_runs():
    scope = Scope(variables={"parts": ["x"] * 3})
    with pytest.raises(ExpressionError, match="join"):
        run(f"@join(variables('parts'), '{'y' * (ev.MAX_TEXT // 2 + 1)}')", scope)


def test_a_time_zone_this_pc_has_no_data_for_says_so(monkeypatch):
    import zoneinfo

    def missing(key):
        raise zoneinfo.ZoneInfoNotFoundError(key)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    with pytest.raises(ExpressionError, match="no time zone data for 'Pacific Standard Time'"):
        run("@convertFromUtc('2026-11-09T10:00:00Z', 'Pacific Standard Time')")
