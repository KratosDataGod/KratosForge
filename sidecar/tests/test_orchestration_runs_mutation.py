"""orchestration/runs.py paths that mutation testing (batch 52, docs/quality/mutation-testing.md) showed
no test pinned. These use the module's real limits (KEEP, the list default, the 64-character id), so a
changed limit or an off-by-one at the edge fails here. Records go to a temporary folder.
"""

import os
import time

import pytest

from fabric_desktop.orchestration import runs
from fabric_desktop.orchestration.runs import RunStore


def _hex(i: int) -> str:
    return f"{i:08x}"


def _fill(store: RunStore, folder, count: int, state: str = "succeeded") -> None:
    now = time.time()
    for i in range(count):
        store.save({"run_id": _hex(i), "state": state})
        os.utime(folder / f"{_hex(i)}.json", (now - 10_000 + i, now - 10_000 + i))  # newer as i grows


def test_trim_keeps_exactly_the_newest_200(tmp_path):
    assert runs.KEEP == 200
    store = RunStore(tmp_path)
    _fill(store, tmp_path, 201)
    store.trim()
    left = sorted(p.stem for p in tmp_path.glob("*.json"))
    assert len(left) == 200 and _hex(0) not in left and _hex(200) in left


def test_list_gives_50_by_default_and_at_least_one(tmp_path):
    store = RunStore(tmp_path)
    _fill(store, tmp_path, 60)
    assert len(store.list()) == 50
    assert [r["run_id"] for r in store.list(limit=0)] == [_hex(59)]  # the newest
    assert len(store.list(limit=500)) == 60


def test_a_run_id_may_be_64_characters_and_use_every_allowed_character(tmp_path):
    store = RunStore(tmp_path)
    full = "0123456789abcdefABCDEF-"  # pragma: allowlist secret (the allowed id characters, not a secret)
    store.save({"run_id": full, "state": "succeeded"})
    assert store.get(full)["run_id"] == full
    store.save({"run_id": "a" * 64, "state": "succeeded"})
    assert store.get("a" * 64) is not None
    with pytest.raises(ValueError):
        store.save({"run_id": "a" * 65, "state": "succeeded"})


@pytest.mark.parametrize("run_id", ["../x", "a/b", "a\\b", "c:x", "..", "a.b", ""])
def test_ids_with_other_characters_are_refused(tmp_path, run_id):
    with pytest.raises(ValueError):
        RunStore(tmp_path)._path(run_id)


def test_recover_runs_once_per_store(tmp_path):
    store = RunStore(tmp_path)
    store.save({"run_id": "aa", "state": "running"})
    assert store.recover() == 1
    # A run this process starts after recovery is never marked interrupted by a later read.
    store.save({"run_id": "bb", "state": "running"})
    assert store.recover() == 0
    assert store.get("bb")["state"] == "running"
    assert {r["run_id"]: r["state"] for r in store.list()} == {"aa": "interrupted", "bb": "running"}


def test_recover_leaves_the_runs_this_process_is_running(tmp_path):
    store = RunStore(tmp_path)
    store.save({"run_id": "aa", "state": "running"})
    store.save({"run_id": "bb", "state": "pending"})
    assert store.recover(live={"aa"}) == 1
    assert store.get("aa")["state"] == "running"
    assert store.get("bb")["state"] == "interrupted"


def test_an_interrupted_run_gets_an_end_time_and_keeps_one_it_had(tmp_path):
    store = RunStore(tmp_path)
    store.save({"run_id": "aa", "state": "running"})
    store.save({"run_id": "bb", "state": "running", "ended": "2026-11-01T10:00:00.000+00:00"})
    assert store.recover() == 2
    assert store.get("aa")["ended"].startswith("20") and store.get("aa")["ended"].endswith("+00:00")
    assert store.get("bb")["ended"] == "2026-11-01T10:00:00.000+00:00"
