"""Mutation testing: the external callers' contract (integrations/core.py) is exact.

Pins the nesting limits for arguments and results at their boundaries, how a result is redacted
(keys, blanked fields, tuples, values that aren't JSON), how an engine exception becomes an
IntegrationError (a file error naming only the file, a home folder naming nobody, the text of a
KeyError, each Fabric status), how a passed-on IntegrationError is redacted, and the error's shape.
"""

from __future__ import annotations

import dataclasses
import os
import threading
from pathlib import PurePosixPath

import pytest

from fabric_desktop.integrations import core
from fabric_desktop.integrations.core import (MAX_DEPTH, MAX_RESULT_DEPTH, CallContext, Caller, IntegrationError,
                                              error_from, plain_message, redact_result, redacted, run_call, too_deep, validate)
from fabric_desktop.redact import REMOVED

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"  # pragma: allowlist secret (a made-up token)


def nested(levels: int, inner=None):
    """A list nested ``levels`` deep (one level is a flat list)."""
    value = [] if inner is None else [inner]
    for _ in range(levels - 1):
        value = [value]
    return value


def nested_dict(levels: int):
    """An object nested ``levels`` deep, the outermost counting as the first."""
    value: dict = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


# ---- too_deep

def test_too_deep_counts_the_value_itself_as_the_first_level():
    assert too_deep(nested(3), 3) is False
    assert too_deep(nested(4), 3) is True
    assert too_deep(nested_dict(3), 3) is False
    assert too_deep(nested_dict(4), 3) is True


def test_too_deep_reads_tuples_and_object_values_but_not_scalars():
    assert too_deep(((((),),),), 3) is True
    assert too_deep({"k": {"k": {"k": {}}}}, 3) is True
    assert too_deep("x" * 1000, 0) is False
    assert too_deep(5, 0) is False
    assert too_deep(None, 0) is False
    # Only containers count: a string at the deepest level adds no level of its own.
    assert too_deep(nested(3, "leaf"), 3) is False


def test_too_deep_looks_at_every_branch_not_just_the_last():
    deep_first = {"a": nested_dict(5), "b": 1}
    assert too_deep(deep_first, 5) is True
    assert too_deep([nested(5), "x", 1], 5) is True


# ---- arguments nested too deep

def test_the_argument_limit_is_64_levels_counting_the_arguments_object():
    assert MAX_DEPTH == 64
    schema = {"type": "object"}
    assert validate(schema, nested_dict(64)) == nested_dict(64)
    with pytest.raises(IntegrationError) as err:
        validate(schema, nested_dict(65))
    e = err.value
    assert e.code == "invalid_input"
    assert e.message == "The arguments are nested more than 64 levels deep."
    assert e.hint == "Send flatter arguments."
    assert e.details == {"problems": ["arguments are nested more than 64 levels deep"]}


def test_too_deep_arguments_are_refused_before_the_schema_is_read():
    # A schema that would refuse anything for another reason: the depth message comes first.
    with pytest.raises(IntegrationError) as err:
        validate({"type": "string"}, nested_dict(65))
    assert "nested more than" in err.value.message


def test_validate_keeps_at_most_10_problems_in_the_message_and_50_in_details():
    schema = {"type": "object", "additionalProperties": False}
    args = {f"k{i:02}": 1 for i in range(60)}
    with pytest.raises(IntegrationError) as err:
        validate(schema, args)
    e = err.value
    assert e.message.count("isn't an argument this takes") == 10
    assert len(e.details["problems"]) == 50
    assert e.hint == "List the capabilities to see each one's input schema."


def test_validate_fills_only_defaults_that_are_given_and_never_overrides():
    schema = {"type": "object", "properties": {"a": {"type": "integer", "default": 1}, "b": {"type": "integer"}}}
    assert validate(schema, {}) == {"a": 1}
    assert validate(schema, {"a": 5}) == {"a": 5}
    assert validate(schema, None) == {"a": 1}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_number_that_isnt_finite_is_refused_whatever_the_bounds(value):
    with pytest.raises(IntegrationError) as err:
        validate({"type": "object", "properties": {"n": {"type": "number"}}}, {"n": value})
    assert err.value.message == "n must be a finite number"


