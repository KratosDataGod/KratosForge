"""Mutation tests for extensions (integrations/extensions.py): an extension's own checks, the host's
switch and error text, what describe answers, and the built-in outline's schema and cell numbering.
Found by tools/mutate.py."""

import dataclasses

import pytest

from fabric_desktop.integrations import core, extensions
from fabric_desktop.integrations.extensions import ExtensionHost
from tests.test_integrations_extensions import cap, ctx, env, ext  # noqa: F401  (env is a fixture)


def test_an_extension_is_not_built_in_unless_it_says_so_and_cant_be_changed_once_made():
    e = ext()
    assert e.built_in is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.version = "2.0.0"


def test_two_extensions_that_differ_only_in_how_they_build_capabilities_are_the_same():
    assert ext() == dataclasses.replace(ext(), capabilities=lambda: [cap()])


def test_an_id_that_would_give_core_capability_ids_is_refused():
    with pytest.raises(ValueError, match="can't start with fabric_desktop"):
        extensions.check(ext(id="fabric-desktop"))


def test_a_capability_list_holding_something_else_is_refused():
    with pytest.raises(ValueError, match="capabilities must be Capability objects"):
        extensions.check(ext(caps=[cap(), {"id": "sample_thing_x"}]))


def test_switching_an_extension_that_isnt_there_is_refused(env):  # noqa: F811
    host = ExtensionHost(env["registry"], env["store"], catalog=[])
    with pytest.raises(ValueError, match="There is no extension 'nope'"):
        host.set_enabled("nope", True)


def test_describe_lists_every_extension(env):  # noqa: F811
    host = ExtensionHost(env["registry"], env["store"], catalog=[ext()])
    assert [r["id"] for r in host.describe()] == ["sample-thing"]


@pytest.mark.parametrize("exc, text", [(RuntimeError("boom"), "RuntimeError: boom"), (ValueError("bad cap"), "bad cap")])
def test_an_extension_that_fails_when_loaded_says_why(env, exc, text):  # noqa: F811
    calls = []

    def build():
        calls.append(1)
        if len(calls) > 1:
            raise exc
        return [cap()]

    host = ExtensionHost(env["registry"], env["store"], catalog=[ext(capabilities=build)])
    env["store"].set_setting(extensions.SETTING, {"sample-thing": True})
    host.sync()
    assert host.errors == {"sample-thing": text}


def test_the_outline_numbers_cells_from_1_and_cuts_headings_to_200(env):  # noqa: F811
    env["service"].notebooks.save("Long", [{"type": "markdown", "source": "# " + "h" * 300}])
    out = extensions.notebook_outline(env["service"], ctx(env), "Long")
    assert out["cells"][0]["number"] == 1 and out["cells"][0]["headings"] == [{"level": 1, "text": "h" * 200}]


def test_the_outline_takes_a_name_of_1_to_200_characters_and_its_cells_may_say_more():
    [outline] = extensions._outline_capabilities()
    assert outline.timeout == 30
    assert core.validate(outline.input_schema, {"name": "n" * 200})
    for bad in ("", "n" * 201):
        with pytest.raises(core.IntegrationError):
            core.validate(outline.input_schema, {"name": bad})
    cell = outline.output_schema["properties"]["cells"]["items"]
    assert core.validate(cell, {"number": 1, "type": "code", "lines": 2, "more": True})
