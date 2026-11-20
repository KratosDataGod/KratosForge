"""prove_service paths that mutation testing (batch 16, docs/quality/mutation-testing.md) showed no
test checked: how long a proof waits for Fabric, the Fabric job's own times and failure message,
deleting a proof's outputs, verifying a table again, a receipt that can't be saved, the plan's
wording, comparing the two runs, what a proof's receipt keeps, the verdict's wording, proof rules,
the Variables pane's saved choices, exporting and saving receipts, and opening, checking and saving
replay bundles. Fabric is a fake that runs the published notebook in another process; nothing
reaches a tenant."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from fabric_desktop import known_folders, prove_service, replay
from fabric_desktop.onelake import read_storage_options
from fabric_desktop.prove_service import _normalized_rules, _verdict, replay_key

from test_replay import LH as REPLAY_LH
from test_replay import REPLAY_CELLS
from test_prove import LAKEHOUSE, FakeFabric, call, payload, svc, wait  # noqa: F401 - the fixture is used by name


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """App data and Downloads go to this test's folder, never the real ones."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads" / "nested")


def test_a_proof_waits_up_to_six_hours_for_fabric():
    assert prove_service.MAX_FABRIC_SECONDS == 6 * 60 * 60


# ---- a proof's Fabric side, end to end

