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
def appcontainer_available(monkeypatch):
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", True)   # off by default for now
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


def bare_out(tmp_path: Path, code: str) -> tuple[int, str, str]:
    """As ``bare``, also returning stdout."""
    work = tmp_path / f"w-{uuid.uuid4().hex[:6]}"
    work.mkdir()
    box = appcontainer.Box(work, sandbox.BOOTSTRAP)
    job = sandbox._WinJob(sandbox.MEMORY_LIMIT, sandbox.CPU_LIMIT, f"Local\\KFTest-{uuid.uuid4().hex}")
    try:
        proc = box.start([sys.executable, "-I", "-B", "-c", code], sandbox.environment(box.work))
        try:
            job.hold(proc)
            rc = proc.wait(timeout=60)
            out = proc.stdout.read().decode("utf-8", "replace")
            err = proc.stderr.read().decode("utf-8", "replace")
        finally:
            proc.close()
        return rc, err, out
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


def test_dpapi_still_unseals_so_sealed_files_must_stay_unreadable(tmp_path):
    """Found on Windows CI: an AppContainer process can unseal a blob the person's DPAPI key sealed
    (DPAPI keys follow the user, not the token's package). What keeps the token cache safe is that the
    AppContainer can't read the sealed file at all (the token cache test). This test pins that
    behaviour, so a Windows change in either direction is noticed."""
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
    assert rc == 7, "Windows no longer lets an AppContainer unseal the person's DPAPI blobs; update the docs"


