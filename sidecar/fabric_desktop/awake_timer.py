"""A time limit that counts only the time the PC is awake.

``threading.Timer`` waits on the monotonic clock, which on Windows keeps counting while the PC sleeps
(GetTickCount64). A run with a 30-minute limit that was making progress when the lid closed would be
stopped the moment the PC woke, hours later, for time it never ran. ``AwakeTimer`` waits in short
steps and leaves out any step that took far longer than asked: that gap is the PC asleep (or the
process suspended), not the run. A run that really runs past its limit is still stopped on time,
give or take a step. A leaf module: it imports nothing from the package.
"""

from __future__ import annotations

import threading
import time
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
        self._clock = clock or (lambda: time.monotonic())  # looked up each time, so a test can move it

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
