"""Mutation batch 73: waiting for a cell (notebook_cells.wait) — what it asks the kernel for and
which outputs it gives back when a snapshot leaves them out."""

from __future__ import annotations

from fabric_desktop import notebook_cells


class _Kernel:
    def __init__(self, polls, alive=True):
        self.polls, self.asked, self.stopped, self._alive = list(polls), [], False, alive

    def poll(self, version, wait):
        self.asked.append((version, wait))
        return self.polls.pop(0)

    def alive(self):
        return self._alive

    def stop(self):
        self.stopped = True


def test_the_first_poll_asks_from_version_minus_one_then_from_each_version_seen():
    k = _Kernel([{"status": "running", "version": 4}, {"status": "running"}, {"status": "ok", "version": 9}])
    notebook_cells.wait(k, {"status": "running"})
    assert k.asked == [(-1, 0.5), (4, 0.5), (4, 0.5)]   # a snapshot with no version keeps the last one


def test_a_cell_finished_on_start_with_no_outputs_gives_an_empty_list():
    assert notebook_cells.wait(_Kernel([]), {"status": "ok"}) == ({"status": "ok"}, [])
    assert notebook_cells.wait(_Kernel([]), {"status": "error", "outputs": None})[1] == []


def test_a_cell_stopped_at_once_keeps_the_outputs_it_started_with():
    k = _Kernel([])
    snap, outputs = notebook_cells.wait(k, {"status": "running", "seconds": 1, "outputs": [{"text": "a"}]}, lambda: True)
    assert snap == {"status": "cancelled", "seconds": 1} and outputs == [{"text": "a"}] and k.stopped
    assert notebook_cells.wait(_Kernel([]), {"status": "running"}, lambda: True)[1] == []


def test_a_last_snapshot_without_outputs_gives_the_outputs_seen_before_it():
    k = _Kernel([{"status": "running", "version": 1, "outputs": [{"text": "a"}]}, {"status": "ok", "version": 2}])
    snap, outputs = notebook_cells.wait(k, {"status": "running", "outputs": []})
    assert snap == {"status": "ok", "version": 2} and outputs == [{"text": "a"}]


def test_an_empty_list_of_outputs_replaces_the_ones_before_it():
    k = _Kernel([{"status": "running", "version": 1, "outputs": [{"text": "a"}]},
                 {"status": "ok", "version": 2, "outputs": []}])
    assert notebook_cells.wait(k, {"status": "running"})[1] == []
