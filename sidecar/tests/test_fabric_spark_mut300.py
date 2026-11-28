"""Mutation batch 300: Fabric Spark's limits, error wording, retries and timing, against the fake Livy
API in test_fabric_spark (nothing reaches a tenant)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.engines import fabric_spark as fs
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from tests.test_fabric_spark import LH, TOKEN, WS, FakeLivy, Resp, open_session
from tests.test_fabric_spark import fabric_runs_allowed  # noqa: F401  (autouse: Fabric runs allowed)


def client(http, **kw):
    return fs.LivyClient(lambda: TOKEN, WS, LH, http, sleep=lambda s: None, **kw)


class Scripted:
    """Answers each request with the next reply in the list, and records what was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def request(self, method, url, headers=None, timeout=None, json=None):
        self.sent.append((method, url, timeout))
        return self.replies.pop(0)


# ---- limits

def test_a_session_may_take_fifteen_minutes_to_start_and_a_cell_two_hours():
    assert fs.START_SECONDS == 900
    assert fs.MAX_SECONDS == 7200


def test_livy_messages_are_kept_to_three_hundred_characters():
    assert fs._MESSAGE_MAX == 300
    exactly = "x" * 300
    assert fs._one_line(exactly) == exactly
    longer = "y" * 301
    cut = fs._one_line(longer)
    assert cut == "y" * 299 + "…" and len(cut) == 300


# ---- each call

def test_each_call_waits_sixty_seconds_for_fabric():
    http = Scripted(Resp(200, {"id": "7", "state": "idle"}))
    client(http).session("7")
    assert http.sent[0][2] == 60


def test_a_delete_is_sent_again_after_a_429_even_after_cancel_was_pressed():
    http = Scripted(Resp(429, {}), Resp(200, {}))
    c = client(http)
    c.stopped = lambda: True
    c.delete("7")
    assert [m for m, _, _ in http.sent] == ["DELETE", "DELETE"]


def test_a_read_stops_trying_once_cancel_is_pressed():
    http = Scripted(Resp(429, {}), Resp(200, {}))
    c = client(http)
    c.stopped = lambda: True
    with pytest.raises(fs.FabricSparkError, match="HTTP 429"):
        c.session("7")
    assert len(http.sent) == 1


def test_a_cancel_is_sent_again_after_a_503():
    http = Scripted(Resp(503, {}), Resp(200, {"msg": "canceled"}))
    client(http).cancel("7", "0")
    assert [m for m, _, _ in http.sent] == ["POST", "POST"]


def test_a_busy_capacity_and_a_full_spark_limit_each_say_what_to_do():
    busy = Scripted(*[Resp(429, {}) for _ in range(4)])
    with pytest.raises(fs.FabricSparkError) as caught:
        client(busy).session("7")
    assert str(caught.value) == ("Fabric refused the request (HTTP 429). The capacity is busy; try again shortly. "
                                 "Tried 4 times.")
    full = Scripted(Resp(430, {}))
    with pytest.raises(fs.FabricSparkError) as caught:
        client(full).session("7")
    assert str(caught.value) == ("Fabric refused the request (HTTP 430). The capacity's Spark limit is reached; "
                                 "close another session or try later.")


def test_a_refusal_sent_once_does_not_count_its_tries():
    with pytest.raises(fs.FabricSparkError) as caught:
        client(Scripted(Resp(403, {}))).session("7")
    assert str(caught.value) == "Fabric refused the request (HTTP 403). You need Contributor on the workspace."


# ---- outputs

def test_an_error_keeps_its_traceback_lines():
    outputs, rows = fs._outputs({"status": "error", "ename": "NameError", "evalue": "boom",
                                 "traceback": ["Traceback (most recent call last):", "NameError: boom"]}, 10)
    assert rows is None
    assert outputs[0]["traceback"] == ["Traceback (most recent call last):", "NameError: boom"]


def test_a_result_of_exactly_the_rows_asked_for_is_read():
    text = json.dumps({"mark": fs.RESULT_MARK, "fields": [["id", "bigint"]], "rows": [["1"], ["2"]]})
    table, as_text = fs._result_table(text, 2)
    assert table.column("id").to_pylist() == [1, 2] and as_text == []


def test_the_preview_of_a_whole_result_starts_at_its_first_row():
    livy = FakeLivy()
    s = open_session(livy)
    r = s.execute(CellRequest("SELECT 1", "sql", "spark", preview_rows=2, result_rows=10))
    assert r.status == "ok" and r.rows == 3
    shown = r.outputs[-1]
    assert shown["rows"][0][0] == 1 and shown["total_rows"] == 3


# ---- sessions

def test_a_session_already_idle_when_created_is_not_polled():
    livy = FakeLivy(start_polls=0)
    s = open_session(livy)
    assert s.id == "7" and [c[:2] for c in livy.calls] == [("POST", "")]


def test_the_start_limit_is_given_in_whole_minutes():
    livy = FakeLivy(start_polls=10**6)
    now = [0.0]
    with pytest.raises(fs.FabricSparkError, match="didn't start within 2 minutes"):
        fs.FabricSparkSession(SessionConfig("nb"), client(livy), sleep=lambda s: now.__setitem__(0, now[0] + 30),
                              clock=lambda: now[0], start_seconds=90)


def _session_with_clock(livy, now, max_seconds):
    return fs.FabricSparkSession(SessionConfig("nb"), client(livy), sleep=lambda s: now.__setitem__(0, now[0] + 45),
                                 clock=lambda: now[0], max_seconds=max_seconds)


def test_a_cell_is_stopped_only_after_its_time_limit_has_passed():
    now = [0.0]
    livy = FakeLivy(start_polls=0, polls=2)
    s = _session_with_clock(livy, now, max_seconds=45)
    r = s.execute(CellRequest("print(1)", "pyspark"))
    # One poll at exactly 45 seconds is still allowed, and the cell finishes in it.
    assert r.status == "ok" and livy.cancelled == []


def test_the_cell_limit_is_given_in_whole_minutes():
    now = [0.0]
    livy = FakeLivy(start_polls=0, polls=10**6)
    s = _session_with_clock(livy, now, max_seconds=90)
    r = s.execute(CellRequest("print(1)", "pyspark"))
    assert r.status == "error" and "still running after 2 minutes" in r.outputs[0]["evalue"]
    assert livy.cancelled == ["0"]


def test_a_cell_reports_how_long_it_took(monkeypatch):
    s = open_session(FakeLivy())
    ticks = iter([100.0, 102.5])
    monkeypatch.setattr(fs.time, "perf_counter", lambda: next(ticks))
    r = s.execute(CellRequest("print(1)", "pyspark"))
    assert r.status == "ok" and r.seconds == 2.5
