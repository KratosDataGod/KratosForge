"""Failure replay bundles (Prove stage 5, plan item 47): the kernel records what the cells read
(lakehouse.reads), and Save replay bundle writes the code up to the failed cell, those inputs,
the environment and the error with secrets removed to a zip in Downloads."""

from __future__ import annotations

import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

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
WS_GUID, LH_GUID = "0f6e2a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b", "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"

# Run as a cell: tables and files of a fake OneLake under ``root``, so the kernel's own lakehouse
# module (in the engine or in its own process) reads and writes them.
SETUP = """
import shutil
from pathlib import Path
from typing import Any
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

def _publish(data, ref, mode, token, **_):
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
    s._auth = type("Auth", (), {"storage_token": lambda self: "tok", "signed_in_user": lambda self: "a@contoso.com",
                                "silent_token": lambda self, scopes: "tok"})()
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


class FixedClock:
    """prove_service's ``time`` with strftime at one second, so two saves want the same name."""

    def __getattr__(self, name):
        return getattr(time, name)

    def strftime(self, fmt, *args):
        return time.strftime(fmt, time.struct_time((2026, 10, 20, 12, 0, 0, 1, 293, 0)))


def test_save_replay_bundle_never_replaces_a_file_with_its_name(tmp_path, monkeypatch):
    from fabric_desktop import prove_service
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    monkeypatch.setattr(prove_service, "time", FixedClock())
    first = Path(call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH)["path"])
    assert first.name == "replay-events-20261020-120000.zip"
    first.write_bytes(b"hand-edited")
    second = Path(call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH)["path"])
    assert second.name == "replay-events-20261020-120000-2.zip" and first.read_bytes() == b"hand-edited"
    assert replay.read(second)["notebook"] == "events"


def test_save_replay_bundle_is_created_exclusively_even_if_the_name_was_free_a_moment_ago(tmp_path, monkeypatch):
    # Another save makes the file between the free-name check and the write: Path.exists says
    # every name is free, and the file already there must still not be replaced.
    from fabric_desktop import prove_service
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    monkeypatch.setattr(prove_service, "time", FixedClock())
    mine = tmp_path / "home" / "Downloads" / "replay-events-20261020-120000.zip"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"hand-edited")
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: False)
    out = Path(call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH)["path"])
    assert mine.read_bytes() == b"hand-edited"
    assert out.name == "replay-events-20261020-120000-2.zip"


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
    with pytest.raises(ValueError, match="newer KratosForge"):
        replay.read(newer)


# ---- opening a bundle and replaying it

def replay_lake(root: Path) -> Path:
    """A local lakehouse folder with dbo.sales at versions 0 (1 row), 1 (3 rows, overwritten, so
    version 0's file can be deleted as VACUUM would) and 2 (4 rows)."""
    lh = root / "lake"
    sales = str(lh / "Tables" / "dbo" / "sales")
    write_deltalake(sales, pa.table({"id": [0]}))
    write_deltalake(sales, pa.table({"id": [1, 2, 3]}), mode="overwrite")
    write_deltalake(sales, pa.table({"id": [4]}), mode="append")
    return lh


REPLAY_CELLS = [
    {"type": "markdown", "source": "# Events"},
    {"type": "code", "source": "from fabric_desktop import lakehouse\nt = lakehouse.read_table('sales')\n"
                               "lakehouse.write_table(t, 'out')", "count": 1},
    {"type": "code", "source": "raise ValueError(f'rows {t.height}')", "count": 2,
     "outputs": [{"type": "error", "ename": "ValueError", "evalue": "rows 3", "traceback": ""}]},
]


def bundle_file(tmp_path: Path, version: int = 1, sample: Any = None, ids: bool = True) -> Path:
    reads = {"tables": {"lh1/dbo.sales": {"table": "dbo.sales", "version": version, "first": "read",
                                          "workspace_id": "ws1", "lakehouse_id": "lh1", "lakehouse": "LH_Bronze"}},
             "files": {}}
    b = replay.build("events", REPLAY_CELLS, 2, reads, LH)
    if not ids:
        b = replay.strip_ids(b)
    path = tmp_path / "replay-events.zip"
    replay.write(path, b, {"LH_Bronze/dbo.sales": sample} if sample is not None else None)
    return path


