"""The capability registry: the edges the main tests don't reach. Refusals at registration (status,
schema properties, handler, resource rules and clashes), unregistering, a listener that fails, the
preview status, resource URIs with query options, invoke's error mapping and audit line, and bound
arguments (an engine that isn't running, a bind that fails or tries to set an input)."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.integrations.core import IntegrationError
from fabric_desktop.integrations.registry import ReadOnlyAuthorizer, Registry, schema

from test_integrations_registry import cap, ctx


def resource_cap(**changes):
    base = cap(id="ext_item", resource="fabric-desktop://items/{who}",
               input_schema=schema({"who": {"type": "string"}, "limit": {"type": "integer"},
                                    "ratio": {"type": "number"}, "deep": {"type": "boolean"}}, ["who"]))
    return dataclasses.replace(base, **changes)


@pytest.mark.parametrize("changes, problem", [
    ({"status": "sometimes"}, "status must be one of"),
    ({"input_schema": {"type": "object", "properties": []}}, "properties must be an object"),
    ({"handler": "not callable"}, "handler must be callable"),
    ({"workspaces": "everywhere"}, "workspaces must be one of"),
])
def test_more_malformed_capabilities_are_refused(changes, problem):
    with pytest.raises(ValueError, match=problem):
        Registry().register(cap(**changes))


@pytest.mark.parametrize("changes, problem", [
    ({"approval": "always"}, "only read-only capabilities that don't ask"),
    ({"resource": "https://items/{who}"}, "URI template with no query"),
    ({"resource": "fabric-desktop://items/{who}?x=1"}, "URI template with no query"),
    ({"resource": "fabric-desktop://items/{who}/{who}"}, "repeats a placeholder"),
    ({"resource": "fabric-desktop://items/{nobody}"}, "isn't an input"),
    ({"resource": "fabric-desktop://items/all"}, "must be in the resource URI"),
])
def test_resource_rules_are_checked_when_registered(changes, problem):
    with pytest.raises(ValueError, match=problem):
        Registry().register(resource_cap(**changes))


def test_two_resources_of_the_same_shape_clash():
    reg = Registry()
    reg.register(resource_cap())
    with pytest.raises(ValueError, match="is the same as ext_item's"):
        reg.register(resource_cap(id="ext_other", resource="fabric-desktop://items/{who}"))


def test_unregister_says_whether_it_removed_anything_and_a_failing_listener_doesnt_stop_it(caplog):
    reg = Registry()

    def broken():
        raise RuntimeError("listener bug")

    reg.on_change(broken)
    reg.register(cap())
    assert "listener failed" in caplog.text
    assert reg.unregister("ext_hello") is True and reg.unregister("ext_hello") is False
    assert reg.unregister_source("nobody.here") == []


def test_preview_capabilities_say_preview_with_their_reason():
    reg = Registry()
    reg.register(cap(status="preview", reason="Still being tried out"))
    [d] = reg.describe()
    assert (d["status"], d["reason"]) == ("preview", "Still being tried out")


def test_read_only_authorizer_refuses_what_changes_things_even_if_registered_another_way():
    a = ReadOnlyAuthorizer()
    writer = cap(read_only=False, permission="WRITE_ONELAKE", approval="always")
    with pytest.raises(IntegrationError, match="isn't available to external tools yet"):
        a.check(writer, {}, ctx())
    args = {"who": "x"}
    a.check(cap(), args, ctx(), bind=lambda: {"code_hash": "abc"})
    assert args == {"who": "x", "code_hash": "abc"}


def test_resource_uris_carry_their_options_typed():
    reg = Registry()
    reg.register(resource_cap())
    c, args = reg.resolve("fabric-desktop://items/Chris%20B?limit=5&ratio=0.5&deep=true")
    assert c.id == "ext_item" and args == {"who": "Chris B", "limit": 5, "ratio": 0.5, "deep": True}
    for uri, match in [("fabric-desktop://items/x?limit=lots", "isn't a number"),
                       ("fabric-desktop://items/x?deep=yes", "isn't true or false"),
                       ("fabric-desktop://items/x?colour=red", "isn't an option"),
                       ("fabric-desktop://items/x?who=y", "isn't an option"),
                       ("fabric-desktop://nothing/here", "There is no resource"),
                       ("fabric-desktop://items/" + "x" * 3000, "There is no resource")]:
        with pytest.raises(IntegrationError, match=match):
            reg.resolve(uri)


def test_a_handler_that_raises_something_else_is_an_internal_error_and_is_audited():
    reg = Registry()
    lines = []
    reg.audit = lambda *a, **kw: lines.append((a, kw))
    reg.register(cap(handler=lambda svc, ctx, who: {"execution_id": "ex1"} if who == "ok" else 1 / 0))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert err.value.code == "internal" and "ZeroDivisionError" in str(err.value)
    assert reg.invoke(None, "ext_hello", {"who": "ok"}, ctx()) == {"execution_id": "ex1"}
    assert [line[0][-1] for line in lines] == ["", "ex1"]  # the execution id is on the audit line


def test_an_audit_sink_that_fails_doesnt_fail_a_finished_call(capsys):
    reg = Registry()

    def full(*a, **kw):
        raise OSError("disk full")

    reg.audit = full
    reg.register(cap())
    assert reg.invoke(None, "ext_hello", {"who": "x"}, ctx()) == {"text": "hello x"}
    assert "audit: could not record ext_hello" in capsys.readouterr().err


def test_an_approved_call_whose_start_line_cant_be_written_doesnt_run():
    reg = Registry()
    ran = []

    class Approver:
        def check(self, capability, arguments, ctx, bind=None, workspaces=None):
            ctx.decision = "approved"

        def availability(self, capability, ctx):
            return "available", ""

    def full(*a, stage="", **kw):
        if stage == "started":
            raise OSError("disk full")

    reg.authorizer = Approver()
    reg.audit = full
    reg.register(cap(handler=lambda svc, ctx, who: ran.append(who) or {"text": "x"}))
    with pytest.raises(IntegrationError, match="couldn't record the call"):
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert ran == []


class Binder:
    """An authorizer that binds, as the approving one does."""

    def check(self, capability, arguments, ctx, bind=None, workspaces=None):
        if bind is not None:
            arguments.update(bind())

    def availability(self, capability, ctx):
        return "available", ""


def test_bound_arguments_need_the_engine_and_cant_set_an_input():
    reg = Registry(authorizer=Binder())
    reg.register(cap(bind=lambda svc, args: {"code_hash": "h"}))
    with pytest.raises(IntegrationError, match="engine isn't running"):
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())

    reg.register(cap(bind=lambda svc, args: {"who": "someone else"}), replace=True)
    with pytest.raises(IntegrationError, match="a bound argument can't also be an input"):
        reg.invoke(object(), "ext_hello", {"who": "x"}, ctx())

    def failing(svc, args):
        raise FileNotFoundError("no such notebook")

    reg.register(cap(bind=failing), replace=True)
    with pytest.raises(IntegrationError, match="no such notebook"):
        reg.invoke(object(), "ext_hello", {"who": "x"}, ctx())

