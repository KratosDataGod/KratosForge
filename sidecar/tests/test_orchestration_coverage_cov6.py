"""The expression functions' coverage page (orchestration/coverage.py), the paths the other tests leave: a
function whose example fails here, and an example whose value is an object with whole floats in it."""

from __future__ import annotations

from fabric_desktop.orchestration import coverage as C


def test_a_function_whose_example_fails_here_says_why(monkeypatch):
    monkeypatch.setitem(C.EXAMPLES, "div", ("div(1, 0)", 0))
    row = C._function_row("Math", "div")
    assert row["status"] == "fails" and row["example"] == "div(1, 0)"
    assert row["got"] == "error: div() can't divide by zero"


def test_an_object_value_compares_its_whole_floats_as_ints(monkeypatch):
    monkeypatch.setitem(C.EXAMPLES, "json", ("json('{\"a\": 2.0, \"b\": [1.0, 1.5]}')", {"a": 2, "b": [1, 1.5]}))
    row = C._function_row("Conversion", "json")
    assert row["status"] == "runs" and row["got"] == {"a": 2.0, "b": [1.0, 1.5]}
    assert C._canonical({"b": [1.0], "a": 2.0}) == '{"a": 2, "b": [1]}'
