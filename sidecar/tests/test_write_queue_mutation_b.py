"""A flush that is interrupted puts its writes back in front of any write that came in meanwhile,
and loses none of them (write_queue.py)."""

from __future__ import annotations

import pytest

from fabric_desktop import write_queue as wq


class _Interrupting:
    """A write whose wait is interrupted once; a new write comes in while the flush waits on it."""

    def __init__(self, queue):
        self.queue, self.fired = queue, False

    def cancelled(self):
        if not self.fired:
            self.fired = True
            self.queue.submit("t3", "late", 0, lambda: None)
            raise KeyboardInterrupt
        return False

    def cancel(self):
        return False

    def exception(self):
        return None

    def result(self):
        return None


def test_an_interrupted_flush_keeps_the_write_that_came_in_meanwhile():
    q = wq.Queue(workers=1)
    q.submit("t1", "orders", 0, lambda: None).result(10)
    q._pending.append(("customers", _Interrupting(q)))
    with pytest.raises(KeyboardInterrupt):
        q.flush()
    assert [label for label, _ in q._pending] == ["orders", "customers", "late"]
    assert q.flush() == []
    assert q._pending == []