def replay_service(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    s.kernel = s._new_kernel(True)
    lh = replay_lake(tmp_path)
    monkeypatch.setattr(s, "_proof_location", lambda ref: (str(lh), lambda: {}))
    return s, lh


def run_cells(s, name, upto):
    nb = call(s, "open_notebook", name=name)
    snap = None
    for i, c in enumerate(nb["cells"][:upto], 1):
        if c["type"] != "code":
            continue
        call(s, "start_cell", code=c["source"], lakehouse_ref=LH, notebook=name)
        snap = finished(s.kernel)
        if snap["status"] != "ok":
            return i, snap
    return upto, snap


def test_a_replay_reads_the_bundle_versions_and_writes_to_a_sandbox(tmp_path, monkeypatch):
    s, lh = replay_service(tmp_path, monkeypatch)
    try:
        nb = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))
        name = nb["name"]
        assert name == "events replay" and nb["replay"]["failed_cell"] == 3 and nb["lakehouse"]["lakehouse_id"] == "lh1"
        assert [c["source"] for c in nb["cells"]] == [c["source"] for c in REPLAY_CELLS]
        # Its cells don't run outside a replay: they'd read the latest tables and write the real ones.
        out = handle(s, json.dumps({"id": 1, "method": "start_cell", "params": {"code": "1", "lakehouse_ref": LH, "notebook": name}}))
        assert "click Replay" in json.dumps(out["error"])
        check = call(s, "replay_check", name=name, lakehouse_ref=LH)
        assert check["blockers"] == [] and check["tables"][0] | {} == {
            "table": "dbo.sales", "version": 1, "readable": True, "latest": 2, "why": "", "has_sample": False}
        started = call(s, "replay_start", name=name, lakehouse_ref=LH)
        assert started["pins"] == {"dbo.sales": 1} and started["sandbox"].startswith("Files/_fabric_desktop/proofs/replay-")
        stopped, snap = run_cells(s, name, 3)
        assert stopped == 3 and snap["status"] == "error"
        result = call(s, "replay_result", name=name, stopped_at=stopped, status="error", outputs=snap["outputs"])
        assert result["verdict"] == "reproduced" and result["inputs"] == []
        # The write went to the sandbox; the lakehouse's tables are as they were.
        assert not (lh / "Tables" / "dbo" / "out").exists()
        assert DeltaTable(str(lh / started["sandbox"] / "tables" / "dbo" / "out")).to_pyarrow_table().num_rows == 3
        assert DeltaTable(str(lh / "Tables" / "dbo" / "sales")).version() == 2
        # Nothing else runs in the kernel meanwhile.
        out = handle(s, json.dumps({"id": 1, "method": "start_cell", "params": {"code": "1", "notebook": "other"}}))
        assert "replaying events replay" in json.dumps(out["error"])
        out = handle(s, json.dumps({"id": 1, "method": "prove_start", "params": {"name": name, "cells": [], "lakehouse_ref": LH}}))
        assert "replay notebook" in json.dumps(out["error"])
        # Kept as a normal notebook: the replay ends with a fresh kernel and its cells run as any other.
        call(s, "replay_end", name=name, keep=True)
        assert call(s, "open_notebook", name=name)["replay"] is None
        call(s, "start_cell", code="x = 1", lakehouse_ref=None, notebook=name)
        assert finished(s.kernel)["status"] == "ok"
    finally:
        s.kernel.close()


def test_replay_sandboxes_are_remembered_and_deleted_only_after_the_replay_ends(tmp_path, monkeypatch):
    import shutil
    s, lh = replay_service(tmp_path, monkeypatch)
    deleted = []

    def delete(ref, rel):
        deleted.append((ref["lakehouse_id"], rel))
        shutil.rmtree(lh / rel, ignore_errors=True)
    monkeypatch.setattr(s, "_proof_delete_folder", delete)
    try:
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))["name"]
        first = call(s, "replay_start", name=name, lakehouse_ref=LH)
        run_cells(s, name, 3)
        assert (lh / first["sandbox"] / "tables" / "dbo" / "out").exists()
        out = handle(s, json.dumps({"id": 1, "method": "replay_delete_outputs", "params": {"name": name}}))
        assert "End the replay first" in json.dumps(out["error"])
        call(s, "replay_end", name=name)
        second = call(s, "replay_start", name=name, lakehouse_ref=LH)
        call(s, "replay_end", name=name)
        boxes = s.replay_sandboxes(name)
        assert [b["id"] for b in boxes] == [first["id"], second["id"]]
        assert boxes[0]["folder"] == first["sandbox"].rsplit("/", 1)[0] and boxes[0]["lakehouse_id"] == "lh1"
        assert call(s, "replay_check", name=name, lakehouse_ref=LH)["sandboxes"] == boxes  # what the banner shows
        # Fabric's ids are GUIDs; this test's lakehouse isn't, so give the remembered entries real-looking ones.
        s.notebooks.set_extra(name, "replay_sandboxes", [dict(b, workspace_id=WS_GUID, lakehouse_id=LH_GUID)
                                                         for b in s.notebooks.extra(name, "replay_sandboxes")])
        out = call(s, "replay_delete_outputs", name=name)
        assert out == {"deleted": [b["folder"] for b in boxes], "problems": []}
        assert deleted == [(LH_GUID, b["folder"]) for b in boxes]
        assert not (lh / first["sandbox"]).exists() and (lh / "Tables" / "dbo" / "sales").exists()
        assert s.replay_sandboxes(name) == []
    finally:
        s.kernel.close()


