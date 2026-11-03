"""The Academy learner process (ACD-005): a learner's Python exercise run apart from the engine.

What it is, in the words the app uses (docs/academy/grading-sandbox.md): it runs as you on this
PC, without your sign-in, with time and memory limits. It is not called a sandbox, and nothing is
claimed about the network, until the Windows AppContainer layer (ACD-005b) exists.

Layers:

- **A process of its own** (``python -I -c <_bootstrap.py>``), working folder the exercise
  workspace, stdin closed, an environment built from scratch (no proxies, no FABRIC_/AZURE_ or other
  variables, TEMP and HOME inside the workspace). It has no channel back to the engine: no token
  callback, no lakehouse, no RPC. Its output is stdout and stderr, capped. The process holds no
  tokens and gets none from the app; it runs as the same Windows user, so the person's own
  sign-in cache on disk is theirs to read, as it is from any program they run.
- **Limits.** Windows: the process is created suspended, put in a Job Object (one process only,
  memory, CPU time, killed when the job closes, no breakaway) and only then resumed; the bootstrap
  refuses to run if it isn't in a job. Linux and macOS (CI): its own session, data-size, CPU,
  file-size and open-file limits, and the whole group is killed when it ends. Everywhere: a wall
  clock limit.
- **Guards inside** (_bootstrap.py): the engine's package and sign-in libraries can't be imported,
  an audit hook refuses sockets, child processes, ctypes and writes outside the workspace, and
  reads of the app's package (``grading/`` included) and data folders; Polars, DuckDB, delta-rs,
  pyarrow and pandas readers and writers refuse URLs and paths outside the workspace.

``run`` returns only after the process and everything in its job or group has ended, so a
validator never reads a workspace something is still writing (Security read, must 5).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any

from . import workspace as ws

TIME_LIMIT = 60.0              # seconds of wall clock for one run
CPU_LIMIT = 120                # seconds of CPU time (all threads)
MEMORY_LIMIT = 1024 ** 3       # bytes for the whole job
OUTPUT_LIMIT = 1024 * 1024     # bytes of stdout and of stderr each kept
FILE_LIMIT = 512 * 1024 ** 2   # largest file the process may write (POSIX)
KILL_WAIT = 15.0               # seconds an ended process may take to be gone
BOOTSTRAP = Path(__file__).with_name("_bootstrap.py")
PACKAGE_DIR = Path(__file__).resolve().parent.parent       # fabric_desktop, grading/ included
# Packages that hold or reach the person's sign-in, and the engine itself.
BLOCKED_MODULES = ("fabric_desktop", "msal", "msal_extensions", "pymsalruntime", "azure", "keyring",
                   "win32cred", "mssql_python", "mcp")
# Only these come from the engine's environment; all else is set here. Matched without case on Windows.
_ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "PROCESSOR_ARCHITECTURE",
             "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL", "TZ")


def environment(work: Path, parent: dict[str, str] | None = None) -> dict[str, str]:
    """The learner process's environment: a few system variables, and its temp and home folders
    inside the workspace. Nothing else from the engine reaches it."""
    src = os.environ if parent is None else parent
    keep = {k.upper() for k in _ENV_KEEP}
    env = {k: v for k, v in src.items() if k.upper() in keep}
    tmp = str(work / ".tmp")
    env.update({"TEMP": tmp, "TMP": tmp, "TMPDIR": tmp, "HOME": tmp, "USERPROFILE": tmp, "APPDATA": tmp,
                "LOCALAPPDATA": tmp, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "OPENBLAS_NUM_THREADS": "2", "POLARS_MAX_THREADS": "4", "RAYON_NUM_THREADS": "4"})
    return env


def read_roots() -> list[str]:
    """Folders the learner's Python may read besides its workspace: the Python install."""
    roots = {sys.base_prefix, sys.prefix, *(sysconfig.get_paths().get(k, "") for k in ("stdlib", "platstdlib", "purelib", "platlib"))}
    roots.update(p for p in sys.path if p and os.path.isdir(p) and "site-packages" in p)
    return sorted(r for r in roots if r)


