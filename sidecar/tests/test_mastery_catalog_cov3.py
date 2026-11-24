"""Catalog checks (CM-02) that the main suite leaves unexercised: every refusal in problems(), on a tiny catalog."""

from __future__ import annotations

import pytest

from fabric_desktop.mastery import catalog


def tiny():
    skills = {"schema": catalog.SKILLS_SCHEMA, "version": 1, "competencies": [{"id": "c", "title": "C"}],
              "skills": [{"id": f"s{i}", "title": f"Skill {i}", "competency": "c", "cloud": False,
                          "evidence": {"learn": ["LM-01"]}} for i in range(30)]}
    tiers, req = [1, 2, 2, 3, 3, 4, 4, 5, 5, 6], [[], [0], [0], [1], [2], [3], [3, 4], [5], [6], [7, 8]]
    specs = [{"id": sp, "title": sp.title(), "nodes": [
        {"id": f"{sp}.{i + 1:02d}", "skill": f"s{k * 10 + i}", "tier": tiers[i],
         "requires": [f"{sp}.{j + 1:02d}" for j in req[i]], **({"capstone": True} if i == 9 else {})} for i in range(10)]}
        for k, sp in enumerate(("alpha", "beta", "gamma"))]
    cls = {"schema": catalog.CLASS_SCHEMA, "id": "test-class", "version": 1, "title": "Test Warden",
           "professional": "Tester", "fantasy": "F", "competency": "C", "emblem": "E",
           "specializations": specs, "achievements": [{"id": f"a{i}", "name": f"Award {i}",
                                                              "requires": {"level": 3, "talents": ["alpha.01"]}} for i in range(5)]}
    return skills, cls


def node(cls, nid):
    return next(n for sp in cls["specializations"] for n in sp["nodes"] if n["id"] == nid)


def test_the_tiny_catalog_is_clean():
    skills, cls = tiny()
    assert catalog.problems(skills, [cls]) == []


def test_catalog_error_lists_five_problems_and_counts_the_rest():
    e = catalog.CatalogError([f"p{i}" for i in range(7)])
    assert str(e) == "p0; p1; p2; p3; p4 (and 2 more)"
    assert e.problems == [f"p{i}" for i in range(7)]
    assert str(catalog.CatalogError(["only"])) == "only"


def test_class_entries_that_arent_objects_and_repeated_class_ids():
    skills, cls = tiny()
    found = catalog.problems(skills, ["nope", cls, dict(cls)])
    assert found == ["a class file isn't an object", "test-class: class id used twice"]


@pytest.mark.parametrize("doc", [None, [], {"schema": "other@1"}])
def test_a_skills_document_with_the_wrong_schema_is_refused(doc):
    skills, cls = tiny()
    found = catalog.problems(doc, [cls])
    assert found[0] == f"skills.json: schema isn't {catalog.SKILLS_SCHEMA}"
    # With no skills, every node names an unknown skill.
    assert any("names unknown skill 's0'" in p for p in found)


def test_each_bad_skill_field_is_reported():
    skills, _ = tiny()
    s = skills["skills"]
    s.append("not a dict")
    s.append({"id": "Bad_Id"})
    s.append(dict(s[0]))                                     # s0 twice
    s[1]["title"] = "  "
    s[2]["title"] = "Divine Query"
    s[3]["competency"] = "zzz"
    s[4]["cloud"] = "yes"
    s[5]["evidence"] = ["LM-01"]
    s[6]["evidence"] = {"vibes": ["x"], "learn": "LM-01"}
    s[7]["evidence"] = {"learn": ["LM-1"], "academy": ["pack/E01", "pack/X"], "objectives": ["dp-700:1.2.3", "dp:1"],
                        "rules": ["NB001", "nb1"], "events": ["validate.pass"], "checks": ["integrity", "speed"]}
    found = catalog.problems(skills, [])
    assert found == [
        "skills.json: s1 has no title",
        "skills.json: s2 title names a god",
        "skills.json: s3 names unknown competency 'zzz'",
        "skills.json: s4 cloud must be true or false",
        "skills.json: s5 evidence must be an object",
        "skills.json: s6 has unknown evidence kind 'vibes'",
        "skills.json: s6 has unknown evidence kind 'learn'",
        "skills.json: s7 learn hook 'LM-1' isn't valid",
        "skills.json: s7 academy hook 'pack/X' isn't valid",
        "skills.json: s7 objectives hook 'dp:1' isn't valid",
        "skills.json: s7 rules hook 'nb1' isn't valid",
        "skills.json: s7 checks hook 'speed' isn't valid",
        "skills.json: bad skill id '?'",
        "skills.json: bad skill id 'Bad_Id'",
        "skills.json: skill s0 listed twice",
    ]