def test_a_sandbox_that_cant_be_deleted_stays_listed_and_an_odd_id_is_never_a_path(tmp_path, monkeypatch):
    s, _ = replay_service(tmp_path, monkeypatch)
    try:
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))["name"]
        good = {"id": "replay-20261020-120000-abcdef", "workspace_id": WS_GUID, "lakehouse_id": LH_GUID, "lakehouse": "LH"}
        odd = [dict(good, id="../../Tables"), dict(good, lakehouse_id="lh1/../../other"), dict(good, workspace_id=None),
               {k: v for k, v in good.items() if k != "id"}]
        s.notebooks.set_extra(name, "replay_sandboxes", [good, *odd])
        tried = []

        def refuse(ref, rel):
            tried.append(rel)
            raise PermissionError("403 Forbidden token=abc.def.ghi")
        monkeypatch.setattr(s, "_proof_delete_folder", refuse)
        out = call(s, "replay_delete_outputs", name=name)
        assert tried == [f"Files/_fabric_desktop/proofs/{good['id']}"]  # nothing else became a path
        assert len(s.replay_sandboxes(name)) == 5  # an entry without an id is still listed, not an error
        assert out["deleted"] == [] and len(out["problems"]) == 5 and "abc.def.ghi" not in json.dumps(out)
        assert s.notebooks.extra(name, "replay_sandboxes") == [good, *odd]  # all of them stay listed
    finally:
        s.kernel.close()


def test_a_restart_ends_the_replay(tmp_path, monkeypatch):
    s, _ = replay_service(tmp_path, monkeypatch)
    try:
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))["name"]
        call(s, "replay_start", name=name, lakehouse_ref=LH)
        assert call(s, "replay_check", name=name, lakehouse_ref=LH)["live"] is True
        call(s, "restart_kernel")
        assert call(s, "replay_check", name=name, lakehouse_ref=LH)["live"] is False
        out = handle(s, json.dumps({"id": 1, "method": "replay_result", "params": {"name": name, "stopped_at": 3, "status": "ok"}}))
        assert "has ended" in json.dumps(out["error"])
        out = handle(s, json.dumps({"id": 1, "method": "start_cell", "params": {"code": "1", "lakehouse_ref": LH, "notebook": name}}))
        assert "click Replay" in json.dumps(out["error"])
    finally:
        s.kernel.close()


def test_a_vacuumed_version_is_replaced_only_by_choice_and_marked(tmp_path, monkeypatch):
    s, lh = replay_service(tmp_path, monkeypatch)
    try:
        # Version 0's data file is gone, as after VACUUM.
        sales = DeltaTable(str(lh / "Tables" / "dbo" / "sales"), version=0)
        for uri in sales.file_uris():
            Path(url2pathname(urlparse(uri).path) if uri.startswith("file:") else uri).unlink()
        sample = pa.table({"id": [1, 2]})
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path, version=0, sample=sample)))["name"]
        t = call(s, "replay_check", name=name, lakehouse_ref=LH)["tables"][0]
        assert t["readable"] is False and "can't be read any more" in t["why"] and t["has_sample"] is True
        out = handle(s, json.dumps({"id": 1, "method": "replay_start", "params": {"name": name, "lakehouse_ref": LH}}))
        assert "Choose the bundle's sample or the latest version" in json.dumps(out["error"])

        started = call(s, "replay_start", name=name, lakehouse_ref=LH, use={"dbo.sales": "sample"})
        assert started["marked"] == {"dbo.sales": "sample"} and "dbo.sales" not in started["pins"]
        stopped, snap = run_cells(s, name, 3)
        result = call(s, "replay_result", name=name, stopped_at=stopped, status=snap["status"], outputs=snap["outputs"])
        assert result["verdict"] == "same_kind" and "rows 2" in result["text"]
        assert "from the bundle's sample" in result["inputs"][0]
        call(s, "replay_end", name=name)

        started = call(s, "replay_start", name=name, lakehouse_ref=LH, use={"dbo.sales": "latest"})
        assert started["pins"] == {"dbo.sales": 2} and started["marked"] == {"dbo.sales": "latest"}
        stopped, snap = run_cells(s, name, 3)
        result = call(s, "replay_result", name=name, stopped_at=stopped, status=snap["status"], outputs=snap["outputs"])
        assert "rows 4" in result["text"] and "at its latest version" in result["inputs"][0]
    finally:
        s.kernel.close()


