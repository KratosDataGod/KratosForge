"""Mutation-testing follow-up for git/platform_files.py: the edges the existing tests left open.
Zone defaults, header comments, long names in messages, compare with a kind not deployed yet, the
export's held-file message and counts, and the deploy plan's admin, assignment and retire edges."""

from __future__ import annotations

import pytest

from fabric_desktop.git import platform_files as pf
from fabric_desktop.git.cli import GitError


def _defs(**over):
    defs = {k: {} for k in pf.FOLDERS} | {"admins": [], "glossary": {}, "shortcut_rules": {}}
    return defs | over


def deployed(**raw):
    return pf.from_platform({"key_tables": {"prod": []}} | raw)


DEF = {"name": "Table names", "category": "naming"}


def raw_policy(pid, *, status="published", assign=()):
    return {"id": pid, "status": status, "current": {"definition": DEF}, "assign": list(assign)}


# --------------------------------------------------------------------------- records and files


def test_only_the_shared_zone_lets_devtest_read_by_default():
    assert pf.zone_record({"id": "Shared"})["devtest"] == "read"
    assert pf.zone_record({"id": "sales"})["devtest"] == "none"
    assert pf.zone_record({"id": "sales", "devtest": "write"})["devtest"] == "write"


def test_a_blank_header_line_is_a_bare_hash():
    assert pf.dumps({"x": 1}, "first\n\nsecond") == "# first\n#\n# second\nx = 1\n"


def test_a_long_file_name_is_cut_to_200_characters_in_the_problem():
    problems: list[str] = []
    rel = "domains/" + "d" * 300 + ".toml"
    assert pf._parse("not = = toml", problems, rel) is None
    name = problems[0].split(" can't be read: ")[0]
    assert len(name) == 200 and name.endswith("…") and name.startswith("domains/ddd")


# --------------------------------------------------------------------------- comparing


def test_a_table_against_a_list_is_one_change_not_field_by_field():
    assert pf._changes({"a": 1}, [1]) == [{"field": "", "git": {"a": 1}, "deployed": [1]}]
    assert pf._changes([1], {"a": 1}) == [{"field": "", "git": [1], "deployed": {"a": 1}}]
    assert pf._changes([1], [1]) == []


def test_shortcut_rules_not_deployed_yet_compare_field_by_field():
    items = pf.compare({"shortcut_rules": {"mode": "on"}, "admins": ["a@contoso.com"]}, {})
    rules = next(i for i in items if i["kind"] == "shortcut_rules")
    assert rules["changes"] == [{"field": "mode", "git": "on", "deployed": None}]
    admins = next(i for i in items if i["kind"] == "admins")
    assert admins["changes"] == [{"field": "admins", "git": ["a@contoso.com"], "deployed": []}]


# --------------------------------------------------------------------------- export


def _hold(tmp_path, n):
    """A repository whose first n domain files hold uncommitted text the export would replace."""
    (tmp_path / "domains").mkdir()
    ids = [f"d{i}" for i in range(n)]
    for i in ids:
        (tmp_path / "domains" / f"{i}.toml").write_text("old = 1\n", encoding="utf-8")
    defs = _defs(domains={i: {"id": i, "name": i} for i in ids})
    return defs, {f"domains/{i}.toml" for i in ids}


def test_export_without_confirm_refuses_to_replace_uncommitted_files(tmp_path):
    defs, held = _hold(tmp_path, 1)
    with pytest.raises(GitError) as e:
        pf.export(tmp_path, defs, held)
    assert e.value.kind == "confirm" and e.value.changes == ["domains/d0.toml"]
    assert (tmp_path / "domains" / "d0.toml").read_text(encoding="utf-8") == "old = 1\n"


def test_five_held_files_are_all_named(tmp_path):
    defs, held = _hold(tmp_path, 5)
    with pytest.raises(GitError) as e:
        pf.export(tmp_path, defs, held)
    msg = str(e.value)
    assert msg.startswith("5 of these files") and "domains/d4.toml." in msg and "and more" not in msg


def test_six_held_files_name_five_and_more(tmp_path):
    defs, held = _hold(tmp_path, 6)
    with pytest.raises(GitError) as e:
        pf.export(tmp_path, defs, held)
    msg = str(e.value)
    assert "domains/d4.toml and more." in msg and "d5" not in msg


def test_export_counts_the_files_it_left_unchanged(tmp_path):
    defs = _defs(admins=["admin@contoso.com"])
    first = pf.export(tmp_path, defs, set())
    total = len(first["written"])
    assert first["unchanged"] == 0 and total == 3
    (tmp_path / "admins.toml").write_text("changed = 1\n", encoding="utf-8")
    again = pf.export(tmp_path, defs, set())
    assert again["written"] == ["admins.toml"] and again["unchanged"] == 2


# --------------------------------------------------------------------------- deploy plan


def test_a_plan_without_a_problems_list_has_no_stops():
    out = pf.plan({}, {"prod": deployed()}, "prod", set(), "note")
    assert out["stops"] == [] and out["steps"] == []


def test_no_admins_anywhere_and_none_ticked_is_not_a_stop():
    out = pf.plan({"problems": [], "admins": []}, {"prod": deployed()}, "prod", set(), "note")
    assert out["stops"] == [] and out["steps"] == []


def test_a_new_assignment_names_its_scope_and_target():
    a_new = {"scope": "domain", "target": "sales", "settings": {}}
    dep = deployed(policies=[raw_policy("p1")])
    rec = pf.policy_record(raw_policy("p1", assign=[a_new]))
    out = pf.plan({"problems": [], "policies": {"p1": rec}}, {"prod": dep}, "prod", set(), "note")
    assert [(s["op"], s["what"]) for s in out["steps"]] == [("save_assignment", "assign domain sales")]


def test_a_deployed_policy_also_in_git_is_never_removable():
    dep = deployed(policies=[raw_policy("p1"), raw_policy("gone")])
    rec = pf.policy_record(raw_policy("p1"))
    out = pf.plan({"problems": [], "policies": {"p1": rec}}, {"prod": dep}, "prod", {("policies", "p1")}, "note")
    assert {(r["kind"], r["id"]) for r in out["removable"]} == {("policies", "gone")}
    assert all(s["id"] != "p1" for s in out["steps"])
