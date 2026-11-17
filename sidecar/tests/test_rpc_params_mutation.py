"""Mutation batch 42: the RPC parameter check (rpc_params.py, #1173).

The fuzz test builds its wrong values from ``rpc_params`` itself, so a change to how a type is read
also changes what the fuzz tries. These tests use stand-in methods with fixed signatures and say
exactly which calls are taken and which are refused.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from fabric_desktop import rpc_params


class Svc:
    def plain(self, name: str, on: bool, n: int, x: float, items: list, obj: dict) -> None: ...

    def optional(self, path: str | None = None, count: Optional[int] = None) -> None: ...

    def anything(self, a: Any, b: "int | Any", c=None) -> None: ...

    def kwargs(self, name: str, **rest: Any) -> None: ...

    def generic(self, rows: list[int], opts: dict[str, int]) -> None: ...


def _ok(method, params):
    rpc_params.check("m", getattr(Svc, method), params)


def _refused(method, params) -> str:
    with pytest.raises(TypeError) as e:
        rpc_params.check("m", getattr(Svc, method), params)
    return str(e.value)


GOOD = {"name": "a", "on": True, "n": 1, "x": 1.5, "items": [], "obj": {}}


def test_right_types_pass():
    _ok("plain", GOOD)
    _ok("plain", GOOD | {"x": 2})  # a whole number is a number


@pytest.mark.parametrize("key, value, want, got", [
    ("name", 7, "text", "a whole number"),
    ("on", 1, "true or false", "a whole number"),
    ("n", True, "a whole number", "true or false"),
    ("n", 1.5, "a whole number", "a number"),
    ("x", "1", "a number", "text"),
    ("x", True, "a number", "true or false"),
    ("items", {}, "a list", "an object"),
    ("obj", [], "an object", "a list"),
    ("name", None, "text", "null"),
])
def test_a_plain_type_refuses_every_other_json_type(key, value, want, got):
    assert _refused("plain", GOOD | {key: value}) == f"m: {key} must be {want}, not {got}"


def test_an_optional_type_takes_null_and_its_type_only():
    _ok("optional", {})
    _ok("optional", {"path": None, "count": None})
    _ok("optional", {"path": "p", "count": 3})
    assert _refused("optional", {"path": 7}) == "m: path must be text or null, not a whole number"
    assert _refused("optional", {"count": "3"}) == "m: count must be a whole number or null, not text"


def test_any_or_a_union_with_any_or_no_annotation_takes_everything():
    for v in (1, "s", True, None, [1], {"a": 1}):
        _ok("anything", {"a": v, "b": v, "c": v})


def test_only_the_outer_type_of_a_generic_is_checked():
    _ok("generic", {"rows": ["not an int"], "opts": {"k": "v"}})
    assert _refused("generic", {"rows": {}, "opts": {}}) == "m: rows must be a list, not an object"


def test_a_parameter_without_a_default_is_required_and_one_with_a_default_is_not():
    assert _refused("plain", {k: v for k, v in GOOD.items() if k != "n"}) == "m: missing parameter n"
    _ok("optional", {"path": "only one"})


def test_missing_parameters_are_named_in_order():
    assert _refused("plain", {"name": "a"}) == "m: missing parameter items, n, obj, on, x"


def test_an_unknown_name_is_refused_unless_the_method_takes_any_other_name():
    assert _refused("plain", GOOD | {"zz": 1, "aa": 2}) == "m: unknown parameter aa, zz"
    _ok("kwargs", {"name": "a", "zz": 1, "anything": [1]})
    assert _refused("kwargs", {"name": 1}) == "m: name must be text, not a whole number"


def test_params_that_are_not_an_object_are_refused_first():
    assert _refused("plain", [1]) == "m: params must be an object, not a list"
    assert _refused("plain", None) == "m: params must be an object, not null"


def test_rules_for_a_signature():
    allowed, required, extra = rpc_params._rules(Svc.optional)
    assert required == frozenset() and extra is False
    assert allowed == {"path": (str, type(None)), "count": (int, type(None))}
    allowed, required, extra = rpc_params._rules(Svc.kwargs)
    assert required == {"name"} and extra is True and "rest" not in allowed


def test_kinds_reads_unions_and_leaves_unknown_types_unchecked():
    assert rpc_params._kinds(str) == (str,)
    assert rpc_params._kinds(list[int]) == (list,)
    assert rpc_params._kinds(int | None) == (int, type(None))
    assert rpc_params._kinds(int | Any) is None
    assert rpc_params._kinds(Any) is None
    assert rpc_params._kinds(bytes) is None


def test_fits_keeps_bool_and_numbers_apart():
    assert rpc_params._fits(True, (bool,)) and not rpc_params._fits(True, (int,))
    assert rpc_params._fits(1, (float,)) and not rpc_params._fits(1.0, (int,))
    assert not rpc_params._fits(1, (bool,))
    assert rpc_params._fits(1, (int,)) and not rpc_params._fits("1", (float,))


def _unresolvable(self, x: "NoSuchType") -> None: ...  # noqa: F821


def test_a_method_without_readable_type_hints_is_left_to_the_call():
    rpc_params.check("m", _unresolvable, {"anything": 1})
