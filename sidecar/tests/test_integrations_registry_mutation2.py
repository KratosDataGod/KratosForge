"""Mutation testing, second pass: the capability registry's audit lines and refusals are exact.

Pins what ``Registry.invoke`` hands the audit sink for every outcome (the execution id it finds,
the permission, the error, redacted), the "started" line of an approved call and what happens
when that line can't be written, the line for a call a transport refused, the name recorded for
a tool that doesn't exist, and how a resource URI's query is read.
"""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.integrations import registry as registry_module

from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.registry import (MAX_AUDITED_NAME, Capability, Registry, _execution_of,
                                                  _is_execution_record, schema)
from fabric_desktop.redact import REMOVED

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"  # pragma: allowlist secret (a made-up token)
EXECUTION = "ex_" + "a1" * 10


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def ctx():
    return CallContext(Caller("cl_test", "test"), timeout=5)


def cap(**changes):
    base = Capability("ext_hello", "Say hello", "Greets someone.", schema({"who": {"type": "string"}}, ["who"]),
                      schema({}, extra=True), "READ_METADATA",
                      lambda svc, ctx, who: {"text": f"hello {who}"}, source="sample.hello", needs_service=False)
    return dataclasses.replace(base, **changes)


class Lines(list):
    def __call__(self, *args, **kwargs):
        self.append((args, kwargs))


def registry(*caps, authorizer=None):
    reg = Registry(authorizer)
    for c in caps or (cap(),):
        reg.register(c)
    reg.audit = Lines()
    return reg


# ---- _is_execution_record and _execution_of

def test_an_execution_record_needs_an_execution_id_and_kind_state_and_title():
    record = {"id": EXECUTION, "kind": "notebook", "state": "running", "title": "x"}
    assert _is_execution_record(record) is True
    assert _is_execution_record(record | {"extra": 1}) is True
    for missing in ("kind", "state", "title"):
        assert _is_execution_record({k: v for k, v in record.items() if k != missing}) is False
    assert _is_execution_record(record | {"id": "ex_" + "a" * 19}) is False
    assert _is_execution_record(record | {"id": EXECUTION + "0"}) is False
    assert _is_execution_record(record | {"id": 5}) is False
    assert _is_execution_record([record]) is False
    assert _is_execution_record(None) is False


def test_the_execution_of_a_call_is_in_its_result_or_in_the_arguments_of_a_tool_that_takes_one():
    takes = cap(input_schema=schema({"execution_id": {"type": "string"}}))
    assert _execution_of(None, {"execution_id": EXECUTION}, None) == EXECUTION
    assert _execution_of(takes, None, {"execution_id": EXECUTION}) == EXECUTION
    # A tool that doesn't take one: its arguments are the caller's data, not an execution.
    assert _execution_of(cap(), None, {"execution_id": EXECUTION}) == ""
    assert _execution_of(None, None, {"execution_id": EXECUTION}) == ""
    # Only the id format counts.
    assert _execution_of(takes, {"execution_id": "ex_nope"}, {"execution_id": "x"}) == ""
    assert _execution_of(takes, {"execution_id": 5}, None) == ""
    assert _execution_of(takes, ["execution_id"], "execution_id") == ""
    # The result comes first.
    other = "ex_" + "b" * 20
    assert _execution_of(takes, {"execution_id": EXECUTION}, {"execution_id": other}) == EXECUTION
    assert _execution_of(takes, {"execution_id": "bad"}, {"execution_id": other}) == other


# ---- invoke's audit line

def test_a_finished_call_hands_the_audit_its_permission_arguments_and_no_error():
    reg = registry()
    c = ctx()
    assert reg.invoke(None, "ext_hello", {"who": "ann"}, c) == {"text": "hello ann"}
    (args, kwargs), = reg.audit
    assert kwargs == {}
    line_ctx, cap_id, permission, arguments, arrived, seconds, error, execution = args
    assert line_ctx is c and cap_id == "ext_hello" and permission == "READ_METADATA"
    assert arguments == {"who": "ann"} and error is None and execution == ""
    assert arrived > 1_000_000_000 and 0 <= seconds < 5
    assert c.decision == "allowed"


def test_a_call_that_returns_an_execution_record_is_recorded_under_it():
    record = {"id": EXECUTION, "kind": "notebook", "state": "running", "title": "x"}
    reg = registry(cap(handler=lambda svc, ctx, who: record))
    reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert reg.audit[0][0][7] == EXECUTION


def test_a_result_with_an_execution_id_wins_over_its_own_record_id():
    other = "ex_" + "c" * 20
    record = {"id": other, "kind": "k", "state": "s", "title": "t", "execution_id": EXECUTION}
    reg = registry(cap(handler=lambda svc, ctx, who: record))
    reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert reg.audit[0][0][7] == EXECUTION


