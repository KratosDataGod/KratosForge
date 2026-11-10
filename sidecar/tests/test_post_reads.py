"""Reads Fabric takes as POST (the activity-run query, getDefinition) are retried and reported like a GET:
a 5xx is sent again, and a lost reply or a last 5xx never says Fabric "may or may not" have done it."""

import pytest
import requests
from test_fabric_api import Resp, client

from fabric_desktop.fabric_api import FabricError, WriteOutcomeUnknown


def query(c):
    return c.query_activity_runs("ws1", "j1", "a", "b")


def test_the_activity_run_query_is_sent_again_after_a_5xx():
    c, s = client([Resp(503, {"errorCode": "Busy"}, {"Retry-After": "0"}), Resp(200, {"value": [{"id": "r1"}]})])
    assert [r["id"] for r in query(c)] == ["r1"] and len(s.calls) == 2


def test_a_query_that_keeps_failing_doesnt_say_it_may_have_happened():
    c, s = client([Resp(500, {"errorCode": "Oops"}, {"Retry-After": "0"})] * 5)
    with pytest.raises(FabricError) as exc:
        query(c)
    assert len(s.calls) == 5 and "may or may not" not in str(exc.value)


class Drops:
    """Drops the connection on the first request, then answers."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        if len(self.calls) == 1:
            raise requests.ConnectionError("reset")
        return self.answer


def test_a_lost_reply_to_get_definition_is_sent_again_not_reported_as_unknown():
    from fabric_desktop.fabric_api import FabricClient
    session = Drops(Resp(200, {"definition": {"parts": []}}))
    c = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)
    c.get_item_definition("ws1", "it1")
    assert len(session.calls) == 2 and session.calls[1][1].endswith("/items/it1/getDefinition")


def test_a_write_post_still_says_it_may_have_happened():
    c, s = client([Resp(500, {"errorCode": "Oops"})])
    with pytest.raises(FabricError) as exc:
        c.cancel_job("ws1", "it1", "job1")
    assert len(s.calls) == 1 and "may or may not" in str(exc.value)
    assert not isinstance(exc.value, WriteOutcomeUnknown)
