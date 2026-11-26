"""Mutation batch 356: the mastery catalog checks that tests/test_mastery*.py leave unpinned. The tiny class
has one capstone talent per specialization and a rule for each achievement, as #3229 asks."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.mastery import catalog


def tiny(hook="LM-01"):
    skills = {"schema": catalog.SKILLS_SCHEMA, "version": 1, "competencies": [{"id": "c", "title": "C"}],
              "skills": [{"id": f"s{i}", "title": f"Skill {i}", "competency": "c", "cloud": False,
                          "evidence": {"learn": [hook]}} for i in range(30)]}
    tiers, req = [1, 2, 2, 3, 3, 4, 4, 5, 5, 6], [[], [0], [0], [1], [2], [3], [3, 4], [5], [6], [7, 8]]
    specs = [{"id": sp, "title": sp.title(), "nodes": [
        {"id": f"{sp}.{i + 1:02d}", "skill": f"s{k * 10 + i}", "tier": tiers[i],
         "requires": [f"{sp}.{j + 1:02d}" for j in req[i]], **({"capstone": True} if i == 9 else {})}
        for i in range(10)]}
        for k, sp in enumerate(("alpha", "beta", "gamma"))]
    cls = {"schema": catalog.CLASS_SCHEMA, "id": "test-class", "version": 1, "title": "Test Warden",
           "professional": "Tester", "fantasy": "F", "competency": "C", "emblem": "E",
           "specializations": specs, "achievements": [{"id": f"a{i}", "name": f"Award {i}",
                            "requires": {"level": 2, "any_specialization": True}} for i in range(5)]}
    return skills, cls


def write(root, skills, cls):
    (root / "classes").mkdir(parents=True)
    (root / "skills.json").write_text(json.dumps(skills), encoding="utf-8")
    (root / "classes" / "test-class.json").write_text(json.dumps(cls), encoding="utf-8")
    return root


def test_the_error_counts_the_problems_past_five_from_the_sixth():
    assert str(catalog.CatalogError([f"p{i}" for i in range(6)])) == "p0; p1; p2; p3; p4 (and 1 more)"
    assert str(catalog.CatalogError([f"p{i}" for i in range(5)])) == "p0; p1; p2; p3; p4"


def test_a_class_version_of_zero_is_refused():
    skills, cls = tiny()
    cls["version"] = 0
    assert catalog.problems(skills, [cls]) == ["class test-class: version must be a whole number from 1"]


def test_a_specialization_id_used_twice_is_refused():
    skills, cls = tiny()
    # The third specialization takes the first one's id; its nodes are renamed to match it.
    third = cls["specializations"][2]
    third["id"] = "alpha"
    for n in third["nodes"]:
        n["id"] = "alpha.9" + n["id"][-1]
        n["requires"] = ["alpha.9" + r[-1] for r in n["requires"]]
    found = catalog.problems(skills, [cls])
    assert "class test-class: bad or repeated specialization id 'alpha'" in found


def test_known_ids_counts_draft_learn_modules(monkeypatch):
    from fabric_desktop.learn import content

    def modules(include_drafts=False):
        return [{"id": "LM-01"}] + ([{"id": "LM-98"}] if include_drafts else [])

    monkeypatch.setattr(content, "modules", modules)
    known = catalog.known_ids()
    assert set(known) == {"learn", "academy", "objectives", "rules"}
    assert known["learn"] == {"LM-01", "LM-98"}


def test_load_checks_hooks_by_default_and_refuses_a_hook_naming_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "known_ids", lambda: {"learn": {"LM-01"}})
    bad = write(tmp_path / "bad", *tiny(hook="LM-77"))
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load(bad)
    assert "skills.json: s0 learn hook LM-77 names nothing that exists" in e.value.problems
    # Without the hook check, the same files load.
    assert set(catalog.load(bad, False)["classes"]) == {"test-class"}


def test_load_returns_a_clean_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "known_ids", lambda: {"learn": {"LM-01"}})
    root = write(tmp_path / "good", *tiny())
    loaded = catalog.load(root)
    assert loaded["version"] == 1
    assert set(loaded["skills"]) == {f"s{i}" for i in range(30)}
    assert list(loaded["classes"]) == ["test-class"]


def test_a_class_title_or_achievement_that_names_a_god_is_refused():
    skills, cls = tiny()
    cls["title"] = "Data Deity"
    cls["achievements"][0]["name"] = "Divine Loader"
    found = catalog.problems(skills, [cls])
    assert "class test-class: the name 'Data Deity' is or resembles a god" in found
    assert "class test-class: the name 'Divine Loader' is or resembles a god" in found
