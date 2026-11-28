"""localdata/projects.py: rebuilding the list of projects when the damaged list can't be read, can't be
moved aside, or the rebuilt one can't be saved. All in tmp_path."""

from __future__ import annotations

import json
import logging

import pytest

from fabric_desktop.localdata import projects as projects_mod
from fabric_desktop.localdata.projects import ProjectStore


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "local-data")


def test_a_list_gone_before_it_can_be_moved_aside_is_rebuilt_without_a_copy(store, caplog):
    made = store.create("Sales")
    store.index_file.unlink()  # read_bytes and set_aside both find nothing
    with caplog.at_level(logging.WARNING, logger=projects_mod.__name__):
        index = store._rebuild_index()
    assert index == {made.id: str((store.root / made.id).resolve())}
    assert "could not be read or copied aside" in caplog.text
    assert "1 found." in caplog.text and "damaged list is kept" not in caplog.text
    assert json.loads(store.index_file.read_text()) == index  # saved again
    assert list(store.root.glob("projects.json.corrupt-*")) == []


def test_a_rebuilt_list_that_cant_be_saved_is_still_used(store, monkeypatch, caplog):
    made = store.create("Stock")
    store.index_file.write_text('{"' + made.id + '": "' + str(store.root / made.id) + '", "cut')  # a power cut
    monkeypatch.setattr(store, "_save_index", lambda index: (_ for _ in ()).throw(OSError(28, "disk full")))
    with caplog.at_level(logging.WARNING, logger=projects_mod.__name__):
        assert [p.id for p in store.list()] == [made.id]
    assert "couldn't be saved (" in caplog.text and "disk full" in caplog.text
    assert "the damaged list is kept as projects.json.corrupt-" in caplog.text
    assert not store.index_file.exists()  # moved aside, and the rebuilt one wasn't written


def test_with_no_projects_found_no_list_is_written(store, caplog):
    store.root.mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger=projects_mod.__name__):
        assert store._rebuild_index() == {}
    assert not store.index_file.exists()
    assert "0 found." in caplog.text
