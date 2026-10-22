"""Prove this notebook (stage 2): controlled runs on this PC and in Fabric, verified and recorded.

The Fabric side is exercised by running the notebook exactly as it is published for a proof (the
shim, the proof runtime cell, the user's cells, the closing cell) in a separate Python process,
against the same local folders, with a stand-in for Fabric's notebookutils. That proves the code
Fabric would run; it doesn't prove Fabric itself (the job scheduler, the /lakehouse/default mount).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, proof, prove_service, verify
from fabric_desktop.notebook import Kernel
from fabric_desktop.proof_runtime import ProofRuntime, ReadOnlyTable
from fabric_desktop.receipts import receipt_id
from fabric_desktop.server import handle
from fabric_desktop.service import Service

LAKEHOUSE = {"workspace_id": "ws", "workspace_name": "Data", "lakehouse_id": "lh", "name": "LH_Proof"}

FAKE_NOTEBOOKUTILS = '''
class _Credentials:
    @staticmethod
    def getToken(audience):
        return "t"
credentials = _Credentials()
'''


class LocalTables:
    """The lakehouse's Tables/ folder on disk (schema-enabled): versions of every table."""

    def __init__(self, root: Path):
        self.root = root

    def versions(self):
        out = {}
        for d in sorted(self.root.glob("*/*")):
            if (d / "_delta_log").exists():
                out[f"{d.parent.name}.{d.name}"] = DeltaTable(str(d)).version()
        return out


class FakeFiles:
    """lakehouse.local_path's download, from the lakehouse folder on disk."""
    root: Path

    def __init__(self, token):
        pass

    def download(self, ws, lh, rel, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / rel, dest)
        return dest


class FakeAuth:
    def storage_token(self):
        return "t"

    def fabric_token(self):
        return "t"

    def signed_in_user(self):
        return "someone@contoso.com"

    def signed_in_tenant(self):
        return "tenant-1"


class FakeFabric:
    """Publishing saves the notebook; running it runs the published code in another process."""

    def __init__(self, svc, before_run=None, env=None, status="Completed"):
        self.svc, self.before_run, self.env, self.status = svc, before_run, env or {}, status
        self.saved: list[tuple[str, dict]] = []
        self.ran = None

    def save_notebook(self, ws, name, ipynb):
        self.saved.append((name, json.loads(ipynb)))
        return {"id": "nb-proof", "displayName": name}

    def get_lakehouse(self, ws, lh):
        return {"properties": {"defaultSchema": "dbo"}}

    def run_item_job(self, ws, item, job):
        if self.before_run:
            self.before_run()
        name, nb = self.saved[-1]
        self.ran = run_published(nb, self.svc.lh_root, self.svc.tmp, self.env)
        return "job1"

    def job_instance(self, ws, item, job):
        status = self.status if self.ran.returncode == 0 else "Failed"
        return {"status": status, "startTimeUtc": "2026-10-16T10:00:00Z", "endTimeUtc": "2026-10-16T10:01:30Z",
                "failureReason": None if status == "Completed" else {"message": self.ran.stderr[-300:]}}

    def cancel_job(self, ws, item, job):
        pass


def run_published(nb: dict, lh_root: Path, tmp: Path, env: dict) -> subprocess.CompletedProcess:
    """Run a published notebook's code cells in order, as Fabric's Python notebook would."""
    fake = tmp / "fake_fabric"
    fake.mkdir(exist_ok=True)
    (fake / "notebookutils.py").write_text(FAKE_NOTEBOOKUTILS, encoding="utf-8")
    code = "\n\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    # Fabric's lakehouse is abfss://... with a mount at /lakehouse/default; here both are the folder.
    code, n = re.subn(r"^_fabric_desktop_module\((.*)\)$",
                      lambda m: f"_fabric_desktop_module({m.group(1)}, root={str(lh_root)!r}, mount={str(lh_root)!r})",
                      code, flags=re.M)
    assert n == 1, "the shim's call wasn't found"
    script = tmp / "published.py"
    script.write_text(code, encoding="utf-8")
    environ = dict(os.environ, PYTHONPATH=str(fake), **env)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=environ, cwd=tmp, timeout=300)
    return out


