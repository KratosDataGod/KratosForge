"""send_remote (KF-REMOTE-RETRY, #1189): which answers a remote engine call tries again, how long
it waits, and that Cancel cuts the waiting short."""

from __future__ import annotations

from fabric_desktop.retrying import REMOTE_WAIT_CAP, send_remote


class Resp:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


def answers(*statuses, retry_after=None):
    queue = [Resp(s, retry_after) for s in statuses]
    sent = []

    def send():
        sent.append(1)
        return queue.pop(0)
    return send, sent


class Naps:
    def __init__(self):
        self.total = 0.0

    def __call__(self, seconds):
        self.total += seconds


def test_a_read_is_tried_again_through_throttling_and_gateway_errors():
    for status in (429, 502, 503, 504):
        send, sent = answers(status, status, 200)
        resp, tries = send_remote(send, True, Naps(), jitter=lambda: 0.0)
        assert resp.status_code == 200 and tries == 3 and len(sent) == 3


def test_a_submit_is_tried_again_on_429_only():
    send, sent = answers(429, 200)
    assert send_remote(send, False, Naps())[0].status_code == 200 and len(sent) == 2
    for status in (502, 503, 504):
        send, sent = answers(status, 200)
        resp, tries = send_remote(send, False, Naps())
        assert resp.status_code == status and tries == 1 and len(sent) == 1  # it may have started


def test_other_errors_are_never_tried_again():
    for status in (400, 401, 403, 404, 500):
        send, sent = answers(status, 200)
        assert send_remote(send, True, Naps())[1] == 1 and len(sent) == 1


def test_it_gives_up_after_four_tries():
    send, sent = answers(503, 503, 503, 503, 200)
    resp, tries = send_remote(send, True, Naps(), jitter=lambda: 0.0)
    assert resp.status_code == 503 and tries == 4 and len(sent) == 4


def test_retry_after_is_followed_and_capped():
    naps = Naps()
    send, _ = answers(429, 200, retry_after="3")
    send_remote(send, True, naps)
    assert naps.total == 3.0
    naps = Naps()
    send, sent = answers(429, 429, 429, 429, retry_after="3600")
    resp, tries = send_remote(send, True, naps)
    assert naps.total <= REMOTE_WAIT_CAP and resp.status_code == 429  # a server can't stall the cell


def test_without_retry_after_the_wait_backs_off():
    naps = Naps()
    send, _ = answers(503, 503, 503, 200)
    send_remote(send, True, naps, jitter=lambda: 1.0)
    assert naps.total == 1.0 + 2.0 + 4.0


def test_cancel_ends_the_waiting_and_returns_the_last_answer():
    naps = Naps()
    stop = [False]

    def sleep(seconds):
        naps(seconds)
        stop[0] = True  # the person pressed Cancel during the first wait
    send, sent = answers(429, 200, retry_after="20")
    resp, tries = send_remote(send, True, sleep, stopped=lambda: stop[0])
    assert resp.status_code == 429 and tries == 1 and len(sent) == 1 and naps.total < 1


def test_a_dropped_connection_is_tried_again_for_a_read_but_never_for_a_submit():
    calls = []

    def send():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("reset")
        return Resp(200)
    resp, tries = send_remote(send, True, Naps(), jitter=lambda: 0.0)
    assert resp.status_code == 200 and tries == 3
    calls.clear()
    try:
        send_remote(send, False, Naps())
    except ConnectionError:
        pass
    assert calls == [1]  # a submit that lost its connection may have started


def test_a_connection_that_stays_down_raises_after_the_last_try():
    calls = []

    def send():
        calls.append(1)
        raise TimeoutError("timed out")
    try:
        send_remote(send, True, Naps(), jitter=lambda: 0.0)
        raise AssertionError("expected TimeoutError")
    except TimeoutError:
        pass
    assert len(calls) == 4
