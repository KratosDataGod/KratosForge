"""awake_timer at its edges, from the mutation run (batch 474): Windows' unbiased clock read through a
stand-in kernel32 (so it runs on any OS), and AwakeClock readings that step it a step late, where a
later gap is judged from the last step taken."""

from __future__ import annotations

import ctypes
import sys
import threading

import pytest

from fabric_desktop import awake_timer
from fabric_desktop.awake_timer import STEP, SLEEP_GAP, AwakeClock


class Kernel32:
    """kernel32 with QueryUnbiasedInterruptTime answering ``ticks`` (100 ns units), or failing."""

    def __init__(self, ticks: int = 0, ok: int = 1, has: bool = True) -> None:
        self.ticks, self.ok, self.has = ticks, ok, has

    def _query(self, ref) -> int:
        ref._obj.value = self.ticks
        return self.ok


class Query:
    def __init__(self, kernel: Kernel32) -> None:
        self.kernel = kernel

    def __call__(self, ref):
        return self.kernel._query(ref)


def windll(kernel: Kernel32 | None = None, error: type[Exception] | None = None):
    def load(name: str):
        assert name == "kernel32"
        if error:
            raise error("no kernel32")
        k = kernel or Kernel32()

        class Lib:
            pass

        lib = Lib()
        if k.has:
            lib.QueryUnbiasedInterruptTime = Query(k)
        return lib
    return load


def test_off_windows_there_is_no_unbiased_clock(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "WinDLL", windll(Kernel32(ticks=10)), raising=False)
    assert awake_timer._unbiased_clock() is None


def test_on_windows_the_clock_reads_unbiased_time_in_seconds(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    kernel = Kernel32(ticks=123_456_789_000)
    monkeypatch.setattr(ctypes, "WinDLL", windll(kernel), raising=False)
    read = awake_timer._unbiased_clock()
    assert read is not None and read() == 12_345.6789
    kernel.ticks = 10_000_000
    assert read() == 1.0


def test_a_failed_query_is_an_error_not_a_reading(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", windll(Kernel32(ticks=5, ok=0)), raising=False)
    read = awake_timer._unbiased_clock()
    with pytest.raises(OSError, match="QueryUnbiasedInterruptTime failed"):
        read()


@pytest.mark.parametrize("kernel,error", [(None, OSError), (Kernel32(has=False), None)])
def test_without_kernel32_or_the_function_there_is_no_unbiased_clock(monkeypatch, kernel, error):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", windll(kernel, error), raising=False)
    assert awake_timer._unbiased_clock() is None


class Fake:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def clock_by_hand() -> tuple[AwakeClock, Fake]:
    fake = Fake()
    clock = AwakeClock(clock=fake)
    clock._ticker = threading.Thread(target=lambda: None)  # never started: the test reads by hand
    return clock, fake


def test_a_reading_within_a_step_takes_no_step_so_a_later_long_gap_is_sleep():
    clock, fake = clock_by_hand()
    fake.t = 3.0
    assert clock.now() == pytest.approx(3.0)  # a step late: stepped here
    fake.t = 3.5
    assert clock.now() == pytest.approx(3.5)  # within a step of the last: no step
    assert clock._last == 3.0
    fake.t = 3.0 + STEP + SLEEP_GAP + 0.2  # judged from 3.0, the last step: too long, so asleep
    assert clock.now() == pytest.approx(3.0 + STEP)


def test_a_reading_exactly_a_step_late_takes_no_step():
    clock, fake = clock_by_hand()
    fake.t = STEP
    assert clock.now() == pytest.approx(STEP)
    assert clock._last == 0.0
    fake.t = STEP + SLEEP_GAP + 0.5  # from 0: longer than STEP + SLEEP_GAP, so asleep
    assert clock.now() == pytest.approx(STEP)