class ProveService(Service):
    fake = None

    @property
    def auth(self):
        return FakeAuth()

    @property
    def fabric(self):
        return self.fake

    @property
    def fabric_jobs(self):
        return self.fake

    def _proof_location(self, ref):
        return str(self.lh_root), lambda: {}

    def _proof_tables(self, ref):
        return LocalTables(self.lh_root / "Tables")

    def _proof_read_file(self, ref, rel):
        p = self.lh_root / rel
        return p.read_bytes() if p.exists() else None

    def _proof_delete_folder(self, ref, rel):
        shutil.rmtree(self.lh_root / rel, ignore_errors=True)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    lh_root = tmp_path / "onelake" / "lh"
    write_deltalake(str(lh_root / "Tables" / "dbo" / "orders"),
                    pa.table({"order_id": [1, 2, 3], "region": ["east", "west", "east"], "amount": [10.0, 20.5, 7.25]}))
    (lh_root / "Files" / "raw").mkdir(parents=True)
    (lh_root / "Files" / "raw" / "rates.csv").write_text("region,rate\neast,1.1\nwest,0.9\n", encoding="utf-8")
    FakeFiles.root = lh_root
    monkeypatch.setattr(lakehouse, "FileStore", FakeFiles)
    monkeypatch.setattr(prove_service, "POLL_SECONDS", 0)
    s = ProveService(root=tmp_path / "app")
    s.lh_root, s.tmp = lh_root, tmp_path
    s._schema_cache[LAKEHOUSE["lakehouse_id"]] = True
    return s


CELLS = [
    {"type": "markdown", "source": "# Order values"},
    {"type": "code", "source": "import os\nimport polars as pl\nfrom fabric_desktop import lakehouse"},
    {"type": "code", "source": "orders = lakehouse.read_table('dbo.orders')\nrates = lakehouse.read_csv('Files/raw/rates.csv')"},
    {"type": "code", "source": (
        "drift = 1e-12 if os.environ.get('FD_FAKE_FABRIC_DRIFT') else 0.0\n"
        "out = orders.join(rates, on='region').with_columns((pl.col('amount') * pl.col('rate') + drift).alias('value'))\n"
        "if os.environ.get('FD_FAKE_FABRIC_WRONG'):\n"
        "    out = out.with_columns(pl.when(pl.col('order_id') == 2).then(0.0).otherwise(pl.col('value')).alias('value'))\n"
        "lakehouse.write_table(out, 'dbo.order_values')")},
    {"type": "code", "source": "lakehouse.write_table(orders.head(2), 'dbo.orders', mode='append')\n"
                               "print(lakehouse.read_table('dbo.orders').height)"},
]


def payload(cells=CELLS, **extra):
    return {"name": "Order values", "cells": cells, "lakehouse_ref": LAKEHOUSE, "compute": "default",
            "workspace": None} | extra


def call(svc, method, **params):
    out = handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


def wait(svc, pid):
    for _ in range(1200):
        status = call(svc, "prove_status", id=pid)
        if status["state"] not in prove_service.RUNNING:
            # A finished proof already has its scorecard and receipt: the state changes last.
            assert status["receipt_id"] and status["scorecard"], status
            return status
        time.sleep(0.05)
    raise AssertionError("the proof didn't finish")


def shared_version(svc, table="orders"):
    return DeltaTable(str(svc.lh_root / "Tables" / "dbo" / table)).version()


# ---- end to end


