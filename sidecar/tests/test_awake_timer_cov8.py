"""awake_timer: the Windows QueryUnbiasedInterruptTime clock, faked through a stand-in ctypes, and a
timer cancelled just as its time runs out."""

from __future__ import annotations

import sys
import threading
import types

import pytest

from fabric_desktop import awake_timer
from fabric_desktop.awake_timer import AwakeTimer


class _ULongLong:
    def __init__(self) -> None:
        self.value = 0


class _Query:
    """kernel32.QueryUnbiasedInterruptTime: writes ``ticks`` (100 ns units) and returns ``ok``."""

    def __init__(self, ticks: int, ok: int = 1) -> None:
        self.ticks = ticks
        self.ok = ok
        self.argtypes = None
        self.restype = None

    def __call__(self, ref):
        ref.value = self.ticks
        return self.ok


def _fake_ctypes(kernel32):
    calls = []

    def windll(name):
        calls.append(name)
        if isinstance(kernel32, BaseException):
            raise kernel32
        return kernel32

    fake = types.SimpleNamespace(
        WinDLL=windll,
        c_ulonglong=_ULongLong,
        c_int="c_int",
        POINTER=lambda t: ("POINTER", t),
        byref=lambda obj: obj,
    )
    return fake, calls


@pytest.fixture
def on_windows(monkeypatch):
    """The module sees win32 through its own ``sys`` name only; the real sys stays as it is."""
    monkeypatch.setattr(awake_timer, "sys", types.SimpleNamespace(platform="win32"))

    def use(kernel32):
        fake, calls = _fake_ctypes(kernel32)
        monkeypatch.setitem(sys.modules, "ctypes", fake)
        return calls

    return use


def test_on_windows_the_clock_reads_unbiased_time_in_seconds(on_windows):
    query = _Query(ticks=25_000_000)
    calls = on_windows(types.SimpleNamespace(QueryUnbiasedInterruptTime=query))
    read = awake_timer._unbiased_clock()
    assert calls == ["kernel32"]
    assert query.argtypes == [("POINTER", _ULongLong)] and query.restype == "c_int"
    assert read() == pytest.approx(2.5)
    query.ticks = 36_000_000_000
    assert read() == pytest.approx(3600.0)


def test_a_failed_query_raises_rather_than_reading_zero(on_windows):
    on_windows(types.SimpleNamespace(QueryUnbiasedInterruptTime=_Query(ticks=10, ok=0)))
    read = awake_timer._unbiased_clock()
    with pytest.raises(OSError, match="QueryUnbiasedInterruptTime failed"):
        read()


def test_no_kernel32_means_no_unbiased_clock(on_windows):
    on_windows(OSError("no kernel32"))
    assert awake_timer._unbiased_clock() is None


def test_kernel32_without_the_function_means_no_unbiased_clock(on_windows):
    on_windows(types.SimpleNamespace())  # an old Windows: AttributeError on the lookup
    assert awake_timer._unbiased_clock() is None


def test_cancelled_as_the_time_runs_out_the_function_is_not_called():
    fired = []
    timer = AwakeTimer(0.5, lambda: fired.append(True))
    readings = iter([0.0, 0.5])

    def clock():
        value = next(readings)
        if value == 0.5:  # cancel() lands between the last wait and the check
            timer.cancel()
        return value

    timer._clock = clock

    class NoWait(threading.Event):
        def wait(self, timeout=None):
            return False  # the wait ran its time; cancel() comes on the clock read after it

    timer.finished = NoWait()
    timer.run()
    assert fired == [] and timer.finished.is_set()