def test_the_bootstrap_refuses_a_token_that_isnt_an_appcontainers(tmp_path, monkeypatch):
    """A v1 learner process whose settings say AppContainer stops before the script runs: an
    AppContainer run never falls back to finding its job by name, and checks its own token."""
    import json
    import types
    work = tmp_path / "w"
    work.mkdir()
    (work / "main.py").write_text("print('should not run')\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, "_box", lambda work, libs, data_root: None)
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


def test_the_app_and_sign_in_packages_cant_be_imported_at_the_kernel_layer(tmp_path):
    parent = str(sandbox.PACKAGE_DIR.parent)
    err = denied(tmp_path, f"import sys; sys.path.insert(0, {parent!r}); import fabric_desktop.academy.grading")
    assert "PermissionError" in err or "ModuleNotFoundError" in err or "ImportError" in err
    try:
        import msal
    except ImportError:
        return
    folder = Path(msal.__file__).resolve().parent
    site = str(folder.parent)
    probe = (f"import sys, json; sys.path.insert(0, {site!r})\n"
             "out = {'path': sys.path, 'flags': str(sys.flags)}\n"
             f"try:\n    out['open'] = len(open({str(folder / '__init__.py')!r}, 'rb').read(64))\n"
             "except OSError as e:\n    out['open'] = repr(e)\n"
             "try:\n    import msal\n    out['file'] = msal.__file__\n"
             "except Exception as e:\n    out['import'] = repr(e)\n"
             "print(json.dumps(out))\n"
             "raise SystemExit(0 if 'file' in out else 9)\n")
    rc, err, out = bare_out(tmp_path, probe)
    assert rc in (0, 9), f"the probe itself didn't run (exit {rc}): {err}"
    if '"file"' in out:
        # It imported: say exactly from where and what the ACLs allow, all the way up.
        acls = []
        for p in [folder / "__init__.py", folder, *folder.parents]:
            acls.append(f"--- {p}\n" + appcontainer._icacls(str(p)).stdout.decode("ascii", "replace"))
        granted = [str(p) for p, _ in appcontainer.runtime_paths()]
        pytest.fail(f"msal imported inside the AppContainer.\nchild: {out}\nstderr: {err}\n"
                    f"our grant paths: {granted}\nACLs:\n" + "\n".join(acls))


def test_the_token_cache_in_the_data_folder_is_refused(tmp_path):
    cache = tmp_path / "data" / "msal_token_cache.bin"   # where the app keeps it: <data>/
    cache.parent.mkdir()
    cache.write_bytes(b"cache")
    err = denied(tmp_path, f"open({str(cache)!r}, 'rb').read()")
    assert "PermissionError" in err


def test_grants_are_recorded_checked_and_removed(tmp_path):
    from fabric_desktop import jsonfile
    work = tmp_path / "w"
    work.mkdir()
    appcontainer.Box(work, sandbox.BOOTSTRAP, data_root=tmp_path).close()
    record = jsonfile.read_json(tmp_path / "academy" / appcontainer.GRANTS_FILE, {})
    assert record["capability"] == appcontainer.CAPABILITY and record["paths"]
    assert not any(appcontainer.NEVER.match(Path(p).name) for p in record["paths"])
    assert appcontainer._has_grant(Path(sys.base_prefix), record["sid"])
    got = appcontainer.revoke_runtime(tmp_path)
    assert got["failed"] == [] and got["removed"] >= 1
    assert not appcontainer._has_grant(Path(sys.base_prefix), record["sid"])
    assert not (tmp_path / "academy" / appcontainer.GRANTS_FILE).exists()
    # The next run grants them again.
    got = sandbox.run(_script(tmp_path / "again"), "main.py", tmp_path, time_limit=60)
    assert got["status"] == "ok" and got["runs_as"] == appcontainer.RUNS_AS, got["stderr"]


def _script(work: Path) -> Path:
    work.mkdir()
    (work / "main.py").write_text("print('ran')\n", encoding="utf-8")
    return work


def test_files_come_home_only_from_an_empty_job(tmp_path, monkeypatch):
    real_close = sandbox._WinJob.close

    def close_but_not_empty(self):
        real_close(self)
        self.emptied = False
    monkeypatch.setattr(sandbox._WinJob, "close", close_but_not_empty)
    work = tmp_path / "w"
    work.mkdir()
    (work / "kept.txt").write_text("before", encoding="utf-8")
    (work / "main.py").write_text("open('kept.txt', 'w').write('after')\n", encoding="utf-8")
    got = sandbox.run(work, "main.py", tmp_path, time_limit=60)
    assert got["status"] == "error" and "weren't copied back" in got["stderr"]
    assert (work / "kept.txt").read_text(encoding="utf-8") == "before"


def test_a_refused_copy_back_leaves_the_workspace_as_it_was(tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    (work / "kept.txt").write_text("before", encoding="utf-8")
    (work / "main.py").write_text("import os\nfor i in range(30): open(f'f{i}.txt', 'w').write('x')\n",
                                  encoding="utf-8")
    real_copy = appcontainer.copy_tree
    calls = []

    def copy(src, dst):
        calls.append(dst)
        if len(calls) > 1:   # the copy home, not the copy in
            raise appcontainer.ws.Refused("The exercise folder holds more than 2,000 files.")
        real_copy(src, dst)
    monkeypatch.setattr(appcontainer, "copy_tree", copy)
    with pytest.raises(appcontainer.ws.Refused):
        sandbox.run(work, "main.py", tmp_path, time_limit=60)
    assert sorted(p.name for p in work.iterdir()) == [".tmp", "kept.txt", "main.py"]
    assert not [p for p in tmp_path.iterdir() if ".incoming-" in p.name or ".old-" in p.name]


def test_profiles_left_by_a_crash_are_swept(tmp_path, monkeypatch):
    api = appcontainer._api()
    left = appcontainer.Profile(api)   # never closed: as after a crash
    monkeypatch.setattr(appcontainer, "_swept", False)
    monkeypatch.setattr(appcontainer, "ORPHAN_AGE", 0.0)
    assert appcontainer.sweep(api) >= 1
    assert not left.folder.exists()
    monkeypatch.setattr(appcontainer, "_swept", False)
    monkeypatch.setattr(appcontainer, "ORPHAN_AGE", 3600.0)
    fresh = appcontainer.Profile(api)
    try:
        appcontainer.sweep(api)
        assert fresh.folder.exists()   # a profile in use (folder touched just now) is left alone
    finally:
        fresh.close()


def test_a_tampered_grants_record_only_ever_removes_the_capabilitys_own_ace(tmp_path, monkeypatch):
    """The record is the person's to write (a v1 learner process could too): its SID is never used,
    and option-like or relative paths in it are skipped."""
    from fabric_desktop import jsonfile
    outside = tmp_path / "Documents"
    outside.mkdir()
    users = "S-1-5-32-545"   # BUILTIN\Users
    record = tmp_path / "academy" / appcontainer.GRANTS_FILE
    record.parent.mkdir()
    jsonfile.write_json(record, {"sid": users, "paths": [str(outside), "/T", "-x", "relative\\path"]})
    calls = []
    real = appcontainer._icacls
    monkeypatch.setattr(appcontainer, "_icacls", lambda *a: (calls.append(a), real(*a))[1])
    appcontainer.revoke_runtime(tmp_path)
    cap = appcontainer.capability_sid(appcontainer._api())[1]
    removals = [a for a in calls if "/remove:g" in a]
    assert removals and all(a[-1] == f"*{cap}" for a in removals)
    assert not any(a[0] in ("/T", "-x", "relative\\path") for a in removals)
    assert users not in " ".join(" ".join(a) for a in calls)