def test_a_proof_runs_both_sides_from_pinned_inputs_without_touching_shared_tables(svc):
    svc.fake = FakeFabric(svc)
    before = shared_version(svc)
    started = call(svc, "prove_start", **payload())
    out = wait(svc, started["id"])
    assert svc.fake.ran.returncode == 0, svc.fake.ran.stderr
    assert out["state"] == "done", out["message"]
    assert out["verdict"]["verdict"] == "same", out["verdict"]
    # The lakehouse's tables are exactly as they were; the outputs are in the proof's folder.
    assert shared_version(svc) == before and not (svc.lh_root / "Tables" / "dbo" / "order_values").exists()
    assert out["shared"] == {"changed": [], "unchanged": True}
    proof_dir = svc.lh_root / "Files" / "_fabric_desktop" / "proofs" / out["id"]
    assert (proof_dir / "pc" / "tables" / "dbo" / "order_values" / "_delta_log").exists()
    assert (proof_dir / "fabric" / "tables" / "dbo" / "order_values" / "_delta_log").exists()
    # Every row compared; the append started from the pinned table on both sides (3 + 2 rows).
    by = {t["table"]: t for t in out["tables"]}
    assert set(by) == {"dbo.order_values", "dbo.orders"}
    assert by["dbo.orders"]["result"] == "match" and by["dbo.orders"]["pc"]["rows"] == 5
    assert by["dbo.order_values"]["level"] == "complete" and by["dbo.order_values"]["pc"]["rows"] == 3
    # Inputs: same table versions and the same file content on both sides.
    assert out["inputs"]["tables"] == out["inputs"]["tables_fabric"] == [{"name": "dbo.orders", "version": 0}]
    assert out["inputs"]["files"][0]["path"] == "Files/raw/rates.csv"
    assert out["inputs"]["files"][0]["sha256"] == out["inputs"]["files_fabric"][0]["sha256"]
    assert out["inputs"]["equivalent"] is True
    # Both environments recorded; Fabric's packages remembered for the dependency check.
    assert out["environment"]["fabric"]["packages"]["polars"] and out["environment"]["pc"]["python"]
    env = call(svc, "notebook_environment", **payload())
    assert next(m for m in env["modules"] if m["module"] == "polars")["fabric"]["status"] == "observed"
    # Measured on this PC; Fabric times from the job; cost an estimate with its assumptions.
    assert out["pc"]["cpu_seconds"] is not None and out["pc"]["seconds"] > 0
    assert out["fabric"]["job_seconds"] == 90 and out["fabric"]["run_seconds"] is not None
    card = out["scorecard"]
    assert card["cost"]["fabric"]["kind"] == "estimated" and card["cost"]["fabric"]["assumptions"]
    # FinOps classification: the same measurements Monitor > Cost uses.
    fm, pm = card["cost"]["fabric"]["measurement"], card["cost"]["pc"]["measurement"]
    assert card["cost"]["fabric"]["classification"] == fm["classification"] == "estimated"
    assert fm["value"] == card["cost"]["fabric"]["cu_seconds"] and fm["run_id"] == out["fabric"]["job_id"]
    assert pm["classification"] == "measured" and pm["value"] == 0 and card["cost"]["pc"]["kind"] == "not available"
    assert card["resources"]["fabric"]["kind"] == "not available"
    assert card["correctness"] == {"verdict": "same", "text": out["verdict"]["text"], "tables": 2, "matched": 2,
                                   "levels": {"complete": 2}}
    # The PC's cell output shows as usual.
    assert out["pc"]["cells"]["4"]["outputs"][0]["text"].strip() == "5"
    # The published notebook was a separate item; the user's own notebook file has no proof cells.
    name, published = svc.fake.saved[-1]
    assert name == "Order values_proof"
    sources = ["".join(c["source"]) for c in published["cells"]]
    assert sources[0].startswith("# Fabric Desktop setup") and "Fabric Desktop proof" in sources[1]
    assert not any("Fabric Desktop proof" in c["source"] for c in svc.notebooks.load("Order values")["cells"])
    # A valid receipt that names the code, inputs, environments and outputs.
    receipt = call(svc, "receipt_get", id=out["receipt_id"])["receipt"]
    assert receipt_id(receipt) == out["receipt_id"]
    assert receipt["kind"] == "proof" and receipt["verification"]["verdict"] == "same"
    assert receipt["identity"] == {"user": "someone@contoso.com", "tenant": "tenant-1"}
    assert [c["id"] for c in receipt["notebook"]["code"]["cells"]] == \
        [c["cell_id"] for c in svc.notebooks.load("Order values")["cells"]]
    assert call(svc, "prove_list", name="Order values")[0]["id"] == out["id"]