def test_replay_needs_the_kernel_process_and_the_bundles_lakehouse(tmp_path, monkeypatch):
    s, _ = replay_service(tmp_path, monkeypatch)
    try:
        name = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path)))["name"]
        other = LH | {"lakehouse_id": "lh2", "name": "LH_Other"}
        assert "Attach that lakehouse" in call(s, "replay_check", name=name, lakehouse_ref=other)["blockers"][0]
        assert "Attach a lakehouse" in call(s, "replay_check", name=name, lakehouse_ref=None)["blockers"][0]
        shared = call(s, "replay_bundle_open", path=str(bundle_file(tmp_path, ids=False)))
        assert shared["name"] == "events replay 2" and shared["lakehouse"] is None
        assert "check you attached the same one" in call(s, "replay_check", name=shared["name"], lakehouse_ref=LH)["notes"][0]
        s.kernel.close()
        s.kernel = s._new_kernel(False)
        assert "own process" in call(s, "replay_check", name=name, lakehouse_ref=LH)["blockers"][0]
        out = handle(s, json.dumps({"id": 1, "method": "replay_bundle_open", "params": {"content": "bm90IGEgemlw"}}))
        assert "isn't a replay bundle" in json.dumps(out["error"])
    finally:
        if isinstance(s.kernel, ProcessKernel):
            s.kernel.close()


def test_bundles_in_downloads_are_listed(tmp_path, monkeypatch):
    s = service(tmp_path, monkeypatch)
    downloads = tmp_path / "home" / "Downloads"
    downloads.mkdir(parents=True)
    bundle_file(tmp_path).rename(downloads / "replay-events-1.zip")
    (downloads / "replay-junk.zip").write_bytes(b"x")
    rows = call(s, "replay_bundles_list")
    assert [(r["file"], r["notebook"], r["failed_cell"]) for r in rows] == [("replay-events-1.zip", "events", 3)]


def tampered(path: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    """The bundle at ``path`` with bundle.json changed by ``change``, as someone could hand-edit it."""
    out = path.with_name("tampered.zip")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "bundle.json":
                b = json.loads(data)
                change(b)
                data = json.dumps(b).encode()
            dst.writestr(item.filename, data)
    return out


EVIL = "../../../../../../Tables/dbo.evil"


@pytest.mark.parametrize("change", [
    lambda b: [t.update(table=EVIL) for t in b["inputs"]["tables"]],
    lambda b: [s.update(table=f"LH_Bronze/{EVIL}") for s in b["samples"]],
    lambda b: [s.update(file="../outside.parquet") for s in b["samples"]],
])
def test_a_bundle_cannot_name_a_table_or_file_outside_its_own(tmp_path, monkeypatch, change):
    s, lh = replay_service(tmp_path, monkeypatch)
    try:
        bad = tampered(bundle_file(tmp_path, sample=pa.table({"id": [9]})), change)
        out = handle(s, json.dumps({"id": 1, "method": "replay_bundle_open", "params": {"path": str(bad)}}))
        assert "isn't a replay bundle" in json.dumps(out["error"]), out
        assert not (lh / "Files" / "tables").exists() and not (lh / "Tables" / "dbo" / "evil").exists()
        # Checked again when the replay seeds its sandbox, so a bundle changed after it was opened is refused too.
        with pytest.raises(ValueError, match="isn't a replay bundle"):
            replay.read(bad)
    finally:
        s.kernel.close()


def test_an_oversized_bundle_json_is_refused_before_it_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "MAX_BUNDLE_JSON", 1000)
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bundle.json", json.dumps({"format": replay.FORMAT, "version": 1, "pad": " " * 5000}))
    with pytest.raises(ValueError, match="too large"):
        replay.read(big)


