"""The Windows programs the engine starts are named by their full path (winexe.py), so a program of
the same name in the current folder (a notebook's or a repo's) is never started instead."""

from __future__ import annotations

import ast
from pathlib import Path

from fabric_desktop import winexe

PACKAGE = Path(winexe.__file__).parent


def test_programs_are_found_under_the_windows_folder_windows_reports(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"D:\Win")
    assert winexe.system32("taskkill.exe") == r"D:\Win\System32\taskkill.exe"
    assert winexe.windows_exe("explorer.exe") == r"D:\Win\explorer.exe"


def test_without_systemroot_the_usual_windows_folder_is_used(monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    assert winexe.system32("schtasks.exe") == r"C:\Windows\System32\schtasks.exe"


def test_no_windows_tool_is_started_by_its_bare_name():
    """ruff's S607 sees only subprocess calls; this also sees a command list handed to a helper."""
    bare = {"taskkill", "schtasks", "explorer", "cmd", "icacls", "powershell", "reg", "netsh", "msiexec",
            "tasklist", "wmic", "certutil", "takeown"}
    found = []
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                        and first.value.lower().removesuffix(".exe") in bare:
                    found.append(f"{path.relative_to(PACKAGE)}:{node.lineno} {first.value!r}")
    assert not found, "use winexe.system32() or winexe.windows_exe(): " + ", ".join(found)
