"""On Windows the awake clock is QueryUnbiasedInterruptTime, which leaves out only sleep and hibernation.
A process that is suspended or starved, while the PC stays awake, still has that time counted, so a
policy time limit can't be stretched by stopping the engine while its worker or kernel keeps running."""

from __future__ import annotations

import sys
import threading

import pytest

from fabric_desktop import awake_timer
from fabric_desktop.awake_timer import STEP, SLEEP_GAP, AwakeClock, AwakeTimer


class Unbiased:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_with_windows_awake_clock_a_long_stall_counts_in_full(monkeypatch):
    fake = Unbiased()
    monkeypatch.setattr(awake_timer, "_UNBIASED", fake)
    fired = threading.Event()
    timer = AwakeTimer(60, fired.set)

    waits = []

    class Stall(threading.Event):
        def wait(self, timeout=None):  # the process was suspended for an hour while the PC stayed awake
            waits.append(timeout)
            if len(waits) > 3:
                return True  # cancelled: the stall wasn't counted
            fake.now += 3600
            return False

    timer.finished = Stall()
    timer.run()
    assert fired.is_set() and len(waits) == 1


def test_with_windows_awake_clock_the_deadline_clock_counts_a_long_stall(monkeypatch):
    fake = Unbiased()
    monkeypatch.setattr(awake_timer, "_UNBIASED", fake)
    clock = AwakeClock()
    clock._ticker = threading.Thread()  # no ticker: the test steps it
    start = clock.now()
    fake.now += 3600
    assert clock.now() - start == pytest.approx(3600)


def test_without_it_a_step_far_too_long_is_still_taken_as_sleep(monkeypatch):
    monkeypatch.setattr(awake_timer, "_UNBIASED", None)
    timer = AwakeTimer(60, lambda: None)
    assert timer.awake_after(0.0, STEP + SLEEP_GAP, 0.0) == STEP + SLEEP_GAP
    assert timer.awake_after(0.0, 3600.0, 0.0) == 0.0


def test_off_windows_there_is_no_unbiased_clock(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert awake_timer._unbiased_clock() is None


@pytest.mark.skipif(sys.platform != "win32", reason="QueryUnbiasedInterruptTime is Windows'")
def test_windows_gives_an_unbiased_clock_that_moves_with_real_time():
    import time

    read = awake_timer._unbiased_clock()
    assert read is not None
    before, start = read(), time.monotonic()
    time.sleep(0.3)
    moved, real = read() - before, time.monotonic() - start
    assert 0.2 <= moved <= real + 0.1
