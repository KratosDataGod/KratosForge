"""The learner process in a Windows AppContainer (ACD-005b): the kernel-enforced layer.

An AppContainer process runs with a lowbox token: its own package SID, no capabilities except the one
this module names, and file access only where an ACL grants that SID, that capability or ALL
APPLICATION PACKAGES. Native code gets no more than Python does, and with no network capability the
kernel refuses sockets.

Each run gets a profile of its own (``KratosForge.Academy.<random>``), so two runs never share a SID
or a folder. The run's workspace is the profile's own folder (``GetAppContainerFolderPath``), which
Windows already grants to that SID, so no ACL of the person's own folders is changed. The engine
copies the workspace in before the run and back after the Job has ended, refusing links both ways.
Deleting the profile afterwards removes its folder.

The Python runtime is granted read and execute once, to a capability SID
(``KratosForgeAcademyRuntime``) that every run's token carries. The grant covers the interpreter,
its standard library and only the learner packages (``LEARNER_PACKAGES`` and what they need, found
from their installed metadata), never the engine's own package: ``grading/`` is never readable.

The bootstrap reads its own token and says whether it is in an AppContainer. The engine claims
"sandboxed" only when the child says yes. If any step here fails (an old Windows, a policy that
blocks AppContainers, a runtime folder whose ACL can't be changed), the run falls back to the v1
learner process and the v1 wording, and ``status()`` says why.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from . import workspace as ws

PROFILE_PREFIX = "KratosForge.Academy."
CAPABILITY = "KratosForgeAcademyRuntime"
# What learner scripts may import, with everything they need (found from installed metadata).
LEARNER_PACKAGES = ("polars", "pyarrow", "deltalake", "duckdb", "pandas", "numpy", "sqlglot")
NEVER = re.compile(r"^(fabric[_-]desktop|msal|azure|keyring|win32|pywin32|mssql|mcp|pymsalruntime)", re.I)
# ALL APPLICATION PACKAGES and ALL RESTRICTED APPLICATION PACKAGES: every AppContainer token matches one.
APP_PACKAGES = ("S-1-15-2-1", "S-1-15-2-2")
ENGINE_PACKAGE = Path(__file__).resolve().parent.parent   # fabric_desktop, grading/ included
MAX_COPY_FILES = 2_000
MAX_COPY_BYTES = 1024 * 1024 * 1024

RUNS_AS = "an AppContainer on this PC: no network and no access to your own files, with time and memory limits"

_lock = threading.Lock()
_open_boxes = 0   # runs in an AppContainer now (Box made and not yet closed); guarded by _lock
_granted: dict[str, bool] = {}
_denied: set[str] = set()
_why_not = ""


def status() -> dict[str, Any]:
    """Whether runs can use an AppContainer on this PC, and if not, why (for Diagnostics)."""
    return {"available": sys.platform == "win32" and not _why_not, "why_not": _why_not or
            ("" if sys.platform == "win32" else "AppContainers are a Windows feature.")}


def _fail(why: str) -> None:
    global _why_not
    _why_not = why


class SetupFailed(OSError):
    """The AppContainer couldn't be set up before the learner's script started: run it the v1 way."""


# ---- Windows API ---------------------------------------------------------------------------------

def _api() -> Any:
    import ctypes
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class SecurityCapabilities(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.POINTER(SidAndAttributes)),
                    ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    env = ctypes.WinDLL("userenv", use_last_error=True)  # type: ignore[attr-defined]
    ole = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
    kb = ctypes.WinDLL("kernelbase", use_last_error=True)  # type: ignore[attr-defined]
    P, H, D, B = ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    env.CreateAppContainerProfile.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, P, D,
                                              ctypes.POINTER(P)]
    env.CreateAppContainerProfile.restype = ctypes.c_long
    env.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    env.DeleteAppContainerProfile.restype = ctypes.c_long
    env.GetAppContainerFolderPath.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)]
    env.GetAppContainerFolderPath.restype = ctypes.c_long
    adv.ConvertSidToStringSidW.argtypes = [P, ctypes.POINTER(wintypes.LPWSTR)]
    adv.FreeSid.argtypes = [P]
    kb.DeriveCapabilitySidsFromName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.POINTER(P)),
                                                ctypes.POINTER(D), ctypes.POINTER(ctypes.POINTER(P)),
                                                ctypes.POINTER(D)]
    k.InitializeProcThreadAttributeList.argtypes = [P, D, D, ctypes.POINTER(ctypes.c_size_t)]
    k.UpdateProcThreadAttribute.argtypes = [P, D, ctypes.c_size_t, P, ctypes.c_size_t, P, P]
    k.DeleteProcThreadAttributeList.argtypes = [P]
    k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, P, P, B, D, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                 P, ctypes.POINTER(ProcessInformation)]
    k.CreatePipe.argtypes = [ctypes.POINTER(H), ctypes.POINTER(H), P, D]
    k.SetHandleInformation.argtypes = [H, D, D]
    k.CloseHandle.argtypes = [H]
    k.GetCurrentProcess.restype = H
    k.DuplicateHandle.argtypes = [H, H, H, ctypes.POINTER(H), D, B, D]
    k.LocalFree.argtypes = [P]
    ole.CoTaskMemFree.argtypes = [P]
    return type("Api", (), {"ctypes": ctypes, "wt": wintypes, "k": k, "adv": adv, "env": env, "ole": ole, "kb": kb,
                            "SidAndAttributes": SidAndAttributes, "SecurityCapabilities": SecurityCapabilities,
                            "StartupInfoEx": StartupInfoEx, "ProcessInformation": ProcessInformation,
                            "SecurityAttributes": SecurityAttributes})


def _sid_string(api: Any, sid: Any) -> str:
    out = api.wt.LPWSTR()
    if not api.adv.ConvertSidToStringSidW(sid, api.ctypes.byref(out)):
        raise OSError("Windows wouldn't name the exercise's AppContainer.")
    try:
        return str(out.value)
    finally:
        api.k.LocalFree(api.ctypes.cast(out, api.ctypes.c_void_p))


def capability_sid(api: Any) -> tuple[Any, str]:
    """The runtime capability's SID (kept alive for the process's lifetime) and its string form."""
    ctypes = api.ctypes
    groups, gcount = ctypes.POINTER(ctypes.c_void_p)(), api.wt.DWORD()
    sids, scount = ctypes.POINTER(ctypes.c_void_p)(), api.wt.DWORD()
    if not api.kb.DeriveCapabilitySidsFromName(CAPABILITY, ctypes.byref(groups), ctypes.byref(gcount),
                                               ctypes.byref(sids), ctypes.byref(scount)) or not scount.value:
        raise OSError("Windows wouldn't make the exercise runtime's capability.")
    sid = sids[0]
    return sid, _sid_string(api, sid)


class Profile:
    """One run's AppContainer profile: its SID, its folder, deleted on close."""

    def __init__(self, api: Any):
        self.api, self.name = api, PROFILE_PREFIX + uuid.uuid4().hex[:24]
        sid = api.ctypes.c_void_p()
        hr = api.env.CreateAppContainerProfile(self.name, "KratosForge Academy exercise",
                                               "Runs one Academy exercise script", None, 0, api.ctypes.byref(sid))
        if hr != 0:
            raise OSError(f"Windows wouldn't make an AppContainer for the exercise (0x{hr & 0xFFFFFFFF:08X}).")
        self.sid = sid
        self.sid_string = _sid_string(api, sid)
        path = api.wt.LPWSTR()
        hr = api.env.GetAppContainerFolderPath(self.sid_string, api.ctypes.byref(path))
        if hr != 0:
            self.close()
            raise OSError("Windows didn't give the exercise's AppContainer a folder.")
        try:
            self.folder = Path(str(path.value))
        finally:
            api.ole.CoTaskMemFree(api.ctypes.cast(path, api.ctypes.c_void_p))
        self.folder.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        if self.sid:
            self.api.env.DeleteAppContainerProfile(self.name)   # removes its folder too
            self.api.adv.FreeSid(self.sid)
            self.sid = None