def test_tiny_floating_point_drift_passes_the_default_tolerance(svc):
    svc.fake = FakeFabric(svc, env={"FD_FAKE_FABRIC_DRIFT": "1"})
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    assert out["verdict"]["verdict"] == "same", out
    exact = call(svc, "prove_verify", id=out["id"], table="dbo.order_values", rules={"keys": ["order_id"], "rel": 0})
    assert exact["result"] == "differs" and exact["column_diffs"]["value"]["rows"] == 3


def test_a_wrong_value_is_found_by_key_with_both_values(svc):
    svc.fake = FakeFabric(svc, env={"FD_FAKE_FABRIC_WRONG": "1"})
    rules = {"tables": {"dbo.order_values": {"keys": ["order_id"]}}}
    out = wait(svc, call(svc, "prove_start", **payload(rules=rules))["id"])
    assert out["verdict"]["verdict"] == "differs" and out["verdict"]["text"] == "Results differ in 1 of 2 tables."
    t = next(t for t in out["tables"] if t["table"] == "dbo.order_values")
    assert t["method"] == "keyed" and t["counts"] == {"only_pc": 0, "only_fabric": 0, "changed": 1}
    assert t["examples"] == [{"key": {"order_id": 2}, "status": "changed",
                              "columns": {"value": {"pc": pytest.approx(18.45), "fabric": 0.0}}}]
    assert t["column_diffs"]["value"]["max_abs"] == pytest.approx(18.45)
    # Ignoring the column makes it match; saved as its own receipt pointing at the proof's.
    again = call(svc, "prove_verify", id=out["id"], table="dbo.order_values",
                 rules={"keys": ["order_id"], "ignore": ["value"]}, save=True)
    assert again["result"] == "match"
    saved = call(svc, "receipt_get", id=again["receipt_id"])["receipt"]
    assert saved["kind"] == "verification" and saved["proof"]["receipt"] == out["receipt_id"]


def test_a_write_to_an_input_during_the_proof_makes_it_inconclusive(svc):
    def other_writer():
        write_deltalake(str(svc.lh_root / "Tables" / "dbo" / "orders"),
                        pa.table({"order_id": [9], "region": ["east"], "amount": [1.0]}), mode="append")
    svc.fake = FakeFabric(svc, before_run=other_writer)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    assert out["verdict"]["verdict"] == "inconclusive" and "dbo.orders" in out["verdict"]["text"]
    assert out["shared"]["changed"] == ["dbo.orders"]
    # Pinned: Fabric still read version 0, so the outputs themselves agree.
    assert out["inputs"]["tables_fabric"] == [{"name": "dbo.orders", "version": 0}]
    assert all(t["result"] == "match" for t in out["tables"])


def test_a_file_changed_between_the_runs_makes_it_inconclusive(svc):
    def change_file():
        (svc.lh_root / "Files" / "raw" / "rates.csv").write_text("region,rate\neast,2\nwest,0.9\n", encoding="utf-8")
    svc.fake = FakeFabric(svc, before_run=change_file)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    assert out["verdict"]["verdict"] == "inconclusive" and "Files/raw/rates.csv" in out["verdict"]["text"]
    assert out["inputs"]["equivalent"] is False


