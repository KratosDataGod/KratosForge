"""ACD-005b: the learner process in a Windows AppContainer. The kernel, not the Python guards, keeps
the learner away from the person's files, the app's own folders, the network and their secrets.

The kernel tests start a bare Python in the AppContainer (no bootstrap, so no audit hook and no
wrappers) and expect every reach outside the exercise folder to fail."""

from __future__ import annotations

import socket
import sys
import uuid
from pathlib import Path

import pytest

from fabric_desktop.academy import appcontainer, sandbox

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="AppContainers are a Windows feature")


@pytest.fixture(autouse=True)
def appcontainer_available():
    if not appcontainer.status()["available"]:
        pytest.skip(f"AppContainers aren't available here: {appcontainer.status()['why_not']}")


def bare(tmp_path: Path, code: str) -> tuple[int, str]:
    """Run ``code`` in a bare Python in a fresh AppContainer, in its job. (exit code, stderr)."""
    work = tmp_path / f"w-{uuid.uuid4().hex[:6]}"
    work.mkdir()
    box = appcontainer.Box(work, sandbox.BOOTSTRAP)
    job = sandbox._WinJob(sandbox.MEMORY_LIMIT, sandbox.CPU_LIMIT, f"Local\\KFTest-{uuid.uuid4().hex}")
    try:
        proc = box.start([sys.executable, "-I", "-B", "-c", code], sandbox.environment(box.work))
        try:
            job.hold(proc)   # resumes it
            code_ = proc.wait(timeout=60)
            err = proc.stderr.read().decode("utf-8", "replace")
        finally:
            proc.close()
        return code_, err
    finally:
        job.close()
        box.close()


def denied(tmp_path: Path, code: str) -> str:
    rc, err = bare(tmp_path, code)
    assert rc != 0, f"expected the AppContainer to refuse this, but it ran: {code}"
    return err


def test_a_script_runs_in_its_appcontainer_and_its_files_come_home(tmp_path):
    work = tmp_path / "ws"
    (work / "data").mkdir(parents=True)
    (work / "data" / "in.txt").write_text("orders", encoding="utf-8")
    (work / "main.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text(Path('data/in.txt').read_text() + ' done')\n"
        "print('ran')\n", encoding="utf-8")
    got = sandbox.run(work, "main.py", tmp_path, time_limit=60)
    assert got["status"] == "ok", got["stderr"]
    assert got["runs_as"] == appcontainer.RUNS_AS
    assert "ran" in got["stdout"]
    assert (work / "out.txt").read_text(encoding="utf-8") == "orders done"


