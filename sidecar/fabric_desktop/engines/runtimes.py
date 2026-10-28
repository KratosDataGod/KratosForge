"""Engines whose packages run in a Python runtime of their own, never inside the engine (ADR-004).

Databricks Connect brings its own pyspark, and other platforms' clients bring their own pins, so
each such engine installs its packages into a folder of its own and runs each session as a separate
process there. What keeps that safe is the same for every one of them, and lives here:

- ``LockedRuntimes``: one folder per version line, ``<data>/runtimes/<name>-<line>/site-packages``.
  Installs take only the packages a hash-pinned lock file in the app names (``<locks>/<line>.txt``):
  wheels only, no dependency resolution, pip isolated from this PC's pip settings and environment,
  so a changed or substituted file fails the install instead of landing in a folder whose code later
  runs with the person's sign-in. The organization's policy can turn installs off or point them at
  its own mirror (the hashes still apply). Installing is a step the person starts; opening a
  session never installs anything.
- ``scrub_env``, ``drop_ref_variable`` and ``trusted_cli``: the worker's environment keeps none of
  this PC's variables for the platform, and a platform CLI is only ever one found in PATH's
  absolute folders.
- ``WorkerSession``: the engine's side of a worker process (runtime_worker.py, started with
  ``python -I``): a line-per-message protocol, a time limit per cell after which the cell is
  cancelled and, if it doesn't stop, the process ended, and Close.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .contract import CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported

WORKER = Path(__file__).with_name("runtime_worker.py")
INSTALL_SECONDS = 20 * 60
CELL_SECONDS = 2 * 60 * 60       # as the SQL warehouse engine's statement limit; then the cell is cancelled
STOP_SECONDS = 10                # after a cancel, before the process is ended
# What pip may see of this PC: enough to find a temp folder and a proxy, nothing that changes what it
# installs (PIP_*, PYTHON*, a config file) or that it could pass on.
PIP_ENV = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
_LINE = re.compile(r"\d{1,2}\.\d{1,2}")
DRIVE = re.compile(r"[A-Za-z]:[\\/]")


def lock_lines(locks: Path) -> list[str]:
    """Version lines with a hash-pinned requirements file in ``locks``."""
    return sorted(p.stem for p in Path(locks).glob("*.txt") if _LINE.fullmatch(p.stem))


class LockedRuntimes:
    """A runtime's folders under ``<root>/runtimes``, installed only from the app's lock files.

    ``name`` names the folders (``<name>-<line>``), ``distribution`` is the package whose version
    says what is installed, ``label`` and ``line_label`` name it in messages ("Databricks Connect for
    Databricks Runtime 16.4"), and ``allow_key`` and ``index_key`` are its policy settings."""

    def __init__(self, root: Path, *, name: str, locks: Path, distribution: str, label: str, line_label: str,
                 allow_key: str, index_key: str, python: str | None = None, run: Callable[..., Any] = subprocess.run):
        self.root = Path(root) / "runtimes"
        self.python = python or sys.executable
        self._run = run
        self.name, self._locks, self.distribution = name, Path(locks), distribution
        self.label, self.line_label = label, line_label
        self.allow_key, self.index_key = allow_key, index_key

    @property
    def locks(self) -> Path:
        return self._locks

    def supported_lines(self) -> list[str]:
        return lock_lines(self.locks)

    def folder(self, line: str) -> Path:
        if not _LINE.fullmatch(line or ""):
            raise ValueError(f"{line!r} isn't a runtime line like 16.4.")
        return self.root / f"{self.name}-{line}" / "site-packages"

    def installed(self, line: str) -> str | None:
        folder = self.folder(line)
        if not folder.is_dir():
            return None
        for dist in importlib.metadata.distributions(path=[str(folder)]):
            if (dist.metadata.get("Name") or "").lower() == self.distribution:
                return dist.version
        return None

    def unsupported(self, line: str) -> str:
        return (f"{self.label} for {self.line_label} {line} isn't supported in this version of KratosForge. "
                f"Supported: {', '.join(self.supported_lines())}.")

    def lock(self, line: str) -> Path:
        self.folder(line)
        path = self.locks / f"{line}.txt"
        if not path.is_file():
            raise Unsupported(self.unsupported(line))
        return path

    def install_command(self, line: str, index_url: str = "") -> list[str]:
        cmd = [self.python, "-I", "-m", "pip", "install", "--isolated", "--require-hashes", "--only-binary=:all:",
               "--no-deps", "--upgrade", "--no-input", "--disable-pip-version-check",
               "--target", str(self.folder(line)), "-r", str(self.lock(line))]
        if index_url:
            if not re.fullmatch(r"https://[^\s@]+", index_url):
                raise ValueError(f"{self.index_key} must be an https address with no user name or password.")
            cmd += ["--index-url", index_url]
        return cmd

    def install(self, line: str, base: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Download the packages for ``line``, hash-checked. Takes minutes."""
        from .. import app_settings, redact
        if app_settings.value(self.allow_key) is False:
            raise PermissionError(f"Your organization doesn't allow installing {self.label} from KratosForge.")
        command = self.install_command(line, str(app_settings.value(self.index_key) or ""))
        folder = self.folder(line)
        folder.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in (os.environ if base is None else base).items() if k.upper() in PIP_ENV}
        try:
            done = self._run(command, capture_output=True, text=True, timeout=INSTALL_SECONDS, env=env, cwd=str(folder),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "log": f"pip didn't finish within {INSTALL_SECONDS // 60} minutes, so it was stopped."}
        return {"ok": done.returncode == 0, "version": self.installed(line),
                "log": redact.secrets(((done.stdout or "") + (done.stderr or ""))[-20000:])}


def scrub_env(base: Mapping[str, str] | None, prefixes: tuple[str, ...]) -> dict[str, str]:
    """This PC's environment without any variable starting with one of ``prefixes`` (any case).
    PYTHON* always goes: it would change what the worker's Python loads."""
    drop = tuple(p.upper() for p in prefixes) + ("PYTHON",)
    return {k: v for k, v in (os.environ if base is None else base).items() if not k.upper().startswith(drop)}


def drop_ref_variable(env: dict[str, str], ref: str) -> dict[str, str]:
    """``env`` without the variable an ``env:NAME`` secret reference names: the secret goes to the
    worker under the platform's own name, and the worker can't know to remove the original."""
    kind, _, name = str(ref).partition(":")
    if kind != "env":
        return env
    return {k: v for k, v in env.items() if k.upper() != name.upper()}


def trusted_cli(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """A platform CLI from PATH's absolute folders only. ``shutil.which`` also looks in the current
    folder on Windows, where a planted ``<name>.exe`` would then get the person's sign-in. On Windows
    only ``<name>.exe`` counts, in folders that start with a drive letter, so no batch file or
    network share stands in for it."""
    env = os.environ if environ is None else environ
    windows = sys.platform == "win32"
    exe = f"{name}.exe" if windows else name
    for folder in (env.get("PATH") or "").split(os.pathsep):
        if not folder or not os.path.isabs(folder) or (windows and not DRIVE.match(folder)):
            continue
        path = os.path.join(folder, exe)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def worker_command(python: str, folder: Path, kind: str) -> list[str]:
    """``python -I runtime_worker.py <folder> <kind>``: -I keeps the engine's own modules away."""
    return [python, "-I", str(WORKER), str(folder), kind]


class WorkerSession:
    """The engine's end of a runtime_worker.py process. Subclasses name the engine (``engine``,
    ``label``), say which cell languages it takes (``check_language``) and turn worker outputs into
    the notebook's (``output``)."""

    engine = ""
    label = "The runtime"

    def __init__(self, config: SessionConfig, command: list[str], env: dict[str, str],
                 start_seconds: float, cwd: str | None = None, cell_seconds: float = CELL_SECONDS):
        self.notebook = config.notebook
        self._cell_seconds = cell_seconds
        self._closed = False
        self._lock = threading.Lock()
        self._running = False
        self._lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._n = 0
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", env=env, cwd=cwd,
                                     start_new_session=sys.platform != "win32",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True, name=f"{self.engine}-{config.notebook}").start()
        self._send({"op": "start"})
        reply = self._wait(lambda m: m.get("op") in ("started", "error"), start_seconds)
        if reply.get("op") != "started":
            self.close()
            raise RuntimeError(f"{self.label} couldn't start: {reply.get('evalue') or 'the process ended'}")
        self.client = reply.get("client", "")

    # Hooks.
    def stop_seconds(self) -> float:
        return STOP_SECONDS

    def check_language(self, request: CellRequest) -> None:
        if request.language not in ("python", "pyspark"):
            raise Unsupported(f"{self.label} runs Python cells.")

    def output(self, out: dict[str, Any]) -> dict[str, Any]:
        return out

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with contextlib.suppress(ValueError):  # silent-ok: a stray line from a library isn't a message
                self._lines.put(json.loads(line))
        self._lines.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise SessionClosed(f"The {self.label} process has ended. Start the engine again.") from None

    def _wait(self, wanted: Callable[[dict[str, Any]], bool], seconds: float | None) -> dict[str, Any]:
        deadline = None if seconds is None else time.monotonic() + seconds
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if left == 0.0:
                return {"op": "error", "evalue": f"no answer within {seconds / 60:.0f} minutes"}
            try:
                msg = self._lines.get(timeout=left)
            except queue.Empty:
                continue
            if msg is None:
                return {"op": "error", "evalue": "the process ended"}
            if wanted(msg):
                return msg

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        if self._closed:
            raise SessionClosed(f"This {self.label} session was closed. Start the engine again to run cells.")
        self.check_language(request)
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        started = time.perf_counter()
        try:
            self._running = True
            self._n += 1
            cid = self._n
            self._send({"op": "exec", "id": cid, "code": request.code, "preview_rows": request.preview_rows})

            def mine(m: dict[str, Any]) -> bool:
                return m.get("op") in ("result", "error") and m.get("id", cid) == cid
            reply = self._wait(mine, self._cell_seconds)
            if reply.get("op") == "error" and "no answer" in str(reply.get("evalue")):
                # Too long: cancel what it runs, give it a moment to stop, then end the process.
                with contextlib.suppress(SessionClosed):  # silent-ok: close() below ends it either way
                    self._send({"op": "interrupt"})
                reply = self._wait(mine, self.stop_seconds())
                if reply.get("op") != "result":
                    self.close()
                    raise SessionClosed(f"The cell was still running after {self._cell_seconds / 60:.0f} minutes, "
                                        "so it was cancelled and the session ended. Start the engine again.")
        finally:
            self._running = False
            self._lock.release()
        if reply.get("op") != "result":
            self._closed = True
            raise SessionClosed(f"The {self.label} process ended while the cell ran. Start the engine again.")
        raw = reply.get("outputs") or []
        outputs = [self.output(o) for o in raw]
        if on_output:
            for o in outputs:
                on_output(o)
        rows = next((len(o["rows"]) for o in reversed(raw) if o.get("type") == "frame"), None)
        return CellResult(reply.get("status", "error"), outputs, time.perf_counter() - started, rows, self.engine)

    def cancel(self) -> bool:
        if self._closed or not self._running:
            return False
        self._send({"op": "interrupt"})
        return True

    def close(self) -> None:
        if self._closed and self.proc.poll() is not None:
            return
        self._closed = True
        with contextlib.suppress(SessionClosed):  # silent-ok: the process has already gone
            self._send({"op": "quit"})
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            from ..shell import end
            end(self.proc)
