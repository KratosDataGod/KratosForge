"""The Academy learner process (ACD-005): escape attempts as tests (Security read, 2026-11-03).

Each test runs a learner script in the real learner process and checks it was refused. These
guard against accidents (the person owns the PC); they don't make the process a sandbox, and the
app never calls it one (docs/academy/grading-sandbox.md).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from fabric_desktop.academy import sandbox
from fabric_desktop.academy import workspace as ws


@pytest.fixture
def lab(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "data"
    work = ws.workspace_dir(root, "dp-700", "E37")
    work.mkdir(parents=True)
    return root, work


def run(lab: tuple[Path, Path], code: str, **kw: object) -> dict:
    root, work = lab
    (work / "answer.py").write_text(code, encoding="utf-8")
    return sandbox.run(work, "answer.py", root, **kw)  # type: ignore[arg-type]


def refused(res: dict) -> bool:
    return res["status"] == "error" and any(e in res["stderr"] for e in ("PermissionError", "ImportError",
                                                                          "ModuleNotFoundError"))


def test_a_normal_exercise_runs_and_writes_in_its_folder(lab: tuple[Path, Path]) -> None:
    res = run(lab, "import polars as pl, duckdb\n"
                   "df = pl.DataFrame({'id': [1, 1, 2]}).unique()\n"
                   "df.write_parquet('out.parquet')\n"
                   "print(duckdb.sql(\"SELECT count(*) FROM 'out.parquet'\").fetchone()[0])\n")
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].strip() == "2"
    assert (lab[1] / "out.parquet").is_file()
    assert "without your sign-in" in res["runs_as"] and "sandbox" not in res["runs_as"]


def test_a_learner_error_is_reported(lab: tuple[Path, Path]) -> None:
    res = run(lab, "1 / 0\n")
    assert res["status"] == "error" and "ZeroDivisionError" in res["stderr"]


@pytest.mark.parametrize("module", ["fabric_desktop", "fabric_desktop.auth", "msal", "pymsalruntime"])
def test_the_engine_and_sign_in_packages_cant_be_imported(lab: tuple[Path, Path], module: str) -> None:
    res = run(lab, f"import {module}\n")
    assert res["status"] == "error" and refused(res), res["stderr"]


def test_importlib_cant_get_round_the_import_guard(lab: tuple[Path, Path]) -> None:
    res = run(lab, "import importlib, sys\nsys.meta_path.clear()\nimportlib.import_module('fabric_desktop')\n")
    assert res["status"] == "error" and refused(res), res["stderr"]


def test_grading_material_cant_be_read(lab: tuple[Path, Path]) -> None:
    grading = sandbox.PACKAGE_DIR / "academy" / "packs"
    target = sandbox.PACKAGE_DIR / "academy" / "validators.py"
    res = run(lab, f"print(open({str(target)!r}).read()[:20])\n")
    assert refused(res), res["stderr"]
    res = run(lab, f"import os\nprint(os.listdir({str(grading.parent)!r}))\n")
    assert refused(res), res["stderr"]


def test_the_apps_data_folder_outside_the_workspace_cant_be_read(lab: tuple[Path, Path]) -> None:
    root, _ = lab
    (root / "tokens.json").write_text('{"refresh": "x"}')
    res = run(lab, f"print(open({str(root / 'tokens.json')!r}).read())\n")
    assert refused(res) and "refresh" not in res["stdout"]


@pytest.mark.parametrize("code", [
    "open('../../escape.txt', 'w').write('x')",
    "open({outside!r}, 'w').write('x')",
    "import os; os.remove({outside!r})",
    "import os; os.rename('answer.py', {outside!r})",
    "import shutil; shutil.rmtree({outside_dir!r})",
    "import os; os.mkdir({outside_dir!r} + '/new')",
    "import os; os.chdir('..')",
])
def test_writes_outside_the_workspace_are_refused(lab: tuple[Path, Path], tmp_path: Path, code: str) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    res = run(lab, code.format(outside=str(outside), outside_dir=str(tmp_path)) + "\n")
    assert refused(res), res["stderr"]
    assert outside.read_text() == "keep"
    assert not (lab[0] / "academy" / "escape.txt").exists()


def test_a_symlink_out_of_the_workspace_is_refused(lab: tuple[Path, Path], tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret")
    try:
        os.symlink(secret, lab[1] / "link.txt")
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    res = run(lab, "print(open('link.txt').read())\n")
    assert refused(res) and "s3cret" not in res["stdout"]
    res = run(lab, f"import os; os.symlink({str(secret)!r}, 'mine.txt')\n")
    assert refused(res)


@pytest.mark.parametrize("code", [
    "import socket; socket.create_connection(('127.0.0.1', 9))",
    "import socket; socket.socket()",
    "import urllib.request; urllib.request.urlopen('https://example.com')",
    "import socket; socket.getaddrinfo('example.com', 443)",
])
def test_python_sockets_are_refused(lab: tuple[Path, Path], code: str) -> None:
    assert refused(run(lab, code + "\n"))


@pytest.mark.parametrize("code", [
    "import polars as pl; pl.read_parquet('https://example.com/x.parquet')",
    "import polars as pl; pl.scan_csv('s3://bucket/x.csv')",
    "import polars as pl; pl.DataFrame({{'a': [1]}}).write_parquet({outside!r})",
    "import polars as pl; pl.DataFrame({{'a': [1]}}).lazy().sink_parquet({outside!r})",
    "import polars as pl; pl.read_csv('x.csv', storage_options={{'k': 'v'}})",
    "import deltalake, polars as pl; deltalake.write_deltalake({outside_dir!r}, pl.DataFrame({{'a': [1]}}).to_arrow())",
    "import deltalake; deltalake.DeltaTable('abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/t')",
    "import duckdb; duckdb.sql(\"COPY (SELECT 1) TO '{outside_posix}'\")",
    "import duckdb; duckdb.connect().sql(\"SELECT * FROM read_csv('{outside_posix}')\").fetchall()",
    "import duckdb; duckdb.connect({outside!r})",
    "import duckdb; duckdb.sql(\"SET enable_external_access = true\")",
    "import pyarrow.parquet as pq, pyarrow as pa; pq.write_table(pa.table({{'a': [1]}}), {outside!r})",
    "import pyarrow.fs as fs; fs.S3FileSystem()",
    "import pandas as pd; pd.DataFrame({{'a': [1]}}).to_parquet({outside!r})",
    "import pyarrow as pa; pa.output_stream({outside!r}).write(b'x')",
    "import pyarrow as pa; pa.OSFile({outside!r}, 'w').write(b'x')",
    "import pyarrow as pa; pa.create_memory_map({outside!r}, 10)",
    "import polars.io.csv.functions as f; f.read_csv('https://example.com/x.csv')",
    "import polars as pl, polars.io.parquet.functions as f; f.read_parquet({outside!r})",
])
def test_native_readers_and_writers_refuse_urls_and_outside_paths(lab: tuple[Path, Path], tmp_path: Path, code: str) -> None:
    outside = tmp_path / "native-out.parquet"
    res = run(lab, code.format(outside=str(outside), outside_posix=outside.as_posix(),
                               outside_dir=str(tmp_path / "dl")) + "\n")
    assert res["status"] == "error", res
    last = res["stderr"].strip().splitlines()[-1]
    assert last.startswith(("PermissionError", "_duckdb.PermissionException", "duckdb.PermissionException",
                            "_duckdb.InvalidInputException")), last
    assert not outside.exists() and not (tmp_path / "dl").exists()


@pytest.mark.parametrize("code", [
    "import subprocess; subprocess.run([sys.executable, '-c', 'print(1)'])",
    "import os; os.system('echo hi')",
    "import os; os.fork() if hasattr(os, 'fork') else os.system('x')",
    "import os; os.execv(sys.executable, [sys.executable])",
    "import ctypes; ctypes.CDLL('kernel32' if sys.platform == 'win32' else None)",
    "import os; os.kill(os.getppid(), 9)",
])
def test_child_processes_and_ctypes_are_refused_even_first(lab: tuple[Path, Path], code: str) -> None:
    res = run(lab, "import sys\n" + code + "\n")
    assert refused(res), res["stderr"]
    assert "hi" not in res["stdout"]


def test_secrets_in_the_engines_environment_dont_reach_it(lab: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "do-not-leak")
    monkeypatch.setenv("FABRIC_TOKEN", "do-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@proxy:8080")
    res = run(lab, "import os\nprint(sorted(os.environ))\nprint(os.environ.get('TEMP'))\n")
    assert res["status"] == "ok", res["stderr"]
    assert "do-not-leak" not in res["stdout"] and "AZURE" not in res["stdout"] and "PROXY" not in res["stdout"].upper()
    assert str(lab[1]) in res["stdout"]


def test_the_time_limit_stops_it(lab: tuple[Path, Path]) -> None:
    started = time.monotonic()
    res = run(lab, "while True:\n    pass\n", time_limit=2)
    assert res["status"] == "time_limit"
    assert time.monotonic() - started < 15


def test_the_memory_limit_stops_it(lab: tuple[Path, Path]) -> None:
    res = run(lab, "x = bytearray(3 * 1024 ** 3)\nprint('got it')\n", memory_limit=512 * 1024 ** 2)
    assert res["status"] == "memory_limit", res
    assert "got it" not in res["stdout"]


def test_output_is_capped(lab: tuple[Path, Path]) -> None:
    res = run(lab, "import sys\nfor _ in range(5000):\n    sys.stdout.write('x' * 1000)\n")
    assert len(res["stdout"]) <= sandbox.OUTPUT_LIMIT + 100 and "cut at 1 MB" in res["stdout"]


def test_nothing_runs_on_after_run_returns(lab: tuple[Path, Path]) -> None:
    """Validators read the workspace only after the process and its job or group have ended."""
    res = run(lab, "import time, pathlib\n"
                   "for i in range(1000):\n"
                   "    pathlib.Path('tick.txt').write_text(str(i))\n"
                   "    time.sleep(0.01)\n", time_limit=6)
    assert res["status"] == "time_limit"
    tick = lab[1] / "tick.txt"
    assert tick.is_file(), res["stderr"]
    seen = tick.read_text()
    time.sleep(0.5)
    assert tick.read_text() == seen


def test_only_python_files_in_the_workspace_run(lab: tuple[Path, Path], tmp_path: Path) -> None:
    root, work = lab
    (tmp_path / "evil.py").write_text("print(1)")
    with pytest.raises(ws.Refused):
        sandbox.run(work, "../../../../evil.py", root)
    (work / "notes.txt").write_text("print(1)")
    with pytest.raises(ws.Refused):
        sandbox.run(work, "notes.txt", root)


def test_the_environment_is_built_from_scratch(tmp_path: Path) -> None:
    env = sandbox.environment(tmp_path, {"PATH": "/bin", "SystemRoot": "C:\\Windows", "AZURE_TENANT_ID": "t",
                                         "FABRIC_DESKTOP_DATA": "x", "HTTP_PROXY": "p", "PYTHONPATH": "/eng"})
    assert env["PATH"] == "/bin" and env["SystemRoot"] == "C:\\Windows"
    assert not {"AZURE_TENANT_ID", "FABRIC_DESKTOP_DATA", "HTTP_PROXY", "PYTHONPATH"} & set(env)
    assert env["TEMP"] == str(tmp_path / ".tmp")


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
def test_windows_job_stops_a_child_process_even_without_the_hook(lab: tuple[Path, Path]) -> None:
    """The job itself (ActiveProcessLimit 1, no breakaway) refuses a child process as the very first
    statement: plain Python, no bootstrap and no audit hook."""
    _, work = lab
    code = ("import subprocess, sys\n"
            "try:\n    subprocess.run([sys.executable, '-c', 'pass'], creationflags=0x01000000)\n"   # BREAKAWAY_FROM_JOB
            "except OSError as e:\n    print('refused', e.winerror)\n    sys.exit(7)\n"
            "try:\n    subprocess.run([sys.executable, '-c', 'pass'])\n"
            "except OSError as e:\n    print('refused', e.winerror)\n    sys.exit(7)\n")
    proc, job = sandbox._start([sys.executable, "-I", "-c", code], work, sandbox.MEMORY_LIMIT, 30,
                                   "Local\\\\KratosForgeAcademy-test-" + os.urandom(8).hex())
    try:
        out, _ = proc.communicate(timeout=60)
    finally:
        job.close()
    assert proc.returncode == 7 and b"refused" in out


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
def test_windows_bootstrap_wont_run_outside_a_job(lab: tuple[Path, Path]) -> None:
    import json
    import subprocess
    _, work = lab
    (work / "answer.py").write_text("print('ran')")
    cfg = {"workspace": str(work), "script": "answer.py", "read_roots": sandbox.read_roots(),
           "denied_roots": [], "blocked_modules": list(sandbox.BLOCKED_MODULES),
           "job_name": "Local\\KratosForgeAcademy-not-a-job"}   # the CI runner's own job mustn't count
    res = subprocess.run([sys.executable, "-I", "-c", sandbox.BOOTSTRAP.read_text(encoding="utf-8"), json.dumps(cfg)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 3 and "ran" not in res.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only")
def test_windows_job_can_only_be_queried_by_name() -> None:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k.OpenJobObjectW.restype = wintypes.HANDLE
    k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    name = "Local\\KratosForgeAcademy-test-" + os.urandom(8).hex()
    job = sandbox._WinJob(sandbox.MEMORY_LIMIT, sandbox.CPU_LIMIT, name)
    try:
        for access in (0x0010, 0x0008, 0x001F001F):   # SET_ATTRIBUTES, TERMINATE, ALL_ACCESS
            assert not k.OpenJobObjectW(access, False, name)
            assert ctypes.get_last_error() == 5  # type: ignore[attr-defined]  # access denied, even for the engine's own user
        query = k.OpenJobObjectW(0x0004, False, name)
        assert query
        k.CloseHandle(query)
    finally:
        job.close()


def test_a_linked_temp_folder_is_refused(lab: tuple[Path, Path], tmp_path: Path) -> None:
    root, work = lab
    (work / "answer.py").write_text("print(1)")
    try:
        os.symlink(tmp_path, work / ".tmp", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused, match="link"):
        sandbox.run(work, "answer.py", root)


def test_a_learner_library_folder_is_importable_and_read_only(lab: tuple[Path, Path]) -> None:
    root, work = lab
    lib = root / "academy" / "_lib"
    (lib / "kf_learner").mkdir(parents=True)
    (lib / "kf_learner" / "__init__.py").write_text("ANSWER = 42\n")
    (work / "answer.py").write_text("import kf_learner\nprint(kf_learner.ANSWER)\n")
    res = sandbox.run(work, "answer.py", root, lib_roots=[lib])
    assert res["status"] == "ok" and res["stdout"].strip() == "42", res["stderr"]
    (work / "answer.py").write_text(f"open({str(lib / 'kf_learner' / 'x.py')!r}, 'w').write('1')\n")
    assert refused(sandbox.run(work, "answer.py", root, lib_roots=[lib]))
    assert not (lib / "kf_learner" / "x.py").exists()


@pytest.mark.parametrize("which", ["package", "inside_package", "data_root", "workspace"])
def test_a_learner_library_folder_cant_be_an_app_folder(lab: tuple[Path, Path], which: str) -> None:
    root, work = lab
    lib = {"package": sandbox.PACKAGE_DIR, "inside_package": sandbox.PACKAGE_DIR / "academy",
           "data_root": root, "workspace": work}[which]
    (work / "answer.py").write_text("print(1)\n")
    with pytest.raises(ws.Refused):
        sandbox.run(work, "answer.py", root, lib_roots=[lib])


def test_native_reads_outside_the_workspace_are_refused(lab: tuple[Path, Path], tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret")
    for code in (f"import pyarrow.fs as fs; print(fs.LocalFileSystem().open_input_stream({str(secret)!r}).read())",
                 f"import pyarrow as pa; print(pa.input_stream({str(secret)!r}).read())",
                 f"import pyarrow as pa; print(pa.memory_map({str(secret)!r}).read())",
                 f"import polars.io.csv.functions as f; print(f.read_csv({str(secret)!r}))"):
        res = run(lab, code + "\n")
        assert res["status"] == "error" and "s3cret" not in res["stdout"], (code, res["stderr"][-300:])


def test_pyarrow_file_systems_are_refused_but_its_readers_work(lab: tuple[Path, Path]) -> None:
    for code in ("import pyarrow.fs as fs; fs.LocalFileSystem().open_output_stream('../../escape.txt')",
                 "import pyarrow.fs as fs; fs.LocalFileSystem().open_input_stream('/etc/hostname').read()",
                 "import pyarrow.fs as fs; fs.SubTreeFileSystem('/', None)",
                 "import pyarrow.fs as fs; fs.S3FileSystem()",
                 "import pyarrow.fs as fs; fs.copy_files('/etc/hostname', 'x')"):
        assert refused(run(lab, code + "\n")), code
    res = run(lab, "import pyarrow as pa, pyarrow.parquet as pq\n"
                   "pq.write_table(pa.table({'a': [1, 2]}), 'x.parquet')\n"
                   "print(pq.read_table('x.parquet').num_rows)\n")
    assert res["status"] == "ok" and res["stdout"].strip() == "2", res["stderr"]
