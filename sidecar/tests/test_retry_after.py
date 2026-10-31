"""DI-7 / HD-039: Retry-After is parsed in both forms, capped, and shared by every HTTP client."""

import datetime as dt

import pytest
import requests

from fabric_desktop.fabric_api import FabricClient, FabricError
from fabric_desktop.leases import OneLakeBackend
from fabric_desktop.onelake import FileStore, TableCatalog
from fabric_desktop.retrying import RETRY_AFTER_CAP, RETRY_AFTER_FLOOR, retry_after, send_with_retry, should_retry

NOW = dt.datetime(2026, 10, 18, 12, 0, 0, tzinfo=dt.timezone.utc)


def http_date(t: dt.datetime) -> str:
    return t.strftime("%a, %d %b %Y %H:%M:%S GMT")


# ---- parsing


def test_delta_seconds():
    assert retry_after({"Retry-After": "7"}, default=5, now=NOW) == 7
    assert retry_after({"Retry-After": " 0 "}, default=5, now=NOW) == RETRY_AFTER_FLOOR


def test_http_date_in_the_future():
    assert retry_after({"Retry-After": http_date(NOW + dt.timedelta(seconds=30))}, default=5, now=NOW) == 30


def test_http_date_in_the_past_means_now_after_the_floor():
    assert retry_after({"Retry-After": http_date(NOW - dt.timedelta(hours=1))}, default=5, now=NOW) == RETRY_AFTER_FLOOR


def test_huge_values_are_capped():
    assert retry_after({"Retry-After": "86400"}, default=5, now=NOW) == RETRY_AFTER_CAP
    assert retry_after({"Retry-After": "9" * 400}, default=5, now=NOW) == 5  # overflows to inf: unreadable
    far = http_date(NOW + dt.timedelta(days=3))
    assert retry_after({"Retry-After": far}, default=5, now=NOW) == RETRY_AFTER_CAP
    assert retry_after({"Retry-After": "120"}, default=5, cap=10, now=NOW) == 10


@pytest.mark.parametrize("raw", ["soon", "-5", "", "1e9", "inf", "NaN", "Thu, 99 Foo 2026", "5 seconds"])
def test_garbage_and_negative_values_give_the_default(raw):
    assert retry_after({"Retry-After": raw}, default=5, now=NOW) == 5


def test_missing_header_gives_the_default():
    assert retry_after({}, default=5, now=NOW) == 5
    assert retry_after(None, default=2, now=NOW) == 2
    assert retry_after({}, default=500, now=NOW) == RETRY_AFTER_CAP


def test_header_lookup_is_case_insensitive_on_real_responses():
    headers = requests.structures.CaseInsensitiveDict({"retry-after": "3"})
    assert retry_after(headers, default=5) == 3


# ---- which requests are repeated


def test_only_reads_are_repeated_on_5xx_and_any_method_on_429():
    assert should_retry("GET", 503) and should_retry("get", 500) and should_retry("HEAD", 504)
    assert should_retry("POST", 429) and should_retry("DELETE", 429)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert not should_retry(method, 503)
    assert not should_retry("GET", 404) and not should_retry("GET", 501)


class R:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body or {}
        self.content = b"x"
        self.text = str(self._body)
        self.closed = False

    def json(self):
        return self._body

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sender(items):
    items = list(items)
    calls = []

    def send():
        calls.append(1)
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    return send, calls


def test_send_with_retry_waits_the_capped_retry_after():
    slept = []
    first = R(503, {"Retry-After": "7200"})
    send, calls = sender([first, R(200)])
    assert send_with_retry(send, "GET", sleep=slept.append).status_code == 200
    assert slept == [RETRY_AFTER_CAP] and len(calls) == 2 and first.closed


def test_a_dropped_connection_is_retried_for_a_read_but_never_for_a_write():
    send, calls = sender([requests.ConnectionError("reset"), R(200)])
    assert send_with_retry(send, "GET", sleep=lambda s: None).status_code == 200 and len(calls) == 2

    send, calls = sender([requests.ConnectionError("reset"), R(201)])
    with pytest.raises(requests.ConnectionError):
        send_with_retry(send, "POST", sleep=lambda s: None)
    assert len(calls) == 1


def test_a_write_that_got_a_5xx_is_returned_not_repeated():
    send, calls = sender([R(503), R(201)])
    assert send_with_retry(send, "PUT", sleep=lambda s: None).status_code == 503 and len(calls) == 1


def test_attempts_are_bounded():
    send, calls = sender([R(503) for _ in range(5)])
    assert send_with_retry(send, "GET", sleep=lambda s: None, attempts=5).status_code == 503
    assert len(calls) == 5


