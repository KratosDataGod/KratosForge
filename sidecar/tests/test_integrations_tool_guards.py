"""Guards every MCP and local API tool gets, checked across the whole registry (#2363 hardening).

Each test walks every registered capability, core and built-in extensions alike, so a tool added
later is covered without anyone remembering to add it here:

- Bad arguments are refused by name before anything runs: a field the tool doesn't take, the
  wrong type, a string longer than its limit, a number past its bounds, and, for every field with
  a pattern, path traversal and absolute paths. The handler is a spy that must never be called.
- Every input string is bounded and no tool takes fields it doesn't list, so the checks above
  can't be skipped by a tool that forgets to declare limits.
- A tool that changes or runs something is refused until the person approves it in KratosForge,
  even for a tool granted every category it may be given. The one that doesn't ask is listed with
  the reason, and a new tool that changes something must be added below.
"""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import _TYPES, CallContext, Caller, IntegrationError
from fabric_desktop.integrations.extensions import BUILT_IN, check as check_extension
from fabric_desktop.integrations.permissions import OFFERED, PermissionAuthorizer
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore

TRAVERSAL = ("../../Windows/System32", "..\\..\\Users", "C:\\Windows\\win.ini", "/etc/passwd", "a/../b",
             "\\\\server\\share", "x\x00y")
# Values of every JSON type: the first that isn't one of a field's types is sent as its wrong type.
WRONG = (12345, "x", True, [], {}, None)

# Tools that change or run something without asking, and why that's safe. A new one must be added here.
DOESNT_ASK = {
    "fabric_desktop_cancel_execution": "cancels only the calling tool's own execution, which it already ran with approval",
}
# Valid arguments for each tool that changes or runs something: enough to get past the schema to the
# permission check. A new tool that changes something must be added here, so it gets this test.
CHANGE_ARGS = {
    "fabric_desktop_cancel_execution": {"execution_id": "ex_" + "a" * 20},
    "fabric_desktop_cancel_pipeline_run": {"id": "run-1"},
    "fabric_desktop_cancel_proof": {"id": "proof-1"},
    "fabric_desktop_delete_proof_outputs": {"id": "proof-1"},
    "fabric_desktop_execute_python": {"code": "print(1)"},
    "fabric_desktop_run_notebook": {"name": "nb"},
    "fabric_desktop_start_pipeline_run": {"definition": "{}"},
    "fabric_desktop_start_proof": {"name": "nb"},
}


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


class Spy:
    def __init__(self):
        self.calls: list[str] = []

    def handler(self, cap_id):
        def run(svc, ctx, **args):
            self.calls.append(cap_id)
            return {"ran": cap_id}
        return run

    def bind(self, cap_id):
        def bound(svc, args):
            return {}
        return bound


def spied(authorizer=None) -> tuple[Registry, Spy]:
    """Every capability, with its handler (and bind) replaced by a spy that records being called."""
    plain = Registry()
    register_core(plain)
    for ext in BUILT_IN:
        for cap in check_extension(ext):
            plain.register(cap)
    spy = Spy()
    reg = Registry(authorizer)
    for cap in plain.all():
        reg.register(dataclasses.replace(cap, handler=spy.handler(cap.id),
                                         bind=spy.bind(cap.id) if cap.bind else None))
    return reg, spy


def ctx(client_id="t"):
    return CallContext(Caller(client_id, "test"), timeout=5)


def refused(reg, cap_id, args, client_id="t") -> IntegrationError:
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), cap_id, args, ctx(client_id))
    return err.value


def named(err: IntegrationError, field: str) -> bool:
    return err.code == "invalid_input" and any(field in p for p in err.details.get("problems", [err.message]))


def cases(cap):
    """(field, bad value, why) for each top-level property of the capability's input."""
    for field, prop in cap.input_schema.get("properties", {}).items():
        kinds = prop.get("type")
        kinds = [kinds] if isinstance(kinds, str) else (kinds or [])
        wrong = next((v for v in WRONG if not any(_TYPES[k](v) for k in kinds)), None)
        if kinds and wrong is not None:
            yield field, wrong, "wrong type"
        if "maxLength" in prop:
            yield field, "a" * (prop["maxLength"] + 1), "too long"
        if "maximum" in prop:
            yield field, prop["maximum"] + 1, "too big"
        if "minimum" in prop:
            yield field, prop["minimum"] - 1, "too small"
        if "enum" in prop:
            yield field, "not-one-of-them", "not in enum"
        if "pattern" in prop and "string" in kinds:
            for bad in TRAVERSAL:
                yield field, bad, "path-like"


