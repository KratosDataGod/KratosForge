"""Proof and replay edges the end-to-end proof tests don't reach: refusals before anything runs,
receipts and bundles with no free file name, a cancel that can't reach Fabric, and the sample read
when signed out or offline. A stand-in service is used; nothing runs a kernel or reaches Fabric."""

import base64
import types

import pytest
import requests

from fabric_desktop import prove_service as ps
from fabric_desktop.onelake import DFS_HOST

REF = {"workspace_id": "ws-1", "lakehouse_id": "lh-1", "name": "LH_Gold"}


class Notebooks:
    def __init__(self, extras=None):
        self.extras = extras or {}

    def extra(self, name, key):
        return self.extras.get((name, key))

    def set_extra(self, name, key, value):
        self.extras[(name, key)] = value


class Svc(ps.ProveMixin):
    def __init__(self, root, notebooks=None, auth=None):
        self.root, self.notebooks, self.auth = root, notebooks or Notebooks(), auth


def _record(state="done", **extra):
    return {"id": "p1", "name": "nb", "state": state, "pc": {}, "fabric": {}, "lakehouse": REF,
            "started_at": "2026-11-11T00:00:00+00:00", "_cancel": False} | extra


def test_a_time_that_isnt_a_date_reads_as_none_and_a_naive_one_as_utc():
    assert ps._parse("yesterday") is None
    assert ps._parse("2026-11-11T05:00:00").tzinfo is not None


