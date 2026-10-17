"""Failure replay bundles (Prove stage 5, plan item 47): the kernel records what the cells read
(lakehouse.reads), and Save replay bundle writes the code up to the failed cell, those inputs,
the environment and the error with secrets removed to a zip in Downloads."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, replay
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel
from fabric_desktop.server import handle
from fabric_desktop.service import Service

LH = {"name": "LH_Bronze", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}

# Run as a cell: tables and files of a fake OneLake under ``root``, so the kernel's own lakehouse
# module (in the engine or in its own process) reads and writes them.
SETUP = """
import shutil
from pathlib import Path
from deltalake import DeltaTable as _DT, write_deltalake as _write
from fabric_desktop import lakehouse
_ROOT = Path({root!r})
{attach}
lakehouse.DeltaTable = lambda uri, **kw: _DT(str(_ROOT / "onelake" / uri.split("/Tables/", 1)[1]), version=kw.get("version"))

class _Files:
    def __init__(self, token):
        pass

    def download(self, ws, lh, rel, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_ROOT / "files" / rel, dest)
        return dest

def _publish(data, ref, mode, token):
    path = str(_ROOT / "onelake" / ref.schema / ref.table)
    _write(path, data, mode=mode)
    return _DT(path)

lakehouse.FileStore = _Files
lakehouse.publish = _publish
"""
ATTACH = 'lakehouse.attach(lakehouse.Attached("ws1", "lh1", "LH_Bronze", lambda: "tok", _ROOT / "work"))'


def lake(root: Path) -> None:
    sales = str(root / "onelake" / "dbo" / "sales")
    write_deltalake(sales, pa.table({"id": [1, 2], "amount": [10.0, 5.0]}))
    write_deltalake(sales, pa.table({"id": [3], "amount": [7.5]}), mode="append")  # version 1
    write_deltalake(str(root / "onelake" / "dbo" / "other"), pa.table({"id": [9], "amount": [1.0]}))  # version 0
    fx = root / "files" / "Files" / "raw" / "fx.csv"
    fx.parent.mkdir(parents=True)
    fx.write_bytes(b"currency,rate\nEUR,1.1\n")  # bytes: Windows would write \r\n


def finished(k, timeout=60):
    deadline = time.monotonic() + timeout
    snap = k.poll(-1, 0.5)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = k.poll(-1, 0.5)
    return snap


def keep_lakehouse(monkeypatch):
    """The in-engine kernel patches this process's lakehouse module: put it back afterwards."""
    for name in ("DeltaTable", "FileStore", "publish", "_attached", "write_guard"):
        monkeypatch.setattr(lakehouse, name, getattr(lakehouse, name))
    lakehouse.reads_reset()


@pytest.mark.parametrize("make", [Kernel, ProcessKernel], ids=["in engine", "own process"])
def test_the_kernel_records_what_its_cells_read(make, tmp_path, monkeypatch):
    keep_lakehouse(monkeypatch)
    lake(tmp_path)
    k = make()
    try:
        k.start(SETUP.format(root=str(tmp_path), attach=ATTACH))
        assert finished(k)["status"] == "ok"
        assert k.reads() == {"tables": {}, "files": {}}
        k.start("t = lakehouse.read_table('sales')\n"
                "fx = lakehouse.read_csv('Files/raw/fx.csv')\n"
                "lakehouse.write_table(t, 'silver_out')\n"  # a new table
                "lakehouse.write_table(t, 'other')\n"  # an existing table, not read first
                "lakehouse.write_table(t, 'sales', mode='append')\n"  # read first: stays at the version read
                "lakehouse.read_table('silver_out')")
        snap = finished(k)
        assert snap["status"] == "ok", snap["outputs"]
        reads = k.reads()
        tables = reads["tables"]
        assert tables["lh1/dbo.sales"] == {"table": "dbo.sales", "version": 1, "first": "read", "workspace_id": "ws1",
                                           "lakehouse_id": "lh1", "lakehouse": "LH_Bronze"}
        assert (tables["lh1/dbo.silver_out"]["version"], tables["lh1/dbo.silver_out"]["first"]) == (None, "write")
        assert (tables["lh1/dbo.other"]["version"], tables["lh1/dbo.other"]["first"]) == (0, "write")
        fx = reads["files"]["lh1/files/raw/fx.csv"]
        assert fx["path"] == "Files/raw/fx.csv" and fx["bytes"] == len("currency,rate\nEUR,1.1\n")
        assert Path(fx["local"]).exists()
        # A restart starts the record again (a process kernel has a new process).
        k.restart()
        assert k.reads() == {"tables": {}, "files": {}}
    finally:
        if isinstance(k, ProcessKernel):
            k.close()