def test_a_failing_cell_on_this_pc_is_reported_and_still_gets_a_receipt(svc):
    svc.fake = FakeFabric(svc)
    cells = CELLS[:3] + [{"type": "code", "source": "1 / 0"}]
    out = wait(svc, call(svc, "prove_start", **payload(cells))["id"])
    assert out["pc"]["status"] == "failed" and out["pc"]["failed_cell"] == 4
    assert out["fabric"]["status"] == "failed" and "log_problem" in out["fabric"]
    assert out["verdict"]["verdict"] == "failed"
    assert call(svc, "receipt_get", id=out["receipt_id"])["receipt"]["runs"]["pc"]["status"] == "failed"


def test_cancel_during_the_pc_run(svc):
    svc.fake = FakeFabric(svc)
    cells = CELLS[:2] + [{"type": "code", "source": "import time\nfor _ in range(400):\n    time.sleep(0.05)"}] + CELLS[3:]
    started = call(svc, "prove_start", **payload(cells))
    for _ in range(200):
        if call(svc, "prove_status", id=started["id"])["pc"].get("cell") == 2:
            break
        time.sleep(0.05)
    assert call(svc, "prove_cancel", id=started["id"]) == {"cancelled": True}
    out = wait(svc, started["id"])
    assert out["state"] == "cancelled" and svc.fake.saved == []
    assert lakehouse.proof is None  # the engine's lakehouse module is back to normal
    assert out["receipt_id"]


def test_the_kernel_refuses_cells_while_a_proof_runs_and_one_proof_at_a_time(svc):
    svc.fake = FakeFabric(svc)
    cells = CELLS[:2] + [{"type": "code", "source": "import time\ntime.sleep(1.5)"}]
    started = call(svc, "prove_start", **payload(cells))
    out = handle(svc, json.dumps({"id": 1, "method": "start_cell", "params": {"code": "1"}}))
    assert "proof is running" in out["error"]
    out = handle(svc, json.dumps({"id": 1, "method": "prove_start", "params": payload(cells)}))
    assert "already running" in out["error"]
    wait(svc, started["id"])


