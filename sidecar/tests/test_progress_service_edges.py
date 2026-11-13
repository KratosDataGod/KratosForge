"""Profile service: the refusals, quiet notifications, past proofs in a backfill and a full Downloads
folder that the tests in test_profile don't reach."""

import json
from pathlib import Path

import pytest

from fabric_desktop import progress_service
from tests.test_profile import call, plain, proof, validation  # noqa: F401  (plain is a fixture)


def test_with_notifications_off_an_achievement_is_recorded_but_not_announced(plain):  # noqa: F811
    call(plain, "profile_save", settings={"record": True, "notifications": False})
    assert plain._progress_observe(validation()) == []
    assert [a["id"] for a in call(plain, "profile_get")["progression"]["achievements"]] == ["first-flame"]


def test_dismissing_the_notice_and_saving_a_summary(plain):  # noqa: F811
    out = call(plain, "profile_save", dismiss_notice=True, report_summary="  Builds the sales models.  ")
    assert out["notice"] == "" and out["report"]["summary"] == "Builds the sales models."


def test_an_identity_field_that_is_not_part_of_the_profile_is_refused(plain):  # noqa: F811
    with pytest.raises(AssertionError, match="isn't part of the profile"):
        call(plain, "profile_save", identity={"salary": "1"})


def test_excluding_an_accomplishment_that_is_not_there_is_refused(plain):  # noqa: F811
    with pytest.raises(AssertionError, match="isn't in your profile"):
        call(plain, "profile_exclude", id="nope")


@pytest.mark.parametrize("days", [0, 3661])
def test_a_report_period_out_of_range_is_refused(plain, days):  # noqa: F811
    with pytest.raises(AssertionError, match="days is 1 to 3660"):
        call(plain, "profile_report", days=days)


def test_backfill_adds_past_proofs_that_matched(plain):  # noqa: F811
    call(plain, "profile_save", settings={"record": True})
    plain._proofs_file.parent.mkdir(parents=True, exist_ok=True)
    plain._proofs_file.write_text(json.dumps(proof()) + "\n" + json.dumps(proof(name="Other", verdict="differs")) + "\n",
                                  encoding="utf-8")
    out = call(plain, "profile_backfill")
    assert out["added"] == 1 and [r["type"] for r in out["evidence"]] == ["prove.match"]


def test_a_downloads_folder_with_every_name_taken_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(progress_service.known_folders, "downloads", lambda: tmp_path / "dl")
    tried = []

    def taken(self, mode="r", *args, **kwargs):
        tried.append(self.name)
        raise FileExistsError(str(self))

    monkeypatch.setattr(Path, "open", taken)
    with pytest.raises(ValueError, match="free file name in Downloads"):
        progress_service._save_new("my report", "md", "new")
    assert tried[:2] == ["my-report.md", "my-report-2.md"] and tried[-1] == "my-report-999.md"