def test_reads_from_the_explorer_and_during_a_proof_are_not_the_cells(tmp_path, monkeypatch):
    keep_lakehouse(monkeypatch)
    lake(tmp_path)
    k = Kernel()
    k.start(SETUP.format(root=str(tmp_path), attach=ATTACH))
    assert finished(k)["status"] == "ok"
    lakehouse.open_table("sales")  # the RPC thread: the Explorer's preview
    assert lakehouse.reads["tables"] == {}
    monkeypatch.setattr(lakehouse, "proof", object())
    lakehouse._record("tables", "x", lambda: {"table": "x"})
    assert lakehouse.reads["tables"] == {}


def test_bundle_code_redacts_secrets_and_says_how_the_cells_ran():
    cells = [{"type": "code", "source": "a = 1", "count": 2},
             {"type": "markdown", "source": "# notes"},
             {"type": "code", "source": "b = 2", "count": 1},
             {"type": "code", "source": "c = 3"},
             {"type": "code", "source": 'api_key = "abc123456789"\nraise ValueError("x")', "count": 3,
              "meta": {"tags": ["parameters"]}},
             {"type": "code", "source": "after = 1", "count": 4}]
    out, notes = replay.code(cells, 4)
    assert len(out) == 5 and out[4]["parameters"] and "abc123456789" not in out[4]["source"]
    assert any("Cell 5 looked like it held a secret" in n for n in notes)
    assert any("Cell 4 hadn't run" in n for n in notes)
    assert any("different order" in n for n in notes)
    with pytest.raises(ValueError):
        replay.code(cells, 1)  # a markdown cell
    in_order, notes = replay.code([{"type": "code", "source": "a = 1", "count": 1},
                                   {"type": "code", "source": "1/0", "count": 2}], 1)
    assert notes == []


def test_a_file_changed_after_it_was_read_has_no_hash(tmp_path):
    f = tmp_path / "fx.csv"
    f.write_text("a\n1\n")
    st = f.stat()
    entry = {"path": "Files/fx.csv", "local": str(f), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
             "lakehouse": "LH", "workspace_id": "ws1", "lakehouse_id": "lh1"}
    found, notes = replay.inputs({"tables": {}, "files": {"k": entry}})
    assert len(found["files"][0]["sha256"]) == 64 and notes == []
    assert "local" not in found["files"][0] and "mtime_ns" not in found["files"][0]
    f.write_text("a\n1\n2\n")
    found, notes = replay.inputs({"tables": {}, "files": {"k": entry}})
    assert found["files"][0]["sha256"] is None and "changed on this PC" in notes[0]


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def failing_notebook(s, tmp_path):
    """Three cells run in the service's kernel; the last fails with a secret and a home path in its error."""
    cells = [{"type": "code", "source": SETUP.format(root=str(tmp_path), attach="")},
             {"type": "code", "source": "import polars as pl\nt = lakehouse.read_table('sales')\n"
                                        "fx = lakehouse.read_csv('Files/raw/fx.csv')"},
             {"type": "markdown", "source": "Then the join"},
             {"type": "code", "source": "raise ValueError(f'no rate in {Path.home() / \"fx.csv\"} password=hunter22')"}]
    for c in cells:
        if c["type"] != "code":
            continue
        call(s, "start_cell", code=c["source"], lakehouse_ref=LH, notebook="events")
        snap = finished(s.kernel)
        c["count"] = snap["execution_count"]
        c["outputs"] = snap["outputs"]
    assert snap["status"] == "error"
    return cells


