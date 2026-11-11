"""Integration argument checks: the JSON Schema keywords the tests in test_integrations_core don't reach."""

import pytest

from fabric_desktop.integrations.core import IntegrationError, validate


def _problems(schema, value):
    with pytest.raises(IntegrationError) as raised:
        validate(schema, value)
    assert raised.value.code == "invalid_input"
    return raised.value.details["problems"]


def test_a_value_matching_no_allowed_form_says_so():
    schema = {"properties": {"top": {"anyOf": [{"type": "integer"}, {"type": "string", "maxLength": 2}]}}}
    assert _problems(schema, {"top": "long"}) == ["top doesn't match any allowed form"]
    assert validate(schema, {"top": 5}) == {"top": 5}
    assert validate(schema, {"top": "ab"}) == {"top": "ab"}


def test_a_const_and_a_maximum_are_checked():
    schema = {"properties": {"mode": {"const": "read"}, "rows": {"type": "integer", "maximum": 10}}}
    assert _problems(schema, {"mode": "write", "rows": 11}) == ["mode must be 'read'", "rows must be at most 10"]


def test_extra_properties_are_checked_against_their_schema():
    schema = {"type": "object", "properties": {"tags": {"type": "object", "additionalProperties": {"type": "string"}}}}
    assert _problems(schema, {"tags": {"env": "prod", "count": 3}}) == ["tags.count should be string"]
    assert validate(schema, {"tags": {"env": "prod"}}) == {"tags": {"env": "prod"}}


def test_nested_unknown_and_missing_properties_name_their_path():
    schema = {"properties": {"opts": {"type": "object", "required": ["name"], "additionalProperties": False,
                                      "properties": {"name": {"type": "string"}}}}}
    assert _problems(schema, {"opts": {"extra": 1}}) == ["opts.name is required", "opts.extra isn't an argument this takes"]


def test_no_arguments_are_read_as_none_and_get_the_defaults():
    schema = {"type": "object", "properties": {"limit": {"type": "integer", "default": 100}}}
    assert validate(schema, None) == {"limit": 100}


def test_arguments_that_are_not_an_object_come_back_as_they_are():
    assert validate({"type": "array", "items": {"type": "integer"}}, [1, 2]) == [1, 2]
    assert _problems({"type": "array", "items": {"type": "integer"}}, [1, "x"]) == ["arguments[1] should be integer"]


def test_a_list_with_too_few_items_is_refused():
    schema = {"properties": {"ids": {"type": "array", "minItems": 2}}}
    assert _problems(schema, {"ids": ["a"]}) == ["ids needs at least 2 items"]
