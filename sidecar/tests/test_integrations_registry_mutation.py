"""Mutation batch 25: the capability registry's limits, bookkeeping and listing status are exact."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.registry import MAX_URI, Capability, ReadOnlyAuthorizer, Registry, schema


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def ctx():
    return CallContext(Caller("cl_test", "test"), timeout=5)


def cap(**changes):
    base = Capability("ext_hello", "Say hello", "Greets someone.", schema({"who": {"type": "string"}}, ["who"]),
                      schema({"text": {"type": "string"}}, ["text"]), "READ_METADATA",
                      lambda svc, ctx, who: {"text": f"hello {who}"}, source="sample.hello", needs_service=False)
    return dataclasses.replace(base, **changes)


def test_a_resource_uri_of_exactly_2000_characters_is_read_and_one_more_is_not():
    assert MAX_URI == 2000
    reg = Registry()
    reg.register(cap(resource="fabric-desktop://items/{who}"))
    head = "fabric-desktop://items/"
    found, args = reg.resolve(head + "x" * (2000 - len(head)))
    assert found.id == "ext_hello" and len(args["who"]) == 2000 - len(head)
    with pytest.raises(IntegrationError) as err:
        reg.resolve(head + "x" * (2001 - len(head)))
    assert err.value.code == "not_found"


def test_a_capability_starts_at_version_1():
    assert cap().version == 1
    assert cap().describe()["version"] == 1


def test_only_removing_something_counts_as_a_change():
    reg = Registry()
    reg.register(cap())
    seen = []
    reg.on_change(lambda: seen.append(reg.version))
    assert reg.unregister("nothing_here") is False
    assert seen == [] and reg.version == 1
    assert reg.unregister("ext_hello") is True
    assert seen == [2]


@pytest.mark.parametrize("changes", [{"read_only": False, "permission": "READ_DATA"},
                                     {"read_only": True, "permission": "WRITE_ONELAKE"}])
def test_the_read_only_authorizer_refuses_when_either_flag_says_it_writes(changes):
    # Registry.register never lets the two disagree; the authorizer doesn't rely on that.
    c = cap(**changes)
    auth = ReadOnlyAuthorizer()
    assert auth.availability(c, None) == ("unavailable", ReadOnlyAuthorizer.REASON)
    with pytest.raises(IntegrationError) as err:
        auth.check(c, {"who": "x"}, ctx())
    assert err.value.code == "unavailable"


def test_a_timeout_of_up_to_an_hour_is_accepted_and_no_more():
    Registry().register(cap(timeout=3600))
    with pytest.raises(ValueError, match="timeout must be between 0 and 3600 seconds"):
        Registry().register(cap(timeout=3600.5))


def test_paging_defaults_to_100_and_paged_output_allows_more_fields():
    from fabric_desktop.integrations.registry import PAGING, paged_output
    assert (PAGING["limit"]["default"], PAGING["limit"]["minimum"], PAGING["limit"]["maximum"]) == (100, 1, 500)
    out = paged_output({"type": "object"})
    assert "additionalProperties" not in out
    assert out["required"] == ["items", "total", "offset", "next_offset"]


class _Says:
    def __init__(self, status, reason=""):
        self.answer = (status, reason)

    def check(self, *a, **k):
        return None

    def availability(self, capability, ctx):
        return self.answer


def test_a_preview_capability_is_listed_as_preview_only_when_the_caller_could_use_it():
    preview = cap(status="preview", reason="Early")
    assert Registry(_Says("available")).availability(preview) == ("preview", "Early")
    assert Registry(_Says("unavailable", "Not for you")).availability(preview) == ("unavailable", "Not for you")
    assert Registry(_Says("available")).availability(cap()) == ("available", "")


def test_an_unknown_option_is_named_with_the_resource_it_was_given_to():
    reg = Registry()
    reg.register(cap(resource="fabric-desktop://items/{who}"))
    with pytest.raises(IntegrationError) as err:
        reg.resolve("fabric-desktop://items/ana?colour=red")
    assert err.value.code == "invalid_input"
    assert "'colour' isn't an option of fabric-desktop://items/ana." in str(err.value)
