"""A platform repository's files (git/platform_files.py), held to exact values: the shared zone's default read for
Dev and Test, a header's blank lines, a long file name in a problem, comparing a table with something that isn't
one, shortcut rules no platform has yet, export's refusal and its counts, and the deploy plan's stops, step names
and what it offers to remove. Pure functions and files in tmp_path."""

from __future__ import annotations

import pytest

from fabric_desktop.git import platform_files as pf
from fabric_desktop.git.cli import GitError
from tests.test_platform_files_cov3 import deployed, plan, raw_policy


def _defs(n):
    defs = {k: {} for k in pf.FOLDERS} | {"admins": [], "glossary": {}, "shortcut_rules": {}}
    return defs | {"domains": {f"d{i}": {"id": f"d{i}", "name": f"D{i}"} for i in range(1, n + 1)}}


def test_only_the_shared_zone_is_read_from_dev_and_test_by_default():
    assert pf.zone_record({"id": "Shared"})["devtest"] == "read"
    assert pf.zone_record({"id": "z1"})["devtest"] == "none"
    assert pf.zone_record({"id": "z1", "devtest": "write"})["devtest"] == "write"


def test_a_blank_header_line_is_a_bare_hash():
    assert pf.dumps({"a": 1}, header="one\n\ntwo") == "# one\n#\n# two\na = 1\n"


def test_a_file_that_cant_be_read_is_named_in_at_most_200_characters():
    problems = []
    assert pf._parse("x = ", problems, "d/" + "n" * 300) is None
    assert problems[0].startswith("d/" + "n" * 197 + "… can't be read: ")


def test_a_table_compared_with_something_else_is_one_whole_change():
    assert pf._changes({"a": 1}, None) == [{"field": "", "git": {"a": 1}, "deployed": None}]
    assert pf._changes(None, {"a": 1}) == [{"field": "", "git": None, "deployed": {"a": 1}}]


def test_shortcut_rules_no_platform_has_are_compared_rule_by_rule():
    assert pf.compare({"shortcut_rules": {"allow": ["a"]}}, {}) == [
        {"kind": "shortcut_rules", "id": "shortcut_rules.toml", "state": "changed",
         "changes": [{"field": "allow", "git": ["a"], "deployed": None}]}]


@pytest.mark.parametrize("n, more", [(5, ""), (6, " and more")])
def test_export_names_five_uncommitted_files_at_most(tmp_path, n, more):
    held = {f"domains/d{i}.toml" for i in range(1, n + 1)}
    with pytest.raises(GitError) as err:
        pf.export(tmp_path, _defs(n), held)
    assert str(err.value) == (f"{n} of these files have changes that aren't committed: "
                              f"{', '.join(sorted(held)[:5])}{more}. "
                              "Confirm to replace them with what is deployed, or commit them first.")
    assert not (tmp_path / "domains").exists()


def test_export_counts_the_files_it_left_unchanged(tmp_path):
    first = pf.export(tmp_path, _defs(2), set())
    assert first["unchanged"] == 0 and len(first["written"]) == 5
    again = pf.export(tmp_path, _defs(3), set())
    assert again["written"] == ["domains/d3.toml"] and again["unchanged"] == 5


def test_problems_in_the_files_stop_the_deploy():
    out = plan({"problems": ["admins.toml can't be read."]}, deployed())
    assert out["stops"] == ["admins.toml can't be read. Fix the file first."]


def test_no_admins_on_either_side_isnt_a_removal_stop():
    assert plan({"admins": []}, deployed(admins=[]))["stops"] == []


def test_a_new_assignment_is_named_by_its_scope_and_target():
    dep = deployed(policies=[raw_policy("p1")])
    rec = pf.policy_record(raw_policy("p1", assign=[{"scope": "domain", "target": "sales", "settings": {}}]))
    out = plan({"policies": {"p1": rec}}, dep)
    assert [(s["op"], s["what"]) for s in out["steps"]] == [("save_assignment", "assign domain sales")]


def test_a_policy_git_still_has_isnt_offered_for_removal():
    dep = deployed(policies=[raw_policy("p1"), raw_policy("p2")])
    out = plan({"policies": {"p1": pf.policy_record(raw_policy("p1"))}}, dep)
    assert out["removable"] == [{"kind": "policies", "id": "p2", "env": None}]
