"""A test worker that dies stops CI's pytest run at once, so the run fails instead of waiting for the
job's time limit.

conftest ends a worker whose test runs over its time limit. Under xdist 3.8's loadfile scheduler, the
worker started in its place got no work and no word to stop, so one main run sat for an hour after
the dump and was cancelled at the job's limit. Every xdist run in CI passes --max-worker-restart 0."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = [ROOT / ".github" / "workflows" / "ci.yml", ROOT / ".github" / "scripts" / "compat_tests.sh"]


def _xdist_runs() -> list[str]:
    found = []
    for path in RUNS:
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        found += [line.strip() for line in text.splitlines() if re.search(r"pytest .*-n auto", line)]
    return found


def test_every_xdist_run_in_ci_stops_when_a_worker_dies():
    runs = _xdist_runs()
    assert len(runs) == 4, runs  # ci.yml's main and debugger-py313 runs, and compat_tests.sh's sqlglot and duckdb runs
    for run in runs:
        assert "--dist loadfile" in run and "--max-worker-restart 0" in run, run


def test_a_run_with_a_worker_that_dies_ends_and_names_the_test(tmp_path):
    # The file whose test dies sorts last, so it is the last file the first hand-out sends: the worker
    # that runs it gets no more work after it starts (only "shutdown", whose failed send xdist ignores).
    # When it sorted first, the worker could die while the hand-out was still sending to it, and the run
    # ended in an xdist INTERNALERROR ("cannot send (already closed?)") instead of naming the test.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_d_dies.py").write_text("import os\n\ndef test_dies():\n    os._exit(1)\n\n"
                                             "def test_after():\n    pass\n", encoding="utf-8")
    for name in ("a", "b", "c"):
        (tmp_path / f"test_{name}.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    pass\n",
                                                  encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    done = subprocess.run([sys.executable, "-m", "pytest", "-n", "2", "--dist", "loadfile",
                           "--max-worker-restart", "0", "-p", "no:cacheprovider"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=240)
    out = done.stdout + done.stderr
    assert done.returncode == 1, out
    assert "worker 'gw" in out and "crashed while running 'test_d_dies.py::test_dies'" in out, out
    assert "crashed and worker restarting disabled" in out, out
