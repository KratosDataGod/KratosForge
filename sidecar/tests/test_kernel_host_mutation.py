"""The kernel process's line handling (kernel_host.dispatch), held to exact values: the line limit and how a
refusal names it, each kind of malformed line, the stderr note (its length and op, never the line), the refusal a
waiting caller gets, the request thread's name, and that importing the module starts nothing. A fake host and
channel; one subprocess that only imports the module."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import kernel_host

SIDECAR = Path(__file__).resolve().parents[1]
REQUEST_REFUSAL = "a request whose op isn't text or whose args aren't an object"


class _Channel:
    def __init__(self):
        self.sent, self.answered = [], []

    def send(self, message):
        self.sent.append(message)

    def answer(self, message):
        self.answered.append(message)


class _Threads:
    """Stands in for the threading module: records each thread and doesn't start one."""

    def __init__(self):
        self.made = []

    def Thread(self, **kw):  # noqa: N802 - the name threading uses
        self.made.append(kw)
        return SimpleNamespace(start=lambda: None)


def test_a_line_is_at_most_64_mb():
    assert kernel_host.MAX_LINE == 64 * 1024 * 1024


def test_a_line_over_the_limit_is_named_in_mb(monkeypatch, capsys):
    monkeypatch.setattr(kernel_host, "MAX_LINE", 3 * 1024 * 1024)
    channel = _Channel()
    line = "x" * (3 * 1024 * 1024 + 1)
    assert kernel_host.dispatch(SimpleNamespace(), channel, line) == "longer than 3 MB"
    assert capsys.readouterr().err == f"kernel_host: skipped a line that is longer than 3 MB ({len(line)} characters)\n"
    assert kernel_host.dispatch(SimpleNamespace(), channel, "x" * (3 * 1024 * 1024)) == "not JSON", "the limit is allowed"


@pytest.mark.parametrize("message, problem", [
    ([1], "a JSON list, not an object"),
    ({"result": 1}, "an answer without an id"),
    ({"id": 7, "result": 1}, "an answer without an id"),
    ({"op": 5}, REQUEST_REFUSAL),
    ({"op": "run", "args": [1]}, REQUEST_REFUSAL),
])
def test_each_malformed_line_is_refused_by_name_and_nobody_is_answered(message, problem):
    channel = _Channel()
    assert kernel_host.dispatch(SimpleNamespace(), channel, json.dumps(message)) == problem
    assert channel.sent == [] and channel.answered == []


def test_a_request_with_text_op_and_no_args_or_null_args_is_taken(monkeypatch):
    threads = _Threads()
    monkeypatch.setattr(kernel_host, "threading", threads)
    host = SimpleNamespace(handle=lambda m: None)
    assert kernel_host.dispatch(host, _Channel(), json.dumps({"op": "ping", "id": "p1"})) is None
    assert kernel_host.dispatch(host, _Channel(), json.dumps({"op": "o" * 50, "id": "p2", "args": None})) is None
    assert [(t["name"], t["daemon"], t["args"]) for t in threads.made] == [
        ("request-ping", True, ({"op": "ping", "id": "p1"},)),
        ("request-" + "o" * 40, True, ({"op": "o" * 50, "id": "p2", "args": None},))]


def test_a_malformed_request_names_its_op_cut_to_40_and_its_caller_gets_the_refusal(capsys):
    channel = _Channel()
    line = json.dumps({"op": "d" * 50, "id": "p3", "args": "step"})
    assert kernel_host.dispatch(SimpleNamespace(), channel, line) == REQUEST_REFUSAL
    assert channel.sent == [{"id": "p3", "error": {"type": "ValueError", "message": f"Malformed request: {REQUEST_REFUSAL}"}}]
    assert capsys.readouterr().err == (f"kernel_host: skipped a line that is {REQUEST_REFUSAL} ({len(line)} characters, "
                                       f"op {'d' * 40!r})\n")


def test_an_answer_wakes_its_caller():
    channel = _Channel()
    assert kernel_host.dispatch(SimpleNamespace(), channel, '{"id": "t1", "result": 2}') is None
    assert channel.answered == [{"id": "t1", "result": 2}]


def test_importing_the_module_starts_no_kernel():
    done = subprocess.run([sys.executable, "-c", "import fabric_desktop.kernel_host"], cwd=SIDECAR,
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert (done.returncode, done.stdout) == (0, "")
