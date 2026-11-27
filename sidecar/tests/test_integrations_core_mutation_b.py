"""The external-caller contract (integrations/core.py), mutation batch 352: the error shape on the
wire, which Fabric errors are retryable, each schema keyword at its exact edge and with its default
left out, how $ is read inside and outside [...], and the call context's deadline, progress and
worker thread."""

from __future__ import annotations

import threading

import pytest

from fabric_desktop.integrations import core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, error_from, validate


class FabricError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def test_the_error_shape_has_a_hint_and_details_only_when_there_are_some():
    assert IntegrationError("failed", "No.").to_dict() == {"code": "failed", "message": "No.", "retryable": False}
    full = IntegrationError("failed", "No.", hint="Try X.", details={"a": 1}, retryable=True)
    assert full.to_dict() == {"code": "failed", "message": "No.", "retryable": True, "hint": "Try X.", "details": {"a": 1}}


def test_fabric_refusals_busy_answers_and_other_errors_map_to_their_codes():
    assert error_from(FabricError("no", 401)).code == "permission_denied"
    busy = error_from(FabricError("busy", 503))
    assert (busy.code, busy.retryable) == ("upstream", True) and "try again shortly" in busy.hint
    bad = error_from(FabricError("bad request", 400))
    assert (bad.code, bad.retryable, bad.hint) == ("upstream", False, "")


def problems(schema, value):
    return core._problems(schema, value, "")


def test_a_boolean_is_not_an_integer_or_a_number_and_null_is_only_none():
    assert problems({"type": "integer"}, True) == ["arguments should be integer"]
    assert problems({"type": "number"}, True) == ["arguments should be number"]
    assert problems({"type": "null"}, None) == []
    assert problems({"type": "null"}, 0) == ["arguments should be null"]


def test_a_dollar_ends_the_text_outside_brackets_only():
    assert core._end_of_text("$") == r"\Z"
    assert core._end_of_text("[a$]") == "[a$]"
    assert core._end_of_text(r"a\.$") == r"a\.\Z"  # only the one character after a backslash is escaped


def test_limits_hold_at_their_exact_edges_and_are_absent_when_unset():
    assert problems({"type": "string"}, "") == []
    assert problems({"type": "integer", "minimum": 5, "maximum": 9}, 5) == []
    assert problems({"type": "integer", "minimum": 5, "maximum": 9}, 9) == []
    assert problems({"type": "array"}, []) == []
    assert problems({"type": "array", "minItems": 2, "maxItems": 2}, [1, 2]) == []
    assert problems({"type": "array", "minItems": 2}, [1]) == ["arguments needs at least 2 items"]


def test_a_boolean_isnt_held_to_a_numbers_limits():
    assert problems({"type": ["boolean", "integer"], "minimum": 5}, True) == []


def test_the_message_lists_ten_problems_and_the_details_keep_more():
    schema = {"type": "object", "required": [f"k{i:02d}" for i in range(12)]}
    with pytest.raises(IntegrationError) as raised:
        validate(schema, {})
    assert len(str(raised.value).split("; ")) == 10
    assert len(raised.value.details["problems"]) == 12


def test_check_gives_up_exactly_at_the_deadline_and_not_before(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(core.time, "monotonic", lambda: now[0])
    ctx = CallContext(Caller("c", "test"), timeout=5.0, started=100.0)
    ctx.check()
    now[0] = 105.0
    with pytest.raises(IntegrationError) as raised:
        ctx.check()
    assert raised.value.code == "timeout" and raised.value.retryable is True


def test_progress_goes_to_the_callback_when_there_is_one():
    seen = []
    CallContext(Caller("c", "test"), progress=lambda d, t, m: seen.append((d, t, m))).report(1, 2, "half")
    assert seen == [(1, 2, "half")]
    CallContext(Caller("c", "test")).report(1)  # no callback: nothing to call


def test_a_call_runs_on_a_daemon_thread():
    ctx = CallContext(Caller("c", "test"))
    assert core.run_call(lambda: threading.current_thread().daemon, ctx) is True


def test_a_call_gives_up_exactly_at_its_deadline(monkeypatch):
    release = threading.Event()
    real = core.time.monotonic
    ctx = CallContext(Caller("c", "test"), timeout=5.0, started=real())
    monkeypatch.setattr(core.time, "monotonic", lambda: ctx.deadline)
    try:
        with pytest.raises(IntegrationError) as raised:
            core.run_call(lambda: release.wait(2), ctx)
        assert raised.value.code == "timeout"
    finally:
        release.set()