def test_delete_outputs_removes_the_proof_folder_only(svc):
    svc.fake = FakeFabric(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    folder = svc.lh_root / "Files" / "_fabric_desktop" / "proofs" / out["id"]
    assert folder.exists()
    call(svc, "prove_delete_outputs", id=out["id"])
    assert not folder.exists() and (svc.lh_root / "Tables" / "dbo" / "orders").exists()
    assert call(svc, "prove_status", id=out["id"])["outputs_deleted_at"]


# ---- what can be proven


def test_check_blocks_what_a_proof_cant_contain():
    base = CELLS[:2]
    spark = proof.check(base + [{"type": "code", "source": "df = spark.read.table('dbo.x')"}], LAKEHOUSE)
    assert not spark["ok"] and "uses Spark" in spark["blockers"][0]["text"] and spark["blockers"][0]["cell"] == 3
    direct = proof.check(base + [{"type": "code", "cell_id": "c9", "source": "from deltalake import DeltaTable\nt = DeltaTable('abfss://x')"}], LAKEHOUSE)
    assert direct["blockers"][0]["cell_id"] == "c9" and direct["blockers"][0]["line"] == 2
    fs = proof.check(base + [{"type": "code", "source": "notebookutils.fs.put('Files/x', 'y')"}], LAKEHOUSE)
    assert "notebookutils.fs" in fs["blockers"][0]["text"]
    sql = proof.check(base + [{"type": "code", "source": "%%sql\nCREATE TABLE t AS SELECT 1"}], LAKEHOUSE)
    assert "SQL that creates or changes" in sql["blockers"][0]["text"]
    assert not proof.check(base, None)["ok"]
    assert "doesn't use" in proof.check([{"type": "code", "source": "x = 1"}], LAKEHOUSE)["blockers"][0]["text"]


def test_check_warns_about_what_it_cant_contain_or_repeat():
    out = proof.check(CELLS[:2] + [
        {"type": "code", "source": "import requests\nrequests.post('https://example.com', json={})"},
        {"type": "code", "source": "import datetime\nstamp = datetime.datetime.now()"},
        {"type": "code", "source": "open('notes.txt', 'w').write('x')"},
        {"type": "code", "source": "notebookutils.credentials.getSecret('https://kv', 'pw')"}], LAKEHOUSE)
    texts = [w["text"] for w in out["warnings"]]
    assert out["ok"] and any("network" in t for t in texts) and any("clock" in t for t in texts)
    assert any("files on the machine" in t for t in texts) and "credentials" in out["notes"][0]["text"]


def test_bad_rules_are_refused_before_anything_runs(svc):
    svc.fake = FakeFabric(svc)
    out = handle(svc, json.dumps({"id": 1, "method": "prove_start", "params": payload(rules={"default": {"tolerance": 1}})}))
    assert "Unknown comparison rule" in out["error"] and svc.fake.saved == []


def test_prove_check_describes_the_plan(svc):
    plan = call(svc, "prove_check", **payload())
    assert plan["check"]["ok"] and "Order values_proof" in plan["plan"]["fabric"]
    assert any(m["module"] == "polars" for m in plan["environment"]["modules"])


# ---- the runtime's rules


def test_pinned_tables_are_read_only_and_unknown_tables_dont_exist(tmp_path):
    write_deltalake(str(tmp_path / "Tables" / "dbo" / "t"), pa.table({"a": [1]}))
    rt = ProofRuntime({"id": "p1", "pins": {"dbo.t": 0}}, "pc").bind(tmp_path)
    try:
        t = rt.open("dbo", "T", lambda uri, v: DeltaTable(uri, version=v))
        assert isinstance(t, ReadOnlyTable) and t.to_pyarrow_table().num_rows == 1
        with pytest.raises(PermissionError, match="doesn't change shared tables"):
            t.delete()
        with pytest.raises(LookupError, match="didn't exist when this proof started"):
            rt.read_location("dbo", "new_table")
        assert rt.log["reads"] == {"dbo.t": 0} and rt.log["seeded"][0] == "random"
    finally:
        rt.restore()


def test_the_lakehouse_module_follows_the_proof_only_off_the_rpc_thread(svc, monkeypatch):
    svc._attach(LAKEHOUSE)
    rt = ProofRuntime({"id": "p2", "pins": {"dbo.orders": 0}}, "pc").bind(svc.lh_root)
    lakehouse.proof = rt
    try:
        # The app's own requests (previews) run on the main thread and see the real lakehouse.
        monkeypatch.setattr(lakehouse, "open_table", lakehouse.open_table)
        assert lakehouse._proof() is None
        monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)
        lakehouse.write_table(pl.DataFrame({"x": [1]}), "dbo.scratch")
        assert (svc.lh_root / "Files" / "_fabric_desktop" / "proofs" / "p2" / "pc" / "tables" / "dbo" / "scratch").exists()
        assert not (svc.lh_root / "Tables" / "dbo" / "scratch").exists()
        assert lakehouse.read_table("dbo.scratch")["x"].to_list() == [1]
    finally:
        lakehouse.proof = None
        rt.restore()


@pytest.mark.parametrize("rules", [{}, {"keys": ["k"]}, {"limit_rows": 1}], ids=["whole rows", "keyed", "checksum"])
def test_whole_numbers_on_one_side_and_decimals_on_the_other_compare_as_numbers(rules):
    pc = pa.table({"k": [1, 2], "n": pa.array([1, 2], pa.int64())})
    out = verify.compare(pc, pa.table({"k": [1, 2], "n": [1.0, 2.0]}), rules)
    assert out["result"] == "match" and out["columns"]["widened"] == ["n (integer vs number)"]
    assert any("compared as numbers" in x for x in out["limitations"]), "the person sees the types differ"
    assert verify.compare(pc, pa.table({"k": [1, 2], "n": [1.0, 2.5]}), rules)["result"] == "differs"
    assert verify.compare(pc, pa.table({"k": [1, 2], "n": ["1", "2"]}), rules)["result"] == "differs"