def test_known_ids_only_checks_the_kinds_it_knows():
    skills, _ = tiny()
    skills["skills"][0]["evidence"] = {"events": ["prove.match"], "learn": ["LM-02"], "rules": ["NB001"]}
    found = catalog.problems(skills, [], {"learn": {"LM-01", "LM-02"}, "rules": set()})
    assert found == ["skills.json: s0 rules hook NB001 names nothing that exists"]


def test_class_header_fields_are_checked():
    skills, cls = tiny()
    cls.update(schema="x", id="Bad Id", version=0, fantasy="", emblem=None)
    found = catalog.problems(skills, [cls])
    for want in (f"class Bad Id: schema isn't {catalog.CLASS_SCHEMA}", "class Bad Id: bad id",
                 "class Bad Id: version must be a whole number from 1", "class Bad Id: no fantasy",
                 "class Bad Id: no emblem"):
        assert want in found
    skills, cls = tiny()
    cls["version"] = "1"
    assert catalog.problems(skills, [cls]) == ["class test-class: version must be a whole number from 1"]


def test_too_few_specializations_and_repeated_achievement_ids():
    skills, cls = tiny()
    cls["specializations"] = cls["specializations"][:2] + ["junk"]
    cls["achievements"][1]["id"] = "a0"
    found = catalog.problems(skills, [cls])
    assert "class test-class: 2 specializations, needs at least 3" in found
    assert "class test-class: achievement ids repeat" in found


def test_specialization_and_node_ids_and_node_fields_are_checked():
    skills, cls = tiny()
    cls["specializations"][1]["id"] = "alpha"               # repeated
    node(cls, "gamma.01")["id"] = "alpha.01"                 # wrong prefix, and a node id used twice
    node(cls, "gamma.05")["tier"] = 0
    node(cls, "gamma.06")["requires"] = "gamma.04"
    found = catalog.problems(skills, [cls])
    assert "class test-class: bad or repeated specialization id 'alpha'" in found
    assert "class test-class/gamma: node id 'alpha.01' must be 'gamma.NN'" in found
    assert "class test-class: node alpha.01 used twice" in found
    assert "class test-class: node gamma.05 tier must be a whole number from 1" in found
    assert "class test-class: node gamma.06 requires must be a list" in found


def test_a_node_that_requires_itself_is_reported():
    skills, cls = tiny()
    node(cls, "alpha.03")["requires"] = ["alpha.01", "alpha.03"]
    found = catalog.problems(skills, [cls])
    assert found == ["class test-class: node alpha.03 requires itself"]


def test_topological_orders_by_tier_then_id_and_detects_cycles():
    assert catalog.topological({"b": [], "a": [], "c": ["a", "b"]}) == ["a", "b", "c"]
    assert catalog.topological({"a": ["b"], "b": ["a"]}) is None
    assert catalog.topological({}) == []


def test_load_returns_the_checked_catalog_in_title_order(tmp_path):
    import json

    (tmp_path / "classes").mkdir()
    skills, cls = tiny()
    other = json.loads(json.dumps(cls))
    other.update(id="another", title="Alpha Keeper")
    # The same skills in a second class: a shared skill is one entry.
    (tmp_path / "skills.json").write_text(json.dumps(skills))
    (tmp_path / "classes" / "a.json").write_text(json.dumps(cls))
    (tmp_path / "classes" / "b.json").write_text(json.dumps(other))
    cat = catalog.load(tmp_path, check_hooks=False)
    assert cat["version"] == 1
    assert list(cat["competencies"]) == ["c"]
    assert len(cat["skills"]) == 30
    assert list(cat["classes"]) == ["another", "test-class"]
    ordered = catalog.nodes(cat["classes"]["test-class"])
    assert len(ordered) == 30
    pos = {n["id"]: i for i, n in enumerate(ordered)}
    assert all(pos[r] < pos[n["id"]] for n in ordered for r in n["requires"])
    assert ordered[0]["specialization"] == "alpha"
