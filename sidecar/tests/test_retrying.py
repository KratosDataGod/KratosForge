"""One rule for which HTTP statuses are transient: every classifier agrees on every status. And the one retry loop."""

import pytest

from fabric_desktop import capacity
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.integrations.core import error_from
from fabric_desktop.retrying import TRANSIENT_STATUS, is_retryable, retry_call
from fabric_desktop.workloads import classify, model

STATUSES = [400, 401, 403, 404, 408, 409, 429, 500, 501, 502, 503, 504, 505]


def fabric_error(status: int) -> FabricError:
    url = "https://api.fabric.microsoft.com/v1/workspaces/ws1/lakehouses"
    return FabricError(f"GET {url} failed with {status}: {{}}", None, status)


class Resp:
    def __init__(self, status):
        self.status_code, self.headers, self.content, self.text = status, {}, b"{}", "{}"

    def json(self):
        return {}


class Session:
    def __init__(self, *statuses):
        self.responses, self.calls = [Resp(s) for s in statuses], 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def client_retries_read(status: int) -> bool:
    session = Session(status, 200)
    try:
        FabricClient(lambda: "tok", session=session, sleep=lambda s: None).get_lakehouse("ws1", "lh1")
    except FabricError:
        pass
    return session.calls > 1


CLASSIFIERS = {
    "retrying.is_retryable": lambda s: is_retryable(fabric_error(s)),
    "FabricClient GET": client_retries_read,
    "capacity.classify_error": lambda s: capacity.classify_error(fabric_error(s)) == "offline",
    "integrations.error_from": lambda s: error_from(fabric_error(s)).retryable,
    "workloads.classify": lambda s: bool(classify.classify(f"FabricError: {fabric_error(s)}")["retry_helps"]),
    "workloads.model.classify_error": lambda s: model.classify_error(fabric_error(s))[0] in ("throttled", "offline"),
}


def test_the_reported_outage_is_retryable():
    assert is_retryable(FabricError("Service unavailable", code="ServiceUnavailable", status=503))
    assert is_retryable(FabricError("Too many requests", code="TooManyRequests", status=429))


@pytest.mark.parametrize("name", CLASSIFIERS)
def test_every_classifier_agrees_on_every_status(name):
    verdicts = {s: CLASSIFIERS[name](s) for s in STATUSES}
    assert verdicts == {s: s in TRANSIENT_STATUS for s in STATUSES}


# EA-028: the one retry loop the hand-written ones move onto.
def test_retry_call_retries_what_it_is_told_and_raises_the_rest():
    waits: list[float] = []
    tries = iter([TimeoutError("a"), TimeoutError("b"), "ok"])

    def flaky():
        step = next(tries)
        if isinstance(step, Exception):
            raise step
        return step

    assert retry_call(flaky, lambda e: isinstance(e, TimeoutError), 3, lambda n: n * 10.0,
                               waits.append) == "ok"
    assert waits == [10.0, 20.0]
    calls: list[int] = []

    def always(exc: BaseException):
        def fn():
            calls.append(1)
            raise exc
        return fn

    with pytest.raises(TimeoutError):
        retry_call(always(TimeoutError("x")), lambda e: True, 4, lambda n: 0.0, waits.append)
    assert len(calls) == 4 and waits == [10.0, 20.0, 0.0, 0.0, 0.0]
    calls.clear()
    with pytest.raises(KeyError):
        retry_call(always(KeyError("x")), lambda e: isinstance(e, TimeoutError), 4, lambda n: 1.0,
                            waits.append)
    assert len(calls) == 1
    with pytest.raises(ValueError):
        retry_call(lambda: "never", lambda e: True, 0, lambda n: 0.0)


def test_send_with_retry_stops_waiting_at_its_deadline():
    """A wait that would end at or after the deadline isn't started: the 429 in hand is the answer."""
    from types import SimpleNamespace

    from fabric_desktop.retrying import send_with_retry
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    sent = []

    def send():
        sent.append(1)
        return SimpleNamespace(status_code=429, headers={"Retry-After": "4"})

    resp = send_with_retry(send, "PUT", sleep=sleep, attempts=5, safe=False, deadline=10.0, clock=lambda: now[0])
    assert resp.status_code == 429 and slept == [4.0, 4.0] and len(sent) == 3  # 8 + 4 would pass 10
    sent.clear()
    slept.clear()
    now[0] = 0.0
    send_with_retry(send, "PUT", sleep=sleep, attempts=5, safe=False, clock=lambda: now[0])
    assert len(sent) == 5 and slept == [4.0] * 4  # no deadline: every attempt
