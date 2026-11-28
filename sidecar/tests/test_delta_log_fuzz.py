"""Fuzz the readers of Delta JSON commit files (_delta_log/NNN.json) that other tools write: the Academy's
delta_table check, which reads a learner's table from its commits, and the stopped-run dry run
(maintenance.stopped_run_leftovers), which reads every commit still on disk to know which files a
version names. Each case damages one line of a valid commit: torn, a flipped byte, not UTF-8, a NUL, a
BOM, another JSON type, an action without a field or with a value of another type, deep nesting, a
number too long for Python, or a schema that isn't a list of columns.

The check must pass or fail with a plain message; the dry run must report, or refuse because a commit
can't be read. Neither may raise, and the dry run may never guess: a line it can't read might name a
file, so it reports no files rather than calling one a leftover."""

from __future__ import annotations

import json
import random
import shutil
import time
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import maintenance
from fabric_desktop.academy import validators as v
from fabric_desktop.academy import workspace as ws

from tests.settings_fuzz import ODD_VALUES

COMMIT = "0" * 20 + ".json"
SCHEMA = json.dumps({"type": "struct", "fields": [{"name": "id", "type": "long", "nullable": True, "metadata": {}}]})
ACTIONS = [
    {"commitInfo": {"timestamp": 1, "operation": "WRITE"}},
    {"protocol": {"minReaderVersion": 1, "minWriterVersion": 2}},
    {"metaData": {"id": "x", "format": {"provider": "parquet", "options": {}}, "schemaString": SCHEMA,
                  "partitionColumns": [], "configuration": {}, "createdTime": 1}},
    {"add": {"path": "part-00000-a.parquet", "partitionValues": {}, "size": 100, "modificationTime": 1, "dataChange": True}},
    {"remove": {"path": "part-00000-b.parquet", "deletionTimestamp": 1, "dataChange": True}},
]


def _damaged(rnd: random.Random) -> tuple[str, bytes]:
    acts = json.loads(json.dumps(ACTIONS))
    lines = [json.dumps(a) for a in acts]
    kind = rnd.choice(("torn", "flip", "type", "nest", "drop", "odd", "huge", "inf", "bom", "nul", "utf8",
                       "dup", "schema", "list line"))
    j = rnd.randrange(len(lines))
    if kind == "torn":
        lines[j] = lines[j][:rnd.randrange(len(lines[j]))]
    elif kind == "flip":
        raw = bytearray("\n".join(lines).encode())
        raw[rnd.randrange(len(raw))] = rnd.randrange(256)
        return kind, bytes(raw)
    elif kind == "type":
        lines[j] = json.dumps(rnd.choice(ODD_VALUES), allow_nan=True)
    elif kind == "nest":
        lines[j] = '{"add":' * 5000 + "1" + "}" * 5000
    elif kind == "drop":
        inner = next(iter(acts[j].values()))
        inner.pop(rnd.choice(list(inner)))
        lines[j] = json.dumps(acts[j])
    elif kind == "odd":
        key = next(iter(acts[j]))
        if rnd.random() < 0.5:
            acts[j][key] = rnd.choice(ODD_VALUES)
        else:
            acts[j][key][rnd.choice(list(acts[j][key]))] = rnd.choice(ODD_VALUES)
        lines[j] = json.dumps(acts[j], allow_nan=True)
    elif kind == "huge":
        lines[j] = '{"add": {"path": "part-00000-c.parquet", "size": ' + "9" * 5000 + "}}"
    elif kind == "inf":
        lines[3] = lines[3].replace('"size": 100', '"size": 1e999')
    elif kind == "bom":
        lines[0] = "﻿" + lines[0]
    elif kind == "nul":
        lines[j] = lines[j].replace(",", ",\x00", 1)
    elif kind == "utf8":
        raw = "\n".join(lines).encode()
        return kind, raw[:20] + b"\xff\xfe" + raw[20:]
    elif kind == "dup":
        lines[j] = lines[j][:-1] + ', "add": {"path": "part-00000-d.parquet", "size": 1}}'
    elif kind == "schema":
        acts[2]["metaData"]["schemaString"] = json.dumps(rnd.choice(
            [{"fields": rnd.choice(ODD_VALUES)}, {"fields": [rnd.choice(ODD_VALUES)]}, rnd.choice(ODD_VALUES)]),
            allow_nan=True)
        lines[2] = json.dumps(acts[2])
    else:
        lines[j] = '["add", {"path": "part-00000-e.parquet"}]'
    return kind, ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("seed", [1, 2])
