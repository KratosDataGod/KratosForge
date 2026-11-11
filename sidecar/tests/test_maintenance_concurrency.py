"""Optimize and Vacuum on a local table while other processes read and write it.

Real processes, not threads: a reader and a writer in their own Python processes, as the app's
scheduled run or the agent would be, while this one runs maintenance.compact_local (the Maintain
page's Optimize: compaction then VACUUM) under the table's compaction lease.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import polars as pl
import pytest

from fabric_desktop import maintenance
from fabric_desktop.meta.store import LocalStore

SIDECAR = Path(__file__).resolve().parents[1]
BATCH = 50


def batch(start: int) -> pl.DataFrame:
    return pl.DataFrame({"id": list(range(start, start + BATCH)), "amount": [float(i) for i in range(BATCH)]})


def small_files(root: Path, batches: int) -> tuple[LocalStore, str]:
    """A table of ``batches`` small appends, so Optimize has files to compact."""
    store = LocalStore(root, "dev")
    for b in range(batches):
        store.write("LH_Bronze", "dbo", "events", batch(b * BATCH), mode="append")
    return store, store.uri("LH_Bronze", "dbo", "events")


def child(script: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), *args], cwd=SIDECAR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(p: subprocess.Popen[str]) -> dict:
    out, err = p.communicate(timeout=300)
    assert p.returncode == 0, err[-2000:]
    return json.loads(out.strip().splitlines()[-1])


READER = """
    import json, sys, time
    from deltalake import DeltaTable
    uri, rows, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    reads, wrong, errors, end = 0, [], [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            t = DeltaTable(uri).to_pyarrow_table(columns=["id"])
            ids = t.column("id").to_pylist()
            if len(ids) != rows or len(set(ids)) != rows:
                wrong.append([len(ids), len(set(ids))])
            reads += 1
        except Exception as e:
            errors.append(repr(e)[:300])
    print(json.dumps({"reads": reads, "wrong": wrong[:5], "errors": errors[:5]}), flush=True)
    # deltalake's to_pyarrow_table aborts the interpreter at exit ("terminate called without an
    # active exception"), even with no maintenance running; it happens after the result is out.
    import os
    os._exit(0)
"""

WRITER = """
    import json, sys
    import polars as pl
    from pathlib import Path
    from fabric_desktop.meta.store import LocalStore
    root, first, batches, size = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    store = LocalStore(root, "dev")
    done, errors = 0, []
    for b in range(batches):
        start = first + b * size
        df = pl.DataFrame({"id": list(range(start, start + size)), "amount": [0.0] * size})
        try:
            store.write("LH_Bronze", "dbo", "events", df, mode="append")
            done += 1
        except Exception as e:
            errors.append(repr(e)[:300])
    print(json.dumps({"done": done, "errors": errors[:5]}))
"""


def optimize(store: LocalStore, uri: str, rounds: int) -> list[dict]:
    out = []
    for _ in range(rounds):
        with store.compaction_lease("LH_Bronze", "dbo", "events", wait=120):
            out.append(maintenance.compact_local(uri))
    return out


def test_a_reader_in_another_process_sees_every_row_once_while_optimize_and_vacuum_run(tmp_path):
    store, uri = small_files(tmp_path, 40)
    reader = child(READER, uri, str(40 * BATCH), "6")
    time.sleep(1)  # the reader is reading
    results = optimize(store, uri, 3)
    seen = finish(reader)
    assert results[0]["files_removed"] >= 40 and results[0]["files_added"] >= 1, results
    assert seen["reads"] > 0 and seen["wrong"] == [] and seen["errors"] == [], seen


def test_an_appender_in_another_process_racing_optimize_loses_no_row(tmp_path):
    store, uri = small_files(tmp_path, 20)
    writer = child(WRITER, str(tmp_path), str(100_000), "30", str(BATCH))
    results = optimize(store, uri, 4)
    wrote = finish(writer)
    assert wrote["done"] == 30 and wrote["errors"] == [], wrote
    optimize(store, uri, 1)  # and once more after the last append
    ids = store.read("LH_Bronze", "dbo", "events")["id"].to_list()
    assert len(ids) == len(set(ids)) == 20 * BATCH + 30 * BATCH, (len(ids), len(set(ids)), results)


@pytest.mark.parametrize("days", [0, 1, 6])
def test_vacuum_refuses_less_than_the_safe_minimum_and_says_why(tmp_path, days):
    store, uri = small_files(tmp_path, 3)
    before = sorted(p.name for p in Path(uri).glob("*.parquet"))
    with pytest.raises(ValueError, match="at least 7 days") as caught:
        maintenance.compact_local(uri, days)
    assert "readers" in str(caught.value) or "time travel" in str(caught.value)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.vacuum_preview(uri, days)
    assert sorted(p.name for p in Path(uri).glob("*.parquet")) == before  # nothing was compacted or deleted
    assert store.read("LH_Bronze", "dbo", "events").height == 3 * BATCH


def test_a_policy_can_allow_a_shorter_retention(tmp_path):
    _store, uri = small_files(tmp_path, 3)
    assert maintenance.compact_local(uri, 1, allow_shorter=True)["files_removed"] >= 3
    assert isinstance(maintenance.vacuum_preview(uri, 0, allow_shorter=True), list)
