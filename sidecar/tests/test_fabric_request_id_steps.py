"""A Fabric error's next step points at the request id only when Fabric sent one, so a person is never
told to give support an id the message doesn't have. With an id the step is unchanged and the id follows."""

from __future__ import annotations

import pytest

from fabric_desktop.fabric_api import _error, _operation_failed

from test_fabric_api import _Answer

URL = "https://api.fabric.microsoft.com/v1/workspaces"
SERVER_WITH = "Fabric had a problem on its side. Try again, and give the request id to support if it keeps happening."
SERVER_WITHOUT = "Fabric had a problem on its side. Try again, and contact support if it keeps happening."
OTHER_WITH = "Check Fabric's message, then try again, or give the request id to support."
OTHER_WITHOUT = "Check Fabric's message, then try again."


@pytest.mark.parametrize("status, code, with_id, without_id", [
    (502, None, SERVER_WITH, SERVER_WITHOUT),
    (400, "UnknownError", SERVER_WITH, SERVER_WITHOUT),
    (418, None, OTHER_WITH, OTHER_WITHOUT),
])
def test_a_reads_next_step_names_the_request_id_only_when_there_is_one(status, code, with_id, without_id):
    body = {"message": "Something"} | ({"errorCode": code} if code else {})
    head = f"GET /workspaces failed with {status}" + (f" {code}" if code else "") + ": Something. "
    assert str(_error("GET", URL, _Answer(status, body))) == head + without_id
    assert str(_error("GET", URL, _Answer(status, body | {"requestId": "req-7"}))) == (
        head + with_id + " Request id req-7.")


def test_an_id_that_doesnt_look_like_one_counts_as_none():
    text = str(_error("GET", URL, _Answer(503, {"message": "x", "requestId": "has spaces"})))
    assert text.endswith(SERVER_WITHOUT) and "request id" not in text.lower()


def test_a_failed_operations_step_names_the_request_id_only_when_there_is_one():
    error = {"errorCode": "UnknownError", "message": "Bad"}
    assert str(_operation_failed("op1", {"status": "Failed", "error": error})) == (
        "Fabric's operation op1 failed: Bad (UnknownError). " + SERVER_WITHOUT)
    assert str(_operation_failed("op1", {"status": "Failed", "error": error | {"requestId": "r1"}})) == (
        "Fabric's operation op1 failed: Bad (UnknownError). " + SERVER_WITH + " Request id r1.")
