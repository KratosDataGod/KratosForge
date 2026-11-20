"""The OneLake write queue at its exact edges (boundary re-score of batch 1, mutation batch 160,
docs/quality/mutation-testing.md): a wait of exactly 5 ms, and a write that fills the memory cap
exactly."""

from __future__ import annotations

import threading

from fabric_desktop import write_queue as wq


def test_a_wait_of_exactly_5_ms_is_reported():
    s = wq.Stats()
    s.wait = 0.005
    assert s.as_dict() is not None
    s.wait = 0.004
    assert s.as_dict() is None


def test_a_write_that_fills_the_memory_cap_exactly_doesnt_wait():
    q = wq.Queue(workers=1, memory_cap=10)
    q._held = 5  # another table's 5 bytes still waiting
    sent = threading.Event()

    def submit():
        q.submit("t", "t", 5, lambda: None).result(timeout=5)
        sent.set()

    threading.Thread(target=submit, daemon=True).start()
    assert sent.wait(3)
