"""A capacity repository: capacity.toml compared with Fabric, and Fabric's state written to it (item 37, stage 4)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from test_git import git_home  # noqa: F401  (an autouse fixture)

from fabric_desktop.git import capacity_files
from fabric_desktop.service import Service

CAPS = [{"id": "CAP-1", "displayName": "F64 Prod", "sku": "F64", "region": "West Europe", "state": "Active"},
        {"id": "cap-2", "displayName": "F8 Dev", "sku": "F8", "region": "West Europe", "state": "Paused"}]
WORKSPACES = [{"id": "w-sales", "displayName": "Sales Gold", "capacityId": "cap-2"},
              {"id": "w-fin", "displayName": "Finance Silver", "capacityId": "cap-1"},
              {"id": "w-adhoc", "displayName": "Ad hoc", "capacityId": "CAP-1"},
              {"id": "w-dev", "displayName": "Dev", "capacityId": "cap-2"},
              {"id": "w-mine", "displayName": "My workspace"}]  # on no capacity
FILE = '''
[capacities."F64 Prod"]
capacity_id = "cap-1"
sku = "F64"
workspaces = [{ id = "w-sales", name = "Sales Gold" }, "w-fin", { id = "w-gone", name = "Hidden" }]

[capacities."F8 Dev"]
capacity_id = "cap-2"
sku = "F4"
state = "Active"
workspaces = ["w-dev"]

[capacities."F32 Test"]
capacity_id = "cap-3"
sku = "F32"
'''


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(s, "_fabric_capacities", lambda: (CAPS, WORKSPACES), raising=False)
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


@pytest.fixture
def repo(svc, tmp_path):
    svc.git_create(str(tmp_path / "capacity"), "capacity")
    root = (tmp_path / "capacity").resolve()
    (root / "capacity.toml").write_text(FILE, encoding="utf-8")
    svc.git_stage_all(str(root))
    svc.git_commit(str(root), "Capacities")
    return str(root)


def test_the_drift_report_compares_settings_and_where_workspaces_are(svc, repo):
    out = svc.git_capacity_diff(repo)
    by = {i["name"]: i for i in out["items"]}
    prod, dev, test = by["F64 Prod"], by["F8 Dev"], by["F32 Test"]
    assert prod["state"] == "differs" and prod["fields"] == []  # region and state aren't in the file: not compared
    assert prod["workspaces"]["same"] == 1  # Finance Silver, matched by id whatever the case of the capacity id
    assert prod["workspaces"]["elsewhere"] == [{"id": "w-sales", "name": "Sales Gold", "on": "F8 Dev"}]
    assert prod["workspaces"]["not_visible"] == [{"id": "w-gone", "name": "Hidden"}]
    assert prod["workspaces"]["not_in_git"] == [{"id": "w-adhoc", "name": "Ad hoc"}]
    # Sales Gold is on F8 Dev in Fabric, but the file lists it under F64 Prod: reported there only.
    assert dev["fields"] == [{"field": "sku", "git": "F4", "fabric": "F8"}, {"field": "state", "git": "Active", "fabric": "Paused"}]
    assert dev["workspaces"]["not_in_git"] == [] and dev["workspaces"]["same"] == 1
    assert test["state"] == "not_visible"  # not shown to this person: not drift
    assert out["counts"] == {"same": 0, "differs": 2, "not_visible": 1, "not_in_git": 0}


def test_a_capacity_only_in_fabric_is_listed_and_a_matching_one_is_same():
    git = [{"name": "F8", "capacity_id": "cap-2", "sku": "F8", "workspaces": [{"id": "w-dev", "name": ""}, {"id": "w-sales", "name": ""}]}]
    items = capacity_files.compare(git, capacity_files.from_fabric(CAPS, WORKSPACES), WORKSPACES)
    assert [(i["name"], i["state"]) for i in items] == [("F8", "same"), ("F64 Prod", "not_in_git")]


def test_writing_fabrics_state_keeps_capacities_this_person_cant_see(svc, repo):
    before = svc.git_capacity_diff(repo)["counts"]["differs"]
    out = svc.git_capacity_write(repo)
    assert out["written"] == ["capacity.toml"] and out["kept"] == ["F32 Test"] and before
    data = tomllib.loads(Path(repo, "capacity.toml").read_text(encoding="utf-8"))
    assert data["capacities"]["F8 Dev"] == {"capacity_id": "cap-2", "sku": "F8", "region": "West Europe", "state": "Paused",
                                            "workspaces": [{"id": "w-dev", "name": "Dev"}, {"id": "w-sales", "name": "Sales Gold"}]}
    assert data["capacities"]["F32 Test"] == {"capacity_id": "cap-3", "sku": "F32", "workspaces": []}
    after = svc.git_capacity_diff(repo)
    assert after["counts"] == {"same": 2, "differs": 0, "not_visible": 1, "not_in_git": 0}
    # It's a change to review, not a commit; writing again changes nothing.
    assert [e["path"] for e in svc.git_status(repo)["unstaged"]] == ["capacity.toml"]
    assert svc.git_capacity_write(repo, confirm=True)["written"] == []


def test_uncommitted_changes_to_the_file_are_replaced_only_after_confirm(svc, repo):
    path = Path(repo, "capacity.toml")
    path.write_text(path.read_text(encoding="utf-8") + "\n# my note\n", encoding="utf-8")
    out = svc.git_capacity_write(repo)
    assert out["needs_confirm"] and "my note" in path.read_text(encoding="utf-8")
    assert svc.git_capacity_write(repo, confirm=True)["written"] == ["capacity.toml"]


def test_a_file_with_problems_is_reported_and_never_overwritten(svc, repo):
    path = Path(repo, "capacity.toml")
    path.write_text(path.read_text(encoding="utf-8") + '\n[capacities.broken]\nsku = "F2"\n', encoding="utf-8")
    assert any("needs a capacity_id" in p for p in svc.git_capacity_diff(repo)["problems"])
    with pytest.raises(Exception, match="Fix the file first"):
        svc.git_capacity_write(repo, confirm=True)
    assert "broken" in path.read_text(encoding="utf-8")


def test_only_capacity_repositories_have_a_drift_report(svc, tmp_path):
    svc.git_create(str(tmp_path / "nb"), "notebooks")
    with pytest.raises(Exception, match="doesn't hold capacity definitions"):
        svc.git_capacity_diff(str(tmp_path / "nb"))


def test_odd_names_round_trip_through_the_file(tmp_path):
    fabric = capacity_files.from_fabric([{"id": "c", "displayName": 'Prod "EU" \\ west', "sku": "F2"}],
                                        [{"id": "w", "displayName": "Ünïcode ws", "capacityId": "c"}])
    capacity_files.write(tmp_path, [], fabric, uncommitted=False)
    back = capacity_files.read(tmp_path)
    assert back["problems"] == [] and back["capacities"][0]["name"] == 'Prod "EU" \\ west'
    assert back["capacities"][0]["workspaces"] == [{"id": "w", "name": "Ünïcode ws"}]