def denied_roots(data_root: Path) -> list[str]:
    """Never readable, even inside a read root: the engine package (grading/ is in it), the
    packages BLOCKED_MODULES name, and the app's data folder (the workspace is allowed first only
    for writes; reads under data_root outside the workspace are refused)."""
    out = [str(PACKAGE_DIR), str(Path(data_root).resolve())]
    for name in BLOCKED_MODULES[1:]:
        for root in read_roots():
            cand = Path(root) / name
            if cand.is_dir():
                out.append(str(cand))
    return out


def _check_lib_root(root: Path, work: Path, data_root: Path) -> str:
    """A folder of learner libraries (such as a generated PySpark shim) the process may read and
    import from: a plain folder that holds neither the engine package, the workspace nor the app's
    data folder, and isn't inside the engine package (grading/ is there)."""
    if Path(root).is_symlink() or not Path(root).is_dir():
        raise ws.Refused("A learner library folder must be a plain folder.")
    lib = Path(root).resolve()
    for other in (PACKAGE_DIR, work, Path(data_root).resolve()):
        if lib == other or other.is_relative_to(lib) or (other == PACKAGE_DIR and lib.is_relative_to(other)):
            raise ws.Refused("A learner library folder can't hold or sit in the app's own folders.")
    return str(lib)


def run(work: Path, script: str, data_root: Path, time_limit: float = TIME_LIMIT,
        memory_limit: int = MEMORY_LIMIT, cpu_limit: int = CPU_LIMIT,
        lib_roots: list[Path] | None = None) -> dict[str, Any]:
    """Run ``script`` (a file in the workspace) in a learner process. Returns ``{status, exit_code,
    stdout, stderr, seconds, limits}``; ``status`` is ok, error, time_limit, memory_limit or refused.
    ``lib_roots``: folders put first on the process's ``sys.path`` and readable, never writable."""
    path = ws.inside(work, script)
    if not path.is_file() or path.suffix != ".py":
        raise ws.Refused(f"{script} isn't a Python file in the exercise.")
    work = Path(work).resolve()
    ws.inside(work, ".tmp", must_exist=False).mkdir(exist_ok=True)   # refused if .tmp is a link
    # The data root is denied for reads, but the workspace lives under it: the hook checks
    # "inside the workspace" first, so the learner reads its own folder and nothing else of the app's.
    cfg = {"workspace": str(work), "script": path.relative_to(work).as_posix(), "read_roots": read_roots(),
           "denied_roots": denied_roots(data_root), "blocked_modules": list(BLOCKED_MODULES),
           "lib_roots": [_check_lib_root(r, work, data_root) for r in lib_roots or []]}
    argv = [sys.executable, "-I", "-X", "utf8", "-c", BOOTSTRAP.read_text(encoding="utf-8"), json.dumps(cfg)]
    started = time.monotonic()
    proc, limiter = _start(argv, work, memory_limit, cpu_limit)
    out: dict[str, bytes] = {}
    readers = [threading.Thread(target=_drain, args=(proc.stdout, out, "stdout"), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, out, "stderr"), daemon=True)]
    for t in readers:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=time_limit)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        limiter.close()          # ends anything left in the job or group, then waits for it
        proc.wait(timeout=KILL_WAIT)
        for t in readers:
            t.join(timeout=5)
    seconds = round(time.monotonic() - started, 3)
    code = proc.returncode
    stderr = out.get("stderr", b"").decode("utf-8", "replace")
    if timed_out:
        status = "time_limit"
    elif limiter.memory_hit() or "MemoryError" in stderr[-2000:]:
        status = "memory_limit"
    elif code == 3 and "isn't in its job" in stderr:
        status = "refused"
    else:
        status = "ok" if code == 0 else "error"
    return {"status": status, "exit_code": code, "stdout": out.get("stdout", b"").decode("utf-8", "replace"),
            "stderr": stderr, "seconds": seconds,
            "limits": {"seconds": time_limit, "memory_bytes": memory_limit, "cpu_seconds": cpu_limit},
            "runs_as": "you on this PC, without your sign-in, with time and memory limits"}


def _drain(stream: Any, out: dict[str, bytes], key: str) -> None:
    kept = bytearray()
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if len(kept) < OUTPUT_LIMIT:
            kept += chunk[: OUTPUT_LIMIT - len(kept)]
    if len(kept) >= OUTPUT_LIMIT:
        kept += b"\n[output cut at 1 MB]"
    out[key] = bytes(kept)


