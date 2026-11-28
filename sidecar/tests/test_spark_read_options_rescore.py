"""Importing spark_read_options writes nothing: only running it as a script rewrites the docs page. The
guard batch 42 left as equivalent for the tests, killed in batch 234 (docs/quality/mutation-testing.md)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[1]


def test_importing_the_module_writes_no_docs_page():
    done = subprocess.run([sys.executable, "-c", "import fabric_desktop.spark_read_options"], cwd=SIDECAR,
                          capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
