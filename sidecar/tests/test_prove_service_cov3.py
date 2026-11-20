"""prove_service.py: a proof cancelled or failing at each stage, a Fabric run whose status or log can't be
read, a verification that can't read an output, and a receipt made offline. A stand-in service with fake
kernels and a fake Fabric; nothing runs a kernel or reaches Fabric."""

import json
import zipfile
from types import SimpleNamespace

import pytest

from fabric_desktop import prove_service as ps

REF = {"workspace_id": "ws-1", "lakehouse_id": "lh-1", "name": "LH_Gold"}
WS = {"workspace_id": "ws-1", "workspace_name": "Dev"}


class Svc(ps.ProveMixin):
    receipts = None  # a plain attribute here, not the store on disk

    def __init__(self, root):
        self.root = root
        self.saved = []
        self.receipts = SimpleNamespace(put=lambda body: "r1")

    def _proof_save(self, record):
        self.saved.append(dict(record))

    def _cost_settings(self):
        return {}


def _record(**extra):
    return {"id": "p1", "name": "nb", "state": "preparing", "pc": {"cells": {}, "status": "ok"},
            "fabric": {"status": "ok"}, "lakehouse": REF, "inputs": {}, "message": "", "_cancel": False,
            "started_at": "2026-11-11T00:00:00+00:00"} | extra


def test_a_cancel_while_fabric_is_starting_needs_no_job_to_stop(tmp_path):
    svc = Svc(tmp_path)
    svc.fabric_jobs = SimpleNamespace(cancel_job=lambda *a: pytest.fail("cancelled a job that hasn't started"))
    svc._proof_state()["p1"] = _record(state="fabric", fabric={})
    assert svc.prove_cancel("p1") == {"cancelled": True}
    assert svc._proof_state()["p1"]["_cancel"] is True


