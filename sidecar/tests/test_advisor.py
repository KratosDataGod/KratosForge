"""FINOPS-005: where to run a notebook, with the evidence, and nothing changed."""

from __future__ import annotations

import json

import pytest
from fabric_desktop import advisor, receipts
from fabric_desktop import measurement as ms
from fabric_desktop.integrations import services
from fabric_desktop.integrations.registry import default_registry
from fabric_desktop.service import Service

OK = {"local": {"ok": True, "reasons": []}, "fabric": {"ok": True, "reasons": []}}
REV = "a" * 64


def proof(verdict="same", level="complete", revision=REV, cu=1800.0, classification=ms.ESTIMATED, pc_status="ok"):
    m = (ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, cu, source="Capacity Metrics export",
                 collected_at="2026-10-18T00:00:00+00:00") if classification == ms.MEASURED else
         ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, cu, method="job time x CU", assumptions=["1 CU"]))
    body = receipts.new_receipt(
        "proof", notebook={"name": "nb", "code": {"revision": revision, "cells": []}},
        verification={"verdict": verdict, "level": level, "text": f"Results: {verdict}."},
        runs={"pc": {"status": pc_status, "seconds": 12}, "fabric": {"status": "ok", "total_seconds": 200}},
        scorecard={"time": {"faster": "This PC was 16.7x faster end to end"}},
        cost={"fabric": {"measurement": m}})
    return {"id": receipts.receipt_id(body), "receipt": body}


def test_a_proved_notebook_with_the_same_results_is_advised_to_run_here_with_its_evidence():
    out = advisor.advise("nb", REV, OK, [proof()])
    assert out["place"] == advisor.PC and out["enforced"] is False and "Runs on" in out["caveat"]
    kinds = [r["kind"] for r in out["reasons"]]
    assert kinds == ["compatibility", "proof", "time", "cu"]
    p = out["reasons"][1]
    assert "every row compared" in p["text"] and p["evidence"]["level"] == "complete" and p["evidence"]["receipt_id"]
    cu = out["reasons"][3]
    assert cu["evidence"]["measurement"]["classification"] == ms.ESTIMATED and "(estimated)" in cu["text"]
    assert "not money saved" in cu["text"] and "0.5 CU hours" in cu["text"]
    json.dumps(out)


def test_a_checksum_only_match_says_so_and_measured_cu_keeps_its_class():
    out = advisor.advise("nb", REV, OK, [proof(level="fingerprint", classification=ms.MEASURED)])
    p = next(r for r in out["reasons"] if r["kind"] == "proof")
    assert "checksum only" in p["text"] and "every row" not in p["text"]
    cu = next(r for r in out["reasons"] if r["kind"] == "cu")
    assert cu["evidence"]["measurement"]["classification"] == ms.MEASURED and "(measured)" in cu["text"]


def test_a_proof_of_older_code_doesnt_count():
    out = advisor.advise("nb", REV, OK, [proof(revision="b" * 64)])
    assert out["place"] == advisor.UNKNOWN
    assert "changed since it was last compared" in out["reasons"][1]["text"] and "Prove" in out["next_step"]
    assert not any(r["kind"] == "cu" for r in out["reasons"])
    never = advisor.advise("nb", REV, OK, [])
    assert never["place"] == advisor.UNKNOWN and "No proof yet" in never["reasons"][1]["text"]


@pytest.mark.parametrize(("verdict", "pc_status", "place"), [
    ("differs", "ok", advisor.FABRIC), ("nothing", "ok", advisor.EITHER), ("failed", "failed", advisor.FABRIC),
    ("failed", "ok", advisor.UNKNOWN), ("inconclusive", "ok", advisor.UNKNOWN)])
def test_each_proof_verdict(verdict, pc_status, place):
    assert advisor.advise("nb", REV, OK, [proof(verdict=verdict, pc_status=pc_status)])["place"] == place