@pytest.mark.parametrize("keys", [[], ["k"]], ids=["whole rows", "keyed"])
def test_with_no_tolerance_decimals_are_compared_exactly_not_as_doubles(keys):
    # Both values are the same DOUBLE, so rel 0 called them equal: a complete "match" that wasn't.
    from decimal import Decimal as D
    pc = pa.table({"k": [1, 2, 3], "n": pa.array([D("12345678901234567.0000000001"), D("1.5"), D("15")], pa.decimal128(38, 10))})
    fabric = pa.table({"k": [1, 2, 3], "n": pa.array([D("12345678901234567.0000000002"), D("1.50"), D("15")], pa.decimal128(30, 12))})
    out = verify.compare(pc, fabric, {"keys": keys, "rel": 0})
    assert out["result"] == "differs" and out["level"] == "complete", out
    if keys:
        assert out["counts"]["changed"] == 1 and out["examples"][0]["key"] == {"k": 1}
        # The difference is below what a DOUBLE shows, so no "largest 0" is reported for it.
        assert out["column_diffs"]["n"] == {"rows": 1}
    else:
        assert out["counts"] == {"only_pc": 1, "only_fabric": 1}
    # The default tolerance still allows it, and 1.5 = 1.50, 15 = 15.0 and an integer = its decimal exactly.
    assert verify.compare(pc, fabric, {"keys": keys})["result"] == "match"
    whole = pa.table({"k": [1, 2], "n": pa.array([12345678901234567, 15], pa.int64())})
    dec = pa.table({"k": [1, 2], "n": pa.array([D("12345678901234567.00"), D("15.0")], pa.decimal128(20, 2))})
    assert verify.compare(whole, dec, {"keys": keys, "rel": 0})["result"] == "match"
    off = pa.table({"k": [1, 2], "n": pa.array([D("12345678901234567.01"), D("15.0")], pa.decimal128(20, 2))})
    assert verify.compare(whole, off, {"keys": keys, "rel": 0})["result"] == "differs"


def test_keys_of_different_types_are_matched_as_text_instead_of_failing():
    # DuckDB refused to COALESCE a text key with a number key, so the comparison failed outright.
    out = verify.compare(pa.table({"k": ["1", "2"], "v": [1, 2]}), pa.table({"k": [1, 3], "v": [1, 2]}), {"keys": ["k"]})
    assert out["result"] == "differs" and out["columns"]["types"] == ["k (string vs integer)"]
    assert out["counts"] == {"only_pc": 1, "only_fabric": 1, "changed": 0}
    assert [e["key"] for e in out["examples"]] == [{"k": "2"}, {"k": "3"}]


@pytest.mark.parametrize("given, says", [
    ({"examples": -1}, "examples is a whole number"), ({"limit_rows": "abc"}, "limit_rows is a whole number"),
    ({"limit_rows": True}, "limit_rows is a whole number"), ({"abs": "abc"}, "abs is a number"),
    ({"rel": float("nan")}, "rel is a number 0 or more"), ({"rel": -1}, "rel is a number 0 or more"),
    ({"keys": [1]}, "keys is a list of column names"), ({"ignore": {"a": 1}}, "ignore is a list of column names"),
    ({"columns": [1]}, "columns gives tolerances per column"), ({"columns": {"a": {"tol": 1}}}, "The tolerance for a is abs"),
    ({"columns": {"a": {"abs": -1}}}, "abs for a is a number 0 or more"), (["k"], "Comparison rules are a set"),
])
def test_bad_rules_are_refused_with_a_message_not_a_database_error(given, says):
    with pytest.raises(ValueError, match=says):
        verify.rules(given)
    with pytest.raises(ValueError, match="offset is a whole number"):
        verify.compare(pa.table({"k": [1]}), pa.table({"k": [1]}), None, offset=-1)


