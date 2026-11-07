"""data_format.py: a data folder a newer build marked is refused, cleanly, by an older one."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import cli, data_format, server


def _mark(root, value):
    root.mkdir(parents=True, exist_ok=True)
    (root / data_format.MARK).write_text(json.dumps({"format": value}), encoding="utf-8")


def test_a_new_folder_is_marked_with_this_builds_format(tmp_path):
    data_format.check(tmp_path / "data")
    assert data_format.marked(tmp_path / "data") == data_format.DATA_FORMAT


@pytest.mark.parametrize("old", [None, 0, "1", True, [], "{broken"])
def test_an_older_missing_or_unreadable_mark_is_raised_to_this_builds(tmp_path, old):
    root = tmp_path / "data"
    root.mkdir()
    (root / data_format.MARK).write_text(old if old == "{broken" else json.dumps({"format": old}), encoding="utf-8")
    data_format.check(root)
    assert data_format.marked(root) == data_format.DATA_FORMAT


def test_a_folder_a_newer_build_marked_is_refused_and_left_as_it_was(tmp_path):
    root = tmp_path / "data"
    _mark(root, data_format.DATA_FORMAT + 1)
    before = (root / data_format.MARK).read_bytes()
    with pytest.raises(data_format.NewerData) as err:
        data_format.check(root)
    text = str(err.value)
    assert "newer KratosForge" in text and str(root) in text and "Going back to an older build" in text
    assert (root / data_format.MARK).read_bytes() == before
    data_format.check(tmp_path / "other")  # only that folder


def test_the_engine_answers_every_call_with_the_reason_and_opens_nothing(tmp_path):
    refused = server._Refused(data_format.NewerData("saved by a newer KratosForge"))
    reply = server.handle(refused, json.dumps({"id": 7, "method": "environment", "params": {}}))  # type: ignore[arg-type]
    assert reply["id"] == 7 and "saved by a newer KratosForge" in reply["error"]
    for method in ("notebook_list", "integrations_status", "settings_get"):
        if method in server.METHODS:
            assert "newer" in server.handle(refused, json.dumps({"id": 1, "method": method, "params": {}}))["error"]  # type: ignore[arg-type]


def test_commands_that_open_the_stores_refuse_a_newer_folder(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    _mark(root, data_format.DATA_FORMAT + 1)
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: root)
    for argv in (["mcp", "--client", "x"], ["run-schedule", "0a1b2c3d4e5f"], ["clean-local"]):
        with pytest.raises(SystemExit) as stop:
            cli.main(argv)
        assert stop.value.code == 2, argv
        assert "newer KratosForge" in capsys.readouterr().err
    assert {"mcp", "integrations", "run-schedule", "run-notebooks", "run-notebook", "restore"} <= cli.DATA_COMMANDS
