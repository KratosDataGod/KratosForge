"""Measuring a run (measure.py): the meter returns itself and samples on a daemon thread, it stops
that thread before the last read, wall and CPU times are differences rounded to milliseconds, memory
is rounded to a tenth of a MB, and the Windows CPU time joins both halves of each FILETIME. Mutation
batch 61."""

import types

import pytest

from fabric_desktop import kernel_proc, measure
from tests.test_measure_meter import Kernels, Win


@pytest.fixture
def windows(monkeypatch):
    import ctypes
    w = Win()
    monkeypatch.setattr(ctypes, "windll", type("windll", (), {"kernel32": w, "psapi": w})(), raising=False)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: w)
    monkeypatch.setattr(measure.os, "name", "nt")
    return w


def test_on_windows_cpu_time_uses_the_high_half_and_the_process_isnt_inherited(windows, monkeypatch):
    windows.times[1234] = (0, 0, 1 << 32, 10_000_000)  # kernel: high part 1, low 0; user: 1 s
    opened = []
    real = windows.OpenProcess
    monkeypatch.setattr(windows, "OpenProcess", lambda a, inherit, pid: opened.append(inherit) or real(a, inherit, pid))
    assert measure.cpu_seconds(1234) == pytest.approx((1 << 32) / 1e7 + 1.0)
    assert opened == [False]


def test_the_meter_is_its_own_context_and_samples_on_a_daemon_thread(monkeypatch):
    monkeypatch.setattr(measure, "rss_bytes", lambda: 1024 ** 2)
    meter = measure.RunMeter(interval=3600)
    assert meter.samples == 0
    with meter as inside:
        assert inside is meter
        assert meter._thread.daemon and meter._thread.name == "run-meter"
    assert meter.samples == 0


class _Thread:
    def __init__(self, real):
        self.real, self.joined = real, []

    def join(self, timeout=None):
        self.joined.append(timeout)
        self.real.join(timeout)


def test_the_sampler_is_stopped_and_waited_for_two_seconds_before_the_last_read(monkeypatch):
    monkeypatch.setattr(measure, "rss_bytes", lambda: 1024 ** 2)
    meter = measure.RunMeter(interval=3600)
    with meter:
        meter._thread = fake = _Thread(meter._thread)
    assert fake.joined == [2] and not fake.real.is_alive()


def _clock(monkeypatch, wall, cpu):
    walls, cpus = iter(wall), iter(cpu)
    monkeypatch.setattr(measure, "time", types.SimpleNamespace(monotonic=lambda: next(walls),
                                                               process_time=lambda: next(cpus)))


def test_engine_wall_and_cpu_are_differences_rounded_to_milliseconds(monkeypatch):
    monkeypatch.setattr(measure, "rss_bytes", lambda: 1024 ** 2)
    _clock(monkeypatch, [10.0, 11.23456], [5.0, 5.12345])
    meter = measure.RunMeter(interval=3600)
    with meter:
        pass
    assert meter.wall == 1.235 and meter.cpu == 0.123


def test_kernel_cpu_is_rounded_to_milliseconds(monkeypatch):
    k = Kernels(monkeypatch, pid=9)
    k.cpu = {9: 1.0}
    meter = measure.RunMeter(interval=3600, process=k.process)
    with meter:
        k.cpu[9] = 2.23456
    assert meter.cpu == 1.235


def test_memory_is_reported_to_a_tenth_of_a_megabyte(monkeypatch):
    monkeypatch.setattr(measure, "rss_bytes", lambda: int(10.37 * 1024 ** 2))
    meter = measure.RunMeter(interval=3600)
    with meter:
        pass
    out = meter.result()
    assert out["start_memory_mb"] == 10.4 and out["peak_memory_mb"] == 10.4
