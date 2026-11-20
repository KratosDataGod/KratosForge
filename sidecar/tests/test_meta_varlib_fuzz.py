"""Fuzz the reader of any Fabric variable library's definition (varlib.read), which notebookutils.variableLibrary
on this PC uses for a library someone made in Fabric. Each case is a seeded mutation of variables.json or a
value set file: torn, not UTF-8, a BOM, NUL bytes, wrong JSON types at any depth, deep nesting, huge
strings and numbers, duplicate keys and missing fields. Each must give the values, or be refused with
ValueError or LookupError and a message; never another exception."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.meta import varlib

from tests.settings_fuzz import deep_mutations, mutations

VARIABLES = {"variables": [
    {"name": "server", "type": "String", "value": "dev-sql"}, {"name": "batch_size", "type": "Integer", "value": 500},
    {"name": "full_load", "type": "Boolean", "value": False}, {"name": "ratio", "type": "Number", "value": "0.5"}]}
PROD = {"name": "Prod", "variableOverrides": [{"name": "server", "value": "prod-sql"}, {"name": "batch_size", "value": "2000"}]}


def _read_all(label: str, parts: dict[str, str]) -> None:
    for value_set in (varlib.DEFAULT_SET, "Prod"):
        try:
            got = varlib.read(parts, value_set)
        except (ValueError, LookupError) as exc:
            assert str(exc), label
            continue
        assert isinstance(got, dict) and all(isinstance(k, str) for k in got), label


@pytest.mark.parametrize("seed", [1, 2])
def test_any_variables_file_is_read_or_refused_by_name(seed):
    for label, raw in list(mutations(VARIABLES, seed, 150)) + list(deep_mutations(VARIABLES, seed + 10, 800)):
        _read_all(label, {"variables.json": raw.decode("utf-8", "replace"), "valueSets/Prod.json": json.dumps(PROD)})


@pytest.mark.parametrize("seed", [3, 4])
def test_any_value_set_file_is_read_or_refused_by_name(seed):
    for label, raw in list(mutations(PROD, seed, 150)) + list(deep_mutations(PROD, seed + 10, 800)):
        _read_all(label, {"variables.json": json.dumps(VARIABLES), "valueSets/Prod.json": raw.decode("utf-8", "replace")})


@pytest.mark.parametrize("variables", [5, "server", {"name": "server"}, [1], [None], [{"name": ["server"]}],
                                       [{"name": {"a": 1}}]])
def test_variables_that_arent_a_list_of_named_objects_are_refused_by_name(variables):
    with pytest.raises(ValueError, match="variables.json can't be read, because its variables isn't a list"):
        varlib.read({"variables.json": json.dumps({"variables": variables})}, varlib.DEFAULT_SET)


@pytest.mark.parametrize("overrides", [5, "server", [1], [{"name": []}], [{"name": {}}]])
def test_overrides_that_arent_a_list_of_named_objects_are_refused_by_name(overrides):
    parts = {"variables.json": json.dumps(VARIABLES),
             "valueSets/Prod.json": json.dumps({"name": "Prod", "variableOverrides": overrides})}
    with pytest.raises(ValueError, match="valueSets/Prod.json can't be read, because its variableOverrides isn't a list"):
        varlib.read(parts, "Prod")


def test_a_library_nested_too_deeply_is_refused_by_name():
    with pytest.raises(ValueError, match="variables.json isn't valid JSON"):
        varlib.read({"variables.json": "[" * 100_000 + "]" * 100_000}, varlib.DEFAULT_SET)
