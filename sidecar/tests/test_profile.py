"""Profile (KF-DX-001): verified evidence from Validate, achievements, XP and the professional report.

The slice end to end: a notebook passes Validate with a clean run -> one evidence row -> First Flame
-> 50 XP -> the profile -> one Verified line in the report. Plus what keeps it honest: nothing is
recorded until the person turns it on, the same code never counts twice, exclusions take a row out
of everything, and the report carries no game terms unless asked."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from fabric_desktop import audit, progress, server
from fabric_desktop.server import handle
from fabric_desktop.service import Service

from test_validate import LH, WRITES, done, svc  # noqa: F401 - the clean-run fixture


def validation(name="Orders", revision="r1", verdict="ready", status="ok", reads=("dbo.orders",), writes=(),
               at="2026-10-20T10:00:00+00:00", vid="validate-20261020-100000-abcdef"):
    return {"id": vid, "name": name, "verdict": verdict, "run": {"status": status}, "finished_at": at,
            "code": {"revision": revision, "cells": []}, "tables": {"reads": list(reads), "writes": list(writes)}}


def profile_with(*records):
    p = progress.empty()
    for r in records:
        progress.add(p, progress.from_validation(r))
    return p


class Auth:
    def __init__(self, user):
        self.user = user

    def signed_in_user(self):
        return self.user

    def storage_token(self):
        return "tok"


@pytest.fixture
def plain(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    s = Service(client_id="x", root=tmp_path / "app")
    s._auth = Auth("dev@contoso.com")
    return s


def call(s, method, **params):
    out = handle(s, json.dumps({"id": 1, "method": method, "params": params}))
    assert "error" not in out, out["error"]
    return out["result"]


# ---- what counts as evidence


def test_only_a_passed_clean_run_that_touches_a_lakehouse_table_is_evidence():
    assert progress.from_validation(validation())["type"] == "validate.pass"
    assert progress.from_validation(validation(verdict="ready_with_warnings", reads=(), writes=("dbo.x",)))
    assert progress.from_validation(validation(verdict="not_ready")) is None
    assert progress.from_validation(validation(verdict="checked")) is None       # code checks only, no clean run
    assert progress.from_validation(validation(status="failed")) is None
    assert progress.from_validation(validation(reads=(), writes=())) is None     # print("hello") isn't data engineering


def test_a_row_keeps_references_and_counts_never_code_or_table_names():
    row = progress.from_validation(validation(reads=("dbo.salaries",), writes=("dbo.payroll",)))
    text = json.dumps(row)
    assert "salaries" not in text and "payroll" not in text
    assert row["ref"] == {"source": "validate", "id": "validate-20261020-100000-abcdef", "revision": "r1"}
    assert row["detail"] == {"reads": 1, "writes": 1, "runs_on": "pc"} and row["rules_version"] == progress.RULES_VERSION


def test_the_same_code_validated_again_is_the_same_row():
    p = profile_with(validation())
    assert not progress.add(p, progress.from_validation(validation(vid="validate-20261020-110000-123456", at="2026-10-20T11:00:00+00:00")))
    assert len(p["evidence"]) == 1
    assert progress.add(p, progress.from_validation(validation(revision="r2")))  # changed code: new evidence


# ---- XP, levels and achievements


def test_first_flame_is_fifty_xp_and_level_one():
    p = progress.progression(profile_with(validation()))
    assert [a["id"] for a in p["achievements"]] == ["first-flame"]
    assert (p["xp"], p["level"], p["title"], p["next_level_xp"]) == (50, 1, "Apprentice", 100)


def test_new_revisions_of_one_notebook_earn_no_xp():
    p = progress.progression(profile_with(*[validation(revision=f"r{i}") for i in range(20)]))
    assert p["xp"] == 50 and len(p["achievements"]) == 1


def test_new_notebooks_earn_xp_up_to_a_daily_cap_and_five_earn_steady_hand():
    same_day = [validation(name=f"nb{i}", at=f"2026-10-20T1{i}:00:00+00:00") for i in range(6)]
    p = progress.progression(profile_with(*same_day))
    assert [a["id"] for a in p["achievements"]] == ["first-flame", "steady-hand"]
    assert p["xp"] == 50 + 3 * progress.NEW_NOTEBOOK_XP + 100  # 5 notebooks after the first, 3 a day earn XP
    spread = [validation(name=f"nb{i}", at=f"2026-10-{10 + i:02d}T10:00:00+00:00") for i in range(6)]
    assert progress.progression(profile_with(*spread))["xp"] == 50 + 5 * progress.NEW_NOTEBOOK_XP + 100


def test_the_level_curve_is_fixed_and_titles_follow_it():
    assert [progress.xp_for_level(n) for n in (2, 5, 10, 20, 50)] == [100, 800, 2700, 8282, 34300]
    assert progress.level_for(99) == 1 and progress.level_for(100) == 2 and progress.level_for(34300) == 50
    assert progress.title_for(4) == "Apprentice" and progress.title_for(5) == "Forge Initiate" and progress.title_for(73) == "Legend of the Forge"


def test_missing_evidence_is_not_a_low_score():
    cover = {c["name"]: c["state"] for c in progress.coverage(profile_with(validation()))}
    assert cover["Data Engineering"] == "Insufficient evidence"
    assert cover["Performance & Optimization"] == "Not seen yet"  # tracked through Prove, none yet
    assert cover["Security & Governance"] == "Not tracked yet"
    rows = [validation(name=f"nb{i % 2}", revision=f"r{i}", at=f"2026-10-{1 + 4 * i:02d}T10:00:00+00:00") for i in range(3)]
    assert {c["name"]: c["state"] for c in progress.coverage(profile_with(*rows))}["Data Engineering"] == "Emerging"
    assert all(set(c) == {"name", "state", "rows", "artifacts", "days"} for c in progress.coverage(progress.empty()))  # no score


def proof(name="Orders", revision="p1", verdict="same", state="done", receipt="a" * 64,
          at="2026-10-20T12:00:00+00:00", pc_seconds=12.5, fabric_seconds=95.0):
    return {"id": "20261020-120000-abcdef", "name": name, "state": state, "finished_at": at, "receipt_id": receipt,
            "code": {"revision": revision, "cells": []}, "tables": [{"name": "dbo.order_values"}],
            "verdict": {"verdict": verdict, "level": "exact"},
            "scorecard": {"time": {"pc": {"seconds": pc_seconds, "kind": "measured"},
                                   "fabric": {"total_seconds": fabric_seconds, "kind": "measured"}}}}


def test_only_a_finished_proof_that_matched_is_evidence():
    row = progress.from_proof(proof(), proof()["receipt_id"])
    assert row["type"] == "prove.match" and row["ref"] == {"source": "receipt", "id": "a" * 64, "revision": "p1"}
    assert row["measurement"]["pc_seconds"] == 12.5 and row["measurement"]["kind"] == "measured"
    assert "order_values" not in json.dumps(row)  # a count, never a table name
    for bad in (proof(verdict="differs"), proof(verdict="inconclusive"), proof(state="failed")):
        assert progress.from_proof(bad, "a" * 64) is None
    assert progress.from_proof(proof(), None) is None  # no receipt, no evidence


def test_proof_forged_is_awarded_once_and_more_proofs_earn_capped_xp():
    p = progress.empty()
    progress.add(p, progress.from_proof(proof(), "a" * 64))
    got = progress.progression(p)
    assert [a["id"] for a in got["achievements"]] == ["proof-forged"] and got["xp"] == 150
    progress.add(p, progress.from_proof(proof(revision="p2"), "b" * 64))   # the same notebook again: no XP
    progress.add(p, progress.from_proof(proof(name="Sales", revision="p3"), "c" * 64))
    got = progress.progression(p)
    assert got["xp"] == 150 + progress.NEW_PROOF_XP and [a["id"] for a in got["achievements"]] == ["proof-forged"]
    assert "proof-forged" not in {u["id"] for u in got["next"]}



def test_the_journey_lists_achievements_and_levels_newest_first():
    p = profile_with(validation())
    progress.add(p, progress.from_proof(proof(), "a" * 64))
    got = progress.progression(p)["milestones"]
    assert [m["text"] for m in got] == ["Reached level 2, Apprentice", "Earned Proof Forged (+150 XP)",
                                        "Earned First Flame (+50 XP)"]
    assert got[0]["at"] == "2026-10-20T12:00:00+00:00"

def test_the_report_names_proofs_their_receipts_and_measured_times():
    now = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
    p = profile_with(validation())
    progress.add(p, progress.from_proof(proof(), "a" * 64))
    out = progress.report(p, 90, now=now)["markdown"]
    assert "Proved notebook Orders gives the same results on a workstation and in Microsoft Fabric" in out
    assert "workstation 12.5 s, Microsoft Fabric 95 s end to end, measured in the same proof. *(Verified, E2)*" in out
    assert f"Run receipt {'a' * 64} (SHA-256)" in out and "XP" not in out
    no_times = progress.empty()
    progress.add(no_times, progress.from_proof(proof(pc_seconds=None, fabric_seconds=None), "a" * 64))
    assert "No before and after measurements in this period. *(Not measured)*" in progress.report(no_times, 90, now=now)["markdown"]


# ---- the report


def test_the_report_is_verified_lines_with_no_game_terms_by_default():
    now = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
    out = progress.report(profile_with(validation()), 90, now=now)
    md = out["markdown"]
    assert "Validated notebook Orders: clean run finished Ready" in md and "*(Verified, E1)*" in md
    assert "validate-20261020-100000-abcdef" in md
    assert not any(w in md for w in ("XP", "Level", "First Flame", "Apprentice"))
    assert "No before and after measurements in this period. *(Not measured)*" in md
    with_games = progress.report(profile_with(validation()), 90, include_achievements=True, now=now)["markdown"]
    assert "First Flame" in with_games and "not a skill rating" in with_games


def test_the_report_only_covers_its_period_and_says_when_there_is_nothing():
    now = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
    out = progress.report(profile_with(validation(at="2026-01-01T10:00:00+00:00")), 90, now=now)
    assert out["rows"] == 0 and "No verified accomplishments in this period." in out["markdown"]


# ---- the service, end to end


def test_profile_methods_are_reachable_and_reads_are_not_audited():
    assert progress_methods() <= server.METHODS
    assert not audit.audited("profile_get") and not audit.audited("profile_report")
    assert audit.audited("profile_save") and audit.audited("profile_delete")


def progress_methods():
    from fabric_desktop.progress_service import METHODS
    return METHODS


def test_recording_is_on_from_the_start_and_turning_it_off_stops_it(plain):
    assert call(plain, "profile_get")["settings"]["record"] is True
    call(plain, "profile_save", settings={"record": False})
    assert plain._progress_observe(validation()) == []
    assert call(plain, "profile_get")["evidence"] == []
    call(plain, "profile_save", settings={"record": True})
    assert [a["name"] for a in plain._progress_observe(validation())] == ["First Flame"]
    assert plain._progress_observe(validation(vid="validate-20261020-110000-123456")) == []  # same code again
    got = call(plain, "profile_get")
    assert len(got["evidence"]) == 1 and got["progression"]["xp"] == 50 and got["evidence"][0]["xp"] == 0


def test_each_account_has_its_own_profile(plain):
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation())
    plain._auth = Auth("someone.else@contoso.com")
    assert call(plain, "profile_get")["evidence"] == []
    assert not list((plain.root / "profile").glob("*contoso*"))  # the sign-in name isn't a folder name


def test_excluding_a_row_takes_it_out_of_xp_achievements_and_the_report(plain):
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation())
    rid = call(plain, "profile_get")["evidence"][0]["id"]
    out = call(plain, "profile_exclude", id=rid)
    assert out["evidence"][0]["excluded"] and out["progression"]["xp"] == 0 and not out["progression"]["achievements"]
    assert call(plain, "profile_report")["rows"] == 0
    assert call(plain, "profile_exclude", id=rid, excluded=False)["progression"]["xp"] == 50


def test_settings_and_identity_are_checked(plain):
    with pytest.raises(AssertionError, match="isn't a profile setting"):
        call(plain, "profile_save", settings={"leaderboard": True})
    with pytest.raises(AssertionError, match="isn't a profile setting"):
        call(plain, "profile_save", settings={"backfilled": True})
    who = call(plain, "profile_save", identity={"display_name": " Chris ", "role": "Data engineer"})["identity"]
    assert who["display_name"] == "Chris" and who["role"] == "Data engineer"


def test_backfill_adds_past_validations_once(plain):
    (plain.root).mkdir(parents=True, exist_ok=True)
    (plain.root / "validations.json").write_text(json.dumps([validation(), validation(name="Other", verdict="not_ready")]))
    call(plain, "profile_save", settings={"record": False})
    with pytest.raises(AssertionError, match="Turn on Record"):
        call(plain, "profile_backfill")
    call(plain, "profile_save", settings={"record": True})
    out = call(plain, "profile_backfill")
    assert out["added"] == 1 and out["evidence"][0]["origin"] == "backfill"
    assert call(plain, "profile_backfill")["added"] == 0


def test_report_and_export_are_new_files_and_delete_leaves_validate_history(plain, tmp_path):
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation())
    (plain.root / "validations.json").write_text("[]")
    first = call(plain, "profile_report_save")["path"]
    second = call(plain, "profile_report_save")["path"]
    assert first != second and "Validated notebook Orders" in Path(first).read_text(encoding="utf-8")
    assert call(plain, "profile_export")["path"].endswith(".json")
    assert call(plain, "profile_delete") == {"deleted": True}
    assert call(plain, "profile_get")["evidence"] == [] and (plain.root / "validations.json").exists()


def test_a_clean_run_records_first_flame_and_validating_again_adds_nothing(svc):  # noqa: F811
    svc._auth = Auth("dev@contoso.com")
    call(svc, "profile_save", settings={"record": True})
    st = done(svc, call(svc, "notebook_validate_start", name="Orders", cells=WRITES, lakehouse_ref=LH, runs_on="pc")["id"])
    assert st["verdict"] == "ready" and st["earned"] == [{"id": "first-flame", "name": "First Flame", "xp": 50, "first": True}]
    again = done(svc, call(svc, "notebook_validate_start", name="Orders", cells=WRITES, lakehouse_ref=LH, runs_on="pc")["id"])
    assert again["earned"] == []
    got = call(svc, "profile_get")
    assert len(got["evidence"]) == 1 and got["evidence"][0]["ref"]["id"] == st["id"]
    saved = (svc.root / "profile").rglob("profile.json")
    text = next(saved).read_text()
    assert "lakehouse.read_table" not in text and "dbo.orders" not in text and "tok" not in text


def test_a_profile_that_cannot_be_written_never_changes_the_validation(svc, monkeypatch):  # noqa: F811
    def broken(record):
        raise OSError("disk full")
    monkeypatch.setattr(svc, "_progress_observe", broken)
    st = done(svc, call(svc, "notebook_validate_start", name="Orders", cells=WRITES, lakehouse_ref=LH, runs_on="pc")["id"])
    assert st["state"] == "done" and st["verdict"] == "ready" and "earned" not in st


def test_a_torn_profile_is_set_aside_and_the_person_is_told(plain):
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation())
    path = next((plain.root / "profile").rglob("profile.json"))
    path.write_text('{"schema": "kratosforge/profile@1", "settings": {"rec')
    got = call(plain, "profile_get")
    assert got["evidence"] == [] and got["settings"]["record"]
    assert "couldn't be read" in got["notice"] and "profile.json.corrupt-" in got["notice"]
    assert list(path.parent.glob("profile.json.corrupt-*"))
    assert call(plain, "profile_get")["notice"] == got["notice"]  # kept until seen
    assert call(plain, "profile_save", settings={"progression": False})["notice"] == got["notice"]
    assert call(plain, "profile_save", dismiss_notice=True)["notice"] == ""


def test_names_in_the_markdown_report_are_escaped_and_the_copy_text_is_not():
    out = progress.report(profile_with(validation(name="*x* [a](b) <img>")), 90,
                          now=dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc))
    assert r"Validated notebook \*x\* \[a\]\(b\) \<img\>: clean run" in out["markdown"]
    assert "<img>" not in out["markdown"] and "Validated notebook *x* [a](b) <img>: clean run" in out["summary"]


def test_profile_details_and_the_summary_are_not_written_to_the_audit_log():
    for key in ("identity", "report_summary"):
        assert key in audit._CONTENT


def test_nothing_is_recorded_while_preview_features_are_hidden(plain, monkeypatch):
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "value", lambda key: False if key == "show_preview" else app_settings.DEFAULTS.get(key))
    assert plain._progress_observe(validation()) == []
    assert call(plain, "profile_get")["evidence"] == []
