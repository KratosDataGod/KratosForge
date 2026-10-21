"""Shell commands from notebook cells, on this PC.

``!command`` runs one line in the system shell (cmd.exe on Windows, /bin/sh elsewhere), as
IPython, Fabric and Databricks notebooks do; ``files = !dir`` keeps its output lines instead.
``%sh`` (Databricks), ``%%sh`` and ``%%bash`` cells run the cell as a script in bash or sh (on
Windows, the one Git for Windows or WSL installs), and ``%%cmd`` and ``%%powershell`` run it in
Windows' own shells. Output streams into the cell while it runs; Cancel ends the command and
everything it started. Nothing here runs anything the person didn't write in a cell.
"""

from __future__ import annotations

import contextlib
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable


class ShellError(Exception):
    """A script cell's command ended with a non-zero exit code."""


def expand(command: str, namespace: dict[str, Any]) -> str:
    """IPython's expansion in a ``!`` line: ``{expression}`` and ``$name`` take values from the
    notebook, ``$$`` is a plain ``$``. Anything that doesn't evaluate is left as written, so shell
    braces like ``find . -exec rm {} ;`` are untouched."""
    def braces(m: re.Match[str]) -> str:
        try:
            return str(eval(m.group(1), namespace))  # noqa: S307 - the person's own cell code
        except Exception:  # noqa: BLE001 - not an expression: keep the text for the shell
            return m.group(0)

    def dollar(m: re.Match[str]) -> str:
        if m.group(0) == "$$":
            return "$"
        return str(namespace[m.group(1)]) if m.group(1) in namespace else m.group(0)

    command = re.sub(r"\{([^{}]+)\}", braces, command)
    return re.sub(r"\$\$|\$([A-Za-z_]\w*)", dollar, command)


def system(command: str, echo: Callable[[str], None] | None = None) -> tuple[int, list[str]]:
    """Run one command line in the system shell; returns the exit code and the output lines."""
    return _run(command, shell=True, echo=echo)


def script(kind: str, body: str, echo: Callable[[str], None] | None = None) -> int:
    """Run a cell's body as a script: kind is sh, bash, cmd or powershell. Returns the exit code."""
    if kind in ("sh", "bash"):
        exe = shutil.which(kind) or shutil.which("bash") or shutil.which("sh")
        if exe is None:
            raise FileNotFoundError(f"%%{kind} needs bash or sh, and this PC has neither on its PATH. "
                                    "Git for Windows installs one; or run the commands with %%cmd, %%powershell or !command.")
        return _run([exe, "-s"], stdin=body, echo=echo)[0]
    if kind == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if exe is None:
            raise FileNotFoundError("%%powershell needs PowerShell, which this PC doesn't have on its PATH.")
        return _run([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"], stdin=body, echo=echo)[0]
    if kind == "cmd":
        if os.name != "nt":
            raise OSError("%%cmd runs in Windows' command prompt, which this computer doesn't have. Use %%sh.")
        fd, name = tempfile.mkstemp(suffix=".cmd", prefix="kratosforge-cell-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n" + body.replace("\r\n", "\n").replace("\n", "\r\n"))
            return _run(["cmd.exe", "/d", "/c", name], echo=echo)[0]
        finally:
            Path(name).unlink(missing_ok=True)
    raise ValueError(f"Unknown shell {kind!r}")


def _run(args: str | list[str], shell: bool = False, stdin: str | None = None,
         echo: Callable[[str], None] | None = None) -> tuple[int, list[str]]:
    echo = echo or sys.stdout.write
    flags: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                             else {"start_new_session": True})
    proc = subprocess.Popen(args, shell=shell, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,  # noqa: S602 - the person's own command
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", **flags)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def feed() -> None:
        assert proc.stdin is not None
        with contextlib.suppress(OSError):  # the command ended before reading all of it
            proc.stdin.write(stdin or "")
            proc.stdin.close()

    threading.Thread(target=pump, daemon=True).start()
    if stdin is not None:
        threading.Thread(target=feed, daemon=True).start()
    out: list[str] = []
    try:
        # Short waits, so Cancel (an exception raised in this thread) lands between them.
        while (line := _next(lines)) is not None:
            out.append(line.rstrip("\r\n"))
            echo(line)
        return proc.wait(), out
    except BaseException:
        end(proc)
        raise


def _next(lines: queue.Queue[str | None]) -> str | None:
    while True:
        try:
            return lines.get(timeout=0.1)
        except queue.Empty:
            continue


def end(proc: subprocess.Popen[str]) -> None:
    """End a command and every process it started."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):  # silent-ok: already gone
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
