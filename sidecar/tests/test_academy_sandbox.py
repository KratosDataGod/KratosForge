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
    "import pyarrow.fs as fs; fs.FileSystem.from_uri({outside!r})[0].open_output_stream({outside!r})",
    "import pyarrow.fs as fs; fs.FileSystem.from_uri('file://' + {outside_posix!r})",
    "import pyarrow.fs as fs; fs.LocalFileSystem.from_uri({outside!r})",
    "import pyarrow._fs as f; f.LocalFileSystem().open_output_stream({outside!r})",
    "import pyarrow._fs as f; f.FileSystem.from_uri({outside!r})",
    "import pyarrow.lib as lib; lib.OSFile({outside!r}, 'w').write(b'x')",
    "import pyarrow.lib as lib; lib.output_stream({outside!r}).write(b'x')",
    "import pyarrow.parquet.core as core; core.LocalFileSystem().open_output_stream({outside!r})",
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
    job = sandbox._WinJob(sandbox.MEMORY_LIMIT, 30, "Local\\KratosForgeAcademy-test-" + os.urandom(8).hex())
    proc, job = sandbox._start([sys.executable, "-I", "-c", code], work, sandbox.MEMORY_LIMIT, 30, job)
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


def test_pyarrow_still_reads_and_writes_in_the_workspace(lab: tuple[Path, Path]) -> None:
    pytest.importorskip("pyarrow")
    code = ("import pyarrow as pa, pyarrow.parquet as pq, pyarrow.fs as fs, os\n"
            "pq.write_table(pa.table({'a': [1, 2]}), 'new.parquet')\n"
            "local, path = fs.FileSystem.from_uri(os.path.abspath('new.parquet'))\n"
            "assert isinstance(local, fs.FileSystem) and isinstance(local, fs.LocalFileSystem)\n"
            "assert issubclass(fs.LocalFileSystem, fs.FileSystem)\n"
            "print(pq.read_table('new.parquet').num_rows, local.get_file_info(path).size > 0)\n")
    res = run(lab, code)
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].split() == ["2", "True"]


