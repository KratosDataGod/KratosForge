"""Local pipeline run records: unreadable files and files that can't be removed. Records go to a
temporary folder."""

import os
import time
from pathlib import Path

from fabric_desktop.orchestration import runs
from fabric_desktop.orchestration.runs import RunStore


def _record(run_id, state="succeeded"):
    return {"run_id": run_id, "state": state, "activities": [{"state": "running"}] if state == "running" else []}


def test_unreadable_records_are_skipped_by_list_and_recover(tmp_path):
    store = RunStore(tmp_path)
    store.save(_record("aa"))
    store.save(_record("bb", "running"))
    (tmp_path / "cc.json").write_text("{broken", encoding="utf-8")
    assert store.recover() == 1
    listed = {r["run_id"]: r for r in store.list()}
    assert set(listed) == {"aa", "bb"}
    assert listed["bb"]["state"] == "interrupted" and listed["bb"]["activities"][0]["status"] == "Interrupted"
    assert store.get("cc") is None


def test_a_record_that_cant_be_removed_is_kept_for_the_next_trim(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "KEEP", 1)
    store = RunStore(tmp_path)
    for i, run_id in enumerate(("aa", "bb")):
        store.save(_record(run_id))
        os.utime(tmp_path / f"{run_id}.json", (time.time() - 100 + i, time.time() - 100 + i))
    real = Path.unlink

    def locked(self, *a, **k):
        if self.name == "aa.json":
            raise PermissionError("in use")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    store.trim()
    assert (tmp_path / "aa.json").exists() and (tmp_path / "bb.json").exists()
