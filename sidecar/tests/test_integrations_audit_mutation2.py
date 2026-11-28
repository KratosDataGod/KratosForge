"""Mutation testing, second pass: integrations/audit.py's line for a call is exact.

Pins what a line keeps of the arguments (anything that isn't an object, nested past the argument
limit, or too deep to walk shows as "(given)"), the decision recorded for each refusal code and
for a call that failed after it was allowed, the outcome of a "started" line, and the client name
and error text a line carries.
"""

from __future__ import annotations

import time

import pytest

from fabric_desktop.integrations import audit
from fabric_desktop.integrations.audit import GIVEN, IntegrationAudit, arguments, decision_for
from fabric_desktop.integrations.core import MAX_DEPTH, CallContext, Caller, IntegrationError
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.redact import REMOVED

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"  # pragma: allowlist secret (a made-up token)


@pytest.fixture
def trail(tmp_path):
    return IntegrationAudit(IntegrationStore(tmp_path / "integrations" / "integrations.sqlite"))


def nested_dict(levels: int) -> dict:
    value: dict = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


def ctx(decision: str = "", name: str = "Probe", client_id: str = "") -> CallContext:
    c = CallContext(Caller(client_id, "test", name))
    c.decision = decision
    return c


# ---- arguments

def test_no_arguments_are_an_empty_object():
    assert arguments(None) == {}
    assert arguments({}) == {}


@pytest.mark.parametrize("value", [[1, 2], "text", 5, ("a",)])
def test_arguments_that_arent_an_object_show_as_given(value):
    assert arguments(value) == {"arguments": GIVEN}


def test_arguments_nested_up_to_the_limit_are_kept_and_one_more_level_isnt():
    assert arguments(nested_dict(MAX_DEPTH)) == nested_dict(MAX_DEPTH)
    assert arguments(nested_dict(MAX_DEPTH + 1)) == {"arguments": GIVEN}


def test_arguments_too_deep_to_walk_show_as_given(monkeypatch):
    def recurses(values):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(audit, "target", recurses)
    assert arguments({"a": 1}) == {"arguments": GIVEN}


def test_identifying_arguments_are_kept_and_code_is_given():
    assert arguments({"workspace_id": "w1", "sql": "select 1"}) == {"workspace_id": "w1", "sql": GIVEN}


# ---- decision_for

@pytest.mark.parametrize("code, decision", [
    ("permission_denied", "denied"), ("integrations_disabled", "denied"), ("rate_limited", "denied"),
    ("approval_denied", "approval_denied"), ("approval_required", "approval_pending"),
])
def test_each_refusal_code_records_its_decision_whatever_the_context_says(code, decision):
    assert decision_for(IntegrationError(code, "x"), ctx("approved")) == decision
    assert decision_for(IntegrationError(code, "x"), ctx()) == decision


def test_a_call_that_failed_after_it_was_allowed_keeps_its_decision():
    assert decision_for(IntegrationError("not_found", "x"), ctx("allowed")) == "allowed"
    assert decision_for(IntegrationError("failed", "x"), ctx("approved")) == "approved"


def test_a_call_that_failed_before_the_checks_is_invalid_and_one_that_didnt_fail_is_allowed():
    assert decision_for(IntegrationError("invalid_input", "x"), ctx()) == "invalid"
    assert decision_for(None, ctx()) == "allowed"
    assert decision_for(None, ctx("approved")) == "approved"


# ---- record

def test_a_started_line_says_started_and_an_outcome_line_says_ok_or_the_code(trail):
    c = ctx("approved")
    c.approval_id = "ap_1"
    started = trail.record(c, "ext_cap", "RUN_NOTEBOOK", {}, time.time(), 0.0, stage="started")
    assert started["outcome"] == "started" and started["approval_id"] == "ap_1" and "error" not in started
    done = trail.record(c, "ext_cap", "RUN_NOTEBOOK", {}, time.time(), 1.0)
    assert done["outcome"] == "ok" and done["decision"] == "approved"
    failed = trail.record(c, "ext_cap", "RUN_NOTEBOOK", {}, time.time(), 1.0, IntegrationError("timeout", "slow"))
    assert failed["outcome"] == "timeout" and failed["error"] == "slow"


def test_a_line_without_an_approval_has_no_approval_id(trail):
    line = trail.record(ctx(), "ext_cap", "READ_METADATA", {}, time.time(), 0.0)
    assert "approval_id" not in line and "error" not in line


def test_the_error_on_a_line_is_redacted(trail):
    line = trail.record(ctx(), "ext_cap", "READ_METADATA", {}, time.time(), 0.0,
                        IntegrationError("failed", f"token {JWT}"))
    assert line["error"] == f"token {REMOVED}"
    assert line["decision"] == "invalid" and line["outcome"] == "failed"


def test_the_line_names_the_client_from_the_store_then_the_caller_then_nobody(trail):
    added, _token = trail.store.add_client("Stored name")
    assert trail.record(ctx(name="Given", client_id=added["id"]), "ext_cap", "READ_METADATA", {}, time.time(),
                        0.0)["client"] == "Stored name"
    assert trail.record(ctx(name="Given", client_id="cl_gone"), "ext_cap", "READ_METADATA", {}, time.time(),
                        0.0)["client"] == "Given"
    assert trail.record(ctx(name=""), "ext_cap", "READ_METADATA", {}, time.time(), 0.0)["client"] == ""


def test_a_line_carries_the_execution_and_the_arguments_kept(trail):
    line = trail.record(ctx(), "ext_cap", "READ_METADATA", {"workspace_id": "w", "code": "print(1)"}, time.time(),
                        0.0, execution_id="ex_" + "0" * 20)
    assert line["execution_id"] == "ex_" + "0" * 20
    assert line["arguments"] == {"workspace_id": "w", "code": GIVEN}
    assert trail.recent(1)[0]["execution_id"] == "ex_" + "0" * 20
