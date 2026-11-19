"""Mutation batch 120: shell.py (docs/quality/mutation-testing.md). The survivors were the shell a script
cell falls back to, a cmd script file already gone, a command line given as a list, the reader threads
being daemons, and how a command is ended on Windows (taskkill's arguments and the waits)."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from fabric_desktop import shell


def test_a_sh_cell_falls_back_to_bash_and_then_to_sh(monkeypatch):
    ran = []
    monkeypatch.setattr(shell, "_run", lambda args, stdin=None, echo=None: ran.append(args[0]) or (0, []))
    for found, want in (({"bash": "/x/bash"}, "/x/bash"), ({"sh": "/x/sh"}, "/x/sh"),
                        ({"sh": "/x/sh", "bash": "/x/bash"}, "/x/sh")):
        monkeypatch.setattr(shell, "trusted_cli", found.get)
        assert shell.script("sh", "echo hi") == 0
        assert ran[-1] == want
    monkeypatch.setattr(shell, "trusted_cli", {"sh": "/x/sh", "bash": "/x/bash"}.get)
    shell.script("bash", "echo hi")
    assert ran[-1] == "/x/bash"


def test_a_cmd_script_file_already_gone_is_no_error(monkeypatch):
    monkeypatch.setattr(shell, "os", SimpleNamespace(name="nt", fdopen=os.fdopen))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    def run(args, stdin=None, echo=None, shell=False):
        Path(args[-1]).unlink()
        return 0, []
    monkeypatch.setattr(shell, "_run", run)
    assert shell.script("cmd", "echo hi") == 0


def test_a_command_given_as_a_list_runs_with_its_arguments():
    code, out = shell._run([sys.executable, "-c", "import sys; print(sys.argv[1:])", "a b", "c"], echo=lambda s: None)
    assert code == 0 and out == ["['a b', 'c']"]


def test_the_threads_reading_a_command_are_daemons(monkeypatch):
    made = []

    def thread(**kw):
        made.append(kw["daemon"])
        return threading.Thread(**kw)
    monkeypatch.setattr(shell, "threading", SimpleNamespace(Thread=thread))
    shell._run([sys.executable, "-c", "print(input())"], stdin="hi\n", echo=lambda s: None)
    assert made == [True, True]


class _Proc:
    pid = 4242

    def __init__(self):
        self.ended, self.waits = False, []

    def poll(self):
        return 0 if self.ended else None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return 0

    def kill(self):
        self.ended = True


def test_on_windows_taskkill_ends_the_command_s_tree_and_its_result_is_awaited(monkeypatch):
    assert shell.TASKKILL_SECONDS == 30
    monkeypatch.setattr(shell, "os", SimpleNamespace(name="nt"))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    proc, asked = _Proc(), []

    def taskkill(args, **kw):
        asked.append((args, kw))
        proc.ended = True
    monkeypatch.setattr(shell.subprocess, "run", taskkill)
    assert shell.end(proc) is True
    assert asked == [([r"C:\Windows\System32\taskkill.exe", "/F", "/T", "/PID", "4242"],
                      {"capture_output": True, "check": False, "timeout": 30})]
    assert proc.waits == [5]
