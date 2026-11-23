"""Where to run a notebook (advisor.advise): the remaining edges. A proof with no time or CU recorded, a
side-by-side run with no speed, and a notebook that can only run in Fabric."""

from __future__ import annotations

from fabric_desktop import advisor

OK = {"local": {"ok": True, "reasons": []}, "fabric": {"ok": True, "reasons": []}}
REV = "b" * 64


def bare_proof(verdict="same"):
    """A proof receipt that recorded only its verdict: no scorecard, no cost."""
    return {"id": "rc-1", "receipt": {"kind": "proof", "notebook": {"code": {"revision": REV}},
                                     "verification": {"verdict": verdict}}}


def test_a_proof_without_time_or_cu_gives_only_its_verdict():
    out = advisor.advise("nb", REV, OK, [bare_proof()])
    assert out["place"] == advisor.PC and out["next_step"] is None
    assert [r["kind"] for r in out["reasons"]] == ["compatibility", "proof"]
    assert out["reasons"][1]["text"] == "same" and out["reasons"][1]["evidence"]["level"] is None


def test_a_proof_with_a_cost_but_no_measurement_names_no_cu():
    p = bare_proof()
    p["receipt"]["cost"] = {"fabric": {"note": "not collected"}}
    assert advisor._cu_reason(p["receipt"]["cost"]) is None
    assert [r["kind"] for r in advisor.advise("nb", REV, OK, [p])["reasons"]] == ["compatibility", "proof"]


def test_a_side_by_side_run_without_a_speed_adds_no_time_reason():
    sbs = {"id": "c1", "state": "done", "code_revision": REV, "started_at": "2026-11-19T08:00:00Z",
           "verdict": {"same": True}, "tables": [{"name": "t"}]}
    out = advisor.advise("nb", REV, OK, [], [sbs])
    assert out["place"] == advisor.PC
    assert [r["kind"] for r in out["reasons"]] == ["compatibility", "proof", "comparison"]
    assert out["reasons"][2]["text"] == ("A side-by-side run of the current code on 2026-11-19 found the results the same "
                                         "(1 table compared by row count, columns and a checksum of every row, not row by row).")


def test_a_notebook_the_check_cant_read_for_this_pc_stays_in_fabric_without_a_same_verdict():
    compat = {"local": {"ok": False, "reasons": [], "unknown": [{"cell": 2, "text": "a %run of another notebook"}]},
              "fabric": {"ok": True, "reasons": []}}
    out = advisor.advise("nb", REV, compat, [bare_proof("differs")])
    assert out["place"] == advisor.FABRIC and out["headline"] == "Keep it in Fabric"
    unknown = out["reasons"][0]
    assert unknown["text"] == "Unknown whether it runs on this PC: the check can't read cell 2: a %run of another notebook"
    assert unknown["evidence"]["unknown"] is True


def test_a_notebook_that_cant_run_on_this_pc_is_kept_in_fabric_whatever_the_proof_says():
    compat = {"local": {"ok": False, "reasons": [{"cell": 1, "text": "uses mssparkutils.credentials"}]},
              "fabric": {"ok": True, "reasons": []}}
    out = advisor.advise("nb", REV, compat, [bare_proof("same")])
    assert out["place"] == advisor.FABRIC
    assert out["reasons"][0]["text"] == "Can't run on this PC as it is: cell 1: uses mssparkutils.credentials"
