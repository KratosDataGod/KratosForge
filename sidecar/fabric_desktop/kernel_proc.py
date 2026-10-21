"""The notebook kernel in its own process, seen from the engine.

ProcessKernel has the in-process Kernel's methods (start, poll, interrupt, run, restart,
variables, complete) and runs the same Kernel class in a child process (kernel_host.py). The
engine stays responsive whatever a cell does: Stop ends only the child and every process its
cells started (a process group on Linux and macOS, a Job Object on Windows), a crash in a native
library ends only the child (the next cell starts a fresh one and says so), and a kernel that
stops answering times out instead of freezing the app.

What the kernel needs from the engine it asks for: tokens from the person's sign-in, another
notebook's cells, variable libraries, the managed-table check, and a proof's storage options.
The lakehouse the engine has attached is passed to the kernel before each cell.

Limits (Settings, Preview): a cell that runs longer than ``time_limit`` seconds is interrupted,
keeping variables, and its process is ended if it doesn't stop within TIME_GRACE_SECONDS. A
process that uses more than ``memory_limit`` bytes is ended. Memory is sampled every
WATCH_SECONDS, so a sudden spike can go past the limit before it's seen.
"""

from __future__ import annotations

import functools
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import lakehouse
from .frames import GONE, FrameGone

DOWNLOAD_SECONDS = 1800.0  # writing a whole result to Downloads, or a first page of a large query
ANSWER_SECONDS = 15.0  # a request that isn't a long poll; a kernel that misses this is stuck
STOPPED_TEXT = "Stopped. The notebook's Python process was ended, so variables were cleared."
WATCH_SECONDS = 1.0
TIME_GRACE_SECONDS = 30.0


class KernelGone(RuntimeError):
    """The kernel's process ended or stopped answering."""


