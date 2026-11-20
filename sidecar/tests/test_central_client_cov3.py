"""central/client.py's remaining paths: unknown operations refused before anything is sent, a 404 from
call_once once it was known to be there, an errors value that isn't a list, and a response whose text
isn't a string. UDF_Platform is a scripted session; nothing reaches a tenant."""

from __future__ import annotations

import json

import pytest
import requests

from fabric_desktop.central import core
from fabric_desktop.central.client import FabricPlatform, LocalPlatform, _start


def response(status, body=None, raw=None):
    resp = requests.Response()
    resp.status_code, resp.encoding = status, "utf-8"
    resp._content = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    resp._content_consumed = True
    return resp


class Session:
    def __init__(self, answers):
        self.answers, self.sent = list(answers), []

    def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002 - requests' own name
        self.sent.append((url.rsplit("/functions/", 1)[1].split("/")[0], json))
        return self.answers.pop(0)


def fabric(answers):
    s = Session(answers)
    return FabricPlatform(lambda: "tok", "ws", "fn", session=s, sleep=lambda _s: None), s


CHANGE = next(op for op in sorted(core.OPERATIONS)
              if op not in core.READ_OPERATIONS and op not in core.REPEATABLE_OPERATIONS)
READ = sorted(core.READ_OPERATIONS)[0]


def test_an_unknown_operation_is_refused_locally_and_in_fabric_before_anything_is_sent(tmp_path):
    with pytest.raises(ValueError, match="no operation 'explode'"):
        LocalPlatform(tmp_path / "p.db").call("explode")
    client, s = fabric([])
    with pytest.raises(ValueError, match="no operation 'explode'"):
        client.call("explode", by="me")
    assert s.sent == []


def test_a_404_from_call_once_after_it_answered_is_the_items_own_not_an_older_app():
    client, s = fabric([response(200, {"output": {"result": "done"}}), response(404, {"errorCode": "ItemNotFound"})])
    assert client.call(CHANGE) == "done"
    with pytest.raises(LookupError, match="UDF_Platform wasn't found"):
        client.call(CHANGE)
    assert [op for op, _ in s.sent] == ["call_once", "call_once"]   # never sent as the change itself


def test_an_errors_value_that_isnt_a_list_is_still_the_message():
    client, _ = fabric([response(200, {"errors": {"message": "key table locked"}}),
                        response(200, {"status": "Failed", "errors": "plain text"})])
    with pytest.raises(core.PlatformError, match="couldn't .*: key table locked$"):
        client.call(READ)
    with pytest.raises(core.PlatformError, match=": plain text$"):
        client.call(READ)


def test_start_reads_the_bytes_when_text_isnt_a_string():
    class Odd:
        text = None
        content = b"<html>\n  Bad \xffgateway</html>"

    assert _start(Odd()) == "<html> Bad �gateway</html>"

    class Empty:
        text = None
        content = None

    assert _start(Empty()) == ""
