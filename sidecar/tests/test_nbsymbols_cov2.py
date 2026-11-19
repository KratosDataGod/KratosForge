"""Find references across a notebook's Python cells (nbsymbols.py) where a scope binds the name itself in
less common ways: a starred comprehension target, **kwargs, nonlocal, except, match, nested lambdas and
comprehensions, and where annotations and later generators are read."""

from __future__ import annotations

import ast

from fabric_desktop import nbsymbols
from fabric_desktop.nbsymbols import find_symbol


def code(source: str) -> dict:
    return {"type": "code", "source": source}


def spots(source: str, name: str = "total") -> list[tuple[str, int, int]]:
    """(kind, line, char col) of each place ``name`` is found as the module's name."""
    found = find_symbol([code(source)], 0, 0, name)
    starts = nbsymbols._offsets(source)
    return [(p["kind"], p["line"], p["from"] - starts[p["line"]]) for p in found["places"]]


def test_a_starred_comprehension_target_is_the_comprehensions_own_name():
    src = "total = 1\nx = [total for *total, b in rows]\ny = [b for *a, b in rows]"
    assert spots(src) == [("definition", 1, 0)]
    assert find_symbol([code(src)], 0, 0, "total")["anywhere"] == 3


def test_kwargs_and_nonlocal_names_are_the_functions_not_the_modules():
    src = ("total = 1\n"
           "def f(**total):\n    return total\n"
           "def g():\n    def h():\n        nonlocal total\n        total = 2\n    total = 3\n    return total\n"
           "print(total)")
    assert spots(src) == [("definition", 1, 0), ("reference", 10, 6)]


def test_except_and_match_names_inside_a_function_make_it_local_there_and_in_its_lambdas():
    src = ("total = 1\n"
           "def f():\n    g = lambda: total\n    c = [total for _ in x]\n"
           "    try:\n        pass\n    except E as total:\n        pass\n    return total\n"
           "def k(v):\n    match v:\n        case [total]:\n            return total\n    return total\n"
           "def m(v):\n    match v:\n        case {**total}:\n            return total\n"
           "print(total)")
    assert spots(src) == [("definition", 1, 0), ("reference", 19, 6)]


def test_annotations_and_return_types_are_read_where_the_def_is():
    src = "total = int\ndef f(*a: total, **k: total) -> total:\n    pass"
    assert spots(src) == [("definition", 1, 0), ("reference", 2, 10), ("reference", 2, 22), ("reference", 2, 32)]


def test_a_later_generators_iterable_and_conditions_are_read_inside_the_comprehension():
    src = "total = [1]\nx = [a for a in total for b in total if total]\ny = [total for total in total for c in total]"
    assert spots(src) == [("definition", 1, 0), ("reference", 2, 16), ("reference", 2, 31), ("reference", 2, 40),
                          ("reference", 3, 24)]  # in y only the first iterable is the module's


def test_a_nonlocal_name_takes_what_the_enclosing_function_has():
    src = "total = 1\ndef outer():\n    total = 2\n    def inner():\n        nonlocal total\n        return total\n"
    assert spots(src) == [("definition", 1, 0)]


def test_a_node_that_is_not_a_scope_binds_nothing():
    scope = nbsymbols._scope_of(ast.parse("total = 1").body[0])
    assert scope.bound == scope.globals == scope.nonlocals == set()
