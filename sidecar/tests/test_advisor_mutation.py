"""Where-to-run advice: the exact wording of each piece of evidence, so the reasons a person reads say
what the evidence was. Mutation batch 60."""

from __future__ import annotations

from fabric_desktop import advisor
from fabric_desktop import measurement as ms
from tests.test_advisor import OK, REV, proof, sbs


def _cell(n, text="stops it"):
    return {"cell": n, "text": text}


def test_at_most_three_cells_are_listed_and_the_rest_are_counted():
    three = [_cell(1, "a"), _cell(2, "b"), _cell(3, "c")]
    assert advisor._cells(three) == "cell 1: a; cell 2: b; cell 3: c"
    assert advisor._cells([*three, _cell(4, "d")]) == "cell 1: a; cell 2: b; cell 3: c; and 1 more"
    assert advisor._cells([*three, _cell(4), _cell(5)]).endswith("cell 3: c; and 2 more")
    assert advisor._cells([{"text": "no cell number"}]) == "no cell number"


def test_a_side_the_check_cant_read_is_marked_unknown():
    compat = {"local": {"ok": False, "reasons": [], "unknown": [_cell(3, "uses exec")]},
              "fabric": {"ok": True, "reasons": []}}
    out = advisor.advise("nb", REV, compat, [])
    first = out["reasons"][0]
    assert first["text"] == "Unknown whether it runs on this PC: the check can't read cell 3: uses exec"
    assert first["evidence"]["unknown"] is True and first["evidence"]["side"] == "pc"
    assert out["place"] == advisor.FABRIC


def _cu(measurement):
    p = proof()
    p["receipt"]["cost"]["fabric"]["measurement"] = measurement
    return next(r for r in advisor.advise("nb", REV, OK, [p])["reasons"] if r["kind"] == "cu")


def test_cu_is_unknown_when_its_class_is_unknown_even_with_a_value():
    m = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 3600.0, method="m", assumptions=["1 CU"])
    m["classification"] = ms.UNKNOWN
    assert _cu(m)["text"] == "The Fabric run's job CU isn't known (not recorded)."
    m["reason"] = "no job time"
    assert _cu(m)["text"] == "The Fabric run's job CU isn't known (no job time)."


def test_cu_names_its_label_or_else_its_class():
    m = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 3600.0, method="m", assumptions=["1 CU"])
    m["label"] = "From The Job Log"
    assert "1 CU hours of Fabric job CU (from the job log)" in _cu(m)["text"]
    m.pop("label")
    assert f"1 CU hours of Fabric job CU ({ms.ESTIMATED.lower()})" in _cu(m)["text"]


def _proof_text(verification):
    p = proof()
    p["receipt"]["verification"] = verification
    return next(r for r in advisor.advise("nb", REV, OK, [p])["reasons"] if r["kind"] == "proof")["text"]


def test_a_proofs_text_falls_back_to_its_verdict_then_to_proved():
    assert _proof_text({"verdict": "same", "text": "Results match."}) == "Results match."
    assert _proof_text({"verdict": "same"}) == "same"
    assert _proof_text({}) == "Proved."


def _comparison(**kw):
    out = advisor.advise("nb", REV, OK, [], [sbs(**kw)])
    return next(r for r in out["reasons"] if r["kind"] == "comparison")["text"]


def test_a_side_by_side_says_what_it_found_and_how_many_tables():
    assert _comparison(tables=1) == (
        "A side-by-side run of the current code on 2026-10-17 found the results the same (1 table compared by "
        "row count, columns and a checksum of every row, not row by row).")
    assert _comparison(same=False, tables=2).startswith(
        "A side-by-side run of the current code on 2026-10-17 found the results different (2 tables compared")
    assert _comparison(same=None, tables=0) == (
        "A side-by-side run of the current code on 2026-10-17 found the results not compared, as neither run "
        "wrote a table.")
    assert _comparison(same=False, tables=2, status="failed") == (
        "A side-by-side run of the current code on 2026-10-17 found the results not comparable, as a run failed.")


def test_another_codes_side_by_side_is_quoted_as_context():
    def ctx(c):
        out = advisor.advise("nb", REV, OK, [], [c])
        return next(r for r in out["reasons"] if r["kind"] == "context")["text"]

    assert ctx(sbs(revision="b" * 64)) == (
        "A side-by-side run on 2026-10-17 said: Same results in both places. It ran other code, so it isn't "
        "counted for the current code.")
    bare = {k: v for k, v in sbs().items() if k not in ("code_revision", "verdict")}
    assert ctx(bare) == ("A side-by-side run on 2026-10-17 said: no verdict It didn't record which code it ran, "
                         "so it isn't counted for the current code.")
    # A proof of the current code makes an older side-by-side run beside the point.
    out = advisor.advise("nb", REV, OK, [proof()], [sbs(revision="b" * 64)])
    assert not any(r["kind"] in ("context", "comparison") for r in out["reasons"])


def test_a_failed_proof_says_what_to_do_next():
    out = advisor.advise("nb", REV, OK, [proof(verdict="failed", pc_status="failed")])
    assert out["place"] == advisor.FABRIC and out["next_step"] == "Look at the proof's result, then prove it again."
