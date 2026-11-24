"""advisor.py: a proof with no time or CU recorded, a side-by-side run with no speed, and a notebook that
can't run here but can in Fabric."""

from __future__ import annotations

from fabric_desktop import advisor, receipts

OK = {"local": {"ok": True, "reasons": []}, "fabric": {"ok": True, "reasons": []}}
REV = "c" * 64


def bare_proof(cost):
    body = receipts.new_receipt("proof", notebook={"name": "nb", "code": {"revision": REV, "cells": []}},
                                verification={"verdict": "same", "level": "complete"}, cost=cost)
    return {"id": receipts.receipt_id(body), "receipt": body}


def test_a_proof_with_no_time_or_measurement_adds_no_time_or_cu_reason():
    for cost in ({}, {"fabric": {}}, {"fabric": {"measurement": None}}):
        out = advisor.advise("nb", REV, OK, [bare_proof(cost)])
        assert [r["kind"] for r in out["reasons"]] == ["compatibility", "proof"]
        assert out["place"] == advisor.PC and out["next_step"] is None


def test_a_side_by_side_run_with_no_speed_adds_no_time_reason():
    run = {"id": "s1", "state": "done", "code_revision": REV, "started_at": "2026-11-01T10:00:00Z",
           "verdict": {"same": True}, "tables": [{"name": "t"}], "local": {"status": "ok"}, "fabric": {"status": "ok"}}
    out = advisor.advise("nb", REV, OK, [], [run])
    assert [r["kind"] for r in out["reasons"]] == ["compatibility", "proof", "comparison"]
    assert "found the results the same (1 table compared" in out["reasons"][2]["text"]
    assert out["place"] == advisor.PC and "row-by-row" in out["next_step"]


def test_a_notebook_that_cant_run_here_but_can_in_fabric_is_advised_for_fabric():
    compat = {"local": {"ok": False, "reasons": [{"cell": 2, "text": "uses mssparkutils.credentials"}]},
              "fabric": {"ok": True, "reasons": []}}
    out = advisor.advise("nb", REV, compat, [])
    assert out["place"] == advisor.FABRIC
    assert out["reasons"][0]["text"] == "Can't run on this PC as it is: cell 2: uses mssparkutils.credentials"
    assert out["reasons"][0]["evidence"]["side"] == "pc"
