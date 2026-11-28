"""The kernel process's line handling (kernel_host.dispatch), held to its exact limits: the longest
line it reads, how it names a refused one, and the thread it runs a request on. Importing the module
must not start the protocol: only ``python -m fabric_desktop.kernel_host`` does."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


from fabric_desktop import kernel_host

MB = 1024 * 1024


class Recorder:
    def __init__(self) -> None:
        self.sent: list = []
        self.answered: list = []

    def send(self, message) -> None:
        self.sent.append(message)

    def answer(self, message) -> None:
        self.answered.append(message)


def answer_line(length: int) -> str:
    """A well-formed answer exactly ``length`` characters long."""
    head = '{"id": "a", "result": "'
    return head + "x" * (length - len(head) - 2) + '"}'


def test_the_longest_line_read_is_64_mb():
    assert kernel_host.MAX_LINE == 64 * MB


def test_a_line_of_exactly_64_mb_is_read(capsys):
    channel = Recorder()
    line = answer_line(64 * MB)
    assert len(line) == 64 * MB
    assert kernel_host.dispatch(SimpleNamespace(), channel, line) is None
    (answer,) = channel.answered
    assert answer["id"] == "a" and len(answer["result"]) == 64 * MB - 25
    assert capsys.readouterr().err == ""


def test_a_line_one_character_over_64_mb_is_refused_by_its_size(capsys):
    channel = Recorder()
    line = answer_line(64 * MB + 1)
    assert kernel_host.dispatch(SimpleNamespace(), channel, line) == "longer than 64 MB"
    assert channel.answered == [] and channel.sent == []
    assert capsys.readouterr().err == (
        f"kernel_host: skipped a line that is longer than 64 MB ({64 * MB + 1} characters)\n")


def test_a_refused_request_names_only_the_first_40_characters_of_its_op(capsys):
    op = "o" * 39 + "p" + "q" * 10
    line = json.dumps({"op": op, "args": [1]})
    problem = kernel_host.dispatch(SimpleNamespace(), Recorder(), line)
    assert problem == "a request whose op isn't text or whose args aren't an object"
    assert capsys.readouterr().err == (
        f"kernel_host: skipped a line that is {problem} ({len(line)} characters, op {op[:40]!r})\n")


def test_a_request_runs_on_a_daemon_thread_named_by_the_first_40_characters_of_its_op(monkeypatch):
    threads: list = []

    class Thread:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            threads.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(kernel_host, "threading", SimpleNamespace(Thread=Thread))
    host = SimpleNamespace(handle=lambda message: None)
    op = "p" * 40 + "z" * 5
    assert kernel_host.dispatch(host, Recorder(), json.dumps({"id": "r", "op": op})) is None
    (thread,) = threads
    assert thread.started
    assert thread.kwargs == {"target": host.handle, "args": ({"id": "r", "op": op},),
                             "daemon": True, "name": "request-" + "p" * 40}


def test_importing_the_module_does_not_start_the_protocol():
    root = Path(kernel_host.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    done = subprocess.run(
        [sys.executable, "-c", "import fabric_desktop.kernel_host as k; print('imported', k.MAX_LINE)"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"imported {64 * MB}\n"