def test_someones_data_with_an_id_isnt_an_execution():
    reg = registry(cap(handler=lambda svc, ctx, who: {"id": EXECUTION}))
    reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert reg.audit[0][0][7] == ""


def test_a_refused_call_records_its_error_redacted_and_raises_it():
    def fails(svc, ctx, who):
        raise IntegrationError("failed", f"bad {JWT}", hint=f"h {JWT}")
    reg = registry(cap(handler=fails))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert err.value.message == f"bad {REMOVED}" and err.value.hint == f"h {REMOVED}"
    assert reg.audit[0][0][6] is err.value


def test_an_unknown_tool_is_not_found_and_recorded_with_no_permission():
    reg = registry()
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_nope", {}, ctx())
    assert err.value.code == "not_found"
    assert err.value.message == "There is no capability 'ext_nope'."
    args = reg.audit[0][0]
    assert args[1] == "ext_nope" and args[2] == "" and args[6] is err.value


def test_an_unavailable_tool_is_refused_before_its_arguments_are_read():
    reg = registry(cap(status="unavailable", reason="not here"))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"wrong": 1}, ctx())
    assert err.value.code == "unavailable"
    assert err.value.message == "Say hello isn't available: not here"


def test_an_error_that_isnt_an_integration_error_is_recorded_as_internal_and_raised_as_it_is():
    class Boom(Exception):
        pass

    class Breaks:
        def check(self, *a, **k):
            raise Boom("authorizer broke")

        def availability(self, c, ctx):
            return "available", ""
    reg = registry(authorizer=Breaks())
    with pytest.raises(Boom):
        reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    error = reg.audit[0][0][6]
    assert error.code == "internal" and error.message == "Boom: authorizer broke"


def test_the_name_of_a_tool_that_doesnt_exist_is_redacted_and_cut_on_its_line():
    assert MAX_AUDITED_NAME == 200
    reg = registry()
    name = f"x {JWT} " + "y" * 300
    with pytest.raises(IntegrationError):
        reg.invoke(None, name, {}, ctx())
    recorded = reg.audit[0][0][1]
    assert recorded == (f"x {REMOVED} " + "y" * 300)[:200]
    assert len(recorded) == 200


def test_a_real_tools_name_is_recorded_as_it_is():
    long_id = "ext_" + "h" * 60
    reg = registry(cap(id=long_id))
    reg.invoke(None, long_id, {"who": "a"}, ctx())
    assert reg.audit[0][0][1] == long_id


def test_no_audit_sink_means_no_line_and_the_call_still_runs():
    reg = registry()
    reg.audit = None
    assert reg.invoke(None, "ext_hello", {"who": "a"}, ctx()) == {"text": "hello a"}


def test_a_broken_audit_sink_doesnt_fail_a_finished_call(capsys):
    reg = registry()

    def broken(*a, **k):
        raise OSError("disk full")
    reg.audit = broken
    assert reg.invoke(None, "ext_hello", {"who": "a"}, ctx()) == {"text": "hello a"}
    assert "audit: could not record ext_hello: OSError: disk full" in capsys.readouterr().err


# ---- approved calls

class Approves:
    def check(self, capability, arguments, ctx, bind=None, workspaces=None):
        ctx.decision = "approved"
        ctx.approval_id = "ap_1"

    def availability(self, capability, ctx):
        return "available", ""


def test_an_approved_call_writes_a_started_line_before_it_runs_then_its_outcome():
    order = []
    reg = registry(cap(handler=lambda svc, ctx, who: order.append("ran") or {"ok": 1}), authorizer=Approves())

    def sink(*args, **kwargs):
        order.append(("line", kwargs.get("stage", ""), args[6]))
    reg.audit = sink
    c = ctx()
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    assert order == [("line", "started", None), "ran", ("line", "", None)]
    assert c.decision == "approved"


def test_an_allowed_call_writes_no_started_line():
    reg = registry()
    reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert len(reg.audit) == 1 and reg.audit[0][1] == {}


def test_approved_work_never_runs_without_its_started_line(capsys):
    ran = []
    reg = registry(cap(handler=lambda svc, ctx, who: ran.append(1)), authorizer=Approves())
    lines = []

    def sink(*args, stage=""):
        if stage == "started":
            raise OSError("disk full")
        lines.append(args)
    reg.audit = sink
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert ran == []
    assert err.value.code == "internal" and err.value.message.startswith("KratosForge couldn't record the call")
    assert isinstance(err.value.__cause__, OSError)
    assert lines and lines[-1][6] is err.value  # its outcome line still records why
    assert "audit: could not record ext_hello: OSError: disk full" in capsys.readouterr().err


def test_a_previous_calls_decision_doesnt_carry_over():
    reg = registry()
    c = ctx()
    c.decision = "approved"
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    assert c.decision == "allowed"
    assert len(reg.audit) == 1  # no "started" line: this call wasn't approved