def test_a_string_exactly_at_min_and_max_length_passes_and_past_them_doesnt():
    schema = {"type": "object", "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 3}}}
    assert validate(schema, {"s": "ab"}) == {"s": "ab"}
    assert validate(schema, {"s": "abc"}) == {"s": "abc"}
    for bad, why in (("a", "s is too short"), ("abcd", "s is longer than 3 characters")):
        with pytest.raises(IntegrationError) as err:
            validate(schema, {"s": bad})
        assert err.value.message == why


def test_numbers_and_item_counts_exactly_at_their_bounds_pass():
    schema = {"type": "object", "properties": {"n": {"type": "number", "minimum": 1, "maximum": 3},
                                               "l": {"type": "array", "minItems": 1, "maxItems": 2}}}
    assert validate(schema, {"n": 1, "l": [1]}) == {"n": 1, "l": [1]}
    assert validate(schema, {"n": 3, "l": [1, 2]}) == {"n": 3, "l": [1, 2]}
    with pytest.raises(IntegrationError) as err:
        validate(schema, {"n": 0, "l": []})
    assert err.value.details["problems"] == ["n must be at least 1", "l needs at least 1 items"]
    with pytest.raises(IntegrationError) as err:
        validate(schema, {"n": 4, "l": [1, 2, 3]})
    assert err.value.details["problems"] == ["n must be at most 3", "l has more than 2 items"]


def test_a_boolean_is_not_a_number_for_the_bounds():
    # True would be 1, below a minimum of 2, if a flag were read as a number.
    assert validate({"type": "object", "properties": {"n": {"minimum": 2}}}, {"n": True}) == {"n": True}


def test_a_required_field_inside_an_object_names_its_path():
    schema = {"type": "object", "properties": {"o": {"type": "object", "required": ["x"]}}}
    with pytest.raises(IntegrationError) as err:
        validate(schema, {"o": {}})
    assert err.value.message == "o.x is required"
    with pytest.raises(IntegrationError) as err:
        validate({"type": "object", "required": ["x"]}, {})
    assert err.value.message == "x is required"


# ---- results nested too deep, and redaction

def test_the_result_limit_is_200_levels():
    assert MAX_RESULT_DEPTH == 200
    assert redact_result(nested(200)) == nested(200)
    with pytest.raises(IntegrationError) as err:
        redact_result(nested(201))
    e = err.value
    assert e.code == "too_large"
    assert e.message == "The result is nested more than 200 levels deep."
    assert e.hint == "Ask for less, or for one part at a time."


def test_redact_result_removes_secrets_from_keys_and_values():
    out = redact_result({f"Bearer {JWT}": f"token is {JWT}", "n": 3, "ok": True, "f": 1.5, "z": None})
    assert JWT not in repr(out)
    assert list(out.values())[0] == f"token is {REMOVED}"
    assert out["n"] == 3 and out["ok"] is True and out["f"] == 1.5 and out["z"] is None


def test_a_key_that_isnt_text_is_kept_as_it_is():
    assert redact_result({1: "a", None: "b"}) == {1: "a", None: "b"}


def test_everything_under_a_secret_field_is_blanked_but_numbers_and_flags_stay():
    out = redact_result({"password": {"value": "hunter2", "n": 2, "on": False, "none": None, "items": ["a", ("b",)]},
                         "plain": "hunter2"})
    assert out == {"password": {"value": REMOVED, "n": 2, "on": False, "none": None, "items": [REMOVED, [REMOVED]]},
                   "plain": "hunter2"}


def test_a_tuple_comes_back_as_a_list():
    assert redact_result(("a", ("b", 1))) == ["a", ["b", 1]]


class Shown:
    def __str__(self) -> str:
        return f"shown with {JWT}"


def test_a_value_that_isnt_json_becomes_its_text_without_secrets_or_is_blanked_under_a_secret():
    assert redact_result([Shown()]) == [f"shown with {REMOVED}"]
    assert redact_result({"token": Shown()}) == {"token": REMOVED}


# ---- redacted

def test_redacted_removes_secrets_from_message_args_hint_and_details_in_place():
    err = IntegrationError("failed", f"bad {JWT}", hint=f"use {JWT}", details={"why": [f"x {JWT}"], "secret": "s3"})
    same = redacted(err)
    assert same is err
    assert err.message == f"bad {REMOVED}" and err.args == (err.message,) and str(err) == err.message
    assert err.hint == f"use {REMOVED}"
    assert err.details == {"why": [f"x {REMOVED}"], "secret": REMOVED}


def test_redacted_drops_details_nested_past_the_result_limit():
    err = redacted(IntegrationError("failed", "x", details={"d": nested(200)}))
    assert err.details == {}
    err = redacted(IntegrationError("failed", "x", details={"d": nested(199)}))
    assert err.details == {"d": nested(199)}


def test_redacted_leaves_no_details_as_none_given():
    err = redacted(IntegrationError("failed", "x"))
    assert err.details == {} and "details" not in err.to_dict() and "hint" not in err.to_dict()


def test_to_dict_carries_hint_and_details_only_when_there_are_some():
    d = IntegrationError("timeout", "slow", hint="wait", retryable=True, details={"a": 1}).to_dict()
    assert d == {"code": "timeout", "message": "slow", "retryable": True, "hint": "wait", "details": {"a": 1}}


# ---- error_from

def test_an_integration_error_is_passed_on_redacted():
    err = IntegrationError("not_found", f"no {JWT}", hint=f"h {JWT}")
    out = error_from(err)
    assert out is err and out.code == "not_found"
    assert JWT not in out.message and JWT not in out.hint


@pytest.mark.parametrize("filename", [
    r"C:\Users\ann\AppData\Local\KratosForge\notebooks\x.ipynb",
    "/home/ann/.local/share/kratosforge/x.ipynb",
    b"/home/ann/data/x.ipynb",
    PurePosixPath("/home/ann/data/x.ipynb"),
])
def test_a_file_error_names_only_the_file(filename):
    exc = FileNotFoundError(2, "No such file or directory", filename)
    err = error_from(exc)
    assert err.code == "not_found"
    # not_found strips quotes from the ends of a message (for a KeyError's repr), so the closing
    # quote goes too; what matters here is that only the file's name is left.
    assert err.message.rstrip("'") == "No such file or directory: 'x.ipynb"
    assert "ann" not in err.message


def test_a_file_error_with_two_files_names_both_and_its_code_follows_its_type():
    exc = PermissionError(13, "Permission denied", "/home/ann/a.txt", None, "/home/ann/b.txt")
    err = error_from(exc)
    assert err.code == "permission_denied"
    assert err.message == "Permission denied: 'a.txt' -> 'b.txt'"


def test_a_file_error_with_no_text_says_its_kind():
    exc = OSError()
    exc.filename = "/home/ann/x.txt"
    err = error_from(exc)
    assert err.code == "failed"  # an OSError of no more specific kind
    assert err.message == "OSError: 'x.txt'"


def test_a_file_error_without_a_file_keeps_its_text_but_not_the_folders():
    err = error_from(OSError("cannot read /home/ann/x.txt"))
    assert err.message == "cannot read x.txt"


def test_an_engine_message_naming_a_home_folder_keeps_the_file_but_not_whose():
    err = error_from(ValueError(r"bad file C:\Users\ann\notes\x.ipynb"))
    assert err.code == "invalid_input"
    assert err.message == "bad file x.ipynb"


def test_a_missing_file_keeps_both_quotes_round_its_name():
    err = error_from(FileNotFoundError(2, "No such file or directory", "/a/b/x.ipynb"))
    assert err.code == "not_found" and err.message == "No such file or directory: 'x.ipynb'"
    assert error_from(KeyError("nb")).message == "nb"


def test_a_key_errors_own_text_is_used_so_a_secret_on_its_next_line_is_found():
    err = error_from(KeyError(f"No notebook\npassword={JWT}"))
    assert err.code == "not_found"
    assert JWT not in err.message
    assert err.message.startswith("No notebook\n")  # its text, not the repr with \\n


def test_a_key_error_that_isnt_text_or_has_more_args_is_its_str():
    assert error_from(KeyError(5)).message == "5"
    assert error_from(KeyError("a", "b")).message == "('a', 'b')"


def test_an_exception_with_no_message_is_named_by_its_type():
    assert error_from(ValueError()).message == "ValueError"
    assert error_from(ZeroDivisionError()).message == "ZeroDivisionError: ZeroDivisionError"


def test_an_unknown_exception_is_internal_with_its_name():
    err = error_from(ZeroDivisionError(f"x {JWT}"))
    assert err.code == "internal" and err.message == f"ZeroDivisionError: x {REMOVED}" and not err.retryable


def _named(name: str, status: int | None = None):
    cls = type(name, (Exception,), {})
    exc = cls("boom")
    if status is not None:
        exc.status = status
    return exc


@pytest.mark.parametrize("exc, code, retryable", [
    (_named("FabricError", 401), "permission_denied", False),
    (_named("FabricError", 403), "permission_denied", False),
    (_named("FabricError", 404), "not_found", False),
    (_named("FabricError", 400), "upstream", False),
    (_named("FabricError", 409), "upstream", False),
    (_named("FabricError", 500), "upstream", True),
    (_named("FabricError", 408), "upstream", True),
    (_named("FabricError"), "upstream", False),
    (_named("HTTPError"), "upstream", False),
    (_named("ConnectionError"), "upstream", True),
    (_named("Timeout"), "upstream", True),
    (_named("Other", 502), "upstream", True),
    (_named("Other", 400), "upstream", False),
])
def test_fabric_and_http_errors_map_by_status_and_name(exc, code, retryable):
    err = error_from(exc)
    assert (err.code, err.retryable) == (code, retryable)
    if code == "permission_denied":
        assert err.hint == "Your account can't do this in Fabric. Check your workspace role."
    elif retryable:
        assert err.hint == "Fabric is busy or can't be reached; try again shortly."
    else:
        assert err.hint == ""


@pytest.mark.parametrize("text, code", [("401 Unauthorized", "permission_denied"), ("Forbidden here", "permission_denied"),
                                        ("ItemNotFound", "not_found")])
def test_a_fabric_error_without_a_status_is_read_from_its_text(text, code):
    exc = type("FabricError", (Exception,), {})(text)
    assert error_from(exc).code == code


def test_an_exception_named_otherwise_without_a_status_isnt_upstream():
    # "Unauthorized" in an ordinary error's text doesn't make it a Fabric permission problem.
    assert error_from(RuntimeError("Unauthorized step failed")).code == "failed"


def test_a_gate_errors_reasons_are_redacted_too():
    class Gate(Exception):
        errors = [f"token {JWT}", ValueError("plain")]
    err = error_from(Gate("checks failed"))
    assert err.code == "failed"
    assert err.details == {"reasons": [f"token {REMOVED}", "plain"]}


def test_a_lookup_error_loses_its_quotes():
    assert error_from(LookupError("'nb'")).message == "nb"
    assert error_from(KeyError("nb")).message == "nb"


@pytest.mark.parametrize("text", ["X is running", "x IS ALREADY RUNNING", "please wait for it", "Something is using it"])
def test_a_runtime_error_that_says_something_else_is_busy_is_a_retryable_conflict(text):
    err = error_from(RuntimeError(text))
    assert (err.code, err.retryable) == ("conflict", True)


def test_a_timeout_is_retryable_and_bad_input_isnt():
    assert (error_from(TimeoutError("t")).code, error_from(TimeoutError("t")).retryable) == ("timeout", True)
    assert (error_from(TypeError("t")).code, error_from(TypeError("t")).retryable) == ("invalid_input", False)


# ---- run_call and the context

def test_run_call_returns_none_for_a_handler_that_returns_nothing_and_passes_errors_redacted():
    ctx = CallContext(Caller("c", "test"), timeout=5)
    assert run_call(lambda: None, ctx) is None

    def fails():
        raise IntegrationError("failed", f"oops {JWT}")
    with pytest.raises(IntegrationError) as err:
        run_call(fails, ctx)
    assert err.value.message == f"oops {REMOVED}"


def test_run_call_hint_on_timeout_says_what_to_do():
    ctx = CallContext(Caller("c", "test"), timeout=0.1)
    release = threading.Event()
    try:
        with pytest.raises(IntegrationError) as err:
            run_call(lambda: release.wait(5), ctx)
    finally:
        release.set()
    assert err.value.code == "timeout" and err.value.retryable is True
    assert err.value.hint.startswith("Ask for less")
    assert ctx.cancelled


def test_remaining_is_never_negative_and_counts_down_from_the_timeout():
    ctx = CallContext(Caller("c", "test"), timeout=10, started=-1e9)
    assert ctx.deadline == -1e9 + 10
    assert ctx.remaining() == 0.0
    fresh = CallContext(Caller("c", "test"), timeout=10)
    assert 9.0 < fresh.remaining() <= 10.0


def test_a_progress_report_goes_to_the_callback():
    seen = []
    ctx = CallContext(Caller("c", "test"), progress=lambda d, t, m: seen.append((d, t, m)))
    ctx.report(1, 2, "half")
    ctx.report(3)
    assert seen == [(1, 2, "half"), (3, None, "")]


def test_the_module_keeps_os_paths_short_on_windows_paths_too():
    # ntpath splits on both separators, so a POSIX path gives its last part on every OS.
    assert core._short_os_message(FileNotFoundError(2, "gone", "/a/b/c.txt")) == "gone: 'c.txt'"
    assert core._short_os_message(FileNotFoundError(2, "gone", os.fsencode("C:\\a\\b.txt"))) == "gone: 'b.txt'"


# ---- types, patterns and the context's clock

def test_a_number_is_an_int_or_a_float_and_never_a_flag_or_text():
    schema = {"type": "object", "properties": {"n": {"type": "number"}, "z": {"type": "null"}}}
    assert validate(schema, {"n": 1, "z": None}) == {"n": 1, "z": None}
    for bad in (True, "1", None):
        with pytest.raises(IntegrationError):
            validate(schema, {"n": bad})
    for bad in (0, "", False):
        with pytest.raises(IntegrationError):
            validate(schema, {"z": bad})


def test_end_of_text_changes_every_dollar_outside_a_class_from_the_first_character_on():
    assert core._end_of_text("$") == r"\Z"
    assert core._end_of_text("a$") == r"a\Z"
    assert core._end_of_text(r"\$") == r"\$"
    assert core._end_of_text(r"\.$") == r"\.\Z"  # an escape covers one character, not the rest
    assert core._end_of_text("[ab$]$") == r"[ab$]\Z"


def test_a_caller_cant_be_changed_once_made():
    caller = Caller("c", "api", "n")
    with pytest.raises(dataclasses.FrozenInstanceError):
        caller.client_id = "other"  # type: ignore[misc]
    assert hash(caller) == hash(Caller("c", "api", "n"))


def test_a_handler_runs_on_a_daemon_thread_so_one_that_never_ends_cant_hold_the_process_open():
    ctx = CallContext(Caller("c", "test"), timeout=5)
    assert run_call(lambda: (threading.current_thread().daemon, threading.current_thread().name), ctx) == \
        (True, "integration-call")


def test_run_call_gives_up_at_the_deadline_itself(monkeypatch):
    ctx = CallContext(Caller("c", "test"), timeout=5, started=100.0)
    release = threading.Event()
    reads = []

    def clock():
        reads.append(1)
        return 105.0 if len(reads) == 1 else 106.0  # exactly the deadline, then past it
    monkeypatch.setattr(core.time, "monotonic", clock)
    try:
        with pytest.raises(IntegrationError) as err:
            run_call(lambda: release.wait(5), ctx)
    finally:
        release.set()
    assert err.value.code == "timeout"
    assert len(reads) == 1  # given up on the first look, at the deadline


def test_check_gives_up_at_the_deadline_itself(monkeypatch):
    ctx = CallContext(Caller("c", "test"), timeout=5, started=100.0)
    monkeypatch.setattr(core.time, "monotonic", lambda: 104.5)
    ctx.check()
    monkeypatch.setattr(core.time, "monotonic", lambda: 105.0)
    with pytest.raises(IntegrationError) as err:
        ctx.check()
    assert err.value.code == "timeout" and err.value.message == "Gave up after 5 seconds."


@pytest.mark.parametrize("text, plain", [
    ("rows 10 /20", "rows 10 /20"),  # a slash between numbers isn't a path
    ("/x.txt missing", "/x.txt missing"),  # a file at the root names no folder
    ("cannot read /home/ann/x.txt", "cannot read x.txt"),
    ("see file:///home/a/b.txt", "see b.txt"),
    (r"bad C:\Users\a\x.csv", "bad x.csv"),
])
def test_only_a_path_with_folders_is_cut_to_its_file(text, plain):
    assert plain_message(text) == plain


def test_a_folder_where_a_file_should_be_is_failed_on_every_platform(tmp_path):
    """Opening a folder as a file: Linux raises IsADirectoryError, Windows PermissionError. Both are
    the same damage, so both are failed; a real permission refusal stays permission_denied."""
    folder = tmp_path / "x.ipynb"
    folder.mkdir()
    windows = error_from(PermissionError(13, "Permission denied", str(folder)))
    linux = error_from(IsADirectoryError(21, "Is a directory", str(folder)))
    assert (windows.code, linux.code) == ("failed", "failed")
    assert windows.message == "Permission denied: 'x.ipynb'" and str(tmp_path) not in windows.message
    assert error_from(PermissionError(13, "Permission denied", str(tmp_path / "f.txt"))).code == "permission_denied"
