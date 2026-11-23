"""Academy's certification registry (academy/registry.py), mutation batch 405: which folders are packs, an
unknown pack or platform, a guide whose domains list objectives straight away, and the checks on a
certification's weights, effective date, domains, objectives and file name. Each check starts from a shipped
pack, which has no problems, and changes one thing."""

import copy
import json

import pytest

from fabric_desktop.academy import registry as r

FABRIC, DBX = "dp-700", "dbx-de-associate"


def problems_of(pid, change):
    cert = copy.deepcopy(r.certification(pid))
    change(cert)
    return r._cert_problems(pid, cert)


def weight_problem(pid, domain):
    return f"{pid} domain {domain}: weight is the published range, whole percents, min to max"


def test_the_shipped_packs_have_no_problems():
    assert r._cert_problems(FABRIC, r.certification(FABRIC)) == []
    assert r._cert_problems(DBX, r.certification(DBX)) == []


def test_only_a_folder_with_a_pack_file_is_a_pack(tmp_path):
    (tmp_path / "dp-700").mkdir()
    (tmp_path / "dp-700" / "pack.json").write_text("{}")
    (tmp_path / "dp-900").mkdir()  # no pack.json
    (tmp_path / "notes.json").write_text("{}")
    assert r.pack_ids(tmp_path) == ["dp-700"]


def test_an_id_with_no_pack_folder_isnt_a_pack(tmp_path):
    with pytest.raises(ValueError, match="Not a certification pack: dp-999"):
        r.pack("dp-999", tmp_path)


def test_an_unknown_platform_is_refused():
    with pytest.raises(ValueError, match="Unknown platform snowflake"):
        r.platform({"platform": "snowflake"})
    assert r._cert_problems("x-1", {"platform": "snowflake"}) == [
        f"x-1: Unknown platform snowflake; a pack's platform is one of {', '.join(r.PLATFORMS)}"]


def test_a_domain_with_skill_areas_keeps_them_even_if_it_lists_objectives_too():
    skills = [{"id": "1.1", "title": "t", "objectives": []}]
    assert r._groups({"id": "1", "skills": skills, "objectives": [{"id": "1.1"}]}) == skills
    assert r._groups({"id": "1", "objectives": [{"id": "1.1"}]})[0]["_flat"] is True


@pytest.mark.parametrize("weight, ok", [
    ({"min": 0, "max": 35}, False), ({"min": 1, "max": 35}, True), ({"min": 30, "max": 100}, True),
    ({"min": 30, "max": 101}, False), ({"min": "30", "max": 35}, False), ({"min": 30, "max": "35"}, False),
    ({"min": 40, "max": 35}, False),
])
def test_a_domains_weight_is_whole_percents_from_1_to_100(weight, ok):
    found = problems_of(FABRIC, lambda c: c["domains"][0].__setitem__("weight", weight))
    assert (weight_problem(FABRIC, "1") not in found) is ok, found


def test_an_effective_date_needs_no_note():
    def dated(c):
        c["source"]["effective"] = "2026-01-01"
        c["source"].pop("effective_note", None)

    assert not any("effective_note" in p for p in problems_of(FABRIC, dated))


def test_a_domain_with_a_title_but_nothing_under_it_is_a_problem():
    def empty(c):
        c["domains"][0].pop("skills")

    assert any("domain 1: needs a title and skill areas" in p for p in problems_of(FABRIC, empty))


def test_a_guide_without_skill_areas_isnt_asked_for_their_titles():
    assert not any("skill area" in p and "needs a title and objectives" in p for p in r._cert_problems(DBX, r.certification(DBX)))


def first_objective(c):
    d = c["domains"][0]
    return (d.get("skills") or [d])[0]["objectives"][0]


def test_a_bad_capstone_id_and_another_platforms_check_are_problems():
    other = next(p["check"] for name, p in r.PLATFORMS.items() if name != "fabric")

    def bad(c):
        o = first_objective(c)
        o["capstones"] = ["X1"]
        o["validation"] = [*o["validation"], other]

    found = problems_of(FABRIC, bad)
    assert any("'X1' isn't a capstone id like C1" in p for p in found)
    assert any(f"{other} checks another platform" in p for p in found)


@pytest.mark.parametrize("planned, ok", [(1, True), (0, False)])
def test_an_objective_plans_at_least_one_question(planned, ok):
    found = problems_of(FABRIC, lambda c: first_objective(c).__setitem__("questions_planned", planned))
    assert (not any("questions_planned is a whole number from 1" in p for p in found)) is ok


@pytest.mark.parametrize("name", ["../dp-700/certification.json", "a/b.json", "a\\b.json", 7, "missing.json"])
def test_a_certification_must_name_a_file_in_the_packs_own_folder(tmp_path, name):
    folder = tmp_path / "dp-700"
    folder.mkdir()
    (folder / "pack.json").write_text(json.dumps({"schema": r.PACK_SCHEMA, "id": "dp-700", "status": "draft",
                                                  "platform": "fabric", "certification": name}))
    assert "dp-700: certification names a file in the pack's folder" in r.problems(tmp_path)
