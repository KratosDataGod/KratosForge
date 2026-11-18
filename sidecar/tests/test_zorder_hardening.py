"""A notebook's OPTIMIZE ... ZORDER BY (lakehouse.maintain_table(..., zorder=[...]), delta-rs's z_order on this
PC) while other processes read and write the table, and on odd inputs. Real processes, not threads: an
appender or a reader in its own Python process, as the app's scheduled run or the agent would be. Every
assertion here is behaviour seen with deltalake 1.6.6; nothing here claims a saving."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

SIDECAR = Path(__file__).resolve().parents[1]
BATCH = 50


@pytest.fixture
def allowed(lake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    return lake


def events(lake: Path, batches: int, partition_by: list[str] | None = None) -> str:
    """dbo.events in ``batches`` small appends of BATCH rows each, so Z-Order has files to rewrite."""
    uri = str(lake / "dbo" / "events")
    for b in range(batches):
        start = b * BATCH
        write_deltalake(uri, pa.table({"id": list(range(start, start + BATCH)),
                                       "region": [("EU", "US", "APAC")[i % 3] for i in range(BATCH)],
                                       "amount": [float(i) for i in range(BATCH)]}),
                        mode="append", partition_by=partition_by)
    return uri


def ids(uri: str, at: int | None = None) -> list[int]:
    return DeltaTable(uri, version=at).to_pyarrow_table(columns=["id"]).column("id").to_pylist()


def child(script: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), *args], cwd=SIDECAR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(p: subprocess.Popen[str]) -> dict:
    out, err = p.communicate(timeout=300)
    assert p.returncode == 0, err[-2000:]
    return json.loads(out.strip().splitlines()[-1])


def wait_for(check, what: str, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, f"timed out waiting for {what}"
        time.sleep(0.02)


# Reads until a stop file appears, so the reads cover every Z-Order round however long they take. It
# touches a ready file after its first whole read, so maintenance starts only once it is reading.
READER = """
    import json, os, sys
    from pathlib import Path
    from deltalake import DeltaTable
    uri, rows, ready, stop = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    reads, versions, wrong, errors = 0, set(), [], []
    while True:
        last = stop.exists()
        try:
            t = DeltaTable(uri)
            got = t.to_pyarrow_table(columns=["id"]).column("id").to_pylist()
            if len(got) != rows or len(set(got)) != rows:
                wrong.append([t.version(), len(got), len(set(got))])
            versions.add(t.version())
            reads += 1
        except Exception as e:
            errors.append(repr(e)[:300])
        ready.touch()
        if last:
            break
    print(json.dumps({"reads": reads, "versions": sorted(versions), "wrong": wrong[:5], "errors": errors[:5]}),
          flush=True)
    os._exit(0)  # deltalake's reader can abort or hang the interpreter at exit; the result is already out
"""

# Blind appends of ``batches`` x ``size`` new ids, starting at ``first``, straight to the table folder.
WRITER = """
    import json, os, sys
    import pyarrow as pa
    from deltalake import write_deltalake
    uri, first, batches, size = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    done, errors = 0, []
    for b in range(batches):
        start = first + b * size
        try:
            write_deltalake(uri, pa.table({"id": list(range(start, start + size)),
                                           "region": ["EU"] * size, "amount": [0.0] * size}), mode="append")
            done += 1
        except Exception as e:
            errors.append(repr(e)[:300])
    print(json.dumps({"done": done, "errors": errors[:5]}), flush=True)
    os._exit(0)