WS, LHID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", "11112222-3333-4444-5555-666677778888"


def test_a_bundle_without_ids_has_none_in_its_error_code_or_notes():
    uri = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LHID}/Tables/dbo/sales"
    cells = [{"type": "code", "source": f"df = spark.read.load('{uri}')", "count": 1,
              "outputs": [{"type": "error", "ename": "OSError", "evalue": f"Not found: {uri}",
                           "traceback": f"File x\n  open('https://onelake.dfs.fabric.microsoft.com/{WS}/{LHID}/Files/a.csv')"}]}]
    reads = {"tables": {"t": {"table": "dbo.sales", "version": 1, "first": "read", "workspace_id": WS,
                              "lakehouse_id": LHID, "lakehouse": "LH_Bronze"}}, "files": {}}
    b = replay.build("events", cells, 0, reads, {"workspace_id": WS, "lakehouse_id": LHID, "name": "LH_Bronze"})
    assert WS in json.dumps(b)  # with ids, they stay
    shared = json.dumps(replay.strip_ids(b))
    assert WS not in shared and LHID not in shared, shared
    assert "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>/Tables/dbo/sales" in shared
    assert "IDs in the cells' code were replaced" in shared


def test_a_replay_without_ids_still_recognises_the_same_error():
    evalue = f"Not found: abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LHID}/Tables/dbo/sales"
    cells = [{"type": "code", "source": "x", "count": 1, "outputs": [{"type": "error", "ename": "OSError", "evalue": evalue}]}]
    b = replay.strip_ids(replay.build("events", cells, 0, {"tables": {}, "files": {}}, None))
    # The replay ran in the person's own lakehouse, so its error names their ids: still the same error.
    got = replay.outcome(b, 1, "error", [{"type": "error", "ename": "OSError",
                                         "evalue": evalue.replace(WS, "99999999-9999-9999-9999-999999999999")}])
    assert got["verdict"] == "reproduced", got


@pytest.mark.parametrize("source, kept", [
    ('path = "abfss://Sales@onelake.dfs.fabric.microsoft.com/LH.Lakehouse"\nx = 1',
     'path = "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>"\nx = 1'),
    ("# source: abfss://Sales@onelake.dfs.fabric.microsoft.com/LH.Lakehouse\ndf=read()\nprint(df)",
     "# source: abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>\ndf=read()\nprint(df)"),
    ("open('https://onelake.dfs.fabric.microsoft.com/Sales/LH.Lakehouse/Files/a.csv')\nnext_line()",
     "open('https://onelake.dfs.fabric.microsoft.com/<workspace>/<lakehouse>/Files/a.csv')\nnext_line()"),
    ("Can't reach abfss://Sales@onelake.dfs.fabric.microsoft.com/LH.Lakehouse, try again (or later); done",
     "Can't reach abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>, try again (or later); done"),
    ("read(https://onelake.dfs.fabric.microsoft.com/Sales/LH.Lakehouse); x = 1",
     "read(https://onelake.dfs.fabric.microsoft.com/<workspace>/<lakehouse>); x = 1"),
])
def test_ids_come_out_of_code_without_changing_anything_else(source, kept):
    cells = [{"type": "code", "source": source, "count": 1,
              "outputs": [{"type": "error", "ename": "E", "evalue": source}]}]
    b = replay.strip_ids(replay.build("n", cells, 0, {"tables": {}, "files": {}}, None))
    assert b["cells"][0]["source"] == kept
    assert b["error"]["evalue"] == kept


# ---- #1303: a sample read that can't reach OneLake fails fast, with a plain error

def _save_sample(s, cells):
    return handle(s, json.dumps({"id": 1, "method": "replay_bundle_save", "params": {
        "name": "events", "cells": cells, "failed": 3, "lakehouse_ref": LH, "include_sample": True}}))


def _unreachable(monkeypatch, endpoint):
    """delta-rs's real DeltaTable, sent to ``endpoint`` instead of OneLake, with the options the save chose."""
    import deltalake
    real, seen = deltalake.DeltaTable, {}

    def opened(uri, version=None, storage_options=None):
        seen.update(storage_options)
        return real(uri, version=version, storage_options=storage_options | {
            "azure_storage_endpoint": endpoint, "azure_allow_http": "true"})
    monkeypatch.setattr(deltalake, "DeltaTable", opened)
    return seen


