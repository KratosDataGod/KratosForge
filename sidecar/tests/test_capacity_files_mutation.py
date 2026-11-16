"""Mutation batch 65: the capacities file (git/capacity_files.py). Text limits at their edges, names cut in
problems, a missing file, exact items for capacities only one side has, the confirm default, and the
file written when there are no capacities. All in tmp_path."""

from __future__ import annotations

import pytest

from fabric_desktop.git import capacity_files as C
from fabric_desktop.git.cli import GitError
from fabric_desktop.redact import one_line


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def put(root, text):
    (root / C.FILE).write_text(text, encoding="utf-8")


def test_text_is_fine_up_to_256_characters_with_no_control_characters():
    assert C._MAX == 256
    assert C._ok_text("a" * 256) and not C._ok_text("a" * 257)
    assert C._ok_text("a b~") and C._ok_text("")
    for bad in ("a\x00", "a\x1f", "\x7f", "a\nb", "a\tb"):
        assert not C._ok_text(bad), repr(bad)
    assert C._ok_text("\x80é")


@pytest.mark.parametrize("cid", ["x" * 257, "a\\u007fb", "a\\u001fb"])   # as written in TOML
def test_a_capacity_id_with_other_characters_or_too_long_is_refused(tmp_path, cid):
    put(tmp_path, f'[capacities.Prod]\ncapacity_id = "{cid}"\n')
    got = C.read(tmp_path)
    assert got == {"capacities": [], "problems": [f"{C.FILE}: capacities.Prod needs a capacity_id."]}


def test_a_name_with_a_control_character_is_refused(tmp_path):
    put(tmp_path, '[capacities."a\\u007fb"]\ncapacity_id = "c1"\n')
    assert C.read(tmp_path)["capacities"] == []


def test_a_field_or_workspace_with_other_characters_is_refused(tmp_path):
    put(tmp_path, '[capacities.Prod]\ncapacity_id = "c1"\nsku = "F\\u007f"\n'
                  'workspaces = [{id = "w\\u0001"}, {id = "w2", name = "n\\u007f"}, "w3"]\n')
    got = C.read(tmp_path)
    assert got["capacities"] == [{"name": "Prod", "capacity_id": "c1", "workspaces": [{"id": "w3", "name": ""}]}]
    assert got["problems"] == [f"{C.FILE}: capacities.Prod.sku must be text.",
                               f"{C.FILE}: a workspace under capacities.Prod needs an id.",
                               f"{C.FILE}: a workspace under capacities.Prod needs an id."]


def test_a_missing_file_has_no_capacities_and_no_problems(tmp_path):
    assert C.read(tmp_path) == {"capacities": [], "problems": []}


def test_a_long_name_is_cut_to_80_characters_in_a_problem(tmp_path):
    name = "n" * 100
    put(tmp_path, f'[capacities]\n{name} = 1\n[capacities.{"m" * 100}]\nsku = "F2"\n')
    assert C.read(tmp_path)["problems"] == [f"{C.FILE}: capacities.{one_line(name, 80)} isn't a section.",
                                            f"{C.FILE}: capacities.{one_line('m' * 100, 80)} needs a capacity_id."]
    assert len(one_line(name, 80)) == 80


def test_capacities_only_one_side_has_are_reported_exactly():
    git = [{"name": "Gone", "capacity_id": "G1", "workspaces": [{"id": "w1", "name": "W1"}]}]
    fabric = [{"name": "New", "capacity_id": "N1", "sku": "F2", "region": "r", "state": "Active",
               "workspaces": [{"id": "w2", "name": "W2"}]}]
    assert C.compare(git, fabric, []) == [
        {"capacity_id": "G1", "name": "Gone", "state": "not_visible", "fields": [],
         "workspaces": {"same": 0, "elsewhere": [], "not_visible": [], "not_in_git": []}},
        {"capacity_id": "N1", "name": "New", "state": "not_in_git", "fields": [],
         "workspaces": {"same": 0, "elsewhere": [], "not_visible": [], "not_in_git": [{"id": "w2", "name": "W2"}]}}]


def test_uncommitted_changes_need_confirm_by_default(tmp_path):
    put(tmp_path, "# mine\n")
    with pytest.raises(GitError, match="has changes that aren't committed") as raised:
        C.write(tmp_path, [], [], uncommitted=True)
    assert raised.value.changes == [C.FILE]
    assert (tmp_path / C.FILE).read_text(encoding="utf-8") == "# mine\n"


def test_with_no_capacities_the_file_is_the_header_as_comments(tmp_path):
    got = C.write(tmp_path, [], [], uncommitted=False)
    text = (tmp_path / C.FILE).read_text(encoding="utf-8")
    assert text == "".join(f"# {line}\n" for line in C.HEADER.splitlines())
    assert got == {"written": [C.FILE], "capacities": 0, "kept": [], "kept_workspaces": [], "workspaces": 0}
    assert C.write(tmp_path, [], [], uncommitted=True)["written"] == []   # the same text needs no confirm
