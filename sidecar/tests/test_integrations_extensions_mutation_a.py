"""Extensions: what the first mutation run of integrations/extensions.py left alive (batch 345)."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from types import SimpleNamespace

import jsonschema
import pytest

from fabric_desktop.integrations import extensions
from fabric_desktop.integrations.extensions import BUILT_IN, OUTLINE_ID, Extension, ExtensionHost, check
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    store = IntegrationStore.at(tmp_path / "FabricDesktop")
    return ExtensionHost(Registry(), store, catalog=[])


def cap(cap_id="sample_thing_read", source="sample-thing") -> Capability:
    return Capability(cap_id, "Read a thing", "Reads a thing.", schema(), schema(extra=True), "READ_METADATA",
                      lambda svc, ctx: {"ok": True}, source=source, needs_service=False)


def ext(capabilities=lambda: [cap()], ext_id="sample-thing") -> Extension:
    return Extension(ext_id, "Sample", "1.0.0", "Tests", "A sample.", ("READ_METADATA",), capabilities)


def test_an_extension_is_not_built_in_unless_it_says_so_and_cant_be_changed():
    e = ext()
    assert e.built_in is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.name = "Other"  # type: ignore[misc]


def test_extensions_compare_without_their_capability_builders():
    assert ext(lambda: [cap()]) == ext(lambda: [cap()])


def test_an_id_that_becomes_the_core_prefix_is_refused_by_name():
    with pytest.raises(ValueError, match="an extension id can't start with fabric_desktop"):
        check(ext(ext_id="fabric-desktop-x"))


def test_a_capability_that_isnt_a_capability_object_is_refused():
    with pytest.raises(ValueError, match="capabilities must be Capability objects"):
        check(ext(lambda: [{"id": "sample_thing_read"}]))


def test_describe_lists_every_extension_and_unknown_ones_are_refused(host):
    host.add(ext())
    rows = host.describe()
    assert isinstance(rows, list) and [r["id"] for r in rows] == ["sample-thing"]
    with pytest.raises(ValueError, match="There is no extension 'nope'"):
        host.set_enabled("nope", True)


@pytest.mark.parametrize("exc, message", [(ValueError("bad thing"), "bad thing"),
                                          (RuntimeError("boom"), "RuntimeError: boom")])
def test_a_load_that_fails_keeps_a_readable_error(host, exc, message):
    calls = []

    def capabilities():
        calls.append(1)
        if len(calls) > 2:  # add() and set_enabled() check it; the load in sync() is the third call
            raise exc
        return [cap()]

    host.add(ext(capabilities))
    with pytest.raises(ValueError):
        host.set_enabled("sample-thing", True)
    assert host.errors["sample-thing"] == message


def svc_with(cells):
    return SimpleNamespace(notebooks=SimpleNamespace(load=lambda name: {"cells": cells}))


def test_the_outline_numbers_cells_from_one_and_cuts_headings_at_200():
    long = "x" * 250
    cells = [{"type": "markdown", "source": f"# {long}"}, {"type": "code", "source": "def f():\n    pass"}]
    out = extensions.notebook_outline(svc_with(cells), SimpleNamespace(check=lambda: None), "N")
    assert [c["number"] for c in out["cells"]] == [1, 2]
    assert out["cells"][0]["headings"] == [{"level": 1, "text": "x" * 200}]


def test_the_outline_takes_a_name_of_1_to_200_characters_and_stops_after_30_seconds():
    (c,) = check(BUILT_IN[0])
    assert c.timeout == 30
    for good in ("N", "n" * 200):
        jsonschema.validate({"name": good}, c.input_schema)
    for bad in ("", "n" * 201):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"name": bad}, c.input_schema)
    assert c.source == OUTLINE_ID and replace(BUILT_IN[0]).built_in is True


def test_the_outline_tells_tools_a_cell_may_carry_more_keys_than_listed():
    (c,) = check(BUILT_IN[0])
    item = c.output_schema["properties"]["cells"]["items"]
    assert "additionalProperties" not in item and item["required"] == ["number", "type", "lines"]