def test_a_sample_read_reads_with_short_limits_not_the_upload_ones(tmp_path, monkeypatch):
    from fabric_desktop import onelake
    opts = onelake.read_storage_options("tok")
    assert opts["connect_timeout"] == "10s" and opts["timeout"] == "60s" and opts["retry_timeout"] == "60s"
    assert opts["max_retries"] == "2" and opts["bearer_token"] == "tok" and opts["use_fabric_endpoint"] == "true"
    assert onelake.storage_options("tok")["retry_timeout"] == "1800s"  # uploads keep theirs


def test_saving_a_sample_while_offline_fails_fast_and_saves_nothing(tmp_path, monkeypatch):
    import socket
    import time
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    with socket.socket() as free:  # a port nothing listens on: connections are refused, as offline
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    seen = _unreachable(monkeypatch, f"http://127.0.0.1:{port}")
    start = time.monotonic()
    out = _save_sample(s, cells)
    assert time.monotonic() - start < 30, "it should fail within the short limits"
    assert out["error"].startswith("ConnectionError: Couldn't reach OneLake to read the sample.")
    assert seen["retry_timeout"] == "60s"
    assert not (tmp_path / "home" / "Downloads").exists() or not list((tmp_path / "home" / "Downloads").iterdir())
    # The next call isn't held up behind it.
    assert "bundle" in call(s, "replay_bundle_preview", name="events", cells=cells, failed=3, lakehouse_ref=LH)


def test_a_sample_read_from_a_endpoint_that_never_answers_stops_at_its_time_limit(tmp_path, monkeypatch):
    """A server that takes the connection and never replies: the read stops at the request time limit."""
    import socket
    import threading
    import time

    from fabric_desktop import onelake
    monkeypatch.setattr(onelake, "READ_TIMEOUT", "1s")
    monkeypatch.setattr(onelake, "READ_RETRY_TIMEOUT", "2s")
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held, stop = [], threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(server.accept()[0])  # kept open, never answered
            except OSError:
                continue
    threading.Thread(target=accept, daemon=True).start()
    try:
        _unreachable(monkeypatch, f"http://127.0.0.1:{server.getsockname()[1]}")
        start = time.monotonic()
        out = _save_sample(s, cells)
        assert time.monotonic() - start < 30
        assert out["error"].startswith("ConnectionError: Couldn't reach OneLake")
    finally:
        stop.set()
        for c in held:
            c.close()
        server.close()


def test_saving_a_sample_signed_out_fails_before_reading(tmp_path, monkeypatch):
    import deltalake
    s = service(tmp_path, monkeypatch)
    cells = failing_notebook(s, tmp_path)
    monkeypatch.setattr(deltalake, "DeltaTable", lambda *a, **k: pytest.fail("read while signed out"))
    for auth in ({"signed_in_user": lambda self: None, "silent_token": lambda self, scopes: "tok"},
                 {"signed_in_user": lambda self: "a@contoso.com", "silent_token": lambda self, scopes: None}):
        s._auth = type("Auth", (), auth | {"storage_token": lambda self: pytest.fail("may prompt")})()
        out = _save_sample(s, cells)
        assert out["error"].startswith("PermissionError: Sign in to read the sample from OneLake"), out
    # Without a sample, saving needs no sign-in.
    assert call(s, "replay_bundle_save", name="events", cells=cells, failed=3, lakehouse_ref=LH)["path"]


def test_a_replays_version_check_reads_with_the_short_limits(tmp_path, monkeypatch):
    """replay_check runs when a replay notebook opens, on the same ordered lane as Save (#1303)."""
    import deltalake

    from fabric_desktop import onelake
    s = service(tmp_path, monkeypatch)
    seen = []

    def opened(uri, version=None, storage_options=None):
        seen.append(storage_options)
        raise OSError("OneLake didn't answer")
    monkeypatch.setattr(deltalake, "DeltaTable", opened)
    row = s._replay_table({"table": "dbo.sales", "version": 1}, "abfss://ws1@x/lh1", lambda: onelake.storage_options("tok"), False)
    assert row["readable"] is False and row["why"] == "The table can't be opened: OSError"
    assert seen and all(o["retry_timeout"] == "60s" and o["bearer_token"] == "tok" for o in seen)
