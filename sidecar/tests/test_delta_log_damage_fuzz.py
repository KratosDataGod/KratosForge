"""Fuzz the _delta_log of a local Delta table, as the local engine, the preview and maintenance read it.

Each case is a small real table (delta-rs) whose log is then damaged with a seeded mutation: a commit torn,
bytes flipped or deleted, a line dropped, copied or moved, an unknown action, a protocol or feature this PC
doesn't read, a broken checkpoint or _last_checkpoint, a remove of a file a version still reads, or an add or
remove naming a file outside the table's folder. Every case must be read correctly or refused by name, never
raise anything else, never read a file outside the table's folder, and never lose a file a readable version
still names: Compact (OPTIMIZE then VACUUM) keeps every row and every version inside the retention."""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from urllib.parse import unquote

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError

from fabric_desktop import delta_profile, delta_read, maintenance, preview
from fabric_desktop.localdata import engine

SEEDS = range(40)
REFUSED = (DeltaError, ValueError, OSError, duckdb.Error, delta_profile.ProfileViolation, pa.ArrowException)
OUTSIDE = -999  # the one row of a Parquet file beside the table, which no read may show

pytestmark = pytest.mark.filterwarnings("ignore")


def _commit(t: Path, v: int) -> Path:
    return t / "_delta_log" / f"{v:020d}.json"


def _write(t: Path, v: int, *actions: dict) -> None:
    lines = [json.dumps(a) for a in actions] + [json.dumps({"commitInfo": {"timestamp": int(time.time() * 1000),
                                                                           "operation": "WRITE"}})]
    _commit(t, v).write_text("\n".join(lines) + "\n")


def _build(rng: random.Random, root: Path) -> Path:
    t = root / "t"
    for i in range(rng.randint(2, 5)):
        write_deltalake(str(t), pa.table({"id": list(range(i * 10, i * 10 + rng.randint(1, 10)))}), mode="append")
    if rng.random() < 0.4:
        DeltaTable(str(t)).delete(f"id < {rng.randint(0, 30)}")
    if rng.random() < 0.4:
        DeltaTable(str(t)).create_checkpoint()
        write_deltalake(str(t), pa.table({"id": [2000]}), mode="append")
    pq.write_table(pa.table({"id": [OUTSIDE]}), root / "outside.parquet")
    return t


def _adds(t: Path) -> list[dict]:
    out = []
    for c in sorted((t / "_delta_log").glob("*.json")):
        for line in c.read_text(errors="replace").splitlines():
            try:
                add = json.loads(line).get("add")
            except ValueError:
                continue
            if isinstance(add, dict) and "path" in add:
                out.append(add)
    return out


