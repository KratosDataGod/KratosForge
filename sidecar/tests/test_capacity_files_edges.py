"""The capacities file in a platform repository (git/capacity_files.py), the paths the main tests
don't reach: a file that is a link, can't be read or isn't TOML, sections that can't be used,
capacities listed twice, fields and workspaces that aren't text, and capacities Fabric shows without an
id. All in tmp_path."""

from __future__ import annotations

import os

import pytest

from fabric_desktop.git import capacity_files as C
from fabric_desktop.git.cli import GitError


def put(root, text):
    (root / C.FILE).write_text(text, encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need a privilege on Windows")
def test_a_capacities_file_that_is_a_link_isnt_read_or_written(tmp_path):
    (tmp_path / "elsewhere.toml").write_text("")
    (tmp_path / C.FILE).symlink_to(tmp_path / "elsewhere.toml")
    assert C.read(tmp_path) == {"capacities": [], "problems": [f"{C.FILE} is a link, so it wasn't read."]}
    with pytest.raises(GitError, match="is a link, so it wasn't written"):
        C.write(tmp_path, [], [], uncommitted=False)


def test_a_file_that_isnt_toml_or_text_is_one_problem(tmp_path):
    put(tmp_path, "[capacities\n")
    got = C.read(tmp_path)
    assert got["capacities"] == [] and got["problems"][0].startswith(f"{C.FILE} can't be read:")
    (tmp_path / C.FILE).write_bytes(b"\xff\xfe")
    assert C.read(tmp_path)["problems"][0].startswith(f"{C.FILE} can't be read:")


def test_capacities_that_arent_sections_are_one_problem(tmp_path):
    put(tmp_path, 'capacities = ["F64"]\n')
    assert C.read(tmp_path)["problems"] == [f"{C.FILE}: capacities must be a table of [capacities.<name>] sections."]


def test_sections_that_cant_be_used_are_reported_and_the_rest_read(tmp_path):
    put(tmp_path, "\n".join([
        "[capacities]", 'odd = "F2"',
        "[capacities.noid]", 'sku = "F2"',
        "[capacities.prod]", 'capacity_id = "CAP-1"', "sku = 64", 'region = "West Europe"',
        'workspaces = ["ws-1", { id = "ws-2", name = "Sales" }, 7, { name = "no id" }]',
        "[capacities.again]", 'capacity_id = "cap-1"',
    ]) + "\n")
    got = C.read(tmp_path)
    assert got["capacities"] == [{"name": "prod", "capacity_id": "CAP-1", "region": "West Europe",
                                  "workspaces": [{"id": "ws-1", "name": ""}, {"id": "ws-2", "name": "Sales"}]}]
    assert got["problems"] == [
        f"{C.FILE}: capacities.odd isn't a section.",
        f"{C.FILE}: capacities.noid needs a capacity_id.",
        f"{C.FILE}: capacities.prod.sku must be text.",
        f"{C.FILE}: a workspace under capacities.prod needs an id.",
        f"{C.FILE}: a workspace under capacities.prod needs an id.",
        f"{C.FILE}: capacity cap-1 is listed twice; only the first is used.",
    ]


def test_capacities_fabric_shows_without_an_id_are_left_out():
    got = C.from_fabric([{"displayName": "no id"}, {"id": "c1", "displayName": "Prod", "sku": "F64"}],
                        [{"id": "w1", "displayName": "Sales", "capacityId": "C1"}, {"id": "w2"}])
    assert got == [{"name": "Prod", "capacity_id": "c1", "sku": "F64", "region": "", "state": "",
                    "workspaces": [{"id": "w1", "name": "Sales"}]}]