def test_any_damaged_commit_is_checked_or_refused_with_a_message(tmp_path, seed):
    rnd = random.Random(seed)
    work = ws.workspace_dir(tmp_path, "dp-700", "E37")
    log = work / "tables" / "t" / "_delta_log"
    for _ in range(400):
        kind, raw = _damaged(rnd)
        shutil.rmtree(log, ignore_errors=True)
        log.mkdir(parents=True)
        (log / COMMIT).write_bytes(raw)
        out = v.delta_table(work, "tables/t", columns=["id"])
        assert isinstance(out.get("detail"), str) and out["detail"], kind
        # the commit can be read back by the dry run's reader, or it is named as unreadable
        try:
            maintenance._named_anywhere(str(log.parent), None)
        except maintenance._Unreadable as exc:
            assert str(exc) == COMMIT


@pytest.mark.parametrize("line", [
    '{"add": {"path": "part-00000-c.parquet", "size": ' + "9" * 5000 + "}}",
    '{"add": {"path": "part-00000-c.parquet", "size": 1e999}}',
], ids=["huge int", "infinite size"])
def test_a_size_python_cant_hold_fails_the_check_with_a_message(tmp_path, line):
    work = ws.workspace_dir(tmp_path, "dp-700", "E37")
    log = work / "tables" / "t" / "_delta_log"
    log.mkdir(parents=True)
    (log / COMMIT).write_text(json.dumps(ACTIONS[0]) + "\n" + line + "\n", encoding="utf-8")
    out = v.delta_table(work, "tables/t")
    assert out["passed"] is False and ("isn't valid Delta JSON" in out["detail"] or "can't read" in out["detail"])


@pytest.mark.parametrize("fields", ["id", 5, [1], [None], {"name": "id"}])
def test_a_schema_that_isnt_a_list_of_columns_fails_the_check_with_a_message(tmp_path, fields):
    work = ws.workspace_dir(tmp_path, "dp-700", "E37")
    log = work / "tables" / "t" / "_delta_log"
    log.mkdir(parents=True)
    meta = json.loads(json.dumps(ACTIONS[2]))
    meta["metaData"]["schemaString"] = json.dumps({"fields": fields})
    (log / COMMIT).write_text(json.dumps(ACTIONS[0]) + "\n" + json.dumps(meta) + "\n", encoding="utf-8")
    out = v.delta_table(work, "tables/t", columns=["id"])
    assert out["passed"] is False and out["detail"] == "The table's Delta log has an entry the check can't read."


@pytest.mark.parametrize("damage", [
    b'{"add": {"path": "part-00000-x.parquet", "size": 1',
    b'\xff\xfe{"add": {"path": "part-00000-x.parquet"}}',
    b'["add", {"path": "part-00000-x.parquet"}]',
    b'{"add": {"path": "part-00000-x.parquet", "size": ' + b"9" * 5000 + b"}}",
], ids=["torn", "not utf-8", "not an object", "huge int"])
def test_the_stopped_run_dry_run_refuses_when_an_old_commit_cant_be_read(tmp_path, damage):
    """A commit older than the checkpoint, which delta-rs no longer reads, may still be on disk: a line
    there that can't be read might name a file, so no file is called a leftover."""
    uri = str(tmp_path / "t")
    for i in range(3):
        write_deltalake(uri, pl.DataFrame({"id": [i]}).to_arrow(), mode="append")
    DeltaTable(uri).create_checkpoint()
    write_deltalake(uri, pl.DataFrame({"id": [9]}).to_arrow(), mode="append")
    first = Path(uri) / "_delta_log" / COMMIT
    first.write_bytes(first.read_bytes().rstrip(b"\n") + b"\n" + damage + b"\n")
    time.sleep(0.05)
    found = maintenance.stopped_run_leftovers(uri, time.time())
    assert found["files"] == [] and found["bytes"] == 0
    assert found["refused"] == (f"its files can't be told from the table's own, because commit {COMMIT} in _delta_log "
                                "isn't valid Delta JSON, so they're left for Compact")
