"""Mutation tests for the Profile service (progress_service.py): the profile picture's checks at their
edges, what each hook and call answers, and the defaults the app relies on. Found by tools/mutate.py."""

import base64

import pytest

from fabric_desktop import progress, progress_service, roadmap
from tests.test_profile import call, plain, proof, side_by_side  # noqa: F401  (plain is a fixture)

PS = progress_service


def png(width=256, height=256, size=64, start=PS._PNG, chunk=b"IHDR"):
    head = start + (13).to_bytes(4, "big") + chunk + width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return head + b"\0" * (size - len(head))


def test_a_picture_is_a_256_px_square_png_of_at_most_512_kb():
    assert PS._avatar_ok(png()) is True
    assert PS._avatar_ok(png(size=512 * 1024)) is True
    assert PS._avatar_ok(png(size=512 * 1024 + 1)) is False
    assert PS._avatar_ok(png(size=33)) is True
    assert PS._avatar_ok(png()[:32]) is False
    for bad in (png(257, 257), png(255, 256), png(256, 255), png(300, 300), png(start=b"GIF89a\0\0"), png(chunk=b"IDAT")):
        assert PS._avatar_ok(bad) is False


def test_a_picture_is_kept_shown_and_removed(plain):  # noqa: F811
    data = png()
    url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    assert plain.profile_avatar_set(base64.b64encode(data).decode("ascii"))["avatar"] == url
    assert plain.profile_avatar() == {"avatar": url}
    assert plain.profile_avatar_remove()["avatar"] is None
    assert plain.profile_avatar() == {"avatar": None}


def test_a_picture_that_isnt_one_is_refused_with_the_limits(plain):  # noqa: F811
    with pytest.raises(ValueError, match=r"^The picture must be a 256 px square PNG of at most 512 KB\.$"):
        plain.profile_avatar_set(base64.b64encode(png(100, 100)).decode("ascii"))
    plain._avatar_path().parent.mkdir(parents=True, exist_ok=True)
    plain._avatar_path().write_bytes(png(100, 100))
    assert plain.profile_avatar() == {"avatar": None}


def test_a_matched_proof_or_side_by_side_run_answers_with_what_it_earned(plain):  # noqa: F811
    call(plain, "profile_save", settings={"record": True})
    earned = plain._progress_observe_proof(proof())
    assert isinstance(earned, list) and earned and all(a["first"] for a in earned)
    assert plain._progress_observe_side_by_side(side_by_side(name="Customers")) == []


def test_reports_default_to_90_days_without_achievements(plain, monkeypatch, tmp_path):  # noqa: F811
    asked = []
    monkeypatch.setattr(progress, "report", lambda profile, days, achievements, plan: asked.append((days, achievements)) or {"markdown": "x"})
    monkeypatch.setattr(PS.known_folders, "downloads", lambda: tmp_path / "dl")
    plain.profile_report()
    plain.profile_report_save()
    plain.profile_report_pdf()
    assert asked == [(90, False)] * 3


def test_marking_a_goal_marks_it_done_unless_told_otherwise(plain, monkeypatch):  # noqa: F811
    marked = []
    monkeypatch.setattr(roadmap, "mark_goal", lambda profile, id, done: marked.append((id, done)))
    plain.profile_goal_done("g1")
    plain.profile_goal_done("g1", done=False)
    assert marked == [("g1", True), ("g1", False)]


def test_the_profile_says_whether_someone_is_signed_in(plain):  # noqa: F811
    assert plain.profile_get()["signed_in"] is True
    plain._auth.user = None
    assert plain.profile_get()["signed_in"] is False


def test_a_width_or_height_is_read_from_all_four_of_its_bytes():
    assert PS._avatar_ok(png(0x01000100, 256)) is False
    assert PS._avatar_ok(png(256, 0x01000100)) is False


def test_a_picture_that_isnt_base64_is_refused(plain):  # noqa: F811
    with pytest.raises(ValueError, match="That picture couldn't be read."):
        plain.profile_avatar_set("not base64!")


def test_removing_a_picture_there_isnt_is_fine(plain):  # noqa: F811
    assert plain.profile_avatar_remove()["avatar"] is None


def test_the_profile_and_its_report_use_published_lessons_only(plain, monkeypatch):  # noqa: F811
    drafts = []
    real = PS.learn_content.modules
    monkeypatch.setattr(PS.learn_content, "modules", lambda include_drafts: drafts.append(include_drafts) or real(include_drafts=False))
    plain.profile_get()
    plain.profile_report()
    assert drafts and not any(drafts)


@pytest.mark.parametrize("days", [1, 3660])
def test_a_report_of_one_day_or_ten_years_is_made(plain, days):  # noqa: F811
    assert plain.profile_report(days=days)["markdown"]


def test_a_backfill_happens_once(plain):  # noqa: F811
    call(plain, "profile_save", settings={"record": True})
    assert plain.profile_backfill()["settings"]["backfilled"] is True
    assert plain.profile_get()["settings"]["backfilled"] is True


def test_evidence_is_newest_first_with_the_xp_each_row_earned(plain):  # noqa: F811
    from tests.test_profile import validation
    call(plain, "profile_save", settings={"record": True})
    plain._progress_observe(validation(name="Orders", at="2026-10-20T10:00:00+00:00"))
    plain._progress_observe(validation(name="Customers", at="2026-10-21T10:00:00+00:00"))
    rows = plain.profile_get()["evidence"]
    assert [(r["artifact"]["name"], r["xp"]) for r in rows] == [("Customers", progress.NEW_NOTEBOOK_XP), ("Orders", 0)]


def test_an_export_is_the_profile_as_json_indented_by_one(plain, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(PS.known_folders, "downloads", lambda: tmp_path / "dl")
    text = open(plain.profile_export()["path"], encoding="utf-8").read()
    assert text.splitlines()[1].startswith(' "') and not text.splitlines()[1].startswith('  ')