def test_a_receipt_exports_as_markdown_and_refuses_other_formats(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    monkeypatch.setattr(ps.receipts.ReceiptStore, "get", lambda self, id: {"kind": "proof"})
    monkeypatch.setattr(ps.receipts, "to_markdown", lambda body, id, hide: f"# {id} {hide}")
    assert svc.receipt_export("r1", "markdown") == {"format": "markdown", "text": "# r1 []"}
    with pytest.raises(ValueError, match="markdown, html or json"):
        svc.receipt_export("r1", "pdf")


def test_a_receipt_with_no_free_name_in_downloads_isnt_saved(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    svc.receipt_export = lambda id, format, hide: {"format": "html", "text": "<p>"}
    monkeypatch.setattr(ps.known_folders, "downloads", lambda: tmp_path / "dl")
    real_open = open

    def taken(path, mode="r", *a, **k):
        if mode == "x":
            raise FileExistsError(path)
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("builtins.open", taken)
    with pytest.raises(ValueError, match="free file name in Downloads for the receipt"):
        svc.receipt_save("abc")


class Kernel:
    def __init__(self, status="idle", limits=None):
        self.status, self._limits = status, limits

    def poll(self):
        return {"status": self.status}

    def reads(self):
        return [{"table": "dbo.orders", "version": 3}]


def _bundle_svc(tmp_path, kernel):
    svc = Svc(tmp_path)
    svc.sessions = types.SimpleNamespace(kernel=lambda name: kernel)
    return svc


def test_a_bundle_waits_for_a_proof_and_a_running_cell(tmp_path):
    svc = _bundle_svc(tmp_path, Kernel(status="running"))
    svc._proof_state()["p1"] = _record(state="pc")
    with pytest.raises(RuntimeError, match="A proof is using the kernel"):
        svc._replay_bundle("nb", [], 0, REF)
    svc._proof_state().clear()
    with pytest.raises(RuntimeError, match="A cell is running"):
        svc._replay_bundle("nb", [], 0, REF)


def test_a_bundle_from_a_process_kernel_records_its_limits(tmp_path, monkeypatch):
    kernel = Kernel()
    kernel.limits = lambda: {"memory_mb": 4096}
    seen = []
    monkeypatch.setattr(ps.replay, "build", lambda *a: seen.append(a[-1]) or {"ok": True})
    assert _bundle_svc(tmp_path, kernel)._replay_bundle("nb", [], 0, REF) == {"ok": True}
    assert seen == [{"process": True, "limits": {"memory_mb": 4096}}]


def test_a_bundle_with_no_free_name_in_downloads_isnt_saved(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    svc._replay_bundle = lambda *a: {"cells": []}
    monkeypatch.setattr(ps.known_folders, "downloads", lambda: tmp_path)

    def taken(path, bundle, samples, mode):
        raise FileExistsError(path)
    monkeypatch.setattr(ps.replay, "write", taken)
    with pytest.raises(ValueError, match="free file name in Downloads for the bundle"):
        svc.replay_bundle_save("nb", [], 0)


def test_a_sample_needs_a_sign_in_and_the_network(tmp_path):
    def offline(scopes):
        raise requests.ConnectionError("no network")
    svc = Svc(tmp_path, auth=types.SimpleNamespace(signed_in_user=lambda: "ana", silent_token=offline))
    with pytest.raises(ConnectionError) as raised:
        svc._replay_sample({"table": "dbo.orders"})
    assert str(raised.value) == ps.SAMPLE_UNREACHABLE
    svc.auth = types.SimpleNamespace(signed_in_user=lambda: "ana", silent_token=lambda s: None)
    with pytest.raises(PermissionError) as raised:
        svc._replay_sample({"table": "dbo.orders"})
    assert str(raised.value) == ps.SAMPLE_SIGNED_OUT


def test_a_bundle_too_large_isnt_opened(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    monkeypatch.setattr(Svc, "MAX_BUNDLE_BYTES", 4)
    with pytest.raises(ValueError, match="too large for a replay bundle"):
        svc.replay_bundle_open(content=base64.b64encode(b"12345").decode())


def test_a_notebook_that_isnt_a_replay_is_named(tmp_path):
    with pytest.raises(LookupError, match="nb isn't a replay notebook"):
        Svc(tmp_path)._replay_of("nb")


def test_a_replay_waits_for_a_proof_and_stops_at_its_first_blocker(tmp_path):
    svc = Svc(tmp_path)
    svc._proof_state()["p1"] = _record(state="fabric")
    with pytest.raises(RuntimeError, match="A proof is using the kernel"):
        svc.replay_start("nb", REF)
    svc._proof_state().clear()
    svc.replay_check = lambda name, ref: {"blockers": ["dbo.orders was vacuumed at version 3."]}
    with pytest.raises(RuntimeError, match="vacuumed"):
        svc.replay_start("nb", REF)


def test_wizard_templates_and_preview_come_from_the_wizard(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.wizard, "templates", lambda: {"t": 1})
    monkeypatch.setattr(ps.wizard, "build", lambda template, values: {"template": template, **values})
    svc = Svc(tmp_path)
    assert svc.wizard_templates() == {"t": 1}
    assert svc.wizard_preview("ingest", {"table": "x"}) == {"template": "ingest", "table": "x"}


@pytest.mark.parametrize("bad", ["df", ["ok", "not a name"], ["ok", 3]])
def test_ignored_hidden_names_must_be_a_list_of_identifiers(tmp_path, bad):
    with pytest.raises(ValueError, match="list of variable names"):
        Svc(tmp_path).notebook_debug_set("nb", ignore_hidden=bad)


def test_ignoring_nothing_and_watching_nothing_clears_the_saved_choices(tmp_path):
    nbs = Notebooks({("nb", "debug"): {"watches": [], "ignore_hidden": ["df"]}})
    assert Svc(tmp_path, nbs).notebook_debug_set("nb", ignore_hidden=["b", "a", "b"]) == {
        "watches": [], "ignore_hidden": ["a", "b"]}
    Svc(tmp_path, nbs).notebook_debug_set("nb", ignore_hidden=[])
    assert nbs.extras[("nb", "debug")] is None


def test_proof_locations_point_at_the_lakehouse_with_the_storage_sign_in(tmp_path, monkeypatch):
    calls = []

    class Files:
        def __init__(self, token):
            calls.append(("token", token()))

        def read_bytes(self, ws, lh, rel):
            calls.append(("read", ws, lh, rel))
            return b"{}"

        def delete(self, ws, lh, rel):
            calls.append(("delete", ws, lh, rel))
    monkeypatch.setattr(ps, "FileStore", Files)
    monkeypatch.setattr(ps, "storage_options", lambda token: {"bearer": token})
    svc = Svc(tmp_path, auth=types.SimpleNamespace(storage_token=lambda: "st"))
    svc._sbs_tables = lambda ref: ["tables of", ref["lakehouse_id"]]
    root, options = svc._proof_location(REF)
    assert root == f"abfss://ws-1@{DFS_HOST}/lh-1" and options() == {"bearer": "st"}
    assert svc._proof_tables(REF) == ["tables of", "lh-1"]
    assert svc._proof_read_file(REF, "a/log.json") == b"{}"
    svc._proof_delete_folder(REF, "a")
    assert calls == [("token", "st"), ("read", "ws-1", "lh-1", "a/log.json"), ("token", "st"), ("delete", "ws-1", "lh-1", "a")]


def test_a_check_with_no_publish_workspace_blocks_the_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.proof, "check", lambda cells, ref, **k: {"ok": True, "blockers": []})
    monkeypatch.setattr(ps.envprofile, "check", lambda cells, seen: {})
    svc = Svc(tmp_path)
    svc._checks = lambda name: {}

    def no_ws(name, ref, workspace):
        raise ValueError("Pick a workspace to publish to first.")
    svc._publish_workspace = no_ws
    out = svc.prove_check("nb", [], REF)
    assert out["check"]["ok"] is False and out["check"]["blockers"][0]["text"] == "Pick a workspace to publish to first."
    assert out["plan"]["fabric"] == ""


def test_a_proof_doesnt_start_beside_a_replay_or_a_side_by_side_run(tmp_path):
    svc = Svc(tmp_path)
    svc._replay_live = lambda: {"name": "other"}
    with pytest.raises(RuntimeError, match="A replay is using the kernel"):
        svc.prove_start("nb", [], REF)
    svc._replay_live = lambda: None
    svc._sbs_state = lambda: {"s1": {"state": "fabric"}}
    with pytest.raises(RuntimeError, match="A side-by-side run is going"):
        svc.prove_start("nb", [], REF)


def test_a_proof_that_cant_be_proven_names_the_cell(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    svc._replay_live = lambda: None
    svc._sbs_state = lambda: {}
    svc._checks = lambda name: {}
    monkeypatch.setattr(ps.proof, "check", lambda cells, ref, **k: {
        "ok": False, "blockers": [{"cell": 2, "text": "It calls mssparkutils.fs.rm."}]})
    with pytest.raises(ValueError, match="can't be proven yet: Cell 2 It calls"):
        svc.prove_start("nb", [], REF)
    monkeypatch.setattr(ps.proof, "check", lambda cells, ref, **k: {"ok": False, "blockers": [{"text": "No lakehouse."}]})
    with pytest.raises(ValueError, match="can't be proven yet: No lakehouse."):
        svc.prove_start("nb", [], REF)


def test_status_of_an_unknown_proof_is_a_lookup_error(tmp_path):
    with pytest.raises(LookupError, match="No proof nope"):
        Svc(tmp_path).prove_status("nope")


def test_cancel_stops_the_pc_kernel_or_the_fabric_job(tmp_path):
    svc = Svc(tmp_path)
    interrupted = []
    svc.sessions = types.SimpleNamespace(kernel=lambda name: types.SimpleNamespace(interrupt=lambda: interrupted.append(name)))
    assert svc.prove_cancel("nope") == {"cancelled": False}
    svc._proof_state()["p1"] = _record(state="done")
    assert svc.prove_cancel("p1") == {"cancelled": False}
    svc._proof_state()["p1"] = _record(state="pc")
    assert svc.prove_cancel("p1") == {"cancelled": True} and interrupted == ["nb"]

    cancelled = []
    svc.fabric_jobs = types.SimpleNamespace(cancel_job=lambda *a: cancelled.append(a))
    svc._proof_state()["p2"] = _record(id="p2", state="fabric",
                                       fabric={"workspace_id": "ws-1", "item_id": "nb-1", "job_id": "j-1"})
    assert svc.prove_cancel("p2") == {"cancelled": True} and cancelled == [("ws-1", "nb-1", "j-1")]
    assert svc._proof_state()["p2"]["_cancel"] is True


def test_a_cancel_fabric_refuses_is_recorded_on_the_run(tmp_path):
    svc = Svc(tmp_path)

    def refuse(*a):
        raise RuntimeError("409 job already finishing")
    svc.fabric_jobs = types.SimpleNamespace(cancel_job=refuse)
    svc._proof_state()["p1"] = _record(state="fabric", fabric={"workspace_id": "w", "item_id": "i", "job_id": "j"})
    assert svc.prove_cancel("p1") == {"cancelled": True}
    assert svc._proof_state()["p1"]["message"] == "Couldn't cancel the Fabric run: 409 job already finishing"


def test_verify_again_needs_a_finished_proof_and_a_table_both_sides_wrote(tmp_path):
    svc = Svc(tmp_path)
    svc._proof_state()["p1"] = _record(state="verifying")
    with pytest.raises(RuntimeError, match="still running"):
        svc.prove_verify("p1", "dbo.orders")
    with pytest.raises(RuntimeError, match="Cancel the proof first"):
        svc.prove_delete_outputs("p1")
    svc._proof_state()["p1"] = _record(tables=[{"table": "dbo.orders", "pc_written": True, "fabric_written": False}])
    with pytest.raises(LookupError, match="dbo.orders wasn't written by both sides"):
        svc.prove_verify("p1", "dbo.orders")
    with pytest.raises(LookupError, match="dbo.other wasn't written"):
        svc.prove_verify("p1", "dbo.other")


def test_a_record_that_keeps_changing_is_reported_busy(monkeypatch):
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)

    class Changing(dict):
        def items(self):
            raise RuntimeError("dictionary changed size during iteration")
    with pytest.raises(RuntimeError, match="The proof is busy"):
        ps._copy(Changing(a=1))
