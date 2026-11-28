"""write_queue.py paths that the uncapped mutation run (batch 222, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[1]


def test_importing_the_queue_does_not_load_pyarrow():
    # Every cell makes a Queue and few write, so pyarrow loads only when a write needs it.
    code = "import sys, fabric_desktop.write_queue; print('pyarrow' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=SIDECAR, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_a_cancelled_write_gives_back_the_memory_it_held():
    import threading

    from fabric_desktop import write_queue as W

    q = W.Queue(workers=1, memory_cap=10)
    gate = threading.Event()
    first = q.submit("a", "a", 0, gate.wait)       # keeps the one worker busy
    second = q.submit("b", "b", 6, lambda: "b")    # waits its turn, holding 6 bytes
    assert second.cancel()
    gate.set()
    first.result(timeout=5)
    submitted = threading.Event()

    def third():
        q.submit("c", "c", 6, lambda: "c")         # fits only once the cancelled 6 bytes are given back
        submitted.set()

    threading.Thread(target=third, daemon=True).start()
    assert submitted.wait(5), "a cancelled write kept its memory, so the next one waited"
    assert q.flush() == ["b: not written (the cell was cancelled)"]


def test_a_flush_stopped_part_way_puts_its_writes_back_before_newer_ones():
    import pytest

    from fabric_desktop import write_queue as W

    class Stop(BaseException):
        pass

    q = W.Queue(memory_cap=10)

    class Result:
        @property
        def warning(self):
            q.submit("b", "b", 0, lambda: "b")  # a write made while the flush waits ...
            raise Stop                           # ... and then the flush is stopped (a cancel landing)

    q.submit("a", "a", 0, Result).result(timeout=5)
    with pytest.raises(Stop):
        q.flush()
    assert [label for label, _ in q._pending] == ["a", "b"]
    q.wait()
