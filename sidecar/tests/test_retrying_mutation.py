"""What retrying.py decides is safe to send or write again. These assertions pin each answer and each
wait exactly; a mutation run found these predicates and backoffs changeable without a failing test.

A wrong "retry" here writes an append twice; a wrong "don't" fails a write that would have gone in."""

from __future__ import annotations

import pytest
import requests
from deltalake.exceptions import CommitFailedError

from fabric_desktop import retrying as R
from fabric_desktop.leases import LeaseBusy

LOG = "_delta_log/00000000000000000001.json"


def test_the_transient_statuses_are_exactly_these():
    assert R.TRANSIENT_STATUS == {408, 429, 500, 502, 503, 504}
    assert R.REMOTE_RETRY_STATUS == frozenset({429, 502, 503, 504})


# --------------------------------------------------------------------------- the Windows Delta log race

@pytest.mark.parametrize("text, race", [
    (f"Failed to open {LOG} (os error 5)", True),
    (f"Failed to open {LOG} (os error 32)", True),
    (f"Failed to open {LOG} (os error 2)", False),                # not found isn't the race
    ("Failed to open part-0.parquet (os error 5)", False),        # a data file, not the log
    (f"Failed to open {LOG} (os error 50)", False),               # "os error 5" must end there
])
def test_the_windows_log_race_is_access_denied_or_in_use_on_the_delta_log(text, race):
    assert R.windows_log_race(OSError(text)) is race


def test_only_an_os_error_is_the_log_race():
    assert R.windows_log_race(RuntimeError(f"Failed to open {LOG} (os error 5)")) is False


STAGED = "Unable to open file _delta_log/00000000000000000003.json#1 (os error 5)"


@pytest.mark.parametrize("text, race", [
    (STAGED, True),
    ("Generic error _delta_log/00000000000000000003.json#1 (os error 5)", False),  # not "Unable to open file"
    ("Unable to open file _delta_log/00000000000000000003.json (os error 5)", False),  # no staged #n name
    ("Unable to open file _delta_log/00000000000000000003.json#1 (os error 2)", False),  # not the race
])
def test_a_staged_commit_race_is_the_log_race_on_a_staged_name(text, race):
    assert R.staged_commit_race(OSError(text)) is race


# --------------------------------------------------------------------------- data upload vs log commit

PUT_DATA = "Error performing PUT https://onelake/ws/lh/Tables/t/part-0001.parquet: "
PUT_LOG = f"Error performing PUT https://onelake/ws/lh/Tables/t/{LOG}: "


@pytest.mark.parametrize("why", ["error sending request", "Operation Timed Out", "Connection reset by peer",
                                 "connection closed", "Broken pipe", "status code: 500", "status code: 502",
                                 "status code: 503", "status code: 504", "status code: 408", "status code: 429"])
def test_a_dropped_data_file_upload_is_safe_to_write_again(why):
    assert R.data_upload_dropped(RuntimeError(PUT_DATA + why)) is True
    # The same drop on the log entry is the other case: the commit may have gone in.
    assert R.data_upload_dropped(RuntimeError(PUT_LOG + why)) is False
    assert R.log_commit_dropped(RuntimeError(PUT_LOG + why)) is True
    assert R.log_commit_dropped(RuntimeError(PUT_DATA + why)) is False


@pytest.mark.parametrize("text", [
    "Error performing GET https://onelake/x/part-0001.parquet: timed out",  # not an upload
    "Error performing PUT https://onelake/x/part-0001.csv: timed out",      # not a data file
    PUT_DATA + "status code: 403",                                         # refused, not dropped
    "part-0001.parquet timed out",                                         # no PUT at all
])
def test_anything_else_is_not_a_dropped_data_upload(text):
    assert R.data_upload_dropped(RuntimeError(text)) is False


@pytest.mark.parametrize("text", [PUT_LOG + "status code: 409", "Error performing GET " + LOG + ": timed out"])
def test_anything_else_is_not_a_dropped_log_commit(text):
    assert R.log_commit_dropped(RuntimeError(text)) is False


@pytest.mark.parametrize("why", ["status code: 401", "InvalidAuthenticationInfo", "Token is expired",
                                 "Lifetime validation failed"])
def test_a_write_token_that_ran_out_is_recognised(why):
    assert R.write_token_refused(RuntimeError("Error performing PUT x: " + why)) is True
    assert R.write_token_refused(RuntimeError(why)) is False            # not from a write
    assert R.write_token_refused(RuntimeError("Error performing PUT x: status code: 403")) is False


# --------------------------------------------------------------------------- local_retry