def test_the_time_limit_is_the_smaller_of_the_callers_and_the_tools():
    reg = registry(cap(timeout=30))
    c = CallContext(Caller("c", "test"), timeout=5)
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    assert c.timeout == 5
    c = CallContext(Caller("c", "test"), timeout=100)
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    assert c.timeout == 30
    c = CallContext(Caller("c", "test"), timeout=0)
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    assert c.timeout == 30


def test_a_tool_that_needs_the_engine_is_unavailable_without_it():
    reg = registry(cap(needs_service=True))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert err.value.code == "unavailable"
    assert reg.invoke(object(), "ext_hello", {"who": "a"}, ctx()) == {"text": "hello a"}


def test_the_result_is_redacted_and_the_seconds_recorded():
    reg = registry(cap(handler=lambda svc, ctx, who: {"text": f"t {JWT}"}))
    c = ctx()
    c.seconds = -1.0
    assert reg.invoke(None, "ext_hello", {"who": "a"}, c) == {"text": f"t {REMOVED}"}
    assert 0 <= c.seconds < 5


def test_the_seconds_a_handler_ran_are_rounded_to_milliseconds(monkeypatch):
    now = [1000.0]

    def clock():
        now[0] += 0.12341
        return now[0]
    reg = registry()
    c = ctx()
    monkeypatch.setattr(registry_module.time, "monotonic", clock)
    reg.invoke(None, "ext_hello", {"who": "a"}, c)
    monkeypatch.undo()
    assert c.seconds > 0
    assert c.seconds == round(c.seconds, 3)


# ---- refused

def test_a_refused_call_gets_a_line_like_one_invoke_refuses():
    reg = registry()
    c = ctx()
    err = IntegrationError("invalid_input", "The body must be JSON.")
    reg.refused(c, "ext_hello", {"x": 1}, err)
    (args, kwargs), = reg.audit
    assert kwargs == {}
    assert args[0] is c and args[1:4] == ("ext_hello", "READ_METADATA", {"x": 1})
    assert args[4] > 1_000_000_000 and args[5] == 0.0 and args[6] is err and args[7] == ""


def test_a_refused_call_to_no_tool_records_no_permission_and_a_redacted_name():
    reg = registry()
    reg.refused(ctx(), f"resources/{JWT}", None, IntegrationError("not_found", "no"))
    args = reg.audit[0][0]
    assert args[1] == f"resources/{REMOVED}" and args[2] == ""


def test_a_refused_call_with_a_broken_sink_doesnt_raise(capsys):
    reg = registry()

    def broken(*a, **k):
        raise OSError("disk full")
    reg.audit = broken
    reg.refused(ctx(), "ext_hello", None, IntegrationError("not_found", "no"))
    assert "could not record" in capsys.readouterr().err


# ---- resolve

def resource_registry():
    props = {"who": {"type": "string"}, "limit": {"type": "integer"}, "ratio": {"type": "number"},
             "deep": {"type": "boolean"}}
    return registry(cap(input_schema=schema(props, ["who"]), resource="fabric-desktop://items/{who}",
                        handler=lambda svc, ctx, **kw: kw))


def test_a_resource_query_is_read_by_each_options_type():
    reg = resource_registry()
    found, args = reg.resolve("fabric-desktop://items/a%20b?limit=5&ratio=0.5&deep=true&&")
    assert found.id == "ext_hello"
    assert args == {"who": "a b", "limit": 5, "ratio": 0.5, "deep": True}
    assert reg.resolve("fabric-desktop://items/a?deep=false")[1]["deep"] is False


@pytest.mark.parametrize("query, message", [
    ("limit=x", "'x' isn't a number."),
    ("ratio=x", "'x' isn't a number."),
    ("deep=yes", "'yes' isn't true or false."),
    ("nope=1", "'nope' isn't an option of fabric-desktop://items/a."),
    ("who=b", "'who' isn't an option of fabric-desktop://items/a."),
])
def test_a_resource_query_that_doesnt_fit_is_invalid_input(query, message):
    with pytest.raises(IntegrationError) as err:
        resource_registry().resolve(f"fabric-desktop://items/a?{query}")
    assert err.value.code == "invalid_input" and err.value.message == message


def test_a_uri_that_names_nothing_is_not_found_and_quotes_at_most_200_characters():
    reg = resource_registry()
    uri = "fabric-desktop://other/" + "z" * 400
    with pytest.raises(IntegrationError) as err:
        reg.resolve(uri)
    assert err.value.code == "not_found"
    assert err.value.message == f"There is no resource {uri[:200]}."
    assert err.value.hint == "List resources and resource templates to see what can be read."
    with pytest.raises(IntegrationError) as err:
        reg.resolve("https://items/a")
    assert err.value.code == "not_found"