def test_cells_that_stop_one_side_decide_it_and_unknown_cu_stays_unknown():
    pc_only = {"local": {"ok": True, "reasons": []},
               "fabric": {"ok": False, "reasons": [{"cell": 2, "text": "reads a path on this PC", "code": "open('C:/x')"}]}}
    out = advisor.advise("nb", REV, pc_only, [])
    assert out["place"] == advisor.PC and "cell 2: reads a path on this PC" in out["reasons"][0]["text"]
    neither = {"local": {"ok": False, "reasons": [{"cell": 1, "text": "uses mssparkutils"}]}, "fabric": pc_only["fabric"]}
    assert advisor.advise("nb", REV, neither, [])["place"] == advisor.UNKNOWN
    p = proof()
    p["receipt"]["cost"]["fabric"]["measurement"] = ms.unknown("fabric_job_cu", ms.CU_SECONDS, "no job time")
    cu = next(r for r in advisor.advise("nb", REV, OK, [p])["reasons"] if r["kind"] == "cu")
    assert "isn't known" in cu["text"] and cu["evidence"]["measurement"]["value"] is None


def sbs(same=True, revision=REV, tables=2, status="ok"):
    return {"id": "s1", "state": "done", "started_at": "2026-10-17T10:00:00+00:00", "code_revision": revision,
            "local": {"status": status}, "fabric": {"status": "ok"}, "tables": [{"result": "match"}] * tables,
            "verdict": {"same": same, "text": "Same results in both places."},
            "speed": {"end_to_end": "This PC was 3.0x faster end to end"}}


def test_a_side_by_side_of_the_current_code_counts_and_says_how_it_compared():
    out = advisor.advise("nb", REV, OK, [], [sbs()])
    assert out["place"] == advisor.PC
    c = next(r for r in out["reasons"] if r["kind"] == "comparison")
    assert "checksum of every row, not row by row" in c["text"] and c["evidence"]["comparison_id"] == "s1"
    assert any(r["kind"] == "time" and "side-by-side" in r["text"] for r in out["reasons"])
    assert "row-by-row" in out["next_step"]
    assert advisor.advise("nb", REV, OK, [], [sbs(same=False)])["place"] == advisor.FABRIC
    either = advisor.advise("nb", REV, OK, [], [sbs(same=None, tables=0)])
    assert either["place"] == advisor.EITHER and any("Files/ aren't" in r["text"] for r in either["reasons"])
    failed = advisor.advise("nb", REV, OK, [], [sbs(same=False, status="failed")])
    assert failed["place"] == advisor.UNKNOWN and "a run failed" in failed["reasons"][2]["text"]
    # A proof of the current code wins over a side-by-side run.
    assert advisor.advise("nb", REV, OK, [proof(verdict="differs")], [sbs()])["place"] == advisor.FABRIC


def test_a_side_by_side_of_other_or_unrecorded_code_is_context_only():
    for c in (sbs(revision="b" * 64), {k: v for k, v in sbs().items() if k != "code_revision"}):
        out = advisor.advise("nb", REV, OK, [], [c])
        assert out["place"] == advisor.UNKNOWN
        ctx = next(r for r in out["reasons"] if r["kind"] == "context")
        assert "isn't counted for the current code" in ctx["text"]


@pytest.fixture
def svc(tmp_path):
    s = Service(client_id="x", root=tmp_path / "data")
    s.notebooks.save("nb", [{"type": "code", "source": "x = 6"}], {"name": "LH", "lakehouse_id": "lh1", "workspace_id": "w1"})
    return s


def test_the_service_reads_receipts_for_the_current_code_and_changes_nothing(svc):
    cells = svc.notebooks.load("nb")["cells"]
    rev = receipts.code_revision(cells)["revision"]
    svc.receipts.put(proof(revision=rev)["receipt"])
    before = svc.notebooks.load("nb")
    out = svc.placement_advice("nb")
    assert out["place"] == advisor.PC and out["revision"] == rev
    assert svc.notebooks.load("nb") == before
    cap = default_registry().get("fabric_desktop_placement_advice")
    assert cap.permission == "READ_METADATA" and cap.status == "preview"
    assert services.get_placement_advice(svc, None, "nb")["place"] == advisor.PC
    from fabric_desktop import server
    assert "placement_advice" not in server.METHODS  # the MCP tool calls it in-process (HD-050 slice 4)
