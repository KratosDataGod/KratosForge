"""A number written without its leading zero (.3), as the reference's own sub(10.3, .3) does, is read
where a value starts; a dot after a value is still a member read."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration.evaluate import ExpressionError, Scope, evaluate
from fabric_desktop.orchestration.expressions import tokens


@pytest.mark.parametrize("expr, want", [
    ("@sub(10.3, .3)", 10.0),
    ("@add(-.5, 1)", 0.5),
    ("@createArray(.25, .5)[1]", 0.5),
    ("@json('{\"a\": {\"b\": 2}}').a.b", 2),
    ("@createArray(json('{\"x\": 7}'))[0].x", 7),
])
def test_leading_dot_numbers(expr, want):
    assert evaluate(expr, Scope()) == want


def test_a_dot_after_a_value_is_still_a_member_read():
    toks, error = tokens("pipeline().parameters.x")
    assert not error and [k for k, _ in toks].count("number") == 0
    with pytest.raises(ExpressionError):
        evaluate("@add(1 .5)", Scope())   # not a number after a value: no silent 1.5
