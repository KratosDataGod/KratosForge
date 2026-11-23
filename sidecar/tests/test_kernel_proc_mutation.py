"""kernel_proc.py paths that mutation testing (batch 21, docs/quality/mutation-testing.md) showed no test
checked: what a request does with each kind of answer, the snapshot a starting cell shows, a poll that
keeps the outputs it already has, restart and close, the result viewer's defaults, and how exit codes
and limits are written. Most use a stand-in process; the ones that start a real kernel end it in a
finally block."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from fabric_desktop import kernel_proc as kp
from fabric_desktop.kernel_proc import KernelGone, ProcessKernel


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


class Alive:
    """A process that is running, as far as the kernel can tell."""

    def poll(self):
        return None


def answering(k: ProcessKernel, **answer: Any) -> list[dict[str, Any]]:
    """Make ``k``'s process answer every request with ``answer``; returns what was sent."""
    sent: list[dict[str, Any]] = []

    def send(proc, message):
        sent.append(message)
        slot = k._waiting[message["id"]]
        slot.update(answer)
        slot["done"].set()
    k._send = send  # type: ignore[method-assign]
    return sent


def stop_for_good(k: ProcessKernel) -> None:
    proc = k._proc
    try:
        k.close()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(10)


# ---- requests


def test_a_request_without_a_process_running_is_refused_and_starts_none():
    k = ProcessKernel()
    with pytest.raises(KernelGone, match="stopped"):
        k._request("poll", spawn=False)
    assert k._proc is None


def test_a_request_returns_the_result_and_raises_the_error_it_is_answered_with():
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    sent = answering(k, result={"ok": 1})
    assert k._request("reads", spawn=False, x=2) == {"ok": 1}
    assert sent[0]["op"] == "reads" and sent[0]["args"] == {"x": 2} and k._waiting == {}
    answering(k, error={"type": "ValueError", "message": "bad value"})
    with pytest.raises(RuntimeError, match="^bad value$"):
        k._request("reads", spawn=False)
    answering(k, error={"type": "ValueError", "message": ""})
    with pytest.raises(RuntimeError, match="^ValueError$"):
        k._request("reads", spawn=False)
    answering(k, error={"type": "KernelGone", "message": "gone now"})
    with pytest.raises(KernelGone, match="^gone now$"):
        k._request("reads", spawn=False)


def test_the_variable_libraries_call_answers_none_when_the_engine_gives_none():
    assert ProcessKernel().calls["variable_libraries"]("Lib") is None


# ---- cells


def test_a_starting_cell_shows_running_with_the_next_count_and_nothing_yet(monkeypatch):
    k = ProcessKernel()
    k.count = 4
    seen: list[dict[str, Any]] = []

    def request(op, **args):
        if op == "start":
            seen.append(dict(k._last or {}))
            return {"status": "running", "execution_count": 5, "version": 1, "seconds": 0, "outputs": []}
        return None
    monkeypatch.setattr(k, "_request", request)
    assert k.start("1")["execution_count"] == 5
    assert seen == [{"status": "running", "execution_count": 5, "version": 0, "seconds": 0, "outputs": []}]
    assert k.count == 5


def test_a_finished_poll_without_outputs_keeps_the_outputs_already_shown():
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    k._owner = None
    k._running = True
    k._last = {"status": "running", "version": 1, "outputs": [{"type": "stream", "text": "a"}]}
    answering(k, result={"status": "ok", "version": 1, "outputs": None})
    snap = k.poll(1)
    assert snap["outputs"] is None and k._running is False
    assert k._last["outputs"] == [{"type": "stream", "text": "a"}] and k._last["status"] == "ok"


def test_a_process_that_ends_only_ends_a_cell_that_is_running():
    k = ProcessKernel()
    k._last = {"status": "ok", "version": 3, "outputs": []}
    k._gone("why")
    assert k._last == {"status": "ok", "version": 3, "outputs": []}
    k._running = True
    k._last = None
    k._gone("why")  # no cell to end
    assert k._last is None and k._running is False


def test_a_restart_clears_the_count_and_the_running_cell():
    k = ProcessKernel()
    k._running, k.count, k._last = True, 5, {"status": "running"}
    k.restart()
    assert (k._running, k.count, k._last, k._ended) == (False, 0, None, "")


def recording(k: ProcessKernel, monkeypatch, answer: Any = None) -> list[tuple[str, dict[str, Any]]]:
    """Record each request ``k`` makes, with its timeout and spawn, and answer it with ``answer``."""
    asked: list[tuple[str, dict[str, Any]]] = []

    def request(op, **args):
        asked.append((op, args))
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(k, "_request", request)
    return asked


def test_a_cell_that_ends_its_process_before_starting_shows_its_outputs(monkeypatch):
    k = ProcessKernel()

    def request(op, **args):
        if op == "start":
            k._running = False  # the reader saw the process end
            raise KernelGone("gone")
    monkeypatch.setattr(k, "_request", request)
    snap = k.start("import os; os._exit(1)")
    assert snap["outputs"] == [] and snap["version"] == 0


def test_a_cell_cant_start_while_another_notebooks_cell_runs_in_the_session(monkeypatch):
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    k._running = True
    monkeypatch.setattr(k, "poll", lambda *a: {"status": "ok", "session_running": True})
    with pytest.raises(RuntimeError, match="^Another notebook is running a cell in this shared session"):
        k.start("1")
    k._owner_name = "Sales"
    with pytest.raises(RuntimeError, match="^Sales is running a cell"):
        k.start("1")


def test_another_notebooks_poll_asks_the_running_process_without_starting_one(monkeypatch):
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    k._owner = "other"
    asked = recording(k, monkeypatch, {"status": "running", "version": 2})
    assert k.poll(1, wait=5) == {"status": "running", "version": 2}
    assert asked == [("poll", {"timeout": 2.0 + kp.ANSWER_SECONDS, "spawn": False, "version": 1, "wait": 5})]


