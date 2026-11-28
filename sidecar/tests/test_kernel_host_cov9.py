"""kernel_host: dispatch's refusals of lines that aren't a message (blank, not JSON, too deep, not an object,
an answer without an id), each named on stderr without the line, and rename_guest passed to the kernel."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop import kernel_host


class Channel:
    def __init__(self):
        self.sent, self.answered = [], []

    def send(self, message):
        self.sent.append(message)

    def answer(self, message):
        self.answered.append(message)


def test_a_blank_line_is_skipped_quietly(capsys):
    ch = Channel()
    assert kernel_host.dispatch(SimpleNamespace(), ch, "   \n") is None
    assert ch.sent == ch.answered == [] and capsys.readouterr().err == ""


@pytest.mark.parametrize("line, problem", [
    ('{"op": "start", "id": "x", "args": {"code": "secret = 1"', "not JSON"),
    ("[1, 2]", "a JSON list, not an object"),
    ('"text"', "a JSON str, not an object"),
    ("5", "a JSON int, not an object"),
    ('{"result": 1}', "an answer without an id"),
    ('{"id": 7, "result": 1}', "an answer without an id"),
])
def test_lines_that_arent_messages_are_refused_by_name(line, problem, capsys):
    ch = Channel()
    assert kernel_host.dispatch(SimpleNamespace(), ch, line) == problem
    err = capsys.readouterr().err
    assert f"kernel_host: skipped a line that is {problem} ({len(line)} characters)" in err
    assert "secret" not in err  # never the line itself
    assert ch.sent == ch.answered == []


def test_a_line_nested_deeper_than_python_reads_is_refused(capsys, monkeypatch):
    def deep(_line):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(kernel_host.json, "loads", deep)
    assert kernel_host.dispatch(SimpleNamespace(), Channel(), '{"id": "a"}') == "nested too deep"
    assert "skipped a line that is nested too deep (11 characters)" in capsys.readouterr().err


def test_a_truly_deep_line_is_refused_in_a_fresh_process():
    # A real one, where json's decoder hits the recursion limit.
    code = ("import sys; from fabric_desktop import kernel_host as k; "
            "print(k.dispatch(None, None, '[' * 200000 + ']' * 200000))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)
    assert out.stdout.strip() == "nested too deep"


def test_rename_guest_asks_the_kernel():
    calls = []
    host = object.__new__(kernel_host.Host)
    host.kernel = SimpleNamespace(rename_guest=lambda old, new: calls.append((old, new)) or {"renamed": True})
    assert host.rename_guest("a.ipynb", "b.ipynb") == {"renamed": True}
    assert calls == [("a.ipynb", "b.ipynb")]


def test_an_answer_wakes_its_caller():
    ch = kernel_host.Channel(None, SimpleNamespace(write=lambda s: None, flush=lambda: None))
    slot = {"done": threading.Event()}
    ch.waiting["c1"] = slot
    assert kernel_host.dispatch(SimpleNamespace(), ch, json.dumps({"id": "c1", "result": 3})) is None
    assert slot["done"].is_set() and slot["result"] == 3