def test_a_replay_sample_for_a_table_it_doesnt_read_isnt_written(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w"):
        pass
    monkeypatch.setattr(svc, "_replay_copy", lambda info: bundle)
    monkeypatch.setattr(ps.replay, "read", lambda path: {"samples": [{"table": "LH/dbo.other", "file": "x.parquet"}]})
    svc._replay_seed({}, "rid", (tmp_path / "root").as_posix(), lambda: {}, ["dbo.orders"])
    assert not (tmp_path / "root").exists()


# ---------------------------------------------------------------- the run, stage by stage


def _staged(svc, monkeypatch, fabric):
    monkeypatch.setattr(svc, "_proof_tables", lambda ref: SimpleNamespace(versions=lambda: {"dbo.t": 1}), raising=False)
    monkeypatch.setattr(svc, "_schemas_enabled", lambda ws, lh: True, raising=False)
    monkeypatch.setattr(svc, "_proof_location", lambda ref: ("root", lambda: {}))
    monkeypatch.setattr(svc, "_proof_pc", lambda record, *a: {"reads": {}})
    monkeypatch.setattr(svc, "_proof_fabric", fabric)
    monkeypatch.setattr(svc, "_proof_verify", lambda *a: None)
    monkeypatch.setattr(svc, "_proof_receipt", lambda record: {"state": record["state"]})
    monkeypatch.setattr(ps.scorecard, "build", lambda record, settings: {"cost": None})


def test_a_cancel_during_the_fabric_run_ends_the_proof_cancelled(tmp_path, monkeypatch):
    svc = Svc(tmp_path)

    def cancelled_meanwhile(record, *a):
        record["_cancel"] = True
        return None
    _staged(svc, monkeypatch, cancelled_meanwhile)
    record = _record()
    svc._proof_run(record, [], [], REF, WS)
    assert record["state"] == "cancelled" and record["message"].startswith("Cancelled.")
    assert record["receipt_id"] == "r1" and svc.saved[-1]["state"] == "cancelled"


def test_a_fabric_stage_that_fails_ends_the_proof_failed(tmp_path, monkeypatch):
    svc = Svc(tmp_path)

    def fails(record, *a):
        raise RuntimeError("capacity paused")
    _staged(svc, monkeypatch, fails)
    record = _record()
    svc._proof_run(record, [], [], REF, WS)
    assert record["state"] == "failed" and record["message"] == "RuntimeError: capacity paused"


def test_a_profile_that_cant_take_the_proof_never_changes_its_result(tmp_path, monkeypatch, caplog):
    svc = Svc(tmp_path)
    _staged(svc, monkeypatch, lambda record, *a: {"reads": {}})

    def broken(record):
        raise OSError("profile locked")
    monkeypatch.setattr(svc, "_progress_observe_proof", broken, raising=False)
    record = _record()
    svc._proof_run(record, [], [], REF, WS)
    assert record["state"] == "done" and "earned" not in record
    assert any("couldn't be added to the profile" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- this PC's side


class Kernel:
    def __init__(self, limit_hit=""):
        self.limit_hit, self.started, self.bound = limit_hit, [], None

    def restart(self):
        pass

    def bind_proof(self, config, side, root, options):
        self.bound = side

    def unbind_proof(self):
        self.bound = None

    def start(self, code):
        self.started.append(code)

    def poll(self, version, timeout):
        return {"status": "ok", "seconds": 0.1, "version": 1, "outputs": []}

    def finish_proof(self, modules):
        return {"reads": {}, "modules": modules}


def _pc(svc, monkeypatch, kernel):
    monkeypatch.setattr(svc, "_attach", lambda ref: None, raising=False)
    svc.sessions = SimpleNamespace(kernel=lambda name: kernel)
    monkeypatch.setattr(svc, "_resources_dir", lambda name: None, raising=False)
    monkeypatch.setattr(svc, "_saved_inputs", lambda name: None, raising=False)


def test_a_cancel_before_the_first_cell_runs_nothing(tmp_path, monkeypatch):
    svc, kernel = Svc(tmp_path), Kernel()
    _pc(svc, monkeypatch, kernel)
    record = _record(_cancel=True)
    log = svc._proof_pc(record, [{"source": "x = 1"}], [0], REF, {}, "root", lambda: {}, ["polars"])
    assert kernel.started == [] and record["pc"]["status"] == "cancelled" and kernel.bound is None
    assert log == {"reads": {}, "modules": ["polars"]}


def test_a_kernel_stopped_by_its_limit_says_which(tmp_path, monkeypatch):
    svc, kernel = Svc(tmp_path), Kernel(limit_hit="memory")
    _pc(svc, monkeypatch, kernel)
    record = _record()
    svc._proof_pc(record, [{"source": "x = 1"}], [0], REF, {}, "root", lambda: {}, [])
    assert record["pc"]["stopped_by"] == "the memory limit" and record["pc"]["status"] == "ok"
    assert kernel.started == ["x = 1"]


# ---------------------------------------------------------------- Fabric's side


class Fabric:
    def __init__(self, answers):
        self.answers = list(answers)

    def save_notebook(self, ws, name, ipynb):
        return {}  # a create can come back without the item

    def list_notebooks(self, ws):
        return [{"displayName": "other", "id": "x"}, {"displayName": "nb" + ps.PROOF_SUFFIX, "id": "item-1"}]

    def job_instance(self, ws, item, job):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _fabric(svc, monkeypatch, answers, max_seconds=100):
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(ps, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda s: None))
    monkeypatch.setattr(ps, "MAX_FABRIC_SECONDS", max_seconds)
    svc.fabric = Fabric(answers)
    svc.fabric_jobs = SimpleNamespace(run_item_job=lambda ws, item, job_type, params: "job-1")
    monkeypatch.setattr(svc, "_log_fabric_run", lambda run: None, raising=False)
    monkeypatch.setattr(svc, "_remember_fabric_environment", lambda env, modules: pytest.fail("no environment"),
                        raising=False)


CONFIG = {"id": "p1", "pins": {}, "flat": False, "seed": 0}


def test_a_fabric_run_whose_status_drops_and_whose_log_cant_be_read(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    _fabric(svc, monkeypatch, [ConnectionError("dropped"), {"status": "InProgress"}, {"status": "Completed"}])

    def unreadable(ref, path):
        raise OSError("403")
    monkeypatch.setattr(svc, "_proof_read_file", unreadable, raising=False)
    record = _record()
    assert svc._proof_fabric(record, [{"type": "code", "source": "x = 1"}], REF, WS, CONFIG, []) is None
    f = record["fabric"]
    assert f["item_id"] == "item-1" and f["job_id"] == "job-1" and f["status"] == "ok"
    assert f["log_problem"] == "The Fabric run's log couldn't be read: 403"


def test_a_fabric_run_past_the_time_limit_is_failed_and_its_log_still_read(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    _fabric(svc, monkeypatch, [], max_seconds=0)
    monkeypatch.setattr(svc, "_proof_read_file", lambda ref, path: json.dumps({"seconds": 4}), raising=False)
    record = _record()
    log = svc._proof_fabric(record, [{"type": "code", "source": "x = 1"}], REF, WS, CONFIG, [])
    assert log == {"seconds": 4}
    assert record["fabric"]["status"] == "failed" and record["fabric"]["run_seconds"] == 4
    assert "start_seconds" not in record["fabric"]


# ---------------------------------------------------------------- verifying and the receipt


def test_an_output_that_cant_be_read_is_an_error_row_not_a_failed_verify(tmp_path, monkeypatch):
    svc = Svc(tmp_path)

    def unreadable(root, options, rid, side, key):
        raise OSError(f"{side} copy of {key} is gone")
    monkeypatch.setattr(svc, "_proof_output", unreadable)
    record = _record()
    log = {"writes": {"dbo.t": "overwrite"}}
    svc._proof_verify(record, {"dbo.t": 1}, {"dbo.t": 1}, log, log, "root", lambda: {})
    [row] = record["tables"]
    assert row["result"] == "error" and row["note"] == "Couldn't verify it: pc copy of dbo.t is gone"
    assert record["shared"] == {"changed": [], "unchanged": True}


def test_a_receipt_made_offline_says_who_isnt_known(tmp_path):
    svc = Svc(tmp_path)

    def offline():
        raise ConnectionError("offline")
    svc.auth = SimpleNamespace(signed_in_user=offline, signed_in_tenant=offline)
    body = svc._proof_receipt(_record(state="done"))
    assert body["identity"] == {"user": None, "tenant": None}