def service(tmp_path, monkeypatch):
    keep_lakehouse(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    lake(tmp_path)
    s = Service(client_id="x", root=tmp_path / "app")
    s._auth = type("Auth", (), {"storage_token": lambda self: "tok"})()
    s._schema_cache = {"lh1": True}
    return s


def test_save_replay_bundle_writes_code_inputs_and_the_redacted_error(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    preview = call(s, "replay_bundle_preview", name="events", cells=cells, failed=3, lakehouse_ref=LH)
    b = preview["bundle"]
    assert b["failed_cell"] == 4 and len(b["cells"]) == 4 and b["notebook"] == "events"
    assert [t["table"] for t in b["inputs"]["tables"]] == ["dbo.sales"] and b["inputs"]["tables"][0]["version"] == 1
    assert b["inputs"]["files"][0]["path"] == "Files/raw/fx.csv" and len(b["inputs"]["files"][0]["sha256"]) == 64
    assert b["error"]["ename"] == "ValueError"
    assert "hunter22" not in json.dumps(b["error"]) and str(Path.home()) not in json.dumps(b["error"])
    assert "~" in b["error"]["evalue"]
    assert "polars" in b["environment"]["packages"] and b["environment"]["kernel"]["process"] is False
    assert preview["samples_allowed"] is True and preview["sample_tables"] == ["dbo.sales"]

    # Saved with ids and a sample: a new file in Downloads each time, never over another.
    import deltalake
    real = deltalake.DeltaTable
    monkeypatch.setattr(deltalake, "DeltaTable", lambda uri, version=None, storage_options=None: real(
        str(tmp_path / "onelake" / uri.split("/Tables/", 1)[1]), version=version))
    out = call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH, include_sample=True)
    path = Path(out["path"])
    assert path.parent == tmp_path / "home" / "Downloads" and out["ids_included"] is True
    with zipfile.ZipFile(path) as z:
        saved = json.loads(z.read("bundle.json"))
        sample = pq.read_table(z.open(saved["samples"][0]["file"]))
    assert saved["samples"] == [{"table": "LH_Bronze/dbo.sales", "file": "samples/1.parquet", "rows": 3}]
    assert sample.num_rows == 3  # version 1: all three rows
    assert saved["inputs"]["tables"][0]["workspace_id"] == "ws1"
    assert replay.read(path)["notebook"] == "events"

    second = call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH, include_ids=False)
    assert second["path"] != out["path"] and Path(out["path"]).exists()
    shared = replay.read(Path(second["path"]))
    assert shared["ids_included"] is False and shared["samples"] == []
    assert "ws1" not in json.dumps(shared) and "lh1" not in json.dumps(shared)
    assert shared["lakehouse"] == {"name": "LH_Bronze"}


def test_policy_can_keep_data_out_of_bundles(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"allow_replay_samples": False}))
    assert call(s, "replay_bundle_preview", name="events", cells=cells, failed=3, lakehouse_ref=LH)["samples_allowed"] is False
    out = handle(s, json.dumps({"id": 1, "method": "replay_bundle_save", "params": {
        "name": "events", "cells": cells, "failed": 3, "lakehouse_ref": LH, "include_sample": True}}))
    assert "doesn't allow data samples" in json.dumps(out["error"])
    assert not (tmp_path / "home" / "Downloads").exists()


def test_a_file_that_is_not_a_bundle_is_refused(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="isn't a replay bundle"):
        replay.read(bad)
    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("bundle.json", json.dumps({"format": replay.FORMAT, "version": replay.VERSION + 1}))
    with pytest.raises(ValueError, match="newer Fabric Desktop"):
        replay.read(newer)
