"""Swapping a finished file into place waits out Windows refusing it while something has the file open.

On Windows, os.replace fails with PermissionError (WinError 5) while a reader, a virus scanner or an
indexer has the old or the new file open, often just after the new file is written. Every such swap
goes through jsonfile.replace, which tries again for about 1.5 s before giving up.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from fabric_desktop import jsonfile

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"

# Files that call os.replace themselves, each for a reason of its own.
OWN = {
    "jsonfile.py": "the shared helper",
    "leases.py": "retries a shared lease file for longer (_while_shared)",
    "notebook.py": "retries under the notebook's own write lock",
    "meta/snapshot.py": "moves a whole folder of release files into place",
}


def test_every_swap_into_place_waits_out_a_busy_file():
    bare = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in OWN:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("replace", "rename")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"):
                bare.append(f"{rel}:{node.lineno}")
    assert not bare, f"Use jsonfile.replace, or add the file to OWN with its reason: {bare}"


def test_replace_tries_again_while_windows_refuses_it(tmp_path, monkeypatch):
    src, dst = tmp_path / "new", tmp_path / "old"
    src.write_text("new")
    dst.write_text("old")
    real, calls, waits = os.replace, [], []

    def busy_twice(a, b):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(a, b)

    monkeypatch.setattr(jsonfile.os, "replace", busy_twice)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    jsonfile.replace(src, dst)
    assert dst.read_text() == "new" and not src.exists()
    assert len(calls) == 3 and waits == [0.05, 0.1]


def test_replace_gives_up_with_the_error_after_about_one_and_a_half_seconds(tmp_path, monkeypatch):
    waits = []

    def busy(a, b):
        raise PermissionError(13, "busy")

    monkeypatch.setattr(jsonfile.os, "replace", busy)
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        jsonfile.replace(tmp_path / "a", tmp_path / "b")
    assert len(waits) == 5 and 1.5 <= sum(waits) <= 1.6


def test_other_errors_are_not_waited_out(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    with pytest.raises(FileNotFoundError):
        jsonfile.replace(tmp_path / "missing", tmp_path / "b")
    assert waits == []