def test_local_retry_repeats_the_log_race_with_doubling_waits(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr(R.time, "sleep", waits.append)

    def flaky():
        calls.append(1)
        if len(calls) < 4:
            raise OSError(f"Failed to open {LOG} (os error 32)")
        return "done"

    assert R.local_retry(flaky, attempts=6, backoff=0.05) == "done"
    assert waits == pytest.approx([0.05, 0.1, 0.2])


def test_local_retry_raises_any_other_error_at_once(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: pytest.fail("no wait for an error that isn't the race"))
    calls = []

    def broken():
        calls.append(1)
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        R.local_retry(broken)
    assert calls == [1]


def test_local_retry_gives_up_after_its_attempts(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    calls = []

    def always():
        calls.append(1)
        raise OSError(f"Failed to open {LOG} (os error 5)")

    with pytest.raises(OSError):
        R.local_retry(always)
    assert len(calls) == 6  # the default


def test_local_retry_with_no_attempts_is_an_error_not_a_quiet_none():
    with pytest.raises(AssertionError):
        R.local_retry(lambda: "never called", attempts=0)


# --------------------------------------------------------------------------- is_retryable and status_of

class Err(Exception):
    def __init__(self, **attrs):
        super().__init__("x")
        self.__dict__.update(attrs)


@pytest.mark.parametrize("exc, retry", [
    (LeaseBusy("held"), True),
    (CommitFailedError("lost"), True),
    (requests.ConnectionError("down"), True),
    (requests.Timeout("slow"), True),
    (ConnectionError("down"), True),
    (TimeoutError("slow"), True),
    (OSError(f"Failed to open {LOG} (os error 5)"), True),
    (Err(status=503), True),
    (Err(status=404), False),
    (Err(code=429), True),            # urllib's HTTPError keeps it in code
    (Err(code="429"), False),         # FabricError's code is a string, not a status
    (Err(status=True), False),        # a flag isn't a status
    (Err(), False),
    (OSError("disk full"), False),
])
def test_what_is_worth_another_try(exc, retry):
    assert R.is_retryable(exc) is retry


def test_status_of_reads_status_then_status_code_then_the_response():
    assert R.status_of(Err(status=500, status_code=400)) == 500
    assert R.status_of(Err(status=None, status_code=400)) == 400
    assert R.status_of(Err(response=Err(status_code=502))) == 502
    assert R.status_of(Err(status=False, response=Err(status_code=502))) == 502
    assert R.status_of(Err()) is None


# --------------------------------------------------------------------------- the waits themselves

class Resp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}


def test_a_dropped_read_waits_twice_as_long_each_time(monkeypatch):
    waits = []

    def send():
        raise requests.ConnectionError("down")

    with pytest.raises(requests.ConnectionError):
        R.send_with_retry(send, "GET", sleep=waits.append, attempts=4, default_wait=2.0, wait_cap=60)
    assert waits == [2.0, 4.0, 8.0]


def test_a_dropped_read_wait_is_capped():
    waits = []

    def send():
        raise requests.Timeout("slow")

    with pytest.raises(requests.Timeout):
        R.send_with_retry(send, "GET", sleep=waits.append, attempts=4, default_wait=2.0, wait_cap=5)
    assert waits == [2.0, 4.0, 5.0]


def test_remote_backoff_doubles_from_its_base_with_jitter_scaling_it():
    waits, answers = [], [Resp(503), Resp(503), Resp(503), Resp(200)]
    resp, sent = R.send_remote(lambda: answers.pop(0), repeatable=True, sleep=waits.append,
                               jitter=lambda: 1.0, base_wait=1.0, total_cap=100)
    assert (resp.status_code, sent) == (200, 4)
    # Retry-After isn't given, so each wait is the backoff, 1, 2 then 4 seconds, slept in 0.25 s slices.
    assert sum(waits) == pytest.approx(7.0)
    assert max(waits) == pytest.approx(R._NAP)


def test_remote_jitter_halves_the_backoff_at_its_lowest():
    waits, answers = [], [Resp(429), Resp(200)]
    R.send_remote(lambda: answers.pop(0), repeatable=False, sleep=waits.append, jitter=lambda: 0.0,
                  base_wait=2.0, total_cap=100)
    assert sum(waits) == pytest.approx(1.0)


def test_remote_waits_together_stay_under_the_total_cap():
    waits, answers = [], [Resp(503), Resp(503), Resp(503), Resp(200)]
    resp, sent = R.send_remote(lambda: answers.pop(0), repeatable=True, sleep=waits.append,
                               jitter=lambda: 1.0, base_wait=1.0, total_cap=3.0)
    # 1 s, then 2 s takes the total to the cap, then nothing is left: the 503 in hand is returned.
    assert sum(waits) == pytest.approx(3.0)
    assert (resp.status_code, sent) == (503, 3)


def test_a_dropped_remote_connection_waits_its_backoff_before_the_next_try():
    waits, calls = [], []

    def send():
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("down")
        return Resp(200)

    resp, sent = R.send_remote(send, repeatable=True, sleep=waits.append, jitter=lambda: 1.0,
                               base_wait=2.0, total_cap=100)
    assert (resp.status_code, sent) == (200, 2)
    assert sum(waits) == pytest.approx(2.0)


def test_a_dropped_remote_connection_with_no_time_left_is_raised():
    def send():
        raise requests.ConnectionError("down")

    with pytest.raises(requests.ConnectionError):
        R.send_remote(send, repeatable=True, sleep=lambda s: None, jitter=lambda: 1.0, base_wait=2.0,
                      total_cap=1.0)


def test_dropped_remote_connections_stop_once_the_next_wait_would_pass_the_cap():
    waits, calls = [], []

    def send():
        calls.append(1)
        raise requests.ConnectionError("down")

    with pytest.raises(requests.ConnectionError):
        R.send_remote(send, repeatable=True, sleep=waits.append, jitter=lambda: 1.0, base_wait=1.0,
                      total_cap=2.5, attempts=6)
    # 1 s waited, then 2 s more would pass 2.5 s, so the second drop is raised.
    assert sum(waits) == pytest.approx(1.0)
    assert len(calls) == 2