def test_rules_given_as_text_or_one_name_are_read_as_meant():
    r = verify.rules({"keys": "id", "ignore": " loaded_at ", "examples": "10", "limit_rows": 1e6, "abs": "0.01",
                      "columns": {"amount": {"rel": "1e-6"}}})
    assert (r["keys"], r["ignore"], r["examples"], r["limit_rows"], r["abs"]) == (["id"], ["loaded_at"], 10, 1_000_000, 0.01)
    assert r["columns"] == {"amount": {"rel": 1e-6}} and verify.DEFAULTS["columns"] == {}


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize("rules", [{}, {"keys": ["k"]}, {"limit_rows": 1}], ids=["whole rows", "keyed", "checksum"])
@pytest.mark.parametrize("pc,fabric,result", [
    ([1.0], [NAN], "differs"), ([1], [NAN], "differs"), ([1], [INF], "differs"), ([1.0], [-INF], "differs"),
    ([INF], [-INF], "differs"), ([0], [None], "differs"), ([None], [0.0], "differs"),
    ([NAN], [NAN], "match"), ([INF], [INF], "match"), ([None], [None], "match"), ([0], [-0.0], "match")])
def test_nan_infinity_and_null_are_never_within_a_tolerance(rules, pc, fabric, result):
    a = pa.table({"k": [1], "n": pa.array(pc, pa.int64() if isinstance(pc[0], int) else pa.float64())})
    assert verify.compare(a, pa.table({"k": [1], "n": pa.array(fabric, pa.float64())}), rules)["result"] == result


def test_verify_whole_rows_counts_duplicates_and_reports_levels():
    a = pa.table({"k": [1, 1, 2], "v": [1.0, 1.0, 2.0]})
    b = pa.table({"k": [1, 2, 2], "v": [1.0, 2.0, 2.0]})
    out = verify.compare(a, b)
    assert out["method"] == "whole rows" and out["counts"] == {"only_pc": 1, "only_fabric": 1}
    keyed = verify.compare(a, b, {"keys": ["k"]})
    assert keyed["method"] == "whole rows" and keyed["duplicates"] == {"pc": 1, "fabric": 1}
    big = verify.compare(a, a, {"limit_rows": 2})
    assert big["level"] == "fingerprint" and big["result"] == "match" and big["limitations"]
    schema = verify.compare(pa.table({"k": [1]}), pa.table({"k": [1], "extra": [2]}))
    assert schema["result"] == "differs" and schema["columns"]["only_fabric"] == ["extra"]
    ts = verify.compare(pa.table({"t": pa.array([1_000_000_123], pa.timestamp("us"))}),
                        pa.table({"t": pa.array([1_000_000_999], pa.timestamp("us"))}), {"timestamps": "ms"})
    assert ts["result"] == "match"


def test_a_proof_uses_its_own_notebooks_kernel_not_the_open_one(svc):
    # A kernel per notebook (as with notebooks in their own process), in the engine to keep it quick.
    svc.sessions.in_process, svc.sessions._make = True, lambda _process: Kernel()
    svc.sessions.active = "Other"
    other = svc.sessions.kernel("Other")
    other.run("y = 1")
    svc.fake = FakeFabric(svc)
    cells = CELLS[:2] + [{"type": "code", "source": "import time\nfor _ in range(400):\n    time.sleep(0.05)"}] + CELLS[3:]
    started = call(svc, "prove_start", **payload(cells))
    for _ in range(200):
        if call(svc, "prove_status", id=started["id"])["pc"].get("cell") == 2:
            break
        time.sleep(0.05)
    assert call(svc, "prove_cancel", id=started["id"]) == {"cancelled": True}
    assert wait(svc, started["id"])["state"] == "cancelled"
    assert svc.sessions.kernel("Order values").notebook_name == "Order values"
    assert other.run("print(y)")["outputs"][0]["text"] == "1\n"  # the open notebook kept its variables
