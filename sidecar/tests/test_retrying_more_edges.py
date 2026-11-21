"""Retry helpers: an HTTP date with no zone, and Cancel pressed while the connection is down."""

import datetime as dt

import pytest

from fabric_desktop import retrying as rt


def test_a_retry_after_date_without_a_zone_is_read_as_gmt():
    now = dt.datetime(2026, 10, 21, 7, 27, 0, tzinfo=dt.timezone.utc)
    # "-0000" is RFC 2822's "zone unknown", which parses to a naive time
    wait = rt.retry_after({"Retry-After": "Wed, 21 Oct 2026 07:28:00 -0000"}, 5.0, cap=300, now=now)
    assert wait == 60.0


def test_cancel_while_the_connection_is_down_raises_instead_of_returning_nothing():
    pressed = []

    def send():
        raise ConnectionError("reset")

    def sleep(_):
        pressed.append(True)

    with pytest.raises(ConnectionError, match="cancelled while the connection was down"):
        rt.send_remote(send, repeatable=True, sleep=sleep, stopped=lambda: bool(pressed), jitter=lambda: 0.0)
    assert len(pressed) == 1
