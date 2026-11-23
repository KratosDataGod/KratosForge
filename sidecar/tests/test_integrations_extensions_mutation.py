"""Extensions (integrations/extensions.py), mutation batch 353: an extension is a fixed, comparable
value; each refusal is the one meant; a load that fails says why in the right words; and the built-in
outline's numbering, cuts and schemas."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from typing import Any

import pytest

from fabric_desktop.integrations import extensions
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.extensions import Extension, ExtensionHost, check
from fabric_desktop.integrations.registry import Capability, schema


def cap(cap_id="sample_thing_read", source="sample-thing", **changes):
    base = Capability(cap_id, "Read a thing", "Reads a thing.", schema(), schema(extra=True), "READ_METADATA",
                      lambda svc, ctx: {"ok": True}, source=source, needs_service=False)
    return replace(base, **changes)


def ext(caps=None, **changes):
    base = Extension("sample-thing", "Sample", "1.0.0", "Tests", "A sample.", ("READ_METADATA",),
                     lambda: caps if caps is not None else [cap()])
    return replace(base, **changes)


class Store:
    def __init__(self):
        self.values: dict[str, Any] = {}

    def settings(self):
        return dict(self.values)

    def set_setting(self, key, value):
        self.values[key] = value


class Reg:
    def __init__(self):
        self.caps: list[Capability] = []

    def all(self):
        return list(self.caps)

    def register(self, c):
        self.caps.append(c)

    def unregister_source(self, source):
        self.caps = [c for c in self.caps if c.source != source]


@pytest.fixture(autouse=True)
def no_policy(monkeypatch):
    monkeypatch.setattr(extensions.policy, "current", lambda: {})


def test_an_extension_is_a_fixed_value_compared_without_its_capabilities():
    a, b = ext(), ext()
    assert a.capabilities is not b.capabilities and a == b
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.version = "2.0.0"  # type: ignore[misc]


def test_an_extension_is_not_built_in_unless_it_says_so():
    host = ExtensionHost(Reg(), Store(), catalog=[ext()])
    assert ext().built_in is False
    assert host.describe()[0]["built_in"] is False
    assert [r["id"] for r in host.describe()] == ["sample-thing"]


def test_an_id_that_makes_a_core_prefix_is_refused_for_that():
    with pytest.raises(ValueError, match="an extension id can't start with fabric_desktop"):
        check(ext(id="fabric-desktop-more"))


def test_a_capability_that_isnt_one_is_refused():
    with pytest.raises(ValueError, match="must be Capability objects"):
        check(ext(caps=[object()]))


def test_turning_on_an_extension_that_isnt_there_is_refused():
    host = ExtensionHost(Reg(), Store(), catalog=[ext()])
    with pytest.raises(ValueError, match="There is no extension 'nothing'"):
        host.set_enabled("nothing", True)


def test_a_load_that_fails_keeps_a_value_error_as_said_and_names_any_other_kind():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("boom")
        return [cap()]

    store = Store()
    store.set_setting(extensions.SETTING, {"sample-thing": True})
    host = ExtensionHost(Reg(), store, catalog=[ext(capabilities=flaky)])
    host.sync()
    assert host.errors["sample-thing"] == "RuntimeError: boom"

    class Refusing(Reg):
        def register(self, c):
            raise ValueError("no room")

    host = ExtensionHost(Refusing(), store, catalog=[ext()])
    host.sync()
    assert host.errors["sample-thing"] == "no room"


class Notebooks:
    def __init__(self, cells):
        self.cells = cells

    def load(self, name):
        return {"cells": self.cells}


class Svc:
    def __init__(self, cells):
        self.notebooks = Notebooks(cells)


def test_the_outline_numbers_cells_from_one_and_cuts_headings_at_200():
    long = "x" * 250
    out = extensions.notebook_outline(Svc([{"type": "markdown", "source": f"# {long}"},
                                           {"type": "code", "source": "def f():\n    pass"}]),
                                      CallContext(Caller("c", "test")), "nb")
    assert [c["number"] for c in out["cells"]] == [1, 2]
    assert out["cells"][0]["headings"] == [{"level": 1, "text": "x" * 200}]


def test_the_outline_capability_takes_a_name_of_1_to_200_characters_in_30_seconds():
    (c,) = extensions._outline_capabilities()
    name = c.input_schema["properties"]["name"]
    assert (name["minLength"], name["maxLength"]) == (1, 200)
    assert c.timeout == 30
    cell = c.output_schema["properties"]["cells"]["items"]
    assert "additionalProperties" not in cell  # a cell has headings, or functions and classes
