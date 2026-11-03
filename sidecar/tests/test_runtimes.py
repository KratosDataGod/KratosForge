"""Shared runtime hardening for engines that run in a Python runtime of their own (engines/runtimes.py,
engines/runtime_worker.py): locked installs, policy keys, the isolated worker, stdin, the cell time
limit, the environment and the CLI lookup. Real worker processes run against a fake package."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import app_settings
from fabric_desktop.engines import runtimes as rt
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported

PIN = "--hash=sha256:" + "0" * 64


@pytest.fixture
def locks(tmp_path):
    folder = tmp_path / "locks"
    folder.mkdir()
    (folder / "1.2.txt").write_text(f"thing==1.2.3 \\\n    {PIN}\n")
    (folder / "notes.txt").write_text("not a line")
    return folder


def runtimes_for(tmp_path, locks, seen=None, **kw):
    run = kw.pop("run", None) or (lambda cmd, **k: (seen.append((cmd, k)) if seen is not None else None)
                                  or SimpleNamespace(returncode=0, stdout="", stderr=""))
    return rt.LockedRuntimes(tmp_path, name="thing", locks=locks, distribution="thing", label="Thing Connect",
                             line_label="Thing Runtime", allow_key="allow_dbconnect_install",
                             index_key="dbconnect_index_url", python="py", run=run, **kw)


# ---------------------------------------------------------------- locked installs and policy keys


def test_only_lines_with_a_lock_file_install_and_others_say_what_is_supported(tmp_path, locks):
    r = runtimes_for(tmp_path, locks)
    assert rt.lock_lines(locks) == r.supported_lines() == ["1.2"]
    assert r.folder("1.2") == tmp_path / "runtimes" / "thing-1.2" / "site-packages"
    for bad in ("../1.2", "1.2/..", "", "1.2.3"):
        with pytest.raises(ValueError):
            r.folder(bad)
    with pytest.raises(Unsupported, match=r"^Thing Connect for Thing Runtime 1\.3 isn't supported in this version of "
                                          r"KratosForge\. Supported: 1\.2\.$"):
        r.lock("1.3")


def test_the_install_is_hash_pinned_isolated_and_sees_only_a_minimal_environment(tmp_path, locks):
    seen = []
    r = runtimes_for(tmp_path, locks, seen)
    base = {"PATH": "/bin", "TMP": "/t", "PIP_INDEX_URL": "https://evil.example.com", "PIP_CONFIG_FILE": "/x",
            "PYTHONPATH": "/evil", "THING_TOKEN": "x", "no_proxy": "corp"}
    assert r.install("1.2", base)["ok"]
    cmd, kw = seen[0]
    assert cmd[:6] == ["py", "-I", "-m", "pip", "install", "--isolated"]
    assert {"--require-hashes", "--only-binary=:all:", "--no-deps"} <= set(cmd)
    assert cmd[cmd.index("-r") + 1] == str(locks / "1.2.txt")
    folder = str(r.folder("1.2"))
    assert cmd[cmd.index("--target") + 1] == folder and kw["cwd"] == folder
    assert kw["env"] == {"PATH": "/bin", "TMP": "/t", "no_proxy": "corp"}


def test_policy_turns_installs_off_or_points_them_at_a_mirror(tmp_path, locks, monkeypatch):
    seen = []
    r = runtimes_for(tmp_path, locks, seen)
    values = {"allow_dbconnect_install": True, "dbconnect_index_url": "https://pkgs.contoso.com/simple"}
    monkeypatch.setattr(app_settings, "value", lambda key: values[key])
    r.install("1.2", {})
    assert seen[0][0][-2:] == ["--index-url", "https://pkgs.contoso.com/simple"]
    for bad in ("http://pkgs.contoso.com/simple", "https://u:p@pkgs.contoso.com/simple", "https://a b"):
        values["dbconnect_index_url"] = bad
        with pytest.raises(ValueError, match="dbconnect_index_url must be an https address"):
            r.install("1.2", {})
    values["allow_dbconnect_install"] = False
    with pytest.raises(PermissionError, match="installing Thing Connect"):
        r.install("1.2", {})
    assert len(seen) == 1


def test_a_slow_install_is_stopped_and_the_log_is_redacted(tmp_path, locks):
    slow = runtimes_for(tmp_path, locks, run=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("pip", 1)))
    assert slow.install("1.2", {}) == {"ok": False, "log": "pip didn't finish within 20 minutes, so it was stopped."}
    secret = "Bearer " + "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".c2lnbmF0dXJlLXZhbHVlLWhlcmU"
    noisy = runtimes_for(tmp_path, locks, run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=secret, stderr=""))
    out = noisy.install("1.2", {})
    assert not out["ok"] and "eyJzdWIi" not in out["log"]


# ---------------------------------------------------------------- environment and CLI lookup


def test_the_worker_environment_keeps_none_of_the_platforms_or_pythons_variables():
    base = {"PATH": "/bin", "THING_TOKEN": "t", "thing_host": "h", "PYTHONPATH": "/evil", "PythonHome": "/x", "HOME": "/h"}
    assert rt.scrub_env(base, ("THING_",)) == {"PATH": "/bin", "HOME": "/h"}
    env = {"SP_SECRET": "s", "sp_secret": "s", "OTHER": "o"}
    assert rt.drop_ref_variable(env, "env:SP_SECRET") == {"OTHER": "o"}
    assert rt.drop_ref_variable(env, "credman:SP_SECRET") == env


def test_a_cli_is_found_only_in_absolute_path_folders(tmp_path, monkeypatch):
    name = "thing.exe" if sys.platform == "win32" else "thing"
    here, real = tmp_path / "here", tmp_path / "bin"
    for folder in (here, real):
        folder.mkdir()
    (here / name).write_text("")
    (here / name).chmod(0o755)
    monkeypatch.chdir(here)
    env = {"PATH": os.pathsep.join(["", ".", "here", str(real)])}
    assert rt.trusted_cli("thing", env) is None
    (real / name).write_text("")
    (real / name).chmod(0o755)
    assert rt.trusted_cli("thing", env) == str(real / name)


def test_on_windows_only_the_exe_in_a_drive_letter_folder_counts(tmp_path, monkeypatch):
    windows = sys.platform == "win32"
    (tmp_path / "thing.cmd").write_text("")
    (tmp_path / "thing.exe").write_text("")
    monkeypatch.setattr(rt.sys, "platform", "win32")
    no_drive = "\\\\?\\" + str(tmp_path) if windows else str(tmp_path)
    assert rt.trusted_cli("thing", {"PATH": no_drive}) is None
    assert rt.DRIVE.match("C:\\Tools") and not rt.DRIVE.match("\\\\server\\share")
    if windows:
        (tmp_path / "thing.exe").unlink()
        assert rt.trusted_cli("thing", {"PATH": str(tmp_path)}) is None


# ---------------------------------------------------------------- the worker, for real

FAKE = '''
import time
__version__ = "0.0.1"

class _Spark:
    stop = False
    def interruptAll(self):
        _Spark.stop = True
    def wait(self):
        for _ in range(400):
            if _Spark.stop:
                raise RuntimeError("interrupted")
            time.sleep(0.05)

class _Builder:
    def getOrCreate(self):
        return _Spark()

class DatabricksSession:
    builder = _Builder()
'''


class ThingSession(rt.WorkerSession):
    engine = "thing"
    label = "Thing"
    stop = 1

    def stop_seconds(self):
        return self.stop


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "rt" / "site-packages"
    (f / "databricks" / "connect").mkdir(parents=True)
    (f / "databricks" / "__init__.py").write_text("")
    (f / "databricks" / "connect" / "__init__.py").write_text(FAKE)
    return f


def open_session(folder, kind="databricks-connect", env=None, **kw):
    command = rt.worker_command(sys.executable, folder, kind)
    return ThingSession(SessionConfig("nb"), command, env if env is not None else dict(os.environ),
                        start_seconds=60, cwd=str(folder), **kw)


def test_the_worker_starts_isolated_in_its_folder_with_no_engine_modules(folder):
    command = rt.worker_command("py", folder, "databricks-connect")
    assert command == ["py", "-I", str(rt.WORKER), str(folder), "databricks-connect"]
    s = open_session(folder)
    try:
        r = s.execute(CellRequest("import sys, os\n(sys.path[0] == %r, os.getcwd() == %r, "
                                  "any(m.startswith('fabric_desktop') for m in sys.modules))" % (str(folder), str(folder))))
        assert r.outputs == [{"type": "text", "text": "(True, True, False)"}]
        with pytest.raises(Unsupported, match="Thing runs Python cells"):
            s.execute(CellRequest("select 1", "sql"))
    finally:
        s.close()
    assert s.closed and s.proc.poll() is not None


def test_an_unknown_kind_doesnt_start_and_leaves_no_process(folder):
    with pytest.raises(RuntimeError, match="Thing couldn't start: No runtime of kind 'nope'"):
        open_session(folder, "nope")


def test_secrets_leave_the_environment_and_stdin_never_reaches_a_cell(folder):
    env = dict(os.environ) | {"DATABRICKS_CLIENT_SECRET": "s3cret", "DATABRICKS_TOKEN": "t0ken"}
    s = open_session(folder, env=env)
    try:
        r = s.execute(CellRequest("import os\n[k for k in os.environ if k.startswith('DATABRICKS_')]"))
        assert r.outputs == [{"type": "text", "text": "[]"}]
        r = s.execute(CellRequest("input()"))
        assert r.status == "error" and r.outputs[-1]["ename"] == "EOFError"
        assert s.execute(CellRequest("import os, sys, pandas\n(os.isatty(0), sys.stdin.isatty())")).outputs == \
            [{"type": "text", "text": "(False, False)"}]
    finally:
        s.close()


def test_a_cell_past_its_limit_is_interrupted_then_the_session_ends(folder):
    s = open_session(folder, cell_seconds=1)
    try:
        # interruptAll stops this one: the cell ends with its error and the session stays open.
        r = s.execute(CellRequest("spark.wait()"))
        assert r.status == "error" and "interrupted" in r.outputs[-1]["evalue"] and not s.closed
        # Plain Python ignores the interrupt, so the process is ended.
        with pytest.raises(SessionClosed, match="still running after 0 minutes"):
            s.execute(CellRequest("import time\ntime.sleep(30)"))
        assert s.closed and s.proc.poll() is not None
    finally:
        s.close()
