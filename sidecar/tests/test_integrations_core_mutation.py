"""integrations/core.py's contract at its edges, read directly (mutation testing, batch 342).

test_integrations_core.py checks the contract through the registry; these pin the depth limits at their
edge, what redaction does with keys, None, objects and fields under a secret's name, the words of a file
error and a KeyError, each schema bound at its edge, the $ rewrite, and the deadline at exactly now.
"""

from __future__ import annotations

import dataclasses
import math
import threading

import pytest

from fabric_desktop.integrations import core
from fabric_desktop.integrations.core import (Caller, CallContext, IntegrationError, error_from, redact_result,
                                              run_call, too_deep, validate)
from fabric_desktop.redact import REMOVED


def nest(levels: int) -> list:
    value: list = []
    for _ in range(levels - 1):
        value = [value]
    return value


def test_too_deep_is_past_the_limit_not_at_it():
    assert too_deep(nest(3), 3) is False and too_deep(nest(4), 3) is True
    assert too_deep({"a": {"b": [1]}}, 3) is False and too_deep({"a": {"b": [[1]]}}, 3) is True
    assert too_deep("text", 0) is False


def test_arguments_nest_sixty_four_deep_and_results_two_hundred():
    assert validate({}, {"a": nest(63)}) == {"a": nest(63)}
    with pytest.raises(IntegrationError) as caught:
        validate({}, {"a": nest(64)})
    assert caught.value.code == "invalid_input" and caught.value.details["problems"]
    assert redact_result(nest(200)) == nest(200)
    with pytest.raises(IntegrationError) as caught:
        redact_result(nest(201))
    assert caught.value.code == "too_large"


class Thing:
    def __str__(self):
        return "a thing with password=hunter2"


def test_redaction_of_keys_none_objects_and_everything_under_a_secrets_name():
    out = redact_result({"password=hunter2": 1, 7: "x", "n": None, "obj": Thing(), "password": {"inner": "x"},
                         "token": Thing()})
    assert out[7] == "x" and out["n"] is None
    assert "hunter2" not in str(out) and "[removed]" in out["obj"]
    assert out["password"] == {"inner": REMOVED} and out["token"] == REMOVED
    assert not any(isinstance(k, str) and "hunter2" in k for k in out)


def test_a_file_error_names_the_file_only():
    err = error_from(FileNotFoundError(2, "No such file or directory", "/home/ann/data/x.ipynb"))
    assert err.code == "not_found" and err.message.startswith("No such file or directory: 'x.ipynb")
    assert "ann" not in err.message


@pytest.mark.xfail(strict=True, reason="Bug: error_from strips quotes from both ends of every not_found message "
                   "(meant for a KeyError's repr), which cuts the closing quote off a file error's name: "
                   "\"No such file or directory: 'x.ipynb\"")
def test_a_file_errors_name_keeps_both_its_quotes():
    err = error_from(FileNotFoundError(2, "No such file or directory", "/home/ann/data/x.ipynb"))
    assert err.message == "No such file or directory: 'x.ipynb'"


def test_a_key_error_reads_as_its_own_text_and_a_two_part_error_as_python_writes_it():
    assert error_from(KeyError("line one\nline two")).message == "line one\nline two"
    assert error_from(ValueError("a", "b")).message == "('a', 'b')"


def test_an_integration_error_is_passed_on_with_its_details_redacted():
    err = IntegrationError("failed", "m", details={"k": "password=hunter2"})
    out = error_from(err)
    assert out is err and "hunter2" not in str(out.details)
    deep = error_from(IntegrationError("failed", "m", details={"k": nest(250)}))
    assert deep.details == {}


def test_a_401_from_fabric_is_permission_denied():
    class FabricError(Exception):
        status = 401

    assert error_from(FabricError("no")).code == "permission_denied"


@pytest.mark.parametrize(("schema", "value", "ok"), [
    ({"type": "number"}, "x", False), ({"type": "number"}, 1.5, True), ({"type": "number"}, math.nan, False),
    ({"type": "null"}, None, True), ({"type": "null"}, 1, False),
    ({"minimum": 5}, True, True), ({"minimum": 5}, 5, True), ({"maximum": 5}, 5, True),
    ({"type": "string"}, "", True), ({"type": "array"}, [], True), ({"minItems": 2}, [1, 2], True),
    ({"maxItems": 2}, [1, 2], True),
])
def test_each_bound_holds_at_its_edge(schema, value, ok):
    assert (core._problems(schema, value, "") == []) is ok


def test_a_number_that_isnt_finite_is_refused_by_name():
    with pytest.raises(IntegrationError, match="must be a finite number"):
        validate({"type": "object", "properties": {"n": {"type": "number"}}}, {"n": math.inf})


def test_ten_problems_are_named_and_fifty_kept():
    schema = {"type": "object", "required": [f"k{i:02}" for i in range(60)]}
    with pytest.raises(IntegrationError) as caught:
        validate(schema, {})
    assert caught.value.message.count("is required") == 10 and len(caught.value.details["problems"]) == 50


def test_dollar_becomes_end_of_text_only_outside_escapes_and_classes():
    assert core._end_of_text(r"\d$") == r"\d\Z"
    assert core._end_of_text("[a$]$") == r"[a$]\Z"


def test_a_caller_cant_be_changed():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Caller("c", "test").client_id = "other"  # type: ignore[misc]


def context(timeout: float = 10.0) -> CallContext:
    return CallContext(Caller("c", "test"), timeout=timeout, started=100.0)


def test_progress_is_reported_and_the_time_left_counts_down(monkeypatch):
    seen = []
    ctx = context()
    ctx.progress = lambda done, total, message: seen.append((done, total, message))
    ctx.report(1, 2, "half")
    assert seen == [(1, 2, "half")]
    monkeypatch.setattr(core.time, "monotonic", lambda: 104.0)
    assert ctx.remaining() == 6.0


def test_the_deadline_is_reached_at_exactly_its_time(monkeypatch):
    monkeypatch.setattr(core.time, "monotonic", lambda: 100.0)
    with pytest.raises(IntegrationError) as caught:
        context(0).check()
    assert caught.value.code == "timeout" and caught.value.retryable is True
    release = threading.Event()
    try:
        with pytest.raises(IntegrationError) as caught:
            run_call(lambda: release.wait(5) and "done", context(0))
        assert caught.value.code == "timeout"
    finally:
        release.set()


def test_a_call_runs_on_a_daemon_thread():
    assert run_call(lambda: threading.current_thread().daemon, CallContext(Caller("c", "test"))) is True
