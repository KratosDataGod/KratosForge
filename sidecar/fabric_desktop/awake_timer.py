"""A time limit that counts only the time the PC is awake.

``threading.Timer`` waits on the monotonic clock, which on Windows keeps counting while the PC sleeps
(GetTickCount64). A run with a 30-minute limit that was making progress when the lid closed would be
stopped the moment the PC woke, hours later, for time it never ran. ``AwakeTimer`` waits in short
steps and leaves out any step that took far longer than asked: that gap is the PC asleep (or the
process suspended), not the run. A run that really runs past its limit is still stopped on time,
give or take a step. ``awake_monotonic`` is a clock for deadlines that leaves sleep out the same
way, for limits checked against a deadline rather than waited out by a timer. A leaf module: it
imports nothing from the package.
"""

from __future__ import annotations

import threading
from time import monotonic as _monotonic
from typing import Any, Callable

STEP = 1.0  # seconds between checks
SLEEP_GAP = 5.0  # a step this much longer than STEP was the PC asleep, not counted


class AwakeTimer(threading.Thread):
    """``threading.Timer``'s interface (start, cancel, daemon): calls ``function(*args)`` once
    ``interval`` seconds of awake time have passed, unless cancelled first."""

    def __init__(self, interval: float, function: Callable[..., Any], args: tuple[Any, ...] = (),
                 clock: Callable[[], float] | None = None) -> None:
        super().__init__(daemon=True, name="awake-timer")
        self.interval = interval
        self.function = function
        self.args = args
        self.finished = threading.Event()
        # The OS's clock, bound here rather than read as time.monotonic: tests that replace time.monotonic
        # for their own module don't reach these threads; tests/conftest.py's sleep_jump moves it.
        self._clock = clock or (lambda: _monotonic())

    def cancel(self) -> None:
        self.finished.set()

    def awake_after(self, before: float, after: float, counted: float) -> float:
        """``counted`` plus the step from ``before`` to ``after``, unless that step was the PC asleep."""
        step = after - before
        return counted + step if 0 <= step <= STEP + SLEEP_GAP else counted

    def run(self) -> None:
        counted = 0.0
        last = self._clock()
        while counted < self.interval:
            if self.finished.wait(min(STEP, self.interval - counted)):
                return
            now = self._clock()
            counted = self.awake_after(last, now, counted)
            last = now
        if not self.finished.is_set():
            self.function(*self.args)
        self.finished.set()


class AwakeClock:
    """A monotonic clock that leaves out time the PC was asleep. A daemon thread steps it every STEP
    seconds and leaves out a step that took far longer than asked, as AwakeTimer does; a reading a
    step late steps it too. Between steps it reads the monotonic clock, capped at one STEP, so it
    never goes back."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        # The OS's clock, bound here rather than read as time.monotonic: tests that replace time.monotonic
        # for their own module don't reach these threads; tests/conftest.py's sleep_jump moves it.
        self._clock = clock or (lambda: _monotonic())
        self._lock = threading.Lock()
        self._awake = 0.0
        self._last = self._clock()
        self._ticker: threading.Thread | None = None

    def _step(self) -> None:
        with self._lock:
            self._step_to(self._clock())

    def _step_to(self, now: float) -> None:
        step = now - self._last
        # A step far longer than asked was the PC asleep: count one ordinary step for it, which
        # keeps the clock from going back after now() read up to a STEP past the last one.
        self._awake += step if 0 <= step <= STEP + SLEEP_GAP else (STEP if step > 0 else 0.0)
        self._last = now

    def _tick(self) -> None:
        stop = threading.Event()
        while True:
            stop.wait(STEP)
            self._step()

    def now(self) -> float:
        with self._lock:
            if self._ticker is None:
                self._ticker = threading.Thread(target=self._tick, daemon=True, name="awake-clock")
                self._ticker.start()
            now = self._clock()
            if now < self._last:  # the clock went back (a test's, never the OS's): count on from here
                self._last = now
            elif now - self._last > STEP:  # the ticker is late (a busy PC): step here, by the same rule
                self._step_to(now)
            return self._awake + min(now - self._last, STEP)


_CLOCK = AwakeClock()


def awake_monotonic() -> float:
    """Seconds on a clock that, unlike time.monotonic on Windows, doesn't count time asleep. Only
    differences between two readings mean anything; use it for deadlines that sleep mustn't use up."""
    return _CLOCK.now()