def test_the_command_line_stays_short(lab: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    root, work = lab
    (work / "answer.py").write_text("print(1)")
    seen: list[list[str]] = []

    def capture(argv, *_a, **_k):
        seen.append(argv)
        raise RuntimeError("stop")

    monkeypatch.setattr(sandbox, "_start", capture)
    with pytest.raises(RuntimeError):
        sandbox.run(work, "answer.py", root)
    assert len(" ".join(seen[0])) < 8_000 and "def _main" not in " ".join(seen[0])   # Windows' limit is 32,767


QB = ("import deltalake, polars as pl\n"
      "deltalake.write_deltalake('t', pl.DataFrame({'a': [1, 2]}).to_arrow())\n"
      "qb = deltalake.QueryBuilder().register('t', deltalake.DeltaTable('t'))\n")


@pytest.mark.parametrize("sql", [
    "CREATE EXTERNAL TABLE e STORED AS CSV LOCATION '{outside}'",
    "COPY (SELECT 1 AS a) TO '{outside}' STORED AS CSV",
    "SELECT * FROM t; COPY (SELECT 1 AS a) TO '{outside}' STORED AS CSV",
    "SELECT * FROM t /* ; */ ; COPY (SELECT 1) TO '{outside}'",
    "WITH x AS (SELECT 1) COPY (SELECT 1) TO '{outside}'",
    "SET datafusion.catalog.location = '{outside}'",
])
def test_query_builder_runs_only_a_select(lab: tuple[Path, Path], tmp_path: Path, sql: str) -> None:
    pytest.importorskip("deltalake")
    outside = tmp_path / "qb-out.csv"
    outside.write_text("a\n1\n")
    res = run(lab, QB + f"qb.execute({sql.format(outside=outside.as_posix())!r}).read_all()\n")
    assert res["status"] == "error" and "runs one SELECT" in res["stderr"], res["stderr"]
    assert outside.read_text() == "a\n1\n"


@pytest.mark.parametrize("code", [
    "deltalake.QueryBuilder({'datafusion.catalog.location': '/', 'datafusion.catalog.format': 'csv'})",
    "from deltalake._internal import PyQueryBuilder; PyQueryBuilder(None)",
    "import deltalake.query as q; q.PyQueryBuilder(None)",
    "qb._query_builder.execute(\"COPY (SELECT 1 AS a) TO 'x.csv' STORED AS CSV\").read_all()",
])
def test_query_builder_settings_and_internals_are_refused(lab: tuple[Path, Path], code: str) -> None:
    pytest.importorskip("deltalake")
    res = run(lab, QB + code + "\n")
    assert res["status"] == "error" and "PermissionError" in res["stderr"], res["stderr"]


def test_query_builder_holds_no_native_builder_in_reach(lab: tuple[Path, Path]) -> None:
    pytest.importorskip("deltalake")
    code = QB + ("inner = qb._query_builder\n"
                 "print(type(inner).__name__, [n for n in dir(inner) if not n.startswith('__')])\n"
                 "try:\n    type(inner)(None)\nexcept TypeError:\n    print('no new builder')\n"
                 "print(repr(vars(qb)), [c.__name__ for c in type(qb).__mro__])\n")
    res = run(lab, code)
    assert res["status"] == "ok", res["stderr"]
    assert "PyQueryBuilder" not in res["stdout"] and "no new builder" in res["stdout"]
    assert "['execute', 'register']" in res["stdout"]
    assert "['QueryBuilder', 'object']" in res["stdout"]   # nothing native up the class chain either


def test_query_builder_still_selects_from_registered_tables(lab: tuple[Path, Path]) -> None:
    pytest.importorskip("deltalake")
    code = QB + ("print(qb.execute(\"-- the total\\nWITH s AS (SELECT sum(a) AS n, 'copy; drop' AS note FROM t) "
                 "SELECT n FROM s;\").read_all().column(0).to_pylist())\n")
    res = run(lab, code)
    assert res["status"] == "ok", res["stderr"]
    assert res["stdout"].strip() == "[3]"


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


def test_run_resolves_the_workspace_before_finding_the_script(tmp_path):
    """A workspace given by another spelling of its path (a Windows 8.3 short temp path; here a
    path with "..") is resolved first, so the script is still found inside it."""
    work = tmp_path / "ws"
    work.mkdir()
    (work / "main.py").write_text("print('hi')\n", encoding="utf-8")
    other = tmp_path / "ws" / ".." / "ws"
    got = sandbox.run(other, "main.py", tmp_path, time_limit=30)
    assert got["status"] == "ok", got["stderr"]
    assert "hi" in got["stdout"]


def test_an_exercise_that_cant_use_the_appcontainer_never_asks_for_one(tmp_path, monkeypatch):
    from fabric_desktop.academy import pylab
    assert not pylab.appcontainer_ok({"adapter": "delta_lab", "validation": []})
    assert not pylab.appcontainer_ok({"adapter": "python_lab", "validation": [{"kind": "delta_table", "path": "t"}]})
    assert pylab.appcontainer_ok({"adapter": "python_lab", "validation": [{"kind": "notebook_ran"}]})

    def no_box(*a, **k):
        raise AssertionError("asked for an AppContainer")

    monkeypatch.setattr(sandbox, "_box", no_box)
    work = tmp_path / "w"
    work.mkdir()
    (work / "main.py").write_text("print('ran')\n", encoding="utf-8")
    got = sandbox.run(work, "main.py", tmp_path, time_limit=60, appcontainer_ok=False)
    assert got["status"] == "ok", got["stderr"]
    assert got["runs_as"] == sandbox.RUNS_AS


def test_a_self_test_asks_for_the_appcontainer_as_run_and_check_do(tmp_path, monkeypatch):
    from fabric_desktop.academy import pylab
    asked = []
    monkeypatch.setattr(pylab, "grade", lambda *a, **k: asked.append(k.get("appcontainer_ok")) or {"passed": True})
    grading = tmp_path / "grading"
    grading.mkdir()
    (grading / "solution.py").write_text("print('ok')\n", encoding="utf-8")
    (grading / "wrong-1.py").write_text("print('no')\n", encoding="utf-8")
    for ex, want in (({"id": "D1", "adapter": "delta_lab", "starter": "main.py", "validation": []}, False),
                     ({"id": "P1", "adapter": "python_lab", "starter": "main.py", "validation": []}, True)):
        asked.clear()
        pylab.self_test(tmp_path, grading, ex)
        assert asked == [want, want], ex["adapter"]


# ---- DPAPI, a standing rule (gate 2) -------------------------------------------------------------

def test_a_sealed_file_is_recognised_wherever_the_blob_is(tmp_path, monkeypatch):
    from fabric_desktop.academy import appcontainer
    raw = bytes.fromhex("01000000d08c9ddf0115d1118c7a00c04fc297eb") + b"\x00" * 40
    cases = {"raw.bin": raw, "cache.bin": b"KFDPAPI1\n" + raw,
             "settings.json": b'{"teams_webhook": "dpapi:AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAA"}',
             "plain.csv": b"id,name\n1,dpapi\n"}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
    assert {n for n in cases if appcontainer.sealed(tmp_path / n)} == {"raw.bin", "cache.bin", "settings.json"}
    # A blob that straddles two reads is still found.
    monkeypatch.setattr(appcontainer, "_SCAN_CHUNK", 7)
    (tmp_path / "split.bin").write_bytes(b"x" * 5 + raw)
    assert appcontainer.sealed(tmp_path / "split.bin")


def test_a_sealed_file_never_goes_into_the_appcontainers_copy(tmp_path):
    from fabric_desktop.academy import appcontainer
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "main.py").write_text("print(1)\n", encoding="utf-8")
    (src / "msal_cache.bin").write_bytes(b"KFDPAPI1\n" + b"\x01" * 30)
    with pytest.raises(ws.Refused, match="sealed Windows secret"):
        appcontainer.copy_tree(src, dst, refuse_sealed=True)
    other = tmp_path / "home"
    other.mkdir()
    appcontainer.copy_tree(src, other)   # copying home doesn't look
    assert (other / "msal_cache.bin").exists()