def test_every_bad_argument_is_refused_by_name_before_anything_runs():
    reg, spy = spied()
    leaks = []
    for cap in reg.all():
        err = refused(reg, cap.id, {"zz_not_an_argument": 1})
        if not named(err, "zz_not_an_argument"):
            leaks.append(f"{cap.id}: an unknown field got {err.code}")
        for field, bad, why in cases(cap):
            try:
                reg.invoke(object(), cap.id, {field: bad}, ctx())
            except IntegrationError as err:
                if not named(err, field):
                    leaks.append(f"{cap.id}.{field} ({why}, {bad!r}): {err.code}")
                continue
            leaks.append(f"{cap.id}.{field} ({why}, {bad!r}): accepted")
    assert spy.calls == [], f"a handler ran on bad arguments: {spy.calls}"
    assert leaks == [], "bad arguments not refused by name:\n" + "\n".join(leaks)


def test_every_input_string_is_bounded_and_no_tool_takes_unlisted_fields():
    reg, _ = spied()
    unbounded = []

    def walk(schema, where):
        kinds = schema.get("type")
        kinds = [kinds] if isinstance(kinds, str) else (kinds or [])
        if "string" in kinds and not ({"maxLength", "enum", "const"} & set(schema)):
            unbounded.append(where)
        if "array" in kinds and "maxItems" not in schema and isinstance(schema.get("items"), dict):
            unbounded.append(where + " (no maxItems)")
        for key, sub in schema.get("properties", {}).items():
            walk(sub, f"{where}.{key}")
        if isinstance(schema.get("items"), dict):
            walk(schema["items"], where + "[]")

    for cap in reg.all():
        assert cap.input_schema.get("additionalProperties") is False, f"{cap.id} takes fields it doesn't list"
        walk(cap.input_schema, cap.id)
    assert unbounded == [], "input strings or lists with no length limit:\n" + "\n".join(unbounded)


def test_every_tool_that_changes_something_waits_for_the_persons_approval(store):
    reg, spy = spied(PermissionAuthorizer(store, wait_seconds=0.1, poll=0.02))
    client, _ = store.add_client("Cursor", "cursor", grants=list(OFFERED), auto=list(OFFERED))
    changing = {cap.id for cap in reg.all() if not cap.read_only}
    assert changing == set(CHANGE_ARGS), (
        "tools that change something must be listed in CHANGE_ARGS (and DOESNT_ASK if they don't ask): "
        f"new {sorted(changing - set(CHANGE_ARGS))}, gone {sorted(set(CHANGE_ARGS) - changing)}")
    ran = []
    for cap_id in sorted(changing):
        try:
            reg.invoke(object(), cap_id, CHANGE_ARGS[cap_id], ctx(client["id"]))
            ran.append(cap_id)
        except IntegrationError as err:
            assert cap_id not in DOESNT_ASK, f"{cap_id}: listed as not asking but refused with {err.code}"
            assert err.code in ("approval_required", "permission_denied"), (cap_id, err.code, err.message)
    assert sorted(ran) == sorted(DOESNT_ASK) and spy.calls == sorted(DOESNT_ASK)
    for cap_id in DOESNT_ASK:
        cap = reg.get(cap_id)
        assert cap.approval == "never" and not cap.destructive, cap_id
    for cap_id in changing - set(DOESNT_ASK):
        assert reg.get(cap_id).approval == "always" or reg.get(cap_id).destructive, f"{cap_id} can be pre-approved"


def test_an_approved_change_runs_once(store):
    """The other side of the refusal above: the person's yes lets exactly that call through."""
    import threading
    import time
    reg, spy = spied(PermissionAuthorizer(store, wait_seconds=5, poll=0.02))
    client, _ = store.add_client("Cursor", "cursor", grants=list(OFFERED), auto=[])

    def approve():
        for _ in range(250):
            pending = store.approvals("pending")
            if pending:
                store.decide(pending[0]["id"], True)
                return
            time.sleep(0.02)
    threading.Thread(target=approve, daemon=True).start()
    out = reg.invoke(object(), "fabric_desktop_execute_python", {"code": "print(1)"}, ctx(client["id"]))
    assert out == {"ran": "fabric_desktop_execute_python"} and spy.calls == ["fabric_desktop_execute_python"]
    reg.authorizer.wait_seconds = 0.1
    with pytest.raises(IntegrationError) as err:
        reg.invoke(object(), "fabric_desktop_execute_python", {"code": "print(1)"}, ctx(client["id"]))
    assert err.value.code == "approval_required" and spy.calls == ["fabric_desktop_execute_python"]
