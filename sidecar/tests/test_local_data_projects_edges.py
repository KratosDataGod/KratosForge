"""The list of local data projects on this PC (localdata/projects.py), the paths the main tests don't
reach: settings that can't be used, a list file that can't be read, a project whose folder is gone,
names that can't be used or are taken, a folder that already holds a project, and ids made unique.
All in tmp_path."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata.projects import ProjectStore, Settings


@pytest.mark.parametrize("kw, needle", [
    ({"max_temp_size": "lots"}, "max_temp_size must look like 4GB"),
    ({"threads": 257}, "threads must be between 0 and 256"),
    ({"timeout_seconds": -1}, "timeout_seconds must be between 0 and a week"),
])
def test_settings_that_cant_be_used_are_refused(kw, needle):
    with pytest.raises(ValueError, match=needle):
        Settings(**kw)


@pytest.fixture
def projects(tmp_path):
    return ProjectStore(tmp_path / "local-data")


def test_a_list_file_that_isnt_a_list_of_projects_reads_as_empty(projects):
    projects.root.mkdir(parents=True)
    projects.index_file.write_text("{not json")
    assert projects.list() == []
    projects.index_file.write_text("[1, 2]")
    assert projects.list() == []


def test_a_project_whose_folder_is_gone_is_left_off_the_list_until_it_is_back(projects, tmp_path):
    kept = projects.create("Sales")
    moved = projects.create("Stock", folder=str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere" / "project.json").rename(tmp_path / "project.json.bak")
    assert [p.id for p in projects.list()] == [kept.id]
    (tmp_path / "project.json.bak").rename(tmp_path / "elsewhere" / "project.json")
    assert sorted(p.id for p in projects.list()) == sorted([kept.id, moved.id])


@pytest.mark.parametrize("name, storage, needle", [
    ("  ", "persistent", "Give the project a name"),
    ("x" * 101, "persistent", "Give the project a name"),
    ("Sales", "cloud", "storage must be one of"),
])
def test_a_project_needs_a_name_and_a_known_storage(projects, name, storage, needle):
    with pytest.raises(ValueError, match=needle):
        projects.create(name, storage=storage)


def test_a_name_already_used_is_refused_whatever_its_case(projects):
    projects.create("Sales")
    with pytest.raises(ValueError, match="already a local data project called 'SALES'"):
        projects.create("SALES")


def test_a_folder_that_already_holds_a_project_is_refused(projects, tmp_path):
    projects.create("Sales", folder=str(tmp_path / "shared"))
    projects.forget("sales")
    with pytest.raises(ValueError, match="already holds a local data project. Open it instead."):
        projects.create("Sales again", folder=str(tmp_path / "shared"))


def test_ids_are_made_unique_when_a_name_slugs_to_one_in_use(projects):
    first = projects.create("Sales 2026")
    second = projects.create("Sales-2026!")
    third = projects.create("sales_2026?")
    assert (first.id, second.id) == ("sales-2026", "sales-2026-2")
    assert third.id.startswith("sales")
    assert len({first.id, second.id, third.id}) == 3