def _start(argv: list[str], work: Path, memory_limit: int, cpu_limit: int) -> tuple[subprocess.Popen[bytes], Any]:
    env = environment(work)
    common: dict[str, Any] = {"cwd": str(work), "env": env, "stdin": subprocess.DEVNULL,
                              "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if sys.platform == "win32":
        job = _WinJob(memory_limit, cpu_limit)
        flags = 0x00000004 | getattr(subprocess, "CREATE_NO_WINDOW", 0)   # CREATE_SUSPENDED
        proc = subprocess.Popen(argv, creationflags=flags, **common)
        try:
            job.hold(proc)       # assigned while suspended; resumed only once it is in the job
        except BaseException:
            proc.kill()
            job.close()
            raise
        return proc, job
    limits = _PosixGroup(memory_limit, cpu_limit)
    proc = subprocess.Popen(argv, start_new_session=True, preexec_fn=limits.apply, **common)  # noqa: PLW1509 - limits only
    limits.proc = proc
    return proc, limits


class _PosixGroup:
    """Linux and macOS (CI and development): resource limits and the process's own session."""

    def __init__(self, memory_limit: int, cpu_limit: int):
        self.memory_limit, self.cpu_limit = memory_limit, cpu_limit
        self.proc: subprocess.Popen[bytes] | None = None

    def apply(self) -> None:   # runs in the child before Python starts
        import resource
        resource.setrlimit(resource.RLIMIT_DATA, (self.memory_limit, self.memory_limit))
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_limit, self.cpu_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT, FILE_LIMIT))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def memory_hit(self) -> bool:
        return False

    def close(self) -> None:
        import signal
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass   # the group has already ended
        self.proc.wait(timeout=KILL_WAIT)


class _WinJob:
    """A Job Object: one process, a memory and CPU limit, ended when closed, no breakaway."""

    def __init__(self, memory_limit: int, cpu_limit: int):
        import ctypes
        from ctypes import wintypes
        from ..kernel_proc import _kernel32   # typed CreateJobObjectW and friends
        self.k = _kernel32()
        self.ctypes, self.wintypes = ctypes, wintypes
        self.ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.job = self.k.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("Windows wouldn't make a job for the exercise, so it wasn't run.")
        info = _extended()()
        basic = info.BasicLimitInformation
        # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS | JOB_MEMORY | JOB_TIME | DIE_ON_UNHANDLED_EXCEPTION;
        # BREAKAWAY_OK and SILENT_BREAKAWAY_OK are left off, so nothing can leave the job.
        basic.LimitFlags = 0x2000 | 0x0008 | 0x0200 | 0x0004 | 0x0400
        basic.ActiveProcessLimit = 1
        basic.PerJobUserTimeLimit = int(cpu_limit) * 10_000_000   # 100 ns units
        info.JobMemoryLimit = int(memory_limit)
        if not self.k.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.k.CloseHandle(self.job)
            raise OSError("Windows wouldn't set the exercise's limits, so it wasn't run.")
        self._info = info

    def hold(self, proc: subprocess.Popen[bytes]) -> None:
        handle = int(proc._handle)  # type: ignore[attr-defined]
        if not self.k.AssignProcessToJobObject(self.job, handle):
            raise OSError("Windows wouldn't put the exercise in its job, so it wasn't run.")
        if self.ntdll.NtResumeProcess(handle) != 0:
            raise OSError("The exercise's process couldn't be started.")

    def memory_hit(self) -> bool:
        info = _extended()()
        ok = self.k.QueryInformationJobObject(self.job, 9, self.ctypes.byref(info), self.ctypes.sizeof(info), None)
        return bool(ok) and info.PeakJobMemoryUsed >= self._info.JobMemoryLimit * 0.98

    def active(self) -> int:
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):  # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

        acc = Accounting()
        if not self.k.QueryInformationJobObject(self.job, 1, ctypes.byref(acc), ctypes.sizeof(acc), None):
            return 0
        return int(acc.ActiveProcesses)

    def close(self) -> None:
        """End everything in the job and wait until nothing in it runs, then close it."""
        if not self.job:
            return
        self.k.TerminateJobObject(self.job, 1)
        deadline = time.monotonic() + 10
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.k.CloseHandle(self.job)
        self.job = None


def _extended() -> Any:
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

    return Extended
