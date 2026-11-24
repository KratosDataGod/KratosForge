"""awake_timer at its edges, from the mutation run (docs/quality/mutation-testing.md). Every test runs
on a fake clock the test moves by hand: AwakeTimer.run is called in the test's own thread with an
Event that moves the clock instead of waiting, and AwakeClock's ticker is either kept from starting
or only waited for (with a generous deadline), so nothing here depends on how busy the machine is."""

from __future__ import annotations

import threading
import time

import pytest

from fabric_desktop import awake_timer
from fabric_desktop.awake_timer import STEP, AwakeClock, AwakeTimer


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class MovingEvent(threading.Event):
    """An Event whose timed wait moves the fake clock by the time asked (plus ``asleep[i]`` for the
    i-th wait) instead of waiting, and records what was asked."""

    def __init__(self, clock: FakeClock, asleep: dict[int, float] | None = None) -> None:
        super().__init__()
        self.clock = clock
        self.asleep = asleep or {}
        self.waits: list[float] = []

    def wait(self, timeout=None):
        if len(self.waits) > 20:
            raise AssertionError(f"still waiting after {self.waits}")
        self.clock.t += timeout + self.asleep.get(len(self.waits), 0.0)
        self.waits.append(timeout)
        return self.is_set()


def _timer(interval: float, asleep: dict[int, float] | None = None):
    clock = FakeClock()
    calls: list[tuple] = []
    timer = AwakeTimer(interval, lambda *a: calls.append(a), args=("x",), clock=clock)
    timer.finished = MovingEvent(clock, asleep)
    return timer, calls


def test_the_timer_is_a_daemon_like_threading_timer():
    timer = AwakeTimer(1, lambda: None)
    assert timer.daemon is True
    assert timer.name == "awake-timer"


def test_the_last_wait_is_only_what_is_left_of_the_interval():
    timer, calls = _timer(2.5)
    timer.run()
    assert timer.finished.waits == [1.0, 1.0, 0.5]
    assert calls == [("x",)]
    assert timer.finished.is_set()


def test_a_wait_that_slept_is_not_counted_and_the_timer_waits_on():
    timer, calls = _timer(2.5, asleep={1: 3600.0})
    timer.run()
    assert timer.finished.waits == [1.0, 1.0, 1.0, 0.5]
    assert calls == [("x",)]


def _clock_without_ticker() -> tuple[AwakeClock, FakeClock]:
    fake = FakeClock()
    clock = AwakeClock(clock=fake)
    clock._ticker = threading.Thread(target=lambda: None)  # never started: the test steps it by hand
    return clock, fake


def test_a_reading_after_a_late_ticker_counts_the_whole_step():
    clock, fake = _clock_without_ticker()
    assert clock.now() == 0.0
    fake.t = 3.0
    assert clock.now() == pytest.approx(3.0)
    assert clock._last == 3.0


def test_a_step_shorter_than_step_counts_as_it_was():
    clock, fake = _clock_without_ticker()
    fake.t = 0.5
    clock._step()
    assert clock._awake == pytest.approx(0.5)
    assert clock.now() == pytest.approx(0.5)


def test_a_step_of_exactly_step_plus_the_gap_is_awake_and_one_longer_is_asleep():
    clock, _fake = _clock_without_ticker()
    clock._step_to(STEP + awake_timer.SLEEP_GAP)
    assert clock._awake == STEP + awake_timer.SLEEP_GAP
    before = clock._awake
    clock._step_to(clock._last + STEP + awake_timer.SLEEP_GAP + 0.5)
    assert clock._awake == before + STEP


def test_a_step_back_counts_nothing():
    clock, _fake = _clock_without_ticker()
    clock._step_to(5.0)
    clock._step_to(4.0)
    assert clock._awake == 5.0
    assert clock._last == 4.0


def test_the_first_reading_starts_a_ticker_that_keeps_stepping_the_clock():
    fake = FakeClock()
    clock = AwakeClock(clock=fake)
    assert clock._ticker is None
    clock.now()
    assert clock._ticker is not None and clock._ticker.daemon and clock._ticker.name == "awake-clock"
    fake.t = 4.0  # awake time with no reading: only the ticker sees it in steps short enough to count
    deadline = time.monotonic() + 30
    while clock._last != 4.0 and time.monotonic() < deadline:
        time.sleep(0.05)
    fake.t = 8.0
    # Read with the ticker's step: 4 then 4 more counts all 8; one read of an 8-second step would be sleep.
    assert clock.now() == pytest.approx(8.0)
