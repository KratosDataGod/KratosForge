"""academy/registry.py's checks, each at its edge (mutation testing, batch 390).

test_academy_registry.py breaks the bundled DP-700 pack one way at a time. These pin the checks it leaves
open: what counts as a pack and a lesson, unknown platforms, a domain or skill area missing one of its two
parts, weight ranges at 0, 1 and 100, one planned question, the effective date, capstone ids, another
platform's check, and a certification file named with a folder.
"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from fabric_desktop.academy import registry
from fabric_desktop.learn import content as learn_content


@pytest.fixture(scope="module")
def dp700():
    return registry.certification("dp-700")


def problems(cert, pid="dp-700"):
    return registry._cert_problems(pid, cert)


def edited(dp700, fn):
    cert = copy.deepcopy(dp700)
    fn(cert)
    cert["source"]["snapshot_sha256"] = registry.snapshot(cert)
    return problems(cert)


def first_obj(cert):
    return cert["domains"][0]["skills"][0]["objectives"][0]


def test_the_bundled_dp700_has_no_problems(dp700):
    assert problems(copy.deepcopy(dp700)) == []


def test_a_pack_is_a_folder_with_a_pack_json(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.json").write_text("{}", encoding="utf-8")
    shutil.copytree(registry.ROOT / "dp-700", tmp_path / "dp-700")
    assert registry.pack_ids(tmp_path) == ["dp-700"]


def test_a_folder_named_unlike_a_pack_id_isnt_a_pack_even_with_a_pack_json(tmp_path):
    shutil.copytree(registry.ROOT / "dp-700", tmp_path / "DP_700")
    with pytest.raises(ValueError, match="Not a certification pack: DP_700"):
        registry.pack("DP_700", tmp_path)


def test_an_unknown_platform_raises_and_is_a_problem(dp700):
    with pytest.raises(ValueError, match="Unknown platform mainframe"):
        registry.platform({"platform": "mainframe"})
    assert problems({**dp700, "platform": "mainframe"}) == [
        "dp-700: Unknown platform mainframe; a pack's platform is one of fabric, databricks"]


def test_a_domain_with_neither_skills_nor_objectives_has_no_groups():
    assert registry._groups({"id": "1", "title": "x"}) == []


def test_only_lesson_folders_with_lesson_ids_are_lessons(tmp_path, monkeypatch):
    (tmp_path / "LM-01").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "LM-02").write_text("", encoding="utf-8")
    monkeypatch.setattr(learn_content, "ROOT", tmp_path)
    assert registry._lesson_ids() == {"LM-01"}


def test_an_effective_date_needs_no_note(dp700):
    def dated(c):
        c["source"]["effective"] = "2026-10-30"
        c["source"].pop("effective_note", None)
    assert edited(dp700, dated) == []

    def undated(c):
        c["source"]["effective"] = None
        c["source"].pop("effective_note", None)
    assert edited(dp700, undated) == ["dp-700: with no effective date, source.effective_note says why"]


def test_a_domain_needs_both_a_title_and_skill_areas(dp700):
    want = "dp-700 domain 1: needs a title and skill areas (or objectives, if the guide has no skill areas)"
    assert want in edited(dp700, lambda c: c["domains"][0].update(title=""))
    assert want in edited(dp700, lambda c: c["domains"][0].update(skills=[]))


def test_a_skill_area_needs_both_a_title_and_objectives(dp700):
    found = edited(dp700, lambda c: c["domains"][0]["skills"][0].update(objectives=[]))
    assert "dp-700 skill area 1.1: needs a title and objectives" in found


def weight_problems(dp700, lo, hi):
    def w(c):
        c["domains"][0]["weight"] = {"min": lo, "max": hi}
    return [p for p in edited(dp700, w) if "weight is the published range" in p]


@pytest.mark.parametrize("lo,hi,bad", [(1, 30, False), (30, 100, False), (0, 30, True), (5, 101, True),
                                       (30, 20, True)])
def test_a_weight_range_is_whole_percents_from_1_to_100(dp700, lo, hi, bad):
    assert bool(weight_problems(dp700, lo, hi)) is bad


def test_a_weight_range_needs_both_ends(dp700):
    found = edited(dp700, lambda c: c["domains"][0].update(weight={"min": 20}))
    assert "dp-700 domain 1: weight is the published range, whole percents, min to max" in found


def test_one_planned_question_is_enough(dp700):
    assert edited(dp700, lambda c: first_obj(c).update(questions_planned=1)) == []


def test_capstone_ids_are_checked(dp700):
    found = edited(dp700, lambda c: first_obj(c).update(capstones=["C1", "CX"]))
    assert found == ["dp-700 1.1.1: 'CX' isn't a capstone id like C1"]


def test_another_platforms_check_is_a_problem(dp700):
    found = edited(dp700, lambda c: first_obj(c)["validation"].append("databricks_check"))
    assert found == ["dp-700 1.1.1: databricks_check checks another platform"]


def test_a_certification_file_named_with_a_folder_is_a_problem(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT / "dp-700", root / "dp-700")
    (root / "dp-700" / "sub").mkdir()
    shutil.copy(root / "dp-700" / "certification.json", root / "dp-700" / "sub" / "certification.json")
    p = json.loads((root / "dp-700" / "pack.json").read_text(encoding="utf-8"))
    (root / "dp-700" / "pack.json").write_text(json.dumps({**p, "certification": "sub/certification.json"}),
                                                encoding="utf-8")
    assert registry.problems(root) == ["dp-700: certification names a file in the pack's folder"]