# ---- what may be a resource

@pytest.mark.parametrize("changes, message", [
    ({"approval": "always"}, "only read-only capabilities that don't ask can be resources"),
    ({"read_only": False, "permission": "EXECUTE_LOCAL_CODE", "approval": "never"},
     "only read-only capabilities that don't ask can be resources"),
    ({"resource": "fabric-desktop://items/{who}?x"}, "resource must be a fabric-desktop:// URI template with no query"),
    ({"resource": "fabric-desktop://items/{who}#x"}, "resource must be a fabric-desktop:// URI template with no query"),
    ({"resource": "fabric-desktop://items /{who}"}, "resource must be a fabric-desktop:// URI template with no query"),
    ({"resource": "https://items/{who}"}, "resource must be a fabric-desktop:// URI template with no query"),
    ({"resource": "fabric-desktop://items/{who}/{other}"}, "resource placeholder other isn't an input"),
    ({"resource": "fabric-desktop://items"}, "required inputs who must be in the resource URI"),
])
def test_a_capability_that_cant_be_a_resource_is_refused_when_it_registers(changes, message):
    changes = {"resource": "fabric-desktop://items/{who}"} | changes
    with pytest.raises(ValueError) as err:
        Registry().register(cap(**changes))
    assert message in str(err.value)


def test_a_read_only_capability_that_doesnt_ask_can_be_a_resource():
    assert Registry().register(cap(resource="fabric-desktop://items/{who}")).resource == "fabric-desktop://items/{who}"


# ---- bound arguments

def bound(bind):
    return registry(cap(bind=bind, handler=lambda svc, ctx, who, code="": {"who": who, "code": code}))


def test_bound_arguments_reach_the_handler():
    seen = []

    def bind(svc, args):
        seen.append(args)
        args["who"] = "changed"  # a copy: the call's own arguments stay as they were
        return {"code": "print(1)"}
    reg = bound(bind)
    assert reg.invoke(object(), "ext_hello", {"who": "a"}, ctx()) == {"who": "a", "code": "print(1)"}
    assert seen == [{"who": "changed"}]


def test_binding_needs_the_engine():
    reg = bound(lambda svc, args: {"code": "x"})
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "a"}, ctx())
    assert err.value.code == "unavailable" and err.value.message == "The KratosForge engine isn't running here."


def test_a_bind_that_fails_is_the_engines_error():
    def bind(svc, args):
        raise LookupError("No notebook named a")
    with pytest.raises(IntegrationError) as err:
        bound(bind).invoke(object(), "ext_hello", {"who": "a"}, ctx())
    assert err.value.code == "not_found" and isinstance(err.value.__cause__, LookupError)


@pytest.mark.parametrize("derived", [{"who": "b"}, {"other": 1, "who": "b"}])
def test_a_bound_argument_cant_also_be_an_input(derived):
    with pytest.raises(IntegrationError) as err:
        bound(lambda svc, args: derived).invoke(object(), "ext_hello", {"who": "a"}, ctx())
    assert err.value.code == "internal"
    assert err.value.message == "ext_hello: a bound argument can't also be an input."


def test_a_bound_argument_cant_be_an_input_the_call_left_out():
    reg = registry(cap(input_schema=schema({"who": {"type": "string"}, "code": {"type": "string"}}),
                       bind=lambda svc, args: {"code": "x"}, handler=lambda svc, ctx, **kw: kw))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), "ext_hello", {"who": "a"}, ctx())
    assert err.value.code == "internal"


def test_paging_starts_at_zero_and_takes_at_most_500():
    assert registry_module.PAGING["offset"]["minimum"] == 0 and registry_module.PAGING["offset"]["default"] == 0
    assert registry_module.PAGING["limit"] == {"type": "integer", "minimum": 1, "maximum": 500, "default": 100,
                                               "description": "How many to return (at most 500)."}


# ---- resources: their URIs and clashes

def test_a_resource_uri_quotes_each_placeholders_value():
    c = cap(resource="fabric-desktop://items/{who}")
    assert c.resource_uri(who="a b/c") == "fabric-desktop://items/a%20b%2Fc"
    assert c.describe()["resource"] == "fabric-desktop://items/{who}"
    assert cap().describe()["resource"] is None


def test_two_resources_of_the_same_shape_clash_but_a_resource_can_replace_itself():
    reg = Registry()
    first = reg.register(cap(resource="fabric-desktop://items/{who}"))
    reg.register(first, replace=True)
    with pytest.raises(ValueError) as err:
        reg.register(cap(id="ext_other", resource="fabric-desktop://items/{name}",
                         input_schema=schema({"name": {"type": "string"}}, ["name"])))
    assert str(err.value) == "ext_other: resource fabric-desktop://items/{name} is the same as ext_hello's"