# ---- the runtime grant --------------------------------------------------------------------------

def _distributions() -> dict[str, Any]:
    from importlib import metadata
    out: dict[str, Any] = {}
    for d in metadata.distributions():
        name = (d.metadata.get("Name") or "").lower().replace("_", "-")
        if name:
            out.setdefault(name, d)
    return out


def learner_paths(site: Path) -> list[Path]:
    """The top-level files and folders in ``site`` that the learner packages and what they need
    installed: from each distribution's RECORD, never a name NEVER matches."""
    import re as _re
    dists = _distributions()
    todo = [p.lower() for p in LEARNER_PACKAGES]
    seen: set[str] = set()
    tops: set[str] = set()
    while todo:
        name = todo.pop().replace("_", "-")
        d = dists.get(name)
        if name in seen or d is None:
            continue
        seen.add(name)
        for f in d.files or []:
            top = Path(str(f)).parts[0] if Path(str(f)).parts else ""
            if top and top != ".." and not NEVER.match(top):
                tops.add(top)
        for req in d.requires or []:
            if "extra ==" in req:
                continue
            m = _re.match(r"[A-Za-z0-9_.-]+", req)
            if m:
                todo.append(m.group(0).lower())
    return sorted(site / t for t in tops if (site / t).exists())


def runtime_paths() -> list[tuple[Path, bool]]:
    """What a learner process must read to start Python and import the learner packages, as
    ``(path, inherit)``: the interpreter's folder and files, its standard library, and the learner
    packages, but never a site-packages folder whole."""
    import sysconfig
    out: list[tuple[Path, bool]] = []
    base = Path(sys.base_prefix)
    sites = {Path(p).resolve() for p in (sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"])}
    sites |= {Path(p).resolve() for p in sys.path if p and p.endswith("site-packages") and os.path.isdir(p)}
    out.append((base, False))
    for entry in base.iterdir():
        if entry.is_file():
            out.append((entry, False))
        elif entry.name.lower() in ("dlls",):
            out.append((entry, True))
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    if stdlib.is_dir():
        out.append((stdlib, False))
        for entry in stdlib.iterdir():
            if entry.resolve() not in sites:
                out.append((entry, entry.is_dir()))
    for site in sorted(sites):
        out.append((site, False))   # its listing only, so imports can find the granted packages
        out += [(p, p.is_dir()) for p in learner_paths(site)]
    if Path(sys.prefix) != base:   # a virtual environment: pyvenv.cfg and its Scripts folder
        out += [(Path(sys.prefix), False), *[(p, False) for p in Path(sys.prefix).iterdir() if p.is_file()]]
    return out


GRANTS_FILE = "appcontainer-grants.json"   # in <data>/academy: what this app granted, to check and remove


def _icacls(*args: str) -> Any:
    import subprocess
    # No /C: icacls stops at the first file it can't change and says so in its exit status. Its
    # output is read only for the SID check below.
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")   # never by bare name
    return subprocess.run([exe, *args, "/Q"], capture_output=True, timeout=60, check=False)


def _grant(api: Any, path: Path, sid_string: str, inherit: bool) -> None:
    """Add read and execute for ``sid_string`` to ``path``'s DACL (merged with what is there)."""
    flags = "(OI)(CI)(RX)" if inherit and path.is_dir() else "(RX)"
    if _icacls(str(path), "/grant", f"*{sid_string}:{flags}").returncode != 0:
        raise OSError(f"Windows wouldn't let the exercise runtime read {path.name}.")


def _has_grant(path: Path, sid_string: str) -> bool:
    got = _icacls(str(path))
    return got.returncode == 0 and sid_string.encode("ascii") in got.stdout


def deny_app_packages(paths: list[Path]) -> None:
    """Deny every AppContainer read and execute on ``paths`` and all they hold. Folders that grant ALL
    APPLICATION PACKAGES (Program Files does, and some Python installs) are readable inside any
    AppContainer, and an explicit deny outranks an allow there or inherited from above. Done once per
    engine start, since an engine update brings in new folders. These ACEs stay: they only ever keep
    AppContainers out of the app's own package."""
    for path in paths:
        key = str(path)
        if key in _denied:
            continue
        for sid in APP_PACKAGES:
            if _icacls(key, "/deny", f"*{sid}:(OI)(CI)(RX)").returncode != 0:
                raise OSError(f"Windows wouldn't let the app keep exercises out of {path.name}.")
        _denied.add(key)


def _grants_file(data_root: Path) -> Path:
    return Path(data_root) / "academy" / GRANTS_FILE


def grant_runtime(api: Any, cap_string: str, lib_roots: list[Path] | None = None,
                  data_root: Path | None = None) -> None:
    """Grant the runtime capability read and execute on the runtime. The grants are persistent ACEs
    on the person's Python install, so they're recorded in ``<data>/academy/appcontainer-grants.json``
    and made again only when that record or the interpreter's own ACE is missing (Python reinstalled,
    say). ``revoke_runtime`` removes them."""
    from .. import jsonfile
    paths = [(p, inherit) for p, inherit in runtime_paths() if not NEVER.match(p.name)]
    paths += [(Path(lib), True) for lib in lib_roots or []]
    wanted = sorted({str(p) for p, _ in paths})
    key = cap_string + "|" + "|".join(wanted)
    record = _grants_file(data_root) if data_root else None
    if _granted.get(key) and (record is None or record.exists()):
        return
    had = jsonfile.read_json(record, {}) if record else {}
    done = set(had.get("paths") or []) if had.get("sid") == cap_string else set()
    if not set(wanted) <= done or not _has_grant(Path(sys.base_prefix), cap_string):
        for path, inherit in paths:
            _grant(api, path, cap_string, inherit)
        # Paths gone since (an engine's temporary kf_learner folder, say) took their ACE with them.
        done = {p for p in done | set(wanted) if os.path.exists(p)}
        if record:
            record.parent.mkdir(parents=True, exist_ok=True)
            jsonfile.write_json(record, {"sid": cap_string, "capability": CAPABILITY, "paths": sorted(done)})
    _granted[key] = True


def _plain_path(p: Any) -> bool:
    """An absolute path icacls can't read as an option (the record is the person's to write)."""
    return isinstance(p, str) and os.path.isabs(p) and not p.startswith(("/", "-")) and "\0" not in p


def granted_count(data_root: Path) -> int:
    """How many folders and files the record says hold the runtime grant and still exist (read only)."""
    from .. import jsonfile
    had = jsonfile.read_json(_grants_file(data_root), {})
    listed: list[Any] = had["paths"] if isinstance(had, dict) and isinstance(had.get("paths"), list) else []
    return len([p for p in {p for p in listed if _plain_path(p)} if os.path.exists(p)])


def revoke_runtime(data_root: Path) -> dict[str, Any]:
    """Remove the runtime capability's ACE from the paths it was granted on, then the record.
    ``{removed, failed}``: paths icacls couldn't change are listed, not hidden.

    The record sits in the data folder, which anything running as the person can write, so its SID
    is never used: only the SID derived from CAPABILITY is removed, and removing that ACE from any
    path is harmless. Record paths are used only when absolute and plain."""
    from .. import jsonfile
    if sys.platform != "win32":
        return {"removed": 0, "failed": []}
    record = _grants_file(data_root)
    had = jsonfile.read_json(record, {})
    try:
        _cap, sid = capability_sid(_api())
    except OSError:
        return {"removed": 0, "failed": [], "why": "Windows wouldn't name the exercise runtime's capability."}
    listed = had.get("paths") if isinstance(had, dict) and isinstance(had.get("paths"), list) else []
    paths = {p for p in listed if _plain_path(p)} | {str(p) for p, _ in runtime_paths()}
    removed, failed = 0, []
    # Under the setup lock, so a run starting now grants after this, not half-way through it. A run
    # already going would lose its imports, so nothing is removed while one is.
    with _lock:
        if _open_boxes:
            return {"removed": 0, "failed": [], "why": "An exercise is running. Try again when it has finished."}
        for p in sorted(paths):
            if not os.path.exists(p):
                continue
            if _icacls(p, "/remove:g", f"*{sid}").returncode == 0:
                removed += 1
            else:
                failed.append(p)
        if not failed and record.exists():
            record.unlink()
        _granted.clear()
    return {"removed": removed, "failed": failed}


# ---- copying the workspace in and out ------------------------------------------------------------

def copy_tree(src: Path, dst: Path) -> None:
    """Copy the files under ``src`` into ``dst`` (empty), never following a link, within the caps."""
    names = ws.files_under(src, ".")
    if len(names) > MAX_COPY_FILES:
        raise ws.Refused(f"The exercise folder holds more than {MAX_COPY_FILES:,} files.")
    total = 0
    for rel in names:
        source = ws.inside(src, rel)
        total += source.stat().st_size
        if total > MAX_COPY_BYTES:
            raise ws.Refused("The exercise folder holds more than 1 GB.")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)


def mirror_back(src: Path, dst: Path) -> None:
    """Make ``dst`` hold what ``src`` (the run's folder, after its Job has ended) holds. It's copied
    into a new folder beside ``dst`` first and swapped in only once the whole copy is within the
    caps, so a refused copy leaves ``dst`` as it was."""
    ws.inside(dst, ".")   # the engine's own copy is a plain folder
    incoming = dst.with_name(f".{dst.name}.incoming-{uuid.uuid4().hex[:8]}")
    incoming.mkdir()
    try:
        copy_tree(src, incoming)
    except BaseException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    from .. import jsonfile   # waits out a virus scanner or indexer holding a file open
    old = dst.with_name(f".{dst.name}.old-{uuid.uuid4().hex[:8]}")
    try:
        jsonfile.replace(dst, old)
    except BaseException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    try:
        jsonfile.replace(incoming, dst)
    except BaseException:
        jsonfile.replace(old, dst)   # put the learner's folder back as it was
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


# ---- starting the process ------------------------------------------------------------------------

class LowboxProcess:
    """A suspended AppContainer process with pipes for stdout and stderr, shaped like the parts of
    ``subprocess.Popen`` that sandbox.run uses."""

    def __init__(self, api: Any, argv: list[str], cwd: Path, env: dict[str, str], profile: Profile,
                 cap_sid: Any, inherit: list[int]):
        import msvcrt
        import subprocess
        ctypes, wt, k = api.ctypes, api.wt, api.k
        self.api = api
        sa = api.SecurityAttributes(ctypes.sizeof(api.SecurityAttributes), None, True)
        pipes = []
        for _ in range(2):
            r, w = wt.HANDLE(), wt.HANDLE()
            if not k.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
                raise SetupFailed("Windows wouldn't make the exercise's output pipe.")
            k.SetHandleInformation(r, 1, 0)   # the read end stays with the engine
            pipes.append((r, w))
        (out_r, out_w), (err_r, err_w) = pipes
        handles = (wt.HANDLE * (2 + len(inherit)))(out_w, err_w, *inherit)
        caps = (api.SidAndAttributes * 1)(api.SidAndAttributes(cap_sid, 0x00000004))   # SE_GROUP_ENABLED
        sc = api.SecurityCapabilities(profile.sid, caps, 1, 0)
        size = ctypes.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(buf, 2, 0, ctypes.byref(size)):
            raise SetupFailed("Windows wouldn't prepare the exercise's process.")
        try:
            if not k.UpdateProcThreadAttribute(buf, 0, 0x00020009, ctypes.byref(sc), ctypes.sizeof(sc), None, None):
                raise SetupFailed("Windows wouldn't give the exercise its AppContainer.")   # SECURITY_CAPABILITIES
            if not k.UpdateProcThreadAttribute(buf, 0, 0x00020002, handles, ctypes.sizeof(handles), None, None):
                raise SetupFailed("Windows wouldn't limit the exercise's handles.")      # HANDLE_LIST
            si = api.StartupInfoEx()
            si.StartupInfo.cb = ctypes.sizeof(api.StartupInfoEx)
            si.StartupInfo.dwFlags = 0x00000100   # STARTF_USESTDHANDLES
            si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = out_w, err_w
            si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
            block = "".join(f"{key}={value}\0" for key, value in sorted(env.items())) + "\0"
            cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            pi = api.ProcessInformation()
            flags = 0x00080000 | 0x00000004 | 0x08000000 | 0x00000400   # EXTENDED_STARTUPINFO | SUSPENDED | NO_WINDOW | UNICODE_ENV
            if not k.CreateProcessW(argv[0], cmd, None, None, True, flags, block, str(cwd),
                                    ctypes.byref(si), ctypes.byref(pi)):
                raise SetupFailed(f"Windows wouldn't start the exercise in its AppContainer ({ctypes.get_last_error()}).")
        finally:
            k.DeleteProcThreadAttributeList(buf)
            k.CloseHandle(out_w)
            k.CloseHandle(err_w)
        k.CloseHandle(pi.hThread)
        self._handle, self.pid = int(pi.hProcess), int(pi.dwProcessId)
        self.stdout = os.fdopen(msvcrt.open_osfhandle(int(out_r.value), os.O_RDONLY), "rb")  # type: ignore[attr-defined,unused-ignore]
        self.stderr = os.fdopen(msvcrt.open_osfhandle(int(err_r.value), os.O_RDONLY), "rb")  # type: ignore[attr-defined,unused-ignore]
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        import subprocess
        k, wt = self.api.k, self.api.wt
        k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        ms = 0xFFFFFFFF if timeout is None else int(max(0.0, timeout) * 1000)
        if k.WaitForSingleObject(self._handle, ms) != 0:
            raise subprocess.TimeoutExpired("academy learner process", timeout or 0)
        code = wt.DWORD()
        k.GetExitCodeProcess.argtypes = [wt.HANDLE, self.api.ctypes.POINTER(wt.DWORD)]
        k.GetExitCodeProcess(self._handle, self.api.ctypes.byref(code))
        self.returncode = int(code.value)
        return self.returncode

    def kill(self) -> None:
        self.api.k.TerminateProcess.argtypes = [self.api.wt.HANDLE, self.api.wt.UINT]
        self.api.k.TerminateProcess(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self.api.k.CloseHandle(self._handle)
            self._handle = 0


def query_handle(api: Any, job: Any) -> int:
    """An inheritable JOB_OBJECT_QUERY handle to ``job`` for the bootstrap's own check."""
    wt, k = api.wt, api.k
    out = wt.HANDLE()
    if not k.DuplicateHandle(k.GetCurrentProcess(), job, k.GetCurrentProcess(), api.ctypes.byref(out), 0x0004, True, 0):
        raise OSError("Windows wouldn't share the exercise's job with it.")
    return int(out.value)


def lock() -> threading.Lock:
    """Held around a run's AppContainer setup, so runtime grants happen once at a time."""
    return _lock


# ---- profiles a crash left behind ---------------------------------------------------------------

MAPPINGS = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Mappings"
ORPHAN_AGE = 600.0   # seconds; a run is over in far less, so an older profile of ours is left over
_swept = False


def sweep(api: Any) -> int:
    """Delete this app's AppContainer profiles left by a crash (named PROFILE_PREFIX..., folder
    untouched for ORPHAN_AGE), once per engine start. The number deleted."""
    import time
    import winreg  # type: ignore[import-not-found,unused-ignore]
    global _swept
    if _swept:
        return 0
    _swept = True
    gone = 0
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MAPPINGS)  # type: ignore[attr-defined,unused-ignore]
    except OSError:
        return 0
    with root:
        sids, i = [], 0
        while True:
            try:
                sids.append(winreg.EnumKey(root, i))  # type: ignore[attr-defined,unused-ignore]
            except OSError:
                break
            i += 1
        for sid in sids:
            try:
                with winreg.OpenKey(root, sid) as key:  # type: ignore[attr-defined,unused-ignore]
                    name = str(winreg.QueryValueEx(key, "Moniker")[0])  # type: ignore[attr-defined,unused-ignore]
            except OSError:
                continue
            if not name.lower().startswith(PROFILE_PREFIX.lower()):   # Windows keeps monikers in lower case
                continue
            path = api.wt.LPWSTR()
            if api.env.GetAppContainerFolderPath(sid, api.ctypes.byref(path)) == 0:
                try:
                    folder = Path(str(path.value))
                finally:
                    api.ole.CoTaskMemFree(api.ctypes.cast(path, api.ctypes.c_void_p))
                try:
                    if time.time() - folder.stat().st_mtime < ORPHAN_AGE:
                        continue   # perhaps another app window's run that is still going
                except OSError:
                    pass   # no folder left: nothing in use
            if api.env.DeleteAppContainerProfile(name) == 0:
                gone += 1
    return gone


# ---- one run ------------------------------------------------------------------------------------

class Box:
    """One run's AppContainer: the runtime grant (once), a profile of its own, the workspace copied
    into the profile's folder and the bootstrap beside it (outside the workspace). Close it after
    the Job has ended and ``mirror_back`` has copied the workspace home; that deletes the profile."""

    def __init__(self, work: Path, bootstrap: Path, lib_roots: list[str] | None = None,
                 data_root: Path | None = None):
        global _open_boxes
        self._counted = False
        try:
            self.api = _api()
            with _lock:
                sweep(self.api)
                deny_app_packages([ENGINE_PACKAGE])   # before any run: grading/ is never readable
                self.cap_sid, cap = capability_sid(self.api)
                grant_runtime(self.api, cap, [Path(p) for p in lib_roots or []], data_root)
                # Counted in the same hold as the grant, so a removal can't land in between.
                _open_boxes += 1
                self._counted = True
            self.profile = Profile(self.api)
        except OSError as exc:
            self._uncount()
            raise SetupFailed(str(exc)) from exc
        except BaseException:
            self._uncount()
            raise
        self._shared = 0
        try:
            self.work = self.profile.folder / "work"
            self.work.mkdir()
            copy_tree(Path(work), self.work)
            (self.work / ".tmp").mkdir(exist_ok=True)
            boot = self.profile.folder / "boot"
            boot.mkdir()
            self.bootstrap = boot / "_bootstrap.py"
            shutil.copyfile(bootstrap, self.bootstrap)
        except BaseException:
            self.profile.close()
            self._uncount()
            raise

    def _uncount(self) -> None:
        global _open_boxes
        if self._counted:
            self._counted = False
            with _lock:
                _open_boxes -= 1

    def share(self, job: Any) -> int:
        """An inheritable QUERY-only handle to the run's job, for the bootstrap's own check (an
        AppContainer has its own object namespace, so it can't open the job by name)."""
        self._shared = query_handle(self.api, job)
        return self._shared

    def start(self, argv: list[str], env: dict[str, str]) -> LowboxProcess:
        """The suspended learner process; it inherits its two pipes and the job handle, nothing else."""
        try:
            return LowboxProcess(self.api, argv, self.work, env, self.profile, self.cap_sid,
                                 [self._shared] if self._shared else [])
        finally:
            self._unshare()

    def _unshare(self) -> None:
        if self._shared:
            self.api.k.CloseHandle(self._shared)
            self._shared = 0

    def close(self) -> None:
        self._unshare()
        self.profile.close()
        self._uncount()
