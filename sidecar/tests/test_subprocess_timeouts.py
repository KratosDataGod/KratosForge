"""#1174: every engine subprocess call has a timeout, or a reason here why it can't.

A child process that never returns (a command waiting on a proxy, a stuck taskkill) would hold the
engine call that started it, and the person would see a spinner that never ends. Code the engine
writes for a notebook or Fabric to run is text, not parsed here: those calls run in that kernel.
"""

from __future__ import annotations

import ast
from pathlib import Path

import fabric_desktop

PACKAGE = Path(fabric_desktop.__file__).parent
CALLS = ("run", "call", "check_call", "check_output")

# file:function -> why the call has no timeout
NO_TIMEOUT = {
    "notebook_batch_service.py:one": "a batch's notebook runs in the batch's own background process "
                                     "(cli run-notebooks), not in an engine call, and can rightly run for hours",
}


class _Finder(ast.NodeVisitor):
    """The innermost function around each subprocess call without a timeout."""

    def __init__(self, file: str):
        self.file, self.where, self.found = file, ["<module>"], set()

    def visit_FunctionDef(self, node):
        self.where.append(node.name)
        self.generic_visit(node)
        self.where.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Attribute) and node.func.attr in CALLS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                and not any(k.arg in ("timeout", None) for k in node.keywords)):
            self.found.add(f"{self.file}:{self.where[-1]}")
        self.generic_visit(node)


def _untimed() -> list[str]:
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        finder = _Finder(path.relative_to(PACKAGE).as_posix())
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        found |= finder.found
    return sorted(found)


def test_every_subprocess_call_has_a_timeout_or_a_listed_reason():
    untimed = _untimed()
    unlisted = [c for c in untimed if c not in NO_TIMEOUT]
    assert not unlisted, f"give these a timeout (a named constant), or list them above with why they can't: {unlisted}"
    stale = sorted(set(NO_TIMEOUT) - set(untimed))
    assert not stale, f"listed above but no longer untimed; remove them: {stale}"


def test_the_check_finds_a_call_without_a_timeout(tmp_path, monkeypatch):
    (tmp_path / "new.py").write_text("import subprocess\n\ndef go():\n    subprocess.run(['x'])\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "PACKAGE", tmp_path)
    assert _untimed() == ["new.py:go"]


def test_a_stuck_taskkill_doesnt_hold_the_command_that_ends_a_process(monkeypatch):
    import subprocess
    import sys

    from fabric_desktop import shell
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    seen = {}

    def stuck(args, **kwargs):
        seen.update(kwargs)
        proc.kill()  # what taskkill would have done, before it hung
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(shell.os, "name", "nt")
    monkeypatch.setattr(shell.subprocess, "run", stuck)
    assert shell.end(proc) is True
    assert seen["timeout"] == shell.TASKKILL_SECONDS and proc.poll() is not None


def test_when_taskkill_times_out_the_command_itself_is_still_ended(monkeypatch):
    """A taskkill that hangs without killing anything used to leave the command running behind a quiet return."""
    import subprocess
    import sys

    from fabric_desktop import shell
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    def stuck(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(shell.os, "name", "nt")
    monkeypatch.setattr(shell.subprocess, "run", stuck)
    assert shell.end(proc) is True and proc.poll() is not None