def test_the_runtime_is_never_granted_on_the_data_folder_or_a_sealed_file(tmp_path, monkeypatch):
    from fabric_desktop import auth
    from fabric_desktop.academy import appcontainer
    home = tmp_path / "FabricDesktop"
    data = home / "data"
    engine = home / "engine" / "abc"
    engine.mkdir(parents=True)
    data.mkdir()
    monkeypatch.setattr(auth, "default_cache_path", lambda: home / "msal_cache.bin")
    appcontainer.check_grants_keep_out([(engine, True), (home, False)], data)   # the engine below is fine
    for refused in (data, home, tmp_path):
        with pytest.raises(OSError, match="data folder"):
            appcontainer.check_grants_keep_out([(refused, True)], data)
    secret = engine / "python312._pth"
    secret.write_bytes(b"AQAAANCMnd8BFdERjHoAwE/Cl+s")
    with pytest.raises(OSError, match="sealed Windows secret"):
        appcontainer.check_grants_keep_out([(secret, False)], data)


def test_app_package_aces_read_sids_from_sddl_whatever_the_path_or_language(tmp_path, monkeypatch):
    from fabric_desktop.academy import appcontainer
    saved = ("pkg\r\nD:PAI(D;OICI;0x1200a9;;;AC)(D;OICI;0x1200a9;;;S-1-15-2-2)(A;OICI;FA;;;SY)\r\n"
             "pkg\\Program Data\\a b.py\r\nD:AI(A;;0x1200a9;;;AC)(A;ID;0x1200a9;;;S-1-15-2-2)(A;ID;FA;;;BA)\r\n")

    class Done:
        returncode = 0

    def fake(*args):
        Path(args[args.index("/save") + 1]).write_bytes("﻿".encode("utf-16-le") + saved.encode("utf-16-le"))
        return Done()

    monkeypatch.setattr(appcontainer, "_icacls", fake)
    assert appcontainer.app_package_aces(tmp_path, tree=True) == [
        ("pkg", "D", "OICI"), ("pkg", "D", "OICI"), ("pkg\\Program Data\\a b.py", "A", ""), ("pkg\\Program Data\\a b.py", "A", "ID")]
