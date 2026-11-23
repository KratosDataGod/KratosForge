"""Mutation tests for the contract every external caller gets (integrations/core.py): the error shape,
how the engine's exceptions map to codes, the JSON Schema checks at their edges, and the deadline and
cancel flag of a call. Found by tools/mutate.py."""

import threading
import types

import pytest

from fabric_desktop.integrations import core
from fabric_desktop.integrations.core import IntegrationError

CALLER = core.Caller("c1", "test")


class FabricError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _problems(schema, value):
    try:
        core.validate(schema, value)
    except IntegrationError as e:
        return e.details["problems"]
    return []


def test_an_error_says_its_hint_and_details_only_when_it_has_them():
    assert IntegrationError("failed", "No.").to_dict() == {"code": "failed", "message": "No.", "retryable": False}
    assert IntegrationError("failed", "No.", hint="Try again.", details={"n": 1}).to_dict() == {
        "code": "failed", "message": "No.", "retryable": False, "hint": "Try again.", "details": {"n": 1}}


@pytest.mark.parametrize("exc, code, retryable", [
    (FabricError("Bad request", 400), "upstream", False),
    (FabricError("Busy", 503), "upstream", True),
    (FabricError("Who are you", 401), "permission_denied", False),
    (FabricError("Not yours", 403), "permission_denied", False),
    (type("ConnectionError", (Exception,), {})("refused"), "upstream", True),
    (type("Timeout", (Exception,), {})("slow"), "upstream", True),
])
def test_fabrics_errors_map_to_codes_and_only_the_passing_ones_are_retried(exc, code, retryable):
    err = core.error_from(exc)
    assert (err.code, err.retryable, err.message) == (code, retryable, str(exc))


def test_each_json_type_takes_only_its_own_values():
    for schema, good, bad in (({"type": "number"}, [1, 1.5], ["1", True, None]),
                              ({"type": "null"}, [None], [0, "", False])):
        assert [core._problems(schema, v, "") for v in good] == [[] for _ in good]
        assert all(core._problems(schema, v, "") for v in bad), schema


def test_lengths_and_counts_are_checked_at_their_edges():
    assert _problems({"type": "string"}, "") == []
    assert _problems({"type": "array"}, []) == []
    assert _problems({"type": "array", "minItems": 2}, [1, 2]) == []
    assert _problems({"type": "array", "minItems": 2}, [1]) == ["arguments needs at least 2 items"]
    assert _problems({"type": "array", "maxItems": 2}, [1, 2]) == []
    assert _problems({"type": "array", "maxItems": 2}, [1, 2, 3]) == ["arguments has more than 2 items"]
    assert _problems({"type": "integer", "maximum": 5}, 5) == []
    assert _problems({"type": "integer", "maximum": 5}, 6) == ["arguments must be at most 5"]


def test_number_limits_apply_to_numbers_only():
    for value in ("text", [1], {"a": 1}):
        assert _problems({"minimum": 0, "maximum": 1}, value) == []


def test_a_dollar_inside_a_character_class_is_a_dollar_and_outside_it_is_the_end_of_the_text():
    assert core._end_of_text(r"^[$]a$") == r"^[$]a\Z"
    assert core._end_of_text(r"^[a$]$") == r"^[a$]\Z"
    assert core._end_of_text(r"^[a]$|^b$") == r"^[a]\Z|^b\Z"
    assert _problems({"type": "string", "pattern": "^[a-z]+$"}, "abc\n") == ["arguments isn't in the expected form"]


def test_the_message_lists_the_first_ten_problems_and_the_details_all():
    schema = {"type": "object", "required": [f"k{n:02}" for n in range(12)]}
    with pytest.raises(IntegrationError) as e:
        core.validate(schema, {})
    assert e.value.message.count(" is required") == 10 and len(e.value.details["problems"]) == 12


def test_progress_reaches_the_callback_and_none_is_fine():
    got = []
    core.CallContext(CALLER, progress=lambda *a: got.append(a)).report(1, 2, "half")
    assert got == [(1, 2, "half")]
    core.CallContext(CALLER).report(1)


def test_a_call_at_its_deadline_has_timed_out_and_may_be_retried(monkeypatch):
    monkeypatch.setattr(core, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    ctx = core.CallContext(CALLER, timeout=5, started=95.0)
    with pytest.raises(IntegrationError) as e:
        ctx.check()
    assert (e.value.code, e.value.retryable) == ("timeout", True)


def test_run_call_gives_up_at_the_deadline_on_a_daemon_thread(monkeypatch):
    monkeypatch.setattr(core, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    release, seen = threading.Event(), {}

    def slow():
        seen["daemon"] = threading.current_thread().daemon
        release.wait(5)
        return "late"

    ctx = core.CallContext(CALLER, timeout=5, started=95.0)
    try:
        with pytest.raises(IntegrationError, match="Gave up after 5 seconds"):
            core.run_call(slow, ctx)
        assert ctx.cancelled and seen == {"daemon": True}
    finally:
        release.set()