"""


def zorder(where=None):
    return lakehouse.maintain_table("dbo.events", "optimize", zorder=["amount", "id"], where=where)


# ---- concurrency

def test_an_appender_in_another_process_racing_zorder_loses_no_row_and_duplicates_none(allowed):
    uri = events(allowed, 20)
    start = DeltaTable(uri).version()
    writer = child(WRITER, uri, "100000", "40", str(BATCH))
    wait_for(lambda: DeltaTable(uri).version() > start, "the appender's first commit")
    rounds = []
    while writer.poll() is None or not rounds:  # Z-Order again and again while the appends land
        rounds.append(zorder())
    wrote = finish(writer)
    assert wrote == {"done": 40, "errors": []}, wrote
    ops = [h["operation"] for h in DeltaTable(uri).history()][::-1]
    assert "WRITE" in ops[ops.index("OPTIMIZE"):], ops  # the race happened: appends landed between rounds
    rounds.append(zorder())  # and once more after the last append
    got = ids(uri)
    expected = set(range(20 * BATCH)) | set(range(100000, 100000 + 40 * BATCH))
    assert len(got) == len(set(got)) == len(expected) and set(got) == expected, (len(got), len(set(got)))
    assert all(r.metrics["plannerStrategy"] == "zOrder" for r in rounds)
    assert rounds[0].metrics["numFilesRemoved"] >= 20  # the first round rewrote the small files
    assert len(DeltaTable(uri).file_uris()) == 1  # the last round, with nothing racing it, left one file


def test_a_reader_in_another_process_sees_each_row_exactly_once_during_zorder(allowed, tmp_path):
    uri = events(allowed, 30)
    ready, stop = tmp_path / "reader.ready", tmp_path / "reader.stop"
    reader = child(READER, uri, str(30 * BATCH), str(ready), str(stop))
    wait_for(ready.exists, "the reader's first read")
    results = [zorder() for _ in range(3)]
    stop.touch()
    seen = finish(reader)
    assert [r.delta_version for r in results] == [30, 31, 32]  # each round committed a new version
    assert results[0].metrics["numFilesRemoved"] == 30 and results[0].metrics["numFilesAdded"] == 1
    assert seen["reads"] > 0 and seen["wrong"] == [] and seen["errors"] == [], seen
    assert seen["versions"][-1] == 32, seen  # its last read came after the last round
    assert sorted(ids(uri)) == list(range(30 * BATCH))


# ---- partitions, time travel and odd inputs

def test_zorder_where_a_partition_column_rewrites_only_that_partition(allowed):
    uri = events(allowed, 3, partition_by=["region"])
    t = DeltaTable(uri)
    before, rows = t.version(), sorted(ids(uri))
    others = sorted(u for u in t.file_uris() if "region=EU" not in u)
    result = zorder(where=[("region", "=", "EU")])
    after = DeltaTable(uri)
    assert result.delta_version == before + 1
    assert result.metrics["numFilesRemoved"] == 3 and result.metrics["partitionsOptimized"] == 1
    assert sorted(u for u in after.file_uris() if "region=EU" not in u) == others  # US and APAC untouched
    assert len([u for u in after.file_uris() if "region=EU" in u]) == 1
    assert sorted(ids(uri)) == rows


def test_zorder_where_no_partition_matches_commits_nothing(allowed):
    uri = events(allowed, 3, partition_by=["region"])
    before, files = DeltaTable(uri).version(), sorted(DeltaTable(uri).file_uris())
    result = zorder(where=[("region", "=", "NOWHERE")])
    assert result.delta_version == before and result.metrics["numFilesRemoved"] == 0
    assert sorted(DeltaTable(uri).file_uris()) == files


def test_zorder_by_a_partition_column_is_refused_and_changes_nothing(allowed):
    uri = events(allowed, 3, partition_by=["region"])
    before, files = DeltaTable(uri).version(), sorted(DeltaTable(uri).file_uris())
    with pytest.raises(Exception, match="cannot be partition columns"):
        lakehouse.maintain_table("dbo.events", "optimize", zorder=["id", "region"])
    assert DeltaTable(uri).version() == before and sorted(DeltaTable(uri).file_uris()) == files


@pytest.mark.parametrize("cols, says", [(["nope"], "hasn't got: nope"), (["id", "nope", "gone"], "nope, gone"),
                                        (["ID"], "hasn't got: ID")])  # names are matched as written
def test_zorder_by_a_missing_column_is_refused_and_changes_nothing(allowed, cols, says):
    uri = events(allowed, 3)
    before, files = DeltaTable(uri).version(), sorted(DeltaTable(uri).file_uris())
    with pytest.raises(ValueError, match=says) as said:
        lakehouse.maintain_table("dbo.events", "optimize", zorder=cols)
    assert "Nothing was changed" in str(said.value)
    assert DeltaTable(uri).version() == before and sorted(DeltaTable(uri).file_uris()) == files


def test_zorder_on_an_empty_table_commits_nothing(allowed):
    uri = str(allowed / "dbo" / "events")
    write_deltalake(uri, pa.table({"id": pa.array([], pa.int64()), "amount": pa.array([], pa.float64())}))
    result = lakehouse.maintain_table("dbo.events", "optimize", zorder=["id"])
    assert result.delta_version == 0 and result.metrics["numFilesRemoved"] == 0
    assert result.metrics["numFilesAdded"] == 0 and ids(uri) == []


def test_zorder_by_the_same_column_twice_keeps_every_row(allowed):
    uri = events(allowed, 3)
    result = lakehouse.maintain_table("dbo.events", "optimize", zorder=["id", "id"])
    assert result.delta_version == 3 and sorted(ids(uri)) == list(range(3 * BATCH))


def test_time_travel_to_the_version_before_zorder_reads_the_same_rows(allowed):
    uri = events(allowed, 5)
    before = DeltaTable(uri).version()
    rows = DeltaTable(uri).to_pyarrow_table().sort_by("id")
    first = zorder()
    second = zorder()  # a second Z-Order rewrites the one file again
    assert (first.delta_version, second.delta_version) == (before + 1, before + 2)
    for v in (before, before + 1, before + 2):
        assert DeltaTable(uri, version=v).to_pyarrow_table().sort_by("id").equals(rows), v
    old = DeltaTable(uri, version=before)
    assert len(old.file_uris()) == 5 and len(DeltaTable(uri).file_uris()) == 1
    history = DeltaTable(uri).history(2)
    assert [h["operation"] for h in history] == ["OPTIMIZE", "OPTIMIZE"]
    assert all(h["operationMetrics"]["plannerStrategy"] == "zOrder" for h in history)