def test_the_profile_and_its_folder_are_gone_after_a_run(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    box = appcontainer.Box(work, sandbox.BOOTSTRAP)
    folder = box.profile.folder
    assert folder.is_dir() and (box.work / ".tmp").is_dir() and box.bootstrap.is_file()
    assert not box.bootstrap.is_relative_to(box.work)   # the bootstrap sits outside the workspace
    box.close()
    assert not folder.exists()


def test_files_outside_the_exercise_folder_are_refused(tmp_path):
    secret = tmp_path / "token-cache.bin"   # stands in for the token cache, the keys DB, ~/.ssh
    secret.write_text("secret", encoding="utf-8")
    err = denied(tmp_path, f"open({str(secret)!r}).read()")
    assert "PermissionError" in err


def test_the_apps_own_package_and_grading_are_refused(tmp_path):
    target = next(p for p in (sandbox.PACKAGE_DIR / "academy").glob("*.py"))
    err = denied(tmp_path, f"open({str(target)!r}, 'rb').read(1)")
    assert "PermissionError" in err
    err = denied(tmp_path, f"import os; os.listdir({str(sandbox.PACKAGE_DIR)!r})")
    assert "PermissionError" in err


def test_native_code_is_refused_too(tmp_path):
    secret = tmp_path / "keys.duckdb"
    secret.write_bytes(b"x" * 64)
    code = ("import ctypes; from ctypes import wintypes\n"
            "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "k.CreateFileW.restype = wintypes.HANDLE\n"
            "k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,"
            " wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]\n"
            f"h = k.CreateFileW({str(secret)!r}, 0x80000000, 1, None, 3, 0, None)\n"
            "raise SystemExit(0 if h not in (None, ctypes.c_void_p(-1).value) else 7)\n")
    rc, _err = bare(tmp_path, code)
    assert rc == 7


def test_writing_outside_the_exercise_folder_is_refused(tmp_path):
    err = denied(tmp_path, f"open({str(tmp_path / 'planted.txt')!r}, 'w').write('x')")
    assert "PermissionError" in err
    assert not (tmp_path / "planted.txt").exists()


def test_the_network_is_refused_even_on_loopback(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        denied(tmp_path, f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=5)")
        denied(tmp_path, "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)")
    finally:
        server.close()


def test_a_named_pipe_of_the_engine_is_refused(tmp_path):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k.CreateNamedPipeW.restype = wintypes.HANDLE
    k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    name = rf"\\.\pipe\kf-test-{uuid.uuid4().hex}"
    pipe = k.CreateNamedPipeW(name, 3, 0, 1, 4096, 4096, 0, None)   # PIPE_ACCESS_DUPLEX, default DACL
    assert pipe and pipe != ctypes.c_void_p(-1).value
    try:
        denied(tmp_path, f"open({name!r}, 'r+b')")
    finally:
        k.CloseHandle(pipe)


def test_the_persons_saved_credentials_are_refused(tmp_path):
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", ctypes.c_uint64),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.c_void_p),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    target = f"KratosForgeAcademyTest-{uuid.uuid4().hex}"
    blob = ctypes.create_string_buffer(b"secret")
    cred = Credential(0, 1, target, None, 0, 6, ctypes.cast(blob, ctypes.c_void_p), 1, 0, None, None, "kf")
    assert adv.CredWriteW(ctypes.byref(cred), 0)   # CRED_TYPE_GENERIC, CRED_PERSIST_SESSION
    try:
        code = ("import ctypes\n"
                "adv = ctypes.WinDLL('advapi32', use_last_error=True)\n"
                "p = ctypes.c_void_p()\n"
                f"raise SystemExit(7 if adv.CredReadW({target!r}, 1, 0, ctypes.byref(p)) else 0)\n")
        rc, _err = bare(tmp_path, code)
        assert rc == 0, "the AppContainer read the person's saved credential"
    finally:
        adv.CredDeleteW(target, 1, 0)


def test_the_persons_dpapi_secrets_are_refused(tmp_path):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    data = ctypes.create_string_buffer(b"refresh-token")
    plain, sealed = Blob(len(data.raw), ctypes.cast(data, ctypes.POINTER(ctypes.c_char))), Blob()
    assert crypt.CryptProtectData(ctypes.byref(plain), None, None, None, None, 0, ctypes.byref(sealed))
    secret = ctypes.string_at(sealed.pbData, sealed.cbData)
    ctypes.windll.kernel32.LocalFree(sealed.pbData)  # type: ignore[attr-defined]
    code = ("import ctypes; from ctypes import wintypes\n"
            "class Blob(ctypes.Structure):\n"
            "    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]\n"
            f"raw = ctypes.create_string_buffer({secret!r}, {len(secret)})\n"
            f"inp, out = Blob({len(secret)}, ctypes.cast(raw, ctypes.POINTER(ctypes.c_char))), Blob()\n"
            "ok = ctypes.WinDLL('crypt32').CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0,"
            " ctypes.byref(out))\n"
            "raise SystemExit(7 if ok else 0)\n")
    rc, _err = bare(tmp_path, code)
    assert rc == 0, "the AppContainer unsealed a secret the engine sealed with DPAPI"


def test_the_bootstrap_refuses_a_token_that_isnt_an_appcontainers(tmp_path, monkeypatch):
    """A v1 learner process whose settings say AppContainer stops before the script runs: an
    AppContainer run never falls back to finding its job by name, and checks its own token."""
    import json
    import types
    work = tmp_path / "w"
    work.mkdir()
    (work / "main.py").write_text("print('should not run')\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, "_box", lambda work, libs: None)
    for handle in (0, None):
        said = types.SimpleNamespace(dumps=lambda cfg, h=handle: json.dumps({**cfg, "appcontainer": True,
                                                                              "job_handle": h}))
        monkeypatch.setattr(sandbox, "json", said)
        got = sandbox.run(work, "main.py", tmp_path, time_limit=30)
        assert got["status"] == "refused" and "should not run" not in got["stdout"], got


def test_a_pc_that_refuses_appcontainers_falls_back_and_says_so(tmp_path, monkeypatch):
    def refuse(api):
        raise OSError("Windows wouldn't make an AppContainer for the exercise (0x80070005).")
    monkeypatch.setattr(appcontainer, "Profile", refuse)
    monkeypatch.setattr(appcontainer, "_why_not", "")
    work = tmp_path / "w"
    work.mkdir()
    (work / "main.py").write_text("print('ran')\n", encoding="utf-8")
    got = sandbox.run(work, "main.py", tmp_path, time_limit=60)
    assert got["status"] == "ok" and got["runs_as"] == sandbox.RUNS_AS
    assert "0x80070005" in appcontainer.status()["why_not"] and not appcontainer.status()["available"]
