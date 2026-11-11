"""DuckDB temp files left behind by an engine that was killed while a query spilled.

DuckDB deletes a query's temp files as the query frees them, and never on the next open: measured,
a killed process left 689 MB in its temp folder, and opening and closing that database again left
all of it. Real processes here: a child engine runs a local data query that spills and is killed
(SIGKILL, or TerminateProcess on Windows); the next open of the project removes what it left. The
SQL page's run folders from an earlier session lose their spill folder at the next start.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

from fabric_desktop import sql_exec
from fabric_desktop.service import Service

SIDECAR = Path(__file__).resolve().parents[1]
SPILL = "SELECT count(*) FROM (SELECT DISTINCT md5(i::VARCHAR) || md5((i + 1)::VARCHAR) FROM range(30000000) t(i))"

CHILD = """
    import sys
    from pathlib import Path
    from fabric_desktop.service import Service
    s = Service(root=Path(sys.argv[1]))
    print("ready", flush=True)
    s.local_query(sys.argv[2], sys.argv[3], dialect="duckdb")
"""


def spill_files(folder: Path) -> list[Path]:
    return [f for f in folder.glob("duckdb_temp_*") if f.is_file()]


def test_temp_files_a_killed_engine_left_in_a_local_data_project_go_when_the_project_next_opens(tmp_path):
    root = tmp_path / "app"
    svc = Service(root=root)
    pid = svc.local_create_project("Sales")["id"]
    svc.local_project_settings(pid, {"memory_limit": "64MB", "threads": 1})
    temp = Path(svc.local_data.project(pid).temp_dir)
    svc.local_data.close_all()

    child = subprocess.Popen([sys.executable, "-c", textwrap.dedent(CHILD), str(root), pid, SPILL], cwd=SIDECAR,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready", child.stderr
        deadline = time.monotonic() + 120
        while sum(f.stat().st_size for f in spill_files(temp)) < 20 * 2**20 and time.monotonic() < deadline:
            assert child.poll() is None, child.stderr.read() if child.stderr else "the query ended before spilling"
            time.sleep(0.2)
    finally:
        child.kill()  # SIGKILL; TerminateProcess on Windows
        child.wait(timeout=60)
    assert sum(f.stat().st_size for f in spill_files(temp)) >= 20 * 2**20  # what the killed engine left

    svc = Service(root=root)
    try:
        assert svc.local_query(pid, "SELECT 42 AS a", dialect="duckdb")["rows"] == [[42]]
        assert spill_files(temp) == []
    finally:
        svc.local_data.close_all()


def test_an_earlier_sessions_sql_run_loses_its_spill_folder_at_the_next_start_and_keeps_its_result(tmp_path):
    folder = tmp_path / "sql-runs" / "a1b2c3d4e5f6"  # a run folder from a session that crashed
    (folder / "spill").mkdir(parents=True)
    (folder / "spill" / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"\0" * 2**20)
    (folder / "result.arrow").write_bytes(b"kept while its retention lasts")
    limits = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
              "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    manager = sql_exec.SqlExecutions(tmp_path, token=lambda: "", limits=lambda: limits)
    try:
        manager.cleanup(earlier_sessions=True)  # what the first run of a session does
        assert not (folder / "spill").exists()
        assert (folder / "result.arrow").read_bytes() == b"kept while its retention lasts"
    finally:
        manager.shutdown()
