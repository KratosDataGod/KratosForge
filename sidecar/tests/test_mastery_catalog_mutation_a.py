"""Mastery class catalog: what the first mutation run of mastery/catalog.py left alive (batch 343)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.learn import content
from fabric_desktop.mastery import catalog


def tiny(learn: str = "LM-01"):
    skills = {"schema": catalog.SKILLS_SCHEMA, "version": 1, "competencies": [{"id": "c", "title": "C"}],
              "skills": [{"id": f"s{i}", "title": f"Skill {i}", "competency": "c", "cloud": False,
                          "evidence": {"learn": [learn]}} for i in range(30)]}
    tiers, req = [1, 2, 2, 3, 3, 4, 4, 5, 5, 6], [[], [0], [0], [1], [2], [3], [3, 4], [5], [6], [7, 8]]
    specs = [{"id": sp, "title": sp.title(), "nodes": [
        {"id": f"{sp}.{i + 1:02d}", "skill": f"s{k * 10 + i}", "tier": tiers[i],
         "requires": [f"{sp}.{j + 1:02d}" for j in req[i]], **({"capstone": True} if i == 9 else {})}
        for i in range(10)]}
        for k, sp in enumerate(("alpha", "beta", "gamma"))]
    cls = {"schema": catalog.CLASS_SCHEMA, "id": "test-class", "version": 1, "title": "Test Warden",
           "professional": "Tester", "fantasy": "F", "competency": "C", "emblem": "E",
           "specializations": specs, "achievements": [{"id": f"a{i}", "name": f"Award {i}",
                                             "requires": {"level": 2, "talents": ["alpha.01"]}} for i in range(5)]}
    return skills, cls


def write(root, skills, cls):
    (root / "classes").mkdir(parents=True)
    (root / "skills.json").write_text(json.dumps(skills), encoding="utf-8")
    (root / "classes" / "test-class.json").write_text(json.dumps(cls), encoding="utf-8")
    return root


def test_catalog_error_counts_only_problems_past_five():
    assert str(catalog.CatalogError([f"p{i}" for i in range(5)])) == "p0; p1; p2; p3; p4"
    assert str(catalog.CatalogError([f"p{i}" for i in range(6)])) == "p0; p1; p2; p3; p4 (and 1 more)"


def test_a_class_title_and_an_achievement_name_may_not_name_a_god():
    skills, cls = tiny()
    cls["title"] = "Divine Warden"
    cls["achievements"][0]["name"] = "Olympian Feat"
    found = catalog.problems(skills, [cls])
    assert "class test-class: the name 'Divine Warden' is or resembles a god" in found
    assert "class test-class: the name 'Olympian Feat' is or resembles a god" in found


def test_known_ids_names_what_hooks_may_point_at_drafts_included(monkeypatch):
    real = content.modules

    def modules(include_drafts=False):
        return real(include_drafts=include_drafts) + ([{"id": "LM-98"}] if include_drafts else [])

    monkeypatch.setattr(content, "modules", modules)
    known = catalog.known_ids()
    assert set(known) == {"learn", "academy", "objectives", "rules"}
    assert "LM-98" in known["learn"] and known["rules"]


def test_load_checks_hooks_unless_told_not_to(tmp_path):
    skills, cls = tiny(learn="LM-97")  # well formed, but no Learn module has this id
    root = write(tmp_path / "cat", skills, cls)
    with pytest.raises(catalog.CatalogError, match="learn hook LM-97 names nothing that exists"):
        catalog.load(root)
    out = catalog.load(root, check_hooks=False)
    assert list(out["classes"]) == ["test-class"] and len(out["skills"]) == 30