# ---- the clients


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url):
        self.calls.append((method, url))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def request(self, method, url, **kwargs):
        self.tokens = getattr(self, "tokens", []) + [(kwargs.get("headers") or {}).get("Authorization")]
        return self._next(method, url)

    def get(self, url, **kwargs):
        return self._next("GET", url)

    def put(self, url, **kwargs):
        return self._next("PUT", url)

    def patch(self, url, **kwargs):
        return self._next("PATCH", url)

    def delete(self, url, **kwargs):
        return self._next("DELETE", url)


def test_fabric_client_caps_a_huge_retry_after():
    slept = []
    session = FakeSession([R(429, {"Retry-After": "86400"}), R(200, body={"id": "lh1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=slept.append)
    assert c.get_lakehouse("ws1", "lh1")["id"] == "lh1"
    assert slept == [RETRY_AFTER_CAP]


def test_fabric_client_accepts_an_http_date():
    slept = []
    future = http_date(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=20))
    session = FakeSession([R(503, {"Retry-After": future}), R(200, body={"id": "lh1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=slept.append)
    assert c.get_lakehouse("ws1", "lh1")["id"] == "lh1"
    assert len(slept) == 1 and 0 <= slept[0] <= 20


def test_fabric_client_ignores_garbage_and_caps_the_long_running_poll():
    slept = []
    loc = "https://api.fabric.microsoft.com/v1/operations/op1"
    session = FakeSession([R(429, {"Retry-After": "later"}),
                           R(202, {"Location": loc, "Retry-After": "99999"}),
                           R(200, body={"status": "Succeeded"}),
                           R(200, body={"id": "lh1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=slept.append)
    assert c.create_lakehouse("ws1", "Sales")["id"] == "lh1"
    assert slept == [5, RETRY_AFTER_CAP]


def test_fabric_client_retries_a_read_after_a_dropped_connection_but_not_a_write():
    session = FakeSession([requests.ConnectionError("reset"), R(200, body={"id": "lh1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)
    assert c.get_lakehouse("ws1", "lh1")["id"] == "lh1" and len(session.calls) == 2

    session = FakeSession([requests.ConnectionError("reset"), R(201, body={"id": "w1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(requests.ConnectionError):
        c.create_workspace("Sales")
    assert len(session.calls) == 1


def test_a_post_is_not_retried_on_5xx_even_with_retry_after():
    session = FakeSession([R(503, {"Retry-After": "1"}), R(201, body={"id": "w1"})])
    c = FabricClient(lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(FabricError):
        c.create_workspace("Sales")
    assert len(session.calls) == 1


def test_onelake_reads_retry_with_the_shared_cap():
    slept = []
    session = FakeSession([R(503, {"Retry-After": "3600"}), R(200, body={"paths": []})])
    store = FileStore(lambda: "tok", session=session, sleep=slept.append)
    assert store.list("ws", "lh") == []
    assert slept == [RETRY_AFTER_CAP] and len(session.calls) == 2

    session = FakeSession([R(500), R(200)])
    assert FileStore(lambda: "tok", session=session, sleep=lambda s: None).read_bytes("ws", "lh", "a.txt") == b"x"

    session = FakeSession([R(429, {"Retry-After": "1"}), R(200, body={"schemas": [{"name": "dbo"}]})])
    assert TableCatalog(lambda: "tok", session=session, sleep=lambda s: None).list_schemas("ws", "lh") == ["dbo"]


def test_onelake_writes_are_sent_once():
    session = FakeSession([R(503), R(201)])
    store = FileStore(lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError):
        store.upload("ws", "lh", "a.txt", b"data")
    assert len(session.calls) == 1

    session = FakeSession([R(503), R(200)])
    with pytest.raises(requests.HTTPError):
        FileStore(lambda: "tok", session=session, sleep=lambda s: None).delete("ws", "lh", "a.txt")
    assert len(session.calls) == 1


def test_lease_reads_retry_but_conditional_writes_do_not():
    session = FakeSession([R(503), R(200, {"ETag": "e1"})])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=lambda s: None)
    assert backend.read("k").etag == "e1" and len(session.calls) == 2

    session = FakeSession([R(503), R(201)])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError):
        backend.create("k", b"{}")
    assert len(session.calls) == 1


def test_a_throttled_lease_change_waits_briefly_and_is_sent_again():
    """#1315: OneLake did nothing on a 429 and every lease change is conditional, so it can't take a
    lease from anyone. Waits are capped at leases.THROTTLE_WAIT_CAP."""
    slept = []
    session = FakeSession([R(429, {"Retry-After": "60"}), R(201, {"ETag": "e0"}), R(202),
                           R(429, {"Retry-After": "1"}), R(200, {"ETag": "e1"})])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=slept.append)
    assert backend.create("k", b"{}") == "e1"
    assert [m for m, _ in session.calls] == ["PUT", "PUT", "PATCH", "PATCH", "PATCH"]
    assert slept == [5.0, 1]

    session = FakeSession([R(429), R(200)])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=lambda s: None)
    assert backend.delete("k", "e1") is True and len(session.calls) == 2


def test_a_lease_throttled_on_every_try_fails_without_outliving_the_lease():
    slept = []
    session = FakeSession([R(429, {"Retry-After": "60"})] * 3)
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=slept.append)
    with pytest.raises(requests.HTTPError) as err:
        backend.replace("k", b"{}", "e1")
    assert len(session.calls) == 3 and sum(slept) == 10.0
    assert str(err.value) == "OneLake didn't write the lease for k (429)." and err.value.response.status_code == 429


def test_a_throttled_renewal_stops_once_a_third_of_the_lease_has_gone_by():
    """Local TPC-H's review of #1327: with lease_timeout(ttl) = ttl / 9, a change's retries stop after
    3 x timeout (ttl / 3), so a renewal that can't get through fails while the lease is held."""
    now = [0.0]

    def sleep(s):
        now[0] += s

    session = FakeSession([R(429, {"Retry-After": "5"})] * 3)
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=sleep, timeout=2.0,
                             clock=lambda: now[0])
    with pytest.raises(requests.HTTPError, match="429"):
        backend.replace("k", b"{}", "e1")
    assert len(session.calls) == 2 and now[0] == 5.0  # the second wait would pass 6 s, so it isn't taken

    now[0] = 0.0
    session = FakeSession([R(201, {"ETag": "e0"}), R(429, {"Retry-After": "5"}), R(202), R(429, {"Retry-After": "5"})])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=sleep, timeout=2.0,
                             clock=lambda: now[0])
    with pytest.raises(requests.HTTPError):
        backend.create("k", b"{}")  # one deadline for the whole change: the flush gets no wait
    assert [m for m, _ in session.calls] == ["PUT", "PATCH", "PATCH", "PATCH"] and now[0] == 5.0


def test_a_lease_that_changed_while_throttled_is_still_lost_not_taken():
    session = FakeSession([R(429), R(412)])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=lambda s: None)
    assert backend.replace("k", b"{}", "stale") is None and len(session.calls) == 2

    session = FakeSession([R(201, {"ETag": "e0"}), R(202), R(503)])
    backend = OneLakeBackend("ws", "lh", lambda: "tok", session=session, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError, match="write the lease for k"):
        backend.create("k", b"{}")
    assert len(session.calls) == 3  # a 5xx flush is not sent again


def test_a_401_gets_a_new_token_once_and_sends_the_request_again_even_a_write():
    tokens = iter(["fresh", "fresher"])
    session = FakeSession([R(401), R(200, body={"id": "lh1"})])
    c = FabricClient(lambda: "stale", session=session, sleep=lambda s: None, refresh=lambda: next(tokens))
    assert c.create_lakehouse("ws1", "Sales")["id"] == "lh1"  # a POST: Fabric refused it unread
    assert session.tokens == ["Bearer stale", "Bearer fresh"]


def test_a_second_401_is_the_answer_and_is_not_retried_again():
    session = FakeSession([R(401), R(401), R(200)])
    c = FabricClient(lambda: "stale", session=session, sleep=lambda s: None, refresh=lambda: "fresh")
    with pytest.raises(FabricError):
        c.get_lakehouse("ws1", "lh1")
    assert len(session.calls) == 2


def test_a_401_without_a_new_token_is_not_sent_again():
    session = FakeSession([R(401), R(200)])
    c = FabricClient(lambda: "same", session=session, sleep=lambda s: None)
    with pytest.raises(FabricError):
        c.get_lakehouse("ws1", "lh1")
    assert len(session.calls) == 1


def test_the_service_refreshes_the_signed_in_token_after_a_401(tmp_path):
    from fabric_desktop.auth import EXECUTE_SCOPES, FABRIC_SCOPES
    from fabric_desktop.service import Service

    class Auth:
        def __init__(self):
            self.refreshed = []

        def fabric_token(self):
            return "stale"

        def _token(self, scopes):
            return "stale"

        def refreshed_token(self, scopes):
            self.refreshed.append(scopes)
            return "fresh"

    svc = Service(root=tmp_path / "app")
    svc._auth = auth = Auth()  # no client_id: kept whatever the app ID is
    assert svc.fabric._refresh() == "fresh" and svc.fabric_jobs._refresh() == "fresh"
    assert auth.refreshed == [FABRIC_SCOPES, EXECUTE_SCOPES]
