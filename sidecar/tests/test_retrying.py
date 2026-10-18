"""One rule for which HTTP statuses are transient: every classifier agrees on every status."""

import pytest

from fabric_desktop import capacity
from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.integrations.core import error_from
from fabric_desktop.retrying import TRANSIENT_STATUS, is_retryable
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