class ProcessKernel:
    def __init__(self, token: Callable[[str], str] | None = None,
                 load_notebook: Callable[[str], list[dict[str, Any]]] | None = None,
                 variable_libraries: Callable[[str], dict[str, str] | None] | None = None,
                 proof_options: Callable[[], dict[str, str]] | None = None,
                 command: list[str] | None = None):
        self.calls: dict[str, Callable[..., Any]] = {
            "token": token or _no("tokens"),
            "load_notebook": load_notebook or _no("other notebooks"),
            "variable_libraries": variable_libraries or (lambda name: None),
            "write_guard": lambda name, schema, table: lakehouse.write_guard(name, schema, table) if lakehouse.write_guard else None,
            "proof_options": lambda: (self._proof_options or (lambda: {}))(),
        }
        self._proof_options = proof_options
        self.command = command or [sys.executable, "-m", "fabric_desktop.kernel_host"]
        self.notebook_name = ""
        self.widget_values: dict[str, str] = {}
        self.resources_dir = ""  # passed with each cell (notebook.Kernel.resources_dir)
        self._lock = threading.Lock()  # one process at a time: starting, stopping
        self._proc: subprocess.Popen[str] | None = None
        self._tree: _Tree | None = None  # the process and everything its cells start
        self._send_lock = threading.Lock()
        self._waiting: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._attached: Any = object()  # what the child has attached; nothing yet
        self._last: dict[str, Any] | None = None  # the last snapshot of the running (or last) cell
        self._running = False
        self._ended: str = ""  # why the last process ended, shown on the cell it ended
        self.count = 0
        self.pid: int | None = None
        self.time_limit: float | None = None  # seconds a cell may run; None for no limit
        self.memory_limit: int | None = None  # bytes the process may use; None for no limit
        self._started_at: float | None = None  # when the running cell started, if it has a time limit
        self._interrupted_at: float | None = None
        self.limit_hit = ""  # "time" or "memory" when a limit stopped the last cell

    # ---- the process

    def _ensure(self) -> subprocess.Popen[str]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                    text=True, encoding="utf-8", bufsize=1, creationflags=flags,
                                    start_new_session=sys.platform != "win32",
                                    env=dict(os.environ, PYTHONUNBUFFERED="1"))
            if self._tree is not None:
                self._tree.release()  # processes a crashed kernel left behind
            self._tree = _Tree(proc)
            ready = threading.Event()
            self._waiting["ready"] = {"done": ready}
            threading.Thread(target=self._read, args=(proc,), daemon=True, name="kernel-reader").start()
            threading.Thread(target=self._watch, args=(proc,), daemon=True, name="kernel-limits").start()
            self._proc = proc
            self._attached = object()
            if not ready.wait(60):
                self._tree.kill()
                raise KernelGone("The notebook's Python process didn't start.")
            slot = self._waiting.pop("ready", {})
            self.pid = (slot.get("result") or {}).get("pid")
            return proc

    def _read(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "call" in message:
                threading.Thread(target=self._answer_call, args=(proc, message), daemon=True, name="kernel-call").start()
                continue
            slot = self._waiting.get(message.get("id"))
            if slot is not None:
                slot.update({k: v for k, v in message.items() if k in ("result", "error")})
                slot["done"].set()
        code = proc.wait()
        if proc is self._proc:
            self._gone(f"The notebook's Python process stopped (exit code {code}), so variables were cleared. "
                       "The next cell starts a fresh one." if not self._ended else self._ended)
        for slot in list(self._waiting.values()):
            slot.setdefault("error", {"type": "KernelGone", "message": "The notebook's Python process stopped."})
            slot["done"].set()

    def _watch(self, proc: subprocess.Popen[str]) -> None:
        """Enforce the limits on ``proc`` for as long as it lives."""
        while proc.poll() is None:
            time.sleep(WATCH_SECONDS)
            if proc is not self._proc:
                return
            if self.memory_limit and (memory_used(proc.pid) or 0) > self.memory_limit:
                self.limit_hit = "memory"
                self._end(proc, f"Stopped: the notebook used more than {_size(self.memory_limit)}, the memory limit "
                                "set in Settings. Its process was ended, so variables were cleared.")
                return
            started, now = self._started_at, time.monotonic()
            if not (self._running and started is not None and self.time_limit):
                continue
            if self._interrupted_at is None and now - started > self.time_limit:
                self.limit_hit = "time"
                self._interrupted_at = now
                try:
                    self._request("interrupt", timeout=ANSWER_SECONDS, spawn=False)
                except (KernelGone, RuntimeError):
                    pass
            elif self._interrupted_at is not None and now - self._interrupted_at > TIME_GRACE_SECONDS:
                self._end(proc, f"Stopped: this cell ran longer than the {_duration(self.time_limit)} limit set in "
                                "Settings and didn't stop when interrupted, so its process was ended and variables were cleared.")
                return

    def _end(self, proc: subprocess.Popen[str], why: str) -> None:
        with self._lock:
            if proc is not self._proc or proc.poll() is not None:
                return
            self._ended = why
            self._tree.kill()

    def _limit_note(self, snap: dict[str, Any]) -> dict[str, Any]:
        """A finished cell that was interrupted for running too long says so, in every snapshot of it."""
        if self.limit_hit != "time" or snap.get("outputs") is None:
            return snap
        why = (f"Stopped: this cell ran longer than the {_duration(self.time_limit or 0)} limit set in Settings. "
               "Variables from earlier cells are kept.")
        note = {"type": "error", "ename": "TimeLimit", "evalue": why, "traceback": [why]}
        outputs = list(snap["outputs"])
        if note not in outputs:
            outputs.append(note)
        return {**snap, "status": "error", "outputs": outputs}

    def limits(self) -> dict[str, Any]:
        return {"cell_seconds": self.time_limit, "memory_bytes": self.memory_limit}

    def _gone(self, why: str) -> None:
        """The process ended: the cell it was running ends with the reason."""
        if self._running and self._last is not None:
            outputs = list(self._last.get("outputs") or []) + [{"type": "error", "ename": "KernelStopped", "evalue": why,
                                                                "traceback": [why]}]
            self._last = {**self._last, "status": "cancelled" if why == STOPPED_TEXT else "error",
                          "version": self._last.get("version", 0) + 1, "outputs": outputs}
        self._running = False
        self._proc = None

    def _answer_call(self, proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
        try:
            fn = self.calls.get(message["call"])
            if fn is None:
                raise ValueError(f"Unknown call {message['call']!r}")
            reply: dict[str, Any] = {"id": message["id"], "result": fn(*message.get("args") or [])}
        except Exception as exc:  # noqa: BLE001 - the kernel shows the reason in the cell
            reply = {"id": message["id"], "error": {"type": type(exc).__name__, "message": str(exc)}}
        self._send(proc, reply)

    def _send(self, proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
        assert proc.stdin is not None
        with self._send_lock:
            try:
                proc.stdin.write(json.dumps(message, default=str) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise KernelGone("The notebook's Python process stopped.") from exc

    def _request(self, op: str, timeout: float = ANSWER_SECONDS, spawn: bool = True, **args: Any) -> Any:
        """Send ``op`` and wait for its answer. ``spawn`` False asks only a process that is running:
        a poll or interrupt that starts a fresh one would report its idle state as the ended cell's."""
        proc = self._ensure() if spawn else self._proc
        if proc is None or proc.poll() is not None:
            raise KernelGone("The notebook's Python process stopped.")
        rid = f"p{next(self._ids)}"
        slot: dict[str, Any] = {"done": threading.Event()}
        self._waiting[rid] = slot
        try:
            self._send(proc, {"id": rid, "op": op, "args": args})
            if not slot["done"].wait(timeout):
                raise KernelGone("The notebook's Python process isn't answering. Stop it to start a fresh one; "
                                 "variables will be cleared.")
        finally:
            self._waiting.pop(rid, None)
        if "error" in slot:
            err = slot["error"]
            if err.get("type") == "KernelGone":
                raise KernelGone(err["message"])
            raise RuntimeError(err.get("message") or err.get("type"))
        return slot.get("result")

    def _sync_lakehouse(self) -> None:
        """Pass the engine's attached lakehouse (and whether tables are guarded) to the kernel."""
        a = lakehouse._attached
        guard = lakehouse.write_guard is not None
        context = None if a is None else {
            "workspace_id": a.workspace_id, "lakehouse_id": a.lakehouse_id, "name": a.name,
            "workspace_name": a.workspace_name, "work_dir": str(a.work_dir),
            "history_file": str(a.history_file) if a.history_file else None, "schemas_enabled": bool(a.schemas_enabled())}
        key = (json.dumps(context, sort_keys=True), guard)
        if key != self._attached:
            self._request("attach", context=context, guard=guard)
            self._attached = key

    # ---- the Kernel interface

    def start(self, code: str, time_limit: bool = True, watches: list[str] | None = None) -> dict[str, Any]:
        """Start ``code``. ``time_limit`` False runs this one cell without the cell time limit.
        ``watches`` are evaluated after it (notebook.Kernel.start)."""
        if self._running and self._proc is not None:
            snap = self.poll()
            if snap["status"] == "running":
                raise RuntimeError("A cell is already running. Wait for it, or Cancel it.")
        self._ended = ""
        self.limit_hit = ""
        self._interrupted_at = None
        self._started_at = None
        self._sync_lakehouse()
        self._running = True
        self._last = {"status": "running", "execution_count": self.count + 1, "version": 0, "seconds": 0, "outputs": []}
        if time_limit:
            self._started_at = time.monotonic()
        try:
            snap = self._request("start", code=code, notebook_name=self.notebook_name,
                                 widget_values=dict(self.widget_values), resources_dir=self.resources_dir, watches=list(watches or []))
        except KernelGone:
            # The cell ended its process before the start was even answered (os._exit, a crash).
            deadline = time.monotonic() + 5
            while self._running and time.monotonic() < deadline:
                time.sleep(0.02)
            return self.poll(-1, 0.0)
        self.count = snap.get("execution_count", self.count)
        if self._running:
            self._last = snap
        return snap

    def poll(self, known_version: int = -1, wait: float = 0.0) -> dict[str, Any]:
        if self._proc is None or self._proc.poll() is not None:
            if self._last is None:
                return {"status": "idle", "execution_count": self.count, "version": 0, "seconds": 0, "outputs": []}
            last = dict(self._last)
            if last.get("version") == known_version:
                last["outputs"] = None
            return last
        try:
            snap = self._request("poll", timeout=min(wait, 2.0) + ANSWER_SECONDS, spawn=False, version=known_version,
                                 wait=wait)
        except KernelGone:
            return self.poll(known_version, 0.0) if self._proc is None else self._stuck(known_version)
        if snap.get("outputs") is not None:
            self._last = snap
        elif self._last is not None:
            self._last = {**snap, "outputs": self._last.get("outputs")}
        if snap["status"] != "running":
            self._running = False
            self._started_at = None
            snap = self._limit_note(snap)
            if snap.get("outputs") is not None:
                self._last = snap
        return snap

    def _stuck(self, known_version: int) -> dict[str, Any]:
        last = dict(self._last or {"status": "running", "execution_count": self.count, "version": 0, "seconds": 0})
        last["outputs"] = None if last.get("version") == known_version else last.get("outputs")
        return last

    def interrupt(self) -> dict[str, Any]:
        if self._proc is None or self._proc.poll() is not None:
            return {"interrupted": False}
        try:
            return self._request("interrupt", timeout=ANSWER_SECONDS, spawn=False)
        except KernelGone:
            return {"interrupted": True, "stopped": False}

    def stop(self) -> dict[str, Any]:
        """End the kernel's process: the running cell stops whatever it is doing; variables are cleared."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return {"stopped": False}
            self._ended = STOPPED_TEXT
            self._tree.kill()
        proc.wait(10)
        deadline = time.monotonic() + 5
        while self._proc is proc and time.monotonic() < deadline:  # the reader marks it gone
            time.sleep(0.02)
        return {"stopped": True}

    def restart(self) -> None:
        """A fresh process, so a restart also gives back the memory the notebook used."""
        if self._proc is not None and self._proc.poll() is None:
            self.stop()
        self._ended = ""
        self._last = None
        self._running = False
        self.count = 0

    def run(self, code: str) -> dict[str, Any]:
        """Run ``code`` to the end and return its result (scheduled runs, tests)."""
        snap = self.start(code)
        version = -1
        outputs = snap.get("outputs") or []
        while snap["status"] == "running":
            snap = self.poll(version, 1.0)
            version = snap.get("version", version)
            if snap.get("outputs") is not None:
                outputs = snap["outputs"]
        return {"status": snap["status"], "execution_count": snap["execution_count"], "outputs": outputs}

    def variables(self) -> list[dict[str, Any]]:
        if self._proc is None or self._proc.poll() is not None:
            return []
        return self._request("variables")

    def complete(self, code: str, cursor: int | None = None) -> dict[str, Any]:
        return self._request("complete", code=code, cursor=cursor)

    def reads(self) -> dict[str, Any]:
        """What the cells read since this process started; nothing once it has ended."""
        if self._proc is None or self._proc.poll() is not None:
            return {"tables": {}, "files": {}}
        return self._request("reads")

    # ---- the result viewer (frames.py): results live where the cells ran

    def frame_page(self, frame: str, offset: int = 0, limit: int = 100, sort: int | None = None,
                   desc: bool = False, q: str = "") -> dict[str, Any]:
        if not self.alive():
            raise FrameGone(GONE)
        return self._request("frame_page", timeout=DOWNLOAD_SECONDS, spawn=False, frame=frame, offset=offset,
                             limit=limit, sort=sort, desc=desc, q=q)

    def frame_chart(self, frame: str, **spec: Any) -> dict[str, Any]:
        if not self.alive():
            raise FrameGone(GONE)
        return self._request("frame_chart", timeout=DOWNLOAD_SECONDS, spawn=False, frame=frame, **spec)

    def frame_profile(self, frame: str) -> dict[str, Any]:
        if not self.alive():
            raise FrameGone(GONE)
        return self._request("frame_profile", timeout=DOWNLOAD_SECONDS, spawn=False, frame=frame)

    def frame_wrangle(self, frame: str, steps: list[dict[str, Any]], name: str = "df") -> dict[str, Any]:
        if not self.alive():
            raise FrameGone(GONE)
        return self._request("frame_wrangle", timeout=DOWNLOAD_SECONDS, spawn=False, frame=frame, steps=steps, name=name)

    def frame_download(self, frame: str, format: str, path: str) -> dict[str, Any]:  # noqa: A002 - RPC name
        if not self.alive():
            raise FrameGone(GONE)
        return self._request("frame_download", timeout=DOWNLOAD_SECONDS, spawn=False, frame=frame, format=format, path=path)

    # ---- proofs (prove_service): the proof runtime lives where the cells run

    def bind_proof(self, config: dict[str, Any], side: str, root: str,
                   options: Callable[[], dict[str, str]]) -> None:
        self._proof_options = options
        self._request("bind_proof", config=config, side=side, root=root)

    def unbind_proof(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._request("unbind_proof")

    def finish_proof(self, modules: list[str]) -> dict[str, Any]:
        return self._request("finish_proof", modules=modules)

    def proof_log(self) -> dict[str, Any]:
        """The bound proof runtime's log so far (a replay checks what the cells read)."""
        return self._request("proof_log")

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self.stop()


class _Tree:
    """The kernel process and every process its cells start, so Stop ends them all (HD-037).

    On Linux and macOS the kernel leads its own process group, and Stop also ends every process
    below it by parent links: a cell's process that starts its own session or group (a server,
    ``start_new_session=True``) leaves the group and used to outlive Stop. On Windows it is in a
    Job Object that also ends the whole tree if the engine itself goes away; if the job can't be
    made (an older Windows that doesn't allow nested jobs), only the kernel process is ended."""

    def __init__(self, proc: subprocess.Popen[str]):
        self.proc = proc
        self.job = _job_for(proc) if sys.platform == "win32" else None

    def release(self) -> None:
        """On Windows, close the job, which ends anything still in it."""
        if self.job:
            kernel32 = _kernel32()
            kernel32.TerminateJobObject(self.job, 1)
            kernel32.CloseHandle(self.job)
            self.job = None

    def kill(self) -> None:
        if sys.platform == "win32":
            self.release()
        else:
            import signal
            # Freeze the tree first, so nothing in it starts a process while it is being found,
            # then end it. Parent links are read before the kernel dies and its children move.
            frozen = _signal_tree(self.proc.pid, signal.SIGSTOP)
            for pid in frozen:
                _signal(pid, signal.SIGKILL)
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.proc.kill()


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _signal_tree(root: int, sig: int) -> list[int]:
    """Send ``sig`` to ``root`` and every process below it, looking again until no new one
    turns up (each is stopped as it is found, so it can't start more). Returns them all."""
    sent: list[int] = []
    todo = [root]
    for _ in range(10):
        for pid in todo:
            _signal(pid, sig)
        sent += todo
        todo = [p for p in _descendants(root) if p not in sent]
        if not todo:
            break
    return sent


def _descendants(root: int) -> list[int]:
    """Every process below ``root`` by parent links: /proc on Linux, ps on macOS."""
    children: dict[int, list[int]] = {}
    for pid, ppid in _parents():
        children.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child)
    return out


def _parents() -> list[tuple[int, int]]:
    proc = Path("/proc")
    if proc.is_dir():
        pairs = []
        for entry in proc.iterdir():
            if entry.name.isdigit():
                try:  # "pid (name) state ppid ...": the name can hold spaces and parentheses
                    pairs.append((int(entry.name), int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])))
                except (OSError, IndexError, ValueError):
                    continue  # it ended while being read
        return pairs
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [(int(a), int(b)) for a, b in (line.split() for line in out.splitlines() if len(line.split()) == 2)]


@functools.cache
def _kernel32() -> Any:
    """kernel32 with the Job Object calls typed, so handles pass as pointers on 64-bit Windows."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _job_for(proc: subprocess.Popen[str]) -> int | None:
    """A Job Object holding ``proc`` that ends every process in it when closed, or None."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # extended limits
            and kernel32.AssignProcessToJobObject(job, int(proc._handle))):  # type: ignore[attr-defined]
        return job
    kernel32.CloseHandle(job)
    return None


def memory_used(pid: int) -> int | None:
    """Bytes the process is using: private bytes on Windows, resident memory on Linux; None if unknown."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                               "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PrivateUsage)
        finally:
            kernel32.CloseHandle(handle)
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _size(n: int) -> str:
    gb = n / 1024 ** 3
    return f"{gb:g} GB" if gb >= 1 else f"{n / 1024 ** 2:.0f} MB"


def _duration(seconds: float) -> str:
    """As in "the 1 hour limit"."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{int(seconds // 3600)} hour"
    if seconds >= 60:
        return f"{seconds / 60:g} minute"
    return f"{seconds:g} second"


def _no(what: str) -> Callable[..., Any]:
    def refuse(*_a: Any) -> Any:
        raise RuntimeError(f"{what} aren't available to this kernel.")
    return refuse
