"""Profile service (progress_service.py), mutation batch 278: what its first mutation score found
untested. What the proof and Learn hooks return, the report's defaults, a backfill that runs once,
evidence newest first, the signed-in flag, the exported file's layout, and the picture's limits to
the byte."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from fabric_desktop import progress, progress_service
from tests.test_profile import Auth, call, plain, proof, validation  # noqa: F401  (plain is a fixture)


def _header(width=256, height=256, size=33):
    """A PNG signature and IHDR header of ``size`` bytes."""
    data = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return (data + b"\x00" * size)[:size]


def test_a_picture_is_judged_to_the_byte():
    ok = progress_service._avatar_ok
    assert ok(_header()) is True  # 33 bytes: the shortest a PNG header can be
    assert ok(_header(size=32)) is False
    assert ok(_header(size=512 * 1024)) is True
    assert ok(_header(size=512 * 1024 + 1)) is False
    assert ok(_header(width=0x01000100)) is False  # every byte of the width counts, not only the low ones
    assert ok(_header(height=0x01000100)) is False


def test_a_picture_too_large_says_the_limit_in_kb(plain):  # noqa: F811
    with pytest.raises(AssertionError, match="at most 512 KB"):
        call(plain, "profile_avatar_set", png_base64=base64.b64encode(_header(size=512 * 1024 + 1)).decode())


def test_removing_a_picture_that_isnt_there_is_fine(plain):  # noqa: F811
    assert call(plain, "profile_avatar_remove")["avatar"] is None


def test_the_proof_and_learn_hooks_return_what_they_earned(plain, monkeypatch):  # noqa: F811
    earned = [{"id": "x"}]
    monkeypatch.setattr(type(plain), "_progress_record", lambda self, row, then=None: earned)
    assert plain._progress_observe_proof({"receipt_id": "r"}) is earned
    monkeypatch.setattr(progress, "from_lab", lambda result, module: None)
    assert plain._progress_observe_lab({}, {}) is earned


def test_the_report_defaults_to_90_days_without_achievements(plain, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(progress, "report", lambda profile, days, ach, plan: seen.append((days, ach)) or {"markdown": "r"})
    monkeypatch.setattr(progress_service.known_folders, "downloads", lambda: plain.root / "dl")
    plain.profile_report()
    plain.profile_report_save()
    plain.profile_report_pdf()
    assert seen == [(90, False)] * 3


def test_the_profile_and_its_report_leave_draft_lessons_out(plain, monkeypatch):  # noqa: F811
    asked = []
    real = progress_service.learn_content.modules
    monkeypatch.setattr(progress_service.learn_content, "modules", lambda include_drafts: asked.append(include_drafts) or real(include_drafts=False))
    call(plain, "profile_get")
    call(plain, "profile_report")
    assert asked and set(asked) == {False}


def test_marking_a_goal_done_defaults_to_done(plain, monkeypatch):  # noqa: F811
    marked = []
    monkeypatch.setattr(progress_service.roadmap, "mark_goal", lambda p, gid, done: marked.append((gid, done)))
    plain.profile_goal_done("g1")
    assert marked == [("g1", True)]


def test_a_backfill_runs_once(plain):  # noqa: F811
    call(plain, "profile_save", settings={"record": True})
    plain._proofs_file.parent.mkdir(parents=True, exist_ok=True)
    plain._proofs_file.write_text(json.dumps(proof()) + "\n", encoding="utf-8")
    assert call(plain, "profile_backfill")["added"] == 1
    plain._proofs_file.write_text(json.dumps(proof()) + "\n" + json.dumps(proof(name="Later", revision="p2")) + "\n",
                                  encoding="utf-8")
    assert call(plain, "profile_backfill")["added"] == 0


def test_evidence_is_newest_first_and_a_row_without_xp_shows_0(plain, monkeypatch):  # noqa: F811
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation(name="Old", revision="r1", at="2026-10-01T10:00:00+00:00", vid="validate-1"))
    plain._progress_observe(validation(name="New", revision="r2", at="2026-10-20T10:00:00+00:00", vid="validate-2"))
    real = progress.progression
    monkeypatch.setattr(progress, "progression", lambda p: {**real(p), "xp_by_row": {}})
    rows = call(plain, "profile_get")["evidence"]
    assert [r["at"][:10] for r in rows] == ["2026-10-20", "2026-10-01"] and {r["xp"] for r in rows} == {0}


def test_the_view_says_whether_someone_is_signed_in(plain):  # noqa: F811
    assert call(plain, "profile_get")["signed_in"] is True
    plain._auth = Auth(None)
    assert call(plain, "profile_get")["signed_in"] is False


def test_the_exported_profile_is_indented_by_one_space(plain, monkeypatch):  # noqa: F811
    monkeypatch.setattr(progress_service.known_folders, "downloads", lambda: plain.root / "dl")
    path = call(plain, "profile_export")["path"]
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines[1].startswith(' "') and not lines[1].startswith('  ')


@pytest.mark.parametrize("days", [1, 3660])
def test_a_report_of_1_day_or_3660_days_is_made(plain, days):  # noqa: F811
    assert "markdown" in call(plain, "profile_report", days=days)