def test_a_poll_that_gets_no_answer_shows_the_cell_as_it_last_was(monkeypatch):
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    k._owner = None
    k.count = 3
    recording(k, monkeypatch, KernelGone("stuck"))
    assert k.poll(0) == {"status": "running", "execution_count": 3, "version": 0, "seconds": 0, "outputs": None}
    k._last = {"status": "running", "version": 4, "outputs": ["a"]}
    assert k.poll(2)["outputs"] == ["a"] and k.poll(4)["outputs"] is None


def test_the_debugger_waits_for_its_evaluation_and_starts_no_process(monkeypatch):
    from fabric_desktop.debugger import EVAL_SECONDS
    k = ProcessKernel()
    asked = recording(k, monkeypatch, {"ok": True})
    assert k.debug("evaluate", "x") == {"ok": True}
    assert asked == [("debug", {"timeout": EVAL_SECONDS + kp.ANSWER_SECONDS, "spawn": False, "action": "evaluate",
                                "expression": "x"})]


@pytest.mark.skipif(sys.platform == "win32", reason="reads /proc")
def test_memory_is_read_in_bytes_from_the_kilobytes_linux_reports(monkeypatch):
    monkeypatch.setattr(kp.Path, "read_text", lambda self, *a, **kw: "Name:\tpython\nVmRSS:\t     12 kB\n")
    assert kp.memory_used(1) == 12 * 1024


# ---- the result viewer


def test_the_result_viewer_asks_for_the_first_100_rows_and_gives_back_the_answers():
    k = ProcessKernel()
    k._proc = Alive()  # type: ignore[assignment]
    sent = answering(k, result={"rows": []})
    assert k.frame_page("f") == {"rows": []}
    assert sent[-1]["args"] == {"frame": "f", "offset": 0, "limit": 100, "sort": None, "desc": False, "q": ""}
    assert k.ipywidget_event("m", {"x": 1}) == {"rows": []}
    assert k.frame_download("f", "csv", "/p") == {"rows": []}
    assert sent[-1]["op"] == "frame_download" and sent[-1]["args"] == {"frame": "f", "format": "csv", "path": "/p"}
    assert k.frame_wrangle("f", []) == {"rows": []}


# ---- a real process


class _StopsWaits:
    """kernel_proc's time, keeping each wait ProcessKernel.stop starts once the reader has marked the process
    gone."""

    def __init__(self, marked: threading.Event):
        self.marked, self.after = marked, []

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, seconds):
        if self.marked.is_set() and sys._getframe(1).f_code.co_name == "stop":
            self.after.append(seconds)
        time.sleep(seconds)


def test_stop_returns_once_the_process_has_ended_and_close_ends_one_that_runs(monkeypatch):
    k = ProcessKernel()
    try:
        assert k.run("x = 1")["status"] == "ok"
        readers = [t for t in threading.enumerate() if t.name == "kernel-reader" and t.is_alive()]
        assert readers and all(t.daemon for t in readers)  # never holds the engine open
        gone, marked = k._gone, threading.Event()
        k._gone = lambda why: (time.sleep(0.5), gone(why), marked.set())  # type: ignore[method-assign]  # a slow reader
        waits = _StopsWaits(marked)
        monkeypatch.setattr(kp, "time", waits)
        assert k.stop() == {"stopped": True}
        monkeypatch.undo()
        # Stop waited for the reader, and returned once it had marked the process gone rather than at the end of its
        # own 5 s wait: counted in the waits it started after that, not timed (a time limit failed on busy runners).
        assert marked.is_set() and k._proc is None
        assert len(waits.after) <= 1, f"stop waited on {len(waits.after)} more times after the reader marked it gone"
        del k._gone
        assert k.run("x = 2")["status"] == "ok"
        proc = k._proc
        k.close()
        assert proc is not None and proc.poll() is not None and not k.alive()
    finally:
        stop_for_good(k)


# ---- text


@pytest.mark.parametrize("code, shown", [(0xFFFF, "65535"), (0x10000, "0x00010000"), (-0xFFFF, "-65535"),
                                         (-0x10000, "0xFFFF0000"), (None, "None"), (2, "2")])
def test_an_exit_code_is_shown_in_hex_only_past_four_hex_digits(code, shown):
    assert f"(exit code {shown})" in kp.stopped_text(code)


def test_out_of_memory_codes_are_matched_as_signed_or_unsigned():
    assert "ran out of memory" in kp.stopped_text(0xC000012D - 0x100000000)
    assert "ran out of memory" in kp.stopped_text(-9)
    assert "ran out of memory" not in kp.stopped_text(-10)
    assert "ran out of memory" not in kp.stopped_text(9)


@pytest.mark.parametrize("seconds, text", [(59, "59 second"), (60, "1 minute"), (90, "1.5 minute"),
                                           (3600, "1 hour"), (5400, "90 minute"), (7200, "2 hour")])
def test_a_limit_is_written_in_its_largest_whole_unit(seconds, text):
    assert kp._duration(seconds) == text


def test_a_memory_limit_is_written_in_gb_from_one_gb_up():
    assert kp._size(1024 ** 3) == "1 GB" and kp._size(1024 ** 3 - 1024 ** 2) == "1023 MB"


def test_a_cell_inside_its_time_limit_is_not_interrupted():
    k = ProcessKernel()
    k.time_limit = 30
    try:
        r = k.run("import time\ntime.sleep(2.5)\nprint('done')")
        assert r["status"] == "ok" and k.limit_hit == "" and r["outputs"][-1]["text"].strip() == "done"
    finally:
        stop_for_good(k)
