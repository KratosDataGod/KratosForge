"""Mutation testing batch 3: pins for the list of local data projects (localdata/projects.py) that
its own tests left open. See docs/quality/mutation-testing.md."""

from __future__ import annotations

import os

import pytest

from fabric_desktop.localdata.discovery import Source
from fabric_desktop.localdata.projects import ProjectStore, Settings


@pytest.fixture
def projects(tmp_path):
    return ProjectStore(tmp_path / "local-data")


def test_the_default_settings():
    s = Settings()
    assert (s.memory_limit, s.threads, s.timeout_seconds, s.max_temp_size, s.auto_refresh) == ("", 0, 0, "", True)


def test_a_timeout_may_be_a_week_and_no_more():
    assert Settings(timeout_seconds=7 * 24 * 3600).timeout_seconds == 604800
    with pytest.raises(ValueError, match="between 0 and a week"):
        Settings(timeout_seconds=604801)


def test_where_a_project_keeps_its_database_and_spills(projects):
    disk = projects.create("On disk")
    assert disk.database == str(disk.root / "data.duckdb")
    assert disk.temp_dir == disk.root / "tmp"
    memory = projects.create("In memory", storage="memory")
    assert memory.database == ":memory:"


def test_a_source_is_found_by_its_normalized_path(projects, tmp_path):
    p = projects.create("P")
    folder = tmp_path / "data"
    p.sources.append(Source(path=str(folder)))
    assert p.source(str(folder / "sub" / "..")) is p.sources[0]
    assert p.source(str(tmp_path / "other")) is None


def test_the_summary(projects):
    p = projects.create("P", settings={"threads": 2})
    assert p.summary() == {"id": "p", "name": "P", "folder": p.folder, "storage": "persistent",
                           "settings": {"memory_limit": "", "threads": 2, "timeout_seconds": 0,
                                        "max_temp_size": "", "auto_refresh": True},
                           "sources": [], "tables": 0, "created_at": p.created_at}


def test_an_unknown_project_is_a_key_error(projects):
    with pytest.raises(KeyError, match="No local data project 'nope'"):
        projects.load("nope")


def test_a_list_file_that_cant_be_opened_lists_nothing(projects):
    projects.index_file.mkdir(parents=True)  # a folder where the file should be
    assert projects._index() == {} and projects.list() == []


def test_ids_count_up_from_two(projects):
    ids = [projects.create(name).id for name in ("A B", "A-B", "A_B")]
    assert ids == ["a-b", "a-b-2", "a-b-3"]


def test_a_project_can_be_kept_in_a_folder_not_yet_made(projects, tmp_path):
    folder = tmp_path / "elsewhere" / "deeper" / "proj"
    p = projects.create("Elsewhere", folder=str(folder))
    assert p.folder == str(folder.resolve()) and (folder / "project.json").is_file()
    assert projects.load(p.id).folder == os.fspath(folder.resolve())


def test_a_project_can_be_kept_in_an_empty_folder_that_exists(projects, tmp_path):
    folder = tmp_path / "ready"
    folder.mkdir()
    p = projects.create("Ready", folder=str(folder))
    assert (folder / "project.json").is_file() and p.folder == str(folder.resolve())
