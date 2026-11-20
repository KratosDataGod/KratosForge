"""Optimize and Vacuum on a local table while other processes read and write it.

Real processes, not threads: a reader and a writer in their own Python processes, as the app's
scheduled run or the agent would be, while this one runs maintenance.compact_local (the Maintain
page's Optimize: compaction then VACUUM) under the table's compaction lease. The tests after
"a reader and a writer at once" run a reader and a writer together, against each of the Maintain
page's Optimize and a notebook's OPTIMIZE and OPTIMIZE ... ZORDER BY (lakehouse.maintain_table),
each followed by VACUUM, and reuse test_zorder_hardening's table and helpers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance
from fabric_desktop.meta.store import LocalStore
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_vacuum_hardening import _age_removes
from tests.test_zorder_hardening import allowed, events, ids, wait_for  # noqa: F401 - allowed is a fixture

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


# ---- a reader and a writer at once
#
# Maintenance runs while a reader and a writer, each in its own process, use the table at the same
# time. Ids come in whole batches of BATCH (key = id // BATCH): the table's own first rows, then each
# writer's batches from a key of its own, so every snapshot the reader takes is checked against what
# the writer says it committed, in order. The processes wait on files, never on the clock.

APPENDER_KEY, OVERWRITER_KEY = 2000, 4000

# For each version it reads, how many rows each key has, and any duplicate id or failed read. Reads
# until a stop file appears; touches a ready file after each whole read.
LIVE_READER = """
    import json, os, sys
    from collections import Counter
    from pathlib import Path
    from deltalake import DeltaTable
    uri, size, ready, stop = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    seen, dups, changed, errors, failures, reads = {}, [], [], [], 0, 0
    while True:
        last = stop.exists()
        try:
            t = DeltaTable(uri)
            got = t.to_pyarrow_table(columns=["id"]).column("id").to_pylist()
            v = str(t.version())
            if len(got) != len(set(got)):
                dups.append([v, len(got), len(set(got))])
            keys = {str(k): n for k, n in sorted(Counter(i // size for i in got).items())}
            if seen.setdefault(v, keys) != keys:
                changed.append(v)  # one version read twice with different rows
            reads += 1
            ready.touch()
        except Exception as e:
            failures += 1
            if len(errors) < 5:
                errors.append(f"{type(e).__name__}: {e}"[:300])
        if last:
            break
    print(json.dumps({"reads": reads, "seen": seen, "dups": dups[:5], "changed": changed[:5],
                      "failures": failures, "errors": errors}), flush=True)
    os._exit(0)  # deltalake's reader can abort or hang the interpreter at exit; the result is already out
"""

# Blind appends of whole batches with delta-rs, each loading the table afresh. Half way it touches a
# file and waits for a gate, so the test knows a maintenance round finished between two of its appends.
# Each batch is recorded as committed, or as failed with the exception's type name. On Windows an append
# that loses the race for a version to maintenance's own commit can be refused as the staged commit
# (_delta_log/NNN.json#1, os error 5) instead of being made on the next version: nothing was committed, so
# it is sent again, as the app's own appends do (LocalStore.write, APPEND_RACE_ATTEMPTS). Any other
# failure is recorded.
APPENDER = """
    import json, os, sys, time
    from pathlib import Path
    import pyarrow as pa
    from deltalake import write_deltalake
    from fabric_desktop.meta.store import APPEND_RACE_ATTEMPTS
    from fabric_desktop.retrying import staged_commit_race
    uri, first, batches, size = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    half, gate = Path(sys.argv[5]), Path(sys.argv[6])
    ok, failed, staged = [], [], 0
    for b in range(batches):
        if b == batches // 2:
            half.touch()
            end = time.monotonic() + 120
            while not gate.exists() and time.monotonic() < end:
                time.sleep(0.02)
        start = first + b * size
        rows = pa.table({"id": pa.array(range(start, start + size), pa.int64()), "region": ["EU"] * size,
                         "amount": [0.0] * size})
        for attempt in range(APPEND_RACE_ATTEMPTS):
            try:
                write_deltalake(uri, rows, mode="append")
                ok.append(b)
                break
            except Exception as e:
                if staged_commit_race(e) and attempt < APPEND_RACE_ATTEMPTS - 1:
                    staged += 1
                    time.sleep(0.05)
                    continue
                failed.append([b, type(e).__name__, str(e)[:300]])
                break
    print(json.dumps({"ok": ok, "failed": failed, "staged_races": staged}), flush=True)
    os._exit(0)
"""

# A read-merge-overwrite, as a notebook's read then saveAsTable is: it reads the table, adds batch k
# and replaces every row with predicate "true", which makes delta-rs check for commits since the version
# it read. Between its read and its commit it touches read.<k> and waits for go.<k>; then touches done.<k>.
OVERWRITER = """
    import json, os, sys, time
    from pathlib import Path
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake
    uri, first, batches, size = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    sync = Path(sys.argv[5])
    ok, failed = [], []
    for k in range(batches):
        t = DeltaTable(uri)
        old = t.to_pyarrow_table()
        start = first + k * size
        new = pa.table({"id": pa.array(range(start, start + size), pa.int64()), "region": ["EU"] * size,
                        "amount": [0.0] * size}).cast(old.schema)
        (sync / f"read.{k}").touch()
        end = time.monotonic() + 120
        while not (sync / f"go.{k}").exists() and time.monotonic() < end:
            time.sleep(0.02)
        try:
            write_deltalake(t, pa.concat_tables([old, new]), mode="overwrite", predicate="true")
            ok.append(k)
        except Exception as e:
            failed.append([k, type(e).__name__, str(e)[:300]])
        (sync / f"done.{k}").touch()
    print(json.dumps({"ok": ok, "failed": failed}), flush=True)
    os._exit(0)
"""

# The three ways maintenance runs on this PC, each with VACUUM at the 7-day minimum: the Maintain page's
# Optimize (compaction then VACUUM), and a notebook's OPTIMIZE or OPTIMIZE ... ZORDER BY, then VACUUM.
KINDS = ["maintain-page", "notebook-optimize", "notebook-zorder"]


def maintain(kind: str, uri: str) -> None:
    if kind == "maintain-page":
        maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
        return
    lakehouse.maintain_table("dbo.events", "optimize", zorder=["amount", "id"] if kind == "notebook-zorder" else None)
    lakehouse.maintain_table("dbo.events", "vacuum")


def key_counts(uri: str, version: int | None = None) -> dict[int, int]:
    return dict(Counter(i // BATCH for i in ids(uri, version)))


def check_snapshots(seen: dict, base: dict[int, int], writer_key: int, ok: list[int]) -> None:
    """Each snapshot the reader took holds the base rows, and the batches the writer committed: whole,
    in its commit order, never fewer at a later version, and none it didn't commit. Nothing else."""
    assert seen, "the reader read nothing"
    shown = 0
    for v in sorted(seen, key=int):
        keys = {int(k): n for k, n in seen[v].items()}
        assert {k: keys.pop(k, 0) for k in base} == base, (v, seen[v])  # no base row lost or doubled
        mine = {k - writer_key: keys.pop(k) for k in sorted(keys) if writer_key <= k < writer_key + 1000}
        assert all(n == BATCH for n in mine.values()), (v, mine)  # never part of a batch
        assert list(mine) == ok[:len(mine)], (v, list(mine), ok)  # no loss, nothing uncommitted
        assert len(mine) >= shown, (v, len(mine), shown)
        shown = len(mine)
        assert keys == {}, (v, keys)  # no row from anywhere else


def expected_ids(base_ids: list[int], writer_key: int, ok: list[int]) -> list[int]:
    return sorted(base_ids + [(writer_key + b) * BATCH + i for b in ok for i in range(BATCH)])


def assert_read_cleanly(seen: dict) -> None:
    assert seen["reads"] > 0 and seen["failures"] == 0, seen["errors"]  # never a missing file, nor any error
    assert seen["dups"] == [] and seen["changed"] == [], seen


def assert_conflicts_only(wrote: dict, batches: int) -> None:
    """Every write either committed or failed with delta-rs's named commit conflict."""
    assert all(name == "CommitFailedError" for _b, name, _msg in wrote["failed"]), wrote["failed"]
    assert sorted(wrote["ok"] + [b for b, _n, _m in wrote["failed"]]) == list(range(batches)), wrote


@pytest.mark.parametrize("kind", KINDS)
def test_maintenance_with_a_reader_and_an_appender_in_other_processes_loses_and_duplicates_nothing(
        allowed, tmp_path, kind):  # noqa: F811
    uri = events(allowed, 20)
    base, base_ids = key_counts(uri), ids(uri)
    ready, stop, half, gate = (tmp_path / n for n in ("reader.ready", "reader.stop", "half", "gate"))
    reader = child(LIVE_READER, uri, str(BATCH), str(ready), str(stop))
    writer = child(APPENDER, uri, str(APPENDER_KEY * BATCH), "30", str(BATCH), str(half), str(gate))
    rounds = 0
    try:
        wait_for(ready.exists, "the reader's first read")
        wait_for(half.exists, "the appender's first half")
        maintain(kind, uri)  # the first round rewrites the 20 small files and the appender's first 15
        rounds += 1
        gate.touch()  # the second half of the appends lands while maintenance runs again and again
        while writer.poll() is None and rounds < 200:
            maintain(kind, uri)
            rounds += 1
    finally:
        gate.touch()
        stop.touch()
    wrote = finish(writer)
    seen = finish(reader)
    assert rounds < 200, "the appender didn't finish"
    assert_conflicts_only(wrote, 30)
    assert_read_cleanly(seen)
    check_snapshots(seen["seen"], base, APPENDER_KEY, wrote["ok"])
    ops = [h["operation"] for h in DeltaTable(uri).history()][::-1]
    assert "WRITE" in ops[ops.index("OPTIMIZE"):], ops  # appends landed after a maintenance round
    maintain(kind, uri)  # and once more with nothing racing it
    assert sorted(ids(uri)) == expected_ids(base_ids, APPENDER_KEY, wrote["ok"])


@pytest.mark.parametrize("kind", KINDS)
def test_a_writer_whose_read_predates_a_maintenance_commit_fails_with_commit_failed_and_loses_nothing(
        allowed, tmp_path, kind):  # noqa: F811
    """A read-merge-overwrite caught between its read and its commit by a maintenance commit is refused
    with delta-rs's CommitFailedError, rather than committed over the files maintenance rewrote; one with
    no commit in between goes through. A reader reads all along."""
    uri = events(allowed, 20)
    base, base_ids = key_counts(uri), ids(uri)
    ready, stop, sync = tmp_path / "reader.ready", tmp_path / "reader.stop", tmp_path / "sync"
    sync.mkdir()
    writes = 6
    reader = child(LIVE_READER, uri, str(BATCH), str(ready), str(stop))
    writer = child(OVERWRITER, uri, str(OVERWRITER_KEY * BATCH), str(writes), str(BATCH), str(sync))
    committed_between: list[bool] = []
    try:
        wait_for(ready.exists, "the reader's first read")
        for k in range(writes):
            wait_for((sync / f"read.{k}").exists, f"the writer's read {k}")
            before = DeltaTable(uri).version()
            if k % 2 == 0:
                maintain(kind, uri)  # while the writer holds what it read
            # A rewrite (OPTIMIZE) or a property change in between conflicts; a VACUUM's own commits don't.
            ops = [h["operation"] for h in DeltaTable(uri).history() if h["version"] > before]
            committed_between.append(any(not op.startswith("VACUUM") for op in ops))
            (sync / f"go.{k}").touch()
            wait_for((sync / f"done.{k}").exists, f"the writer's commit {k}")
    finally:
        for k in range(writes):
            (sync / f"go.{k}").touch()
        stop.touch()
    wrote = finish(writer)
    seen = finish(reader)
    assert committed_between[0], "the first maintenance round committed nothing"  # the race happened
    assert_conflicts_only(wrote, writes)
    assert [b for b, _n, _m in wrote["failed"]] == [k for k in range(writes) if committed_between[k]], wrote
    assert_read_cleanly(seen)
    check_snapshots(seen["seen"], base, OVERWRITER_KEY, wrote["ok"])
    assert sorted(ids(uri)) == expected_ids(base_ids, OVERWRITER_KEY, wrote["ok"])


# ---- VACUUM and the versions inside the retention

# Holds a snapshot of one version, opened before maintenance; once go appears, reads it through that
# snapshot and through a new one of the same version.
PINNED = """
    import json, os, sys, time
    from pathlib import Path
    from deltalake import DeltaTable
    uri, version, ready, go = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    held = DeltaTable(uri, version=version)
    ready.touch()
    end = time.monotonic() + 300
    while not go.exists() and time.monotonic() < end:
        time.sleep(0.02)
    out = {}
    for name, open_ in (("held", lambda: held), ("reopened", lambda: DeltaTable(uri, version=version))):
        try:
            out[name] = sorted(open_().to_pyarrow_table(columns=["id"]).column("id").to_pylist())
        except Exception as e:
            out[name] = f"{type(e).__name__}: {e}"[:300]
    print(json.dumps(out), flush=True)
    os._exit(0)
"""

DAY_MS = 24 * 3600 * 1000


def removed_at(uri: str) -> dict[str, int]:
    """When the log says each removed data file was removed, in epoch milliseconds."""
    out = {}
    for commit in sorted((Path(uri) / "_delta_log").glob("*.json")):
        for line in commit.read_text().splitlines():
            action = json.loads(line)
            if "remove" in action:
                out[unquote(action["remove"]["path"])] = int(action["remove"]["deletionTimestamp"])
    return out


def named(uri: str, version: int) -> set[str]:
    return {unquote(u.rsplit("/", 1)[-1]) for u in DeltaTable(uri, version=version).file_uris()}


def test_vacuum_at_the_minimum_keeps_every_file_a_version_inside_the_retention_names(allowed, tmp_path):  # noqa: F811
    """Versions 0 to 3 are one-row overwrites, each removing the file before it: removed 20, 10 and 2 days
    ago (the commit's deletionTimestamp rewritten, as test_vacuum_hardening does). Then ten small appends.
    VACUUM at 7 days, run while a reader, an appender and two readers pinned to old versions are open,
    deletes version 0's and 1's files and none that a version inside the 7 days names."""
    uri = str(allowed / "dbo" / "events")
    for v in range(4):
        write_deltalake(uri, pa.table({"id": [v], "region": ["EU"], "amount": [0.0]}), mode="overwrite")
    for v, days in enumerate((20, 10, 2)):
        _age_removes(uri, v + 1, days)
    for b in range(1, 11):
        write_deltalake(uri, pa.table({"id": list(range(b * BATCH, (b + 1) * BATCH)), "region": ["EU"] * BATCH,
                                       "amount": [0.0] * BATCH}), mode="append")
    latest = DeltaTable(uri).version()
    by_version = {v: named(uri, v) for v in range(latest + 1)}
    base, base_ids = key_counts(uri), ids(uri)
    pinned_rows = {2: [2], latest: sorted(base_ids)}  # version 2's file was removed 2 days ago
    ready, stop, half, gate, go = (tmp_path / n for n in ("reader.ready", "reader.stop", "half", "gate", "go"))
    reader = child(LIVE_READER, uri, str(BATCH), str(ready), str(stop))
    writer = child(APPENDER, uri, str(APPENDER_KEY * BATCH), "20", str(BATCH), str(half), str(gate))
    pinned = {v: child(PINNED, uri, str(v), str(tmp_path / f"pinned.{v}"), str(go)) for v in pinned_rows}
    first: dict = {}
    rounds = 0
    try:
        for v in pinned:
            wait_for((tmp_path / f"pinned.{v}").exists, f"the reader pinned to version {v}")
        wait_for(ready.exists, "the reader's first read")
        wait_for(half.exists, "the appender's first half")
        first = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
        rounds += 1
        gate.touch()
        while writer.poll() is None and rounds < 200:
            maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
            rounds += 1
        lakehouse.maintain_table("dbo.events", "vacuum")  # and a notebook's VACUUM, at the table's 7 days
    finally:
        gate.touch()
        go.touch()
        stop.touch()
    wrote = finish(writer)
    seen = finish(reader)
    reads = {v: finish(p) for v, p in pinned.items()}
    assert rounds < 200, "the appender didn't finish"
    assert first["vacuumed"] == 2, first  # version 0's and 1's files, removed 20 and 10 days ago
    for v, rows in pinned_rows.items():  # a reader pinned to a version inside the retention still reads it
        assert reads[v] == {"held": rows, "reopened": rows}, (v, reads[v])
    gone = {f for files in by_version.values() for f in files if not (Path(uri) / f).exists()}
    assert gone == by_version[0] | by_version[1], gone
    removed, week_ago = removed_at(uri), int(time.time() * 1000) - 7 * DAY_MS
    assert all(removed[f] < week_ago for f in gone), {f: removed.get(f) for f in gone}
    for v in range(2, DeltaTable(uri).version() + 1):  # and every version since, the race's included
        missing = sorted(f for f in named(uri, v) if not (Path(uri) / f).exists())
        assert missing == [], (v, missing)
    assert_conflicts_only(wrote, 20)
    assert_read_cleanly(seen)
    check_snapshots(seen["seen"], base, APPENDER_KEY, wrote["ok"])
    assert sorted(ids(uri)) == expected_ids(base_ids, APPENDER_KEY, wrote["ok"])


def test_compact_tries_again_when_a_writer_wins_the_same_commit_on_windows(tmp_path, monkeypatch):
    """Windows CI: a writer committing the same next version at the same moment leaves compaction unable to open
    its staged log file (``NNN.json#1``, os error 5). Nothing was committed, so Compact compacts again and the
    table keeps every row."""
    from deltalake.table import TableOptimizer
    uri = str(tmp_path / "t")
    for b in range(3):
        write_deltalake(uri, pa.table({"id": [b]}), mode="append")
    real, calls = TableOptimizer.compact, []

    def racing(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(f"Generic LocalFileSystem error: Unable to open file {uri}\\_delta_log\\"
                          "00000000000000000004.json#1: Access is denied. (os error 5)")
        return real(self, *a, **k)
    monkeypatch.setattr(TableOptimizer, "compact", racing)
    out = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    assert len(calls) == 2 and out["files_added"] == 1 and out["files_removed"] == 3
    assert sorted(ids(uri)) == [0, 1, 2]


def test_compact_doesnt_repeat_any_other_failure(tmp_path, monkeypatch):
    from deltalake.table import TableOptimizer
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [0]}), mode="append")
    calls = []
    monkeypatch.setattr(TableOptimizer, "compact", lambda self, *a, **k: calls.append(1) or (_ for _ in ()).throw(
        OSError("Generic LocalFileSystem error: disk full (os error 112)")))
    with pytest.raises(OSError, match="disk full"):
        maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    assert calls == [1]
