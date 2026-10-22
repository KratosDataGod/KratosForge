"""SQL-004 (W3): the warehouse driver is in the engine and stays quiet until a warehouse query uses it."""

from __future__ import annotations

import inspect
import subprocess
import sys


def test_the_driver_imports_without_writing_to_stdout_or_a_log_folder(tmp_path):
    # stdout carries the engine's RPC, and the driver can write trace logs to ./mssql_python_logs.
    r = subprocess.run([sys.executable, "-c", "import mssql_python"], cwd=tmp_path, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and not (tmp_path / "mssql_python_logs").exists()


def test_the_driver_takes_a_token_we_already_hold():
    import mssql_python
    assert mssql_python.apilevel == "2.0"
    assert "token_provider" in inspect.signature(mssql_python.connect).parameters