def test_a_proof_records_how_long_fabric_took_to_start_and_deletes_its_outputs(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    fab = out["fabric"]
    assert fab["job_seconds"] == 90 and fab["message"] == ""
    assert fab["start_seconds"] == round(max(90 - fab["run_seconds"], 0), 2)
    assert call(svc, "prove_delete_outputs", id=out["id"]) == {"deleted": f"Files/_fabric_desktop/proofs/{out['id']}"}


class UntimedFabric(FakeFabric):
    """A Fabric job with no start time and a failure reason given as text."""

    def job_instance(self, ws, item, job):
        return {"status": "Failed", "endTimeUtc": "2026-10-16T10:01:30Z", "failureReason": "Capacity paused"}


def test_a_fabric_job_without_times_has_no_job_seconds_and_keeps_its_reason(svc):  # noqa: F811
    svc.fake = UntimedFabric(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    fab = out["fabric"]
    assert fab["status"] == "failed" and fab["message"] == "Capacity paused"
    assert fab["job_seconds"] is None and "start_seconds" not in fab
    assert out["verdict"]["text"] == "The run failed on Fabric, so the results can't be compared."


def test_verifying_a_table_again_saves_a_receipt_only_when_asked(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    again = svc.prove_verify(out["id"], "dbo.order_values")
    assert again["result"] == "match" and "receipt_id" not in again
    saved = svc.prove_verify(out["id"], "dbo.order_values", save=True)
    body = svc.receipt_get(saved["receipt_id"])["receipt"]
    assert body["verification"]["verdict"] == "same"
    assert body["verification"]["text"] == "dbo.order_values: match under the rules given."


def test_a_receipt_that_cant_be_saved_says_why_in_the_message(svc, monkeypatch):  # noqa: F811
    svc.fake = FakeFabric(svc)

    def no_scorecard(*_args, **_kwargs):
        raise ValueError("no cost settings")

    monkeypatch.setattr(prove_service.scorecard, "build", no_scorecard)
    pid = call(svc, "prove_start", **payload())["id"]
    for _ in range(1200):
        out = call(svc, "prove_status", id=pid)
        if out["state"] not in prove_service.RUNNING:
            break
        time.sleep(0.05)
    assert out["state"] == "done" and out.get("receipt_id") is None
    assert out["message"] == "The receipt couldn't be saved: no cost settings"


def test_the_plan_names_the_workspace_and_lakehouse_or_says_its_own(svc):  # noqa: F811
    named = svc.prove_check("NB", [], LAKEHOUSE)["plan"]
    assert named["fabric"].startswith("Publishes a copy as NB_proof in Data and runs it there.")
    assert named["outputs"] == "Writes go to Files/_fabric_desktop/proofs/<proof>/ in LH_Proof, not to its tables."
    unnamed = svc.prove_check("NB", [], {"workspace_id": "ws", "lakehouse_id": "lh"})["plan"]
    assert unnamed["fabric"].startswith("Publishes a copy as NB_proof in its workspace and runs it there.")
    assert unnamed["outputs"] == ("Writes go to Files/_fabric_desktop/proofs/<proof>/ in the lakehouse, "
                                  "not to its tables.")


# ---- comparing the two runs

def _verified(svc, pins, after, pc_log, fabric_log):
    record = {"id": "p1", "inputs": {}, "pc": {"status": "ok"}, "fabric": {"status": "ok"}}
    svc._proof_verify(record, pins, after, pc_log, fabric_log, "root", dict)
    return record


def test_more_than_five_changed_tables_are_cut_short_with_dots(svc):  # noqa: F811
    six = {f"dbo.t{i}": 1 for i in range(1, 7)}
    rec = _verified(svc, six, {k: 2 for k in six}, {}, {})
    assert "(dbo.t1, dbo.t2, dbo.t3, dbo.t4, dbo.t5...)." in rec["verdict"]["problems"][0]
    five = {f"dbo.t{i}": 1 for i in range(1, 6)}
    rec = _verified(svc, five, {k: 2 for k in five}, {}, {})
    assert "(dbo.t1, dbo.t2, dbo.t3, dbo.t4, dbo.t5). Something else" in rec["verdict"]["problems"][0]


def test_a_table_written_on_one_side_only_says_which(svc):  # noqa: F811
    rec = _verified(svc, {}, {}, {"writes": {"dbo.a": "overwrite"}}, {"writes": {"dbo.b": "append"}})
    rows = {r["table"]: (r["result"], r["note"]) for r in rec["tables"]}
    assert rows == {"dbo.a": ("only_pc", "Written on this PC only."), "dbo.b": ("only_fabric", "Written in Fabric only.")}


def test_the_two_runtimes_differences_are_listed(svc):  # noqa: F811
    rec = _verified(svc, {}, {}, {"environment": {"python": "3.11"}}, {"environment": {"python": "3.12"}})
    assert rec["environment"]["differences"] == [{"what": "Python", "This PC": "3.11", "Fabric": "3.12"}]
    rec = _verified(svc, {}, {}, {"environment": {"python": "3.11"}}, None)
    assert rec["environment"]["differences"] == []


def test_a_proof_receipt_keeps_every_section_of_the_record(svc):  # noqa: F811
    record = {
        "id": "p1", "name": "NB", "code": "x = 1", "parameters": {"day": "2026-11-14"}, "state": "done",
        "started_at": "2026-11-14T10:00:00+00:00", "finished_at": "2026-11-14T10:02:00+00:00",
        "lakehouse": LAKEHOUSE, "inputs": {"pinned_tables": 1}, "environment": {"differences": []},
        "pc": {"status": "ok", "seconds": 1.5}, "fabric": {"status": "ok", "job_seconds": 90},
        "tables": [{"table": "dbo.t", "result": "match", "rules": {"abs": 0.1}, "limitations": ["sampled"]}],
        "verdict": {"verdict": "same"}, "rules": {"default": {"abs": 0.1}}, "shared": {"unchanged": True},
        "scorecard": {"cost": {"pc": 0.0}}, "check": {"limits": ["no display"], "warnings": [{"cell": 2, "text": "random"}]},
    }
    body = svc._proof_receipt(record)
    assert body["notebook"] == {"name": "NB", "code": "x = 1", "parameters": {"day": "2026-11-14"}}
    assert body["inputs"] == {"pinned_tables": 1} and body["environment"] == {"differences": []}
    assert body["runs"] == {"pc": {"status": "ok", "seconds": 1.5}, "fabric": {"status": "ok", "job_seconds": 90}}
    assert body["outputs"] == [{"table": "dbo.t", "result": "match", "limitations": ["sampled"]}]
    assert body["verification"] == {"verdict": "same", "rules": {"default": {"abs": 0.1}}, "shared_unchanged": True}
    assert body["cost"] == {"pc": 0.0}
    assert body["limitations"] == ["no display", "Cell 2: random", "dbo.t: sampled"]


# ---- the verdict

def _record(pc="ok", fabric="ok", stopped=None):
    return {"pc": {"status": pc, **({"stopped_by": stopped} if stopped else {})}, "fabric": {"status": fabric}}


def test_a_run_stopped_on_this_pc_says_what_stopped_it():
    v = _verdict(_record(pc="failed", stopped="the memory limit"), [], [])
    assert v["text"] == "The run failed on this PC (stopped by the memory limit), so the results can't be compared."


def test_a_stop_on_this_pc_isnt_blamed_for_a_fabric_failure():
    v = _verdict(_record(fabric="failed", stopped="the memory limit"), [], [])
    assert v["text"] == "The run failed on Fabric, so the results can't be compared."


def test_a_checksum_compare_on_any_table_makes_the_level_fingerprint():
    rows = [{"result": "match", "level": "complete"}, {"result": "match", "level": "fingerprint"}]
    v = _verdict(_record(), rows, [])
    assert v["level"] == "fingerprint" and "some by checksum only" in v["text"]
    v = _verdict(_record(), [{"result": "match", "level": "complete"}], [])
    assert v["level"] == "complete" and "every row compared" in v["text"]


def test_the_verdict_says_table_for_one_and_tables_for_more():
    one = [{"result": "match", "level": "complete"}]
    assert _verdict(_record(), one, [])["text"] == "Same results in both places (1 table, every row compared)."
    assert _verdict(_record(), one * 2, [])["text"] == "Same results in both places (2 tables, every row compared)."
    bad = [{"result": "differs", "level": "complete"}]
    assert _verdict(_record(), bad, [])["text"] == "Results differ in 1 of 1 table."
    assert _verdict(_record(), bad + one, [])["text"] == "Results differ in 1 of 2 tables."


def test_a_table_is_keyed_lower_case_with_dbo_when_no_schema():
    assert replay_key("Sales.Orders") == "sales.orders"
    assert replay_key("Orders") == "dbo.orders"


# ---- proof rules and the Variables pane

def test_proof_rules_keep_their_default_and_check_each_table_with_it():
    assert _normalized_rules({"default": {"keys": ["id"]}}) == {"default": {"keys": ["id"]}}
    assert _normalized_rules({"tables": {"Orders": {"abs": 0.01}}}) == {"tables": {"dbo.orders": {"abs": 0.01}}}
    with pytest.raises(ValueError, match="abs"):
        _normalized_rules({"tables": {"orders": {"abs": -1}}})


def test_saving_proof_rules_returns_them_as_kept(svc):  # noqa: F811
    svc.notebooks.save("NB", [{"type": "code", "source": "x = 1"}], None)
    assert svc.prove_rules_set("NB", {"tables": {"Orders": {"abs": 0.01}}}) == {"tables": {"dbo.orders": {"abs": 0.01}}}


def test_leaving_out_the_watches_keeps_the_saved_ones(svc):  # noqa: F811
    svc.notebooks.save("NB", [{"type": "code", "source": "x = 1"}], None)
    svc.notebook_debug_set("NB", watches=["x + 1"])
    assert svc.notebook_debug_set("NB", ignore_hidden=["y"]) == {"watches": ["x + 1"], "ignore_hidden": ["y"]}
    assert svc.notebook_debug_get("NB")["watches"] == ["x + 1"]


# ---- receipts

def _receipt(svc):
    out = wait(svc, call(svc, "prove_start", **payload())["id"])
    return out["receipt_id"]


def test_receipts_export_as_html_and_save_under_new_names_in_downloads(svc, tmp_path):  # noqa: F811
    svc.fake = FakeFabric(svc)
    rid = _receipt(svc)
    html = svc.receipt_export(rid, "html")
    assert html["format"] == "html" and html["text"].lstrip().lower().startswith("<!doctype html")
    first = svc.receipt_save(rid, "html")
    second = svc.receipt_save(rid, "html")
    folder = tmp_path / "Downloads" / "nested"
    assert first["path"] == str(folder / f"receipt-{rid[:12]}.html")
    assert second["path"] == str(folder / f"receipt-{rid[:12]} (2).html")


def test_a_receipt_file_is_saved_whole_never_with_parts_left_out(svc, tmp_path):  # noqa: F811
    svc.fake = FakeFabric(svc)
    rid = _receipt(svc)
    with pytest.raises(ValueError, match="saved whole"):
        svc.receipt_save(rid, "json", hide=["code"])
    assert not (tmp_path / "Downloads").exists()


# ---- replay bundles, without running a replay

def _bundle(tmp_path, notebook="events", lakehouse=REPLAY_LH, ids=True, drop_ids_flag=False, file="b.zip"):
    reads = {"tables": {"lh1/dbo.sales": {"table": "dbo.sales", "version": 1, "first": "read", "workspace_id": "ws1",
                                          "lakehouse_id": "lh1", "lakehouse": lakehouse.get("name", "")}}, "files": {}}
    b = replay.build(notebook, REPLAY_CELLS, 2, reads, lakehouse)
    if not ids:
        b = replay.strip_ids(b)
    if drop_ids_flag:
        b.pop("ids_included")
    path = tmp_path / "bundles" / file
    replay.write(path, b)
    return path


def test_ending_when_no_replay_runs_still_says_it_ended(svc):  # noqa: F811
    assert svc.replay_end("events") == {"ended": True, "kept": False}


def test_an_opened_bundle_is_named_safely_and_remembers_its_lakehouse(svc, tmp_path):  # noqa: F811
    path = _bundle(tmp_path, notebook="Q3/sales: v2")
    nb = svc.replay_bundle_open(path=str(path))
    assert nb["name"] == "Q3_sales_ v2 replay"
    info = svc.notebooks.extra(nb["name"], "replay")
    assert info["bundle"] == hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    assert info["lakehouse"] == "LH_Bronze" and info["ids_included"] is True
    assert (svc.root / "replays" / f"{info['bundle']}.zip").read_bytes() == path.read_bytes()


def test_a_bundle_that_doesnt_say_whether_it_has_ids_counts_as_having_them(svc, tmp_path):  # noqa: F811
    nb = svc.replay_bundle_open(path=str(_bundle(tmp_path, drop_ids_flag=True)))
    assert nb["lakehouse"]["lakehouse_id"] == "lh1"
    assert svc.notebooks.extra(nb["name"], "replay")["ids_included"] is True


def test_the_replay_check_names_the_bundles_lakehouse_or_says_another(svc, tmp_path):  # noqa: F811
    other = {"workspace_id": "ws1", "lakehouse_id": "lh2", "name": "LH_Other"}
    named = svc.replay_bundle_open(path=str(_bundle(tmp_path, file="a.zip")))["name"]
    assert ("The bundle's tables are in LH_Bronze. Attach that lakehouse."
            in svc.replay_check(named, other)["blockers"])
    unnamed = svc.replay_bundle_open(path=str(_bundle(tmp_path, lakehouse=REPLAY_LH | {"name": ""}, file="b.zip")))["name"]
    assert ("The bundle's tables are in another lakehouse. Attach that lakehouse."
            in svc.replay_check(unnamed, other)["blockers"])


def test_a_bundle_without_ids_says_which_lakehouse_its_versions_are_from(svc, tmp_path):  # noqa: F811
    named = svc.replay_bundle_open(path=str(_bundle(tmp_path, ids=False, file="a.zip")))["name"]
    notes = svc.replay_check(named, REPLAY_LH)["notes"]
    assert any("Its versions are from LH_Bronze: check you attached the same one." in n for n in notes)
    unnamed = svc.replay_bundle_open(path=str(_bundle(tmp_path, lakehouse=REPLAY_LH | {"name": ""}, ids=False,
                                                      file="b.zip")))["name"]
    notes = svc.replay_check(unnamed, REPLAY_LH)["notes"]
    assert any("Its versions are from its lakehouse: check you attached the same one." in n for n in notes)


def test_a_pinned_tables_reads_use_the_short_read_limits_when_signed_in(svc, monkeypatch):  # noqa: F811
    import deltalake
    seen = []

    class Opened:
        def __init__(self, uri, version=None, storage_options=None):
            seen.append(storage_options)

        def version(self):
            return 3

        def protocol(self):  # plain Delta, so the pyarrow reader reads it
            return type("Protocol", (), {"reader_features": None, "writer_features": None})()

        def metadata(self):
            return type("Metadata", (), {"configuration": {}})()

        def to_pyarrow_dataset(self):
            return type("Dataset", (), {"head": lambda self, n, columns=None: None})()

    monkeypatch.setattr(deltalake, "DeltaTable", Opened)
    row = svc._replay_table({"table": "dbo.sales", "version": 1}, "root", lambda: {"bearer_token": "tok"}, False)
    assert row == {"table": "dbo.sales", "version": 1, "readable": True, "latest": 3, "why": ""}
    assert seen == [read_storage_options("tok")] * 2
    seen.clear()
    svc._replay_table({"table": "dbo.sales", "version": 1}, "root", lambda: {"account": "x"}, False)
    assert seen == [{"account": "x"}] * 2


def test_a_saved_bundles_file_name_keeps_only_safe_characters(svc, tmp_path):  # noqa: F811
    out = svc.replay_bundle_save("a b/c:d", REPLAY_CELLS, 2, REPLAY_LH)
    name = Path(out["path"]).name
    assert name.startswith("replay-a_b_c_d-") and name.endswith(".zip")
    assert Path(out["path"]).parent == tmp_path / "Downloads" / "nested"
