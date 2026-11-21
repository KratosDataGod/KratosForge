"""A long session on this PC doesn't keep more and more: notebook cells, local SQL runs and exports in a loop.

scripts/soak_local.py runs the same loop for an hour and prints each count's slope. This is its short form
for CI: after a warm-up, more loops leave the open files, threads, kept runs and kept exports where they
were. An hour's soak found exports kept for the engine's life, two more each loop (sql_exec.MAX_EXPORTS_KEPT).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from fabric_desktop import sql_exec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import soak_local  # noqa: E402


@pytest.fixture
def soak(tmp_path, monkeypatch):
    from fabric_desktop import known_folders
    monkeypatch.setattr(known_folders, "downloads", known_folders.downloads)  # Soak re-points it; restored after
    s = soak_local.Soak(tmp_path)
    yield s
    s.close()


def test_more_loops_keep_nothing_more(soak, monkeypatch):
    # Limits reached in a few loops, as a long session reaches 20 runs and 50 exports.
    monkeypatch.setattr(sql_exec, "MAX_KEPT", 4)
    monkeypatch.setattr(sql_exec, "MAX_EXPORTS_KEPT", 6, raising=False)
    for _ in range(8):
        soak.loop()
    before = soak.sample(0)
    for _ in range(12):
        soak.loop()
    after = soak.sample(0)
    for key in ("handles", "threads", "app_files", "downloads", "sql_runs", "local_queries", "kernel_handles",
                "kernel_threads", "engine_children"):
        if key in before:
            assert after[key] <= before[key] + 2, (key, before[key], after[key])
    assert after["sql_exports"] <= 6 + 1, "finished exports are kept past MAX_EXPORTS_KEPT"  # + one still running


def test_a_running_export_is_kept_however_many_have_finished(tmp_path):
    runs = object.__new__(sql_exec.SqlExecutions)
    runs._lock = threading.RLock()
    runs._exports = {}
    running = sql_exec.Export(id="live", execution="r", format="csv", path=tmp_path / "a.csv")
    runs._exports["live"] = running
    for i in range(sql_exec.MAX_EXPORTS_KEPT + 5):
        runs._exports[f"done{i}"] = sql_exec.Export(id=f"done{i}", execution="r", format="csv",
                                                    path=tmp_path / f"{i}.csv", state="completed")
    with runs._lock:
        runs._forget_old_exports()
    assert "live" in runs._exports
    kept = [k for k in runs._exports if k != "live"]
    assert len(kept) == sql_exec.MAX_EXPORTS_KEPT and kept[0] == "done5", "the oldest go first"
