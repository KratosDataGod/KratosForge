"""The engine and a notebook start without loading pyarrow.dataset, and with it pandas (together about 0.2 s of
every start), pyarrow.compute (about 30 ms) or PyYAML and the Ossie converter (about 25 ms). Timings are from
examples/benchmark/startup_and_sql.py. Each loads when a step first needs it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[1]
HEAVY = ["pyarrow.dataset", "pandas", "pyarrow.compute", "yaml", "fabric_desktop._vendor.ossie_microsoft"]


def loaded_after(code: str) -> dict[str, bool]:
    probe = f"{code}\nimport json, sys\nprint(json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
    out = subprocess.run([sys.executable, "-c", probe], cwd=SIDECAR, capture_output=True, text=True, timeout=120,
                         check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_engine_starts_without_the_heavy_imports():
    assert loaded_after("import fabric_desktop.server, fabric_desktop.service") == dict.fromkeys(HEAVY, False)


def test_a_notebook_kernel_imports_without_the_heavy_imports():
    assert loaded_after("import fabric_desktop.notebook, fabric_desktop.spark_local, fabric_desktop.spark_stream") \
        == dict.fromkeys(HEAVY, False)