def _mutate(rng: random.Random, t: Path, root: Path) -> str:
    log = t / "_delta_log"
    cs = sorted(log.glob("*.json"))
    c = rng.choice(cs)
    nxt = int(cs[-1].name[:20]) + 1
    outside = rng.choice(["../outside.parquet", "%2E%2E/outside.parquet", str(root / "outside.parquet"),
                          (root / "outside.parquet").as_uri(), "a/../../outside.parquet"])
    kind = rng.choice(["flip", "truncate", "delete", "dupline", "dropline", "garbage", "unknown", "protocol",
                       "lastcp", "checkpoint", "remove_live", "add_outside", "remove_outside"])
    if kind == "flip":
        b = bytearray(c.read_bytes())
        for _ in range(rng.randint(1, 4)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        c.write_bytes(bytes(b))
    elif kind == "truncate":
        c.write_bytes(c.read_bytes()[: rng.randrange(len(c.read_bytes()))])
    elif kind == "delete":
        c.unlink()
    elif kind in ("dupline", "dropline"):
        lines = c.read_text(errors="replace").splitlines() or [""]
        if kind == "dupline":
            lines.insert(rng.randrange(len(lines) + 1), rng.choice(lines))
        else:
            lines.pop(rng.randrange(len(lines)))
        c.write_text("\n".join(lines) + "\n")
    elif kind == "garbage":
        c.write_bytes(bytes(rng.randrange(256) for _ in range(rng.randint(0, 100))))
    elif kind == "unknown":
        _write(t, nxt, rng.choice([{"zzz": {}}, {"add": None}, {"remove": {"path": None}}, {"metaData": None}]))
    elif kind == "protocol":
        _write(t, nxt, {"protocol": rng.choice([
            {"minReaderVersion": 99, "minWriterVersion": 99},
            {"minReaderVersion": 3, "minWriterVersion": 7, "readerFeatures": ["qqq"], "writerFeatures": ["qqq"]},
            {"minReaderVersion": 1, "minWriterVersion": 7, "writerFeatures": ["qqq"]}])})
    elif kind == "lastcp":
        (log / "_last_checkpoint").write_text(rng.choice(['{"version": 2, "size": 3}', "garbage", "", "[]"]))
    elif kind == "checkpoint":
        try:
            DeltaTable(str(t)).create_checkpoint()
        except DeltaError:  # an earlier mutation already broke the log
            return kind
        cp = next(log.glob("*.checkpoint.parquet"))
        b = bytearray(cp.read_bytes())
        for _ in range(rng.randint(1, 20)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        cp.write_bytes(bytes(b))
    elif kind == "remove_live":
        add = rng.choice(_adds(t))
        _write(t, nxt, {"remove": {"path": add["path"], "deletionTimestamp": int(time.time() * 1000),
                                   "dataChange": True, "size": add["size"]}})
    elif kind == "add_outside":
        _write(t, nxt, {"add": {"path": outside, "partitionValues": {}, "size": 100, "modificationTime": 0,
                                "dataChange": True, "stats": json.dumps({"numRecords": 1})}})
    else:
        _write(t, nxt, {"remove": {"path": outside, "deletionTimestamp": 1, "dataChange": True}})
    return kind


def _rows(t: Path) -> list[int]:
    return sorted(delta_read.arrow(DeltaTable(str(t))).column("id").to_pylist())


def _try(f):
    """("ok", value), or ("refused", name) for a refusal by name; anything else is raised."""
    try:
        return "ok", f()
    except REFUSED as exc:
        return "refused", type(exc).__name__


def _named_by_readable_versions(t: Path) -> set[str]:
    named: set[str] = set()
    try:
        latest = DeltaTable(str(t)).version()
    except DeltaError:
        return named
    for v in range(latest + 1):
        try:
            named |= {unquote(u.removeprefix("file://")) for u in DeltaTable(str(t), version=v).file_uris()}
        except (DeltaError, OSError):
            continue
    return named


@pytest.mark.parametrize("seed", SEEDS)
def test_a_damaged_log_is_read_right_or_refused_and_loses_nothing(tmp_path, seed):
    rng = random.Random(seed)
    t = _build(rng, tmp_path)
    kinds = [_mutate(rng, t, tmp_path) for _ in range(rng.randint(1, 2))]
    outside = (tmp_path / "outside.parquet").read_bytes()

    read = _try(lambda: _rows(t))
    picked = _try(lambda: sorted(r[0] for r in duckdb.connect().execute(engine.delta_select(str(t))[0]).fetchall()))
    assert picked[0] == "refused" or OUTSIDE not in picked[1], kinds
    if read[0] == picked[0] == "ok":
        assert read[1] == picked[1], kinds
    shown = _try(lambda: preview.table_preview(DeltaTable(str(t))))
    if shown[0] == read[0] == "ok":
        assert shown[1]["row_count"] in (None, len(read[1])), kinds
    _try(lambda: delta_profile.check_table(DeltaTable(str(t))))
    _try(lambda: maintenance.health(str(t), local=True))
    _try(lambda: maintenance.vacuum_preview(str(t), 7))
    _try(lambda: maintenance.stopped_run_leftovers(str(t), time.time() - 3600))

    named = {p for p in _named_by_readable_versions(t) if os.path.exists(p)}
    compacted = _try(lambda: maintenance.compact_local(str(t), 7))
    assert (tmp_path / "outside.parquet").read_bytes() == outside, kinds
    assert not [p for p in named if not os.path.exists(p)], kinds  # every readable version keeps its files
    if read[0] == compacted[0] == "ok":
        assert _rows(t) == read[1], kinds


# ---- What the fuzz found


def _table(root: Path, commits: int = 3) -> Path:
    t = root / "t"
    for i in range(commits):
        write_deltalake(str(t), pa.table({"id": [i * 10 + j for j in range(10)]}), mode="append")
    pq.write_table(pa.table({"id": [OUTSIDE]}), root / "outside.parquet")
    return t


@pytest.mark.parametrize("path", ["../outside.parquet", "%2E%2E/outside.parquet", "a/../../outside.parquet",
                                  "{root}/outside.parquet", "file://{root}/outside.parquet"])
def test_a_local_read_refuses_a_log_that_names_a_file_outside_the_table(tmp_path, path):
    """SECURITY: the local engine read the rows of a file outside the table's folder that an add names."""
    t = _table(tmp_path)
    path = path.format(root=tmp_path.as_posix())
    _write(t, 3, {"add": {"path": path, "partitionValues": {}, "size": 100, "modificationTime": 0, "dataChange": True}})
    with pytest.raises(ValueError, match="names a data file outside the table's folder"):
        engine.delta_select(str(t))


def test_a_local_read_still_reads_a_file_named_by_its_full_uri_inside_the_table(tmp_path):
    t = _table(tmp_path, 1)
    add = _adds(t)[0]
    _write(t, 1, {"remove": {"path": add["path"], "deletionTimestamp": int(time.time() * 1000), "dataChange": False}},
           {"add": dict(add, path=(t / unquote(add["path"])).as_uri())})
    sql, _info = engine.delta_select(str(t))
    assert duckdb.connect().execute(f"SELECT count(*) FROM ({sql})").fetchone() == (10,)

