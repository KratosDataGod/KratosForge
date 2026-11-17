"""Set up for VS Code: a file that can't be read as text, and a .vscode folder linked outside the
project. Files go to a temporary folder."""

import os

import pytest

from fabric_desktop import vscode_setup


def test_an_existing_file_that_isnt_utf8_counts_as_there_but_not_the_same(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "tasks.json").write_bytes(b"\xff\xfe not text")
    files = {f["path"]: f for f in vscode_setup.plan(tmp_path)["files"]}
    assert files[".vscode/tasks.json"]["state"] == "exists"


@pytest.mark.skipif(os.name == "nt", reason="links need extra rights on Windows")
def test_a_vscode_folder_linked_outside_the_project_is_skipped(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".vscode").symlink_to(outside, target_is_directory=True)
    out = vscode_setup.apply(project, [".vscode/tasks.json", ".github/copilot-instructions.md"])
    assert out == {"added": [".github/copilot-instructions.md"], "skipped": [".vscode/tasks.json"]}
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows feature")
def test_a_vscode_folder_that_is_a_junction_outside_the_project_is_skipped(tmp_path):
    # A junction needs no extra rights on Windows, so it's the likelier way for .vscode to point elsewhere.
    import _winapi

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _winapi.CreateJunction(str(outside), str(project / ".vscode"))
    out = vscode_setup.apply(project, [".vscode/tasks.json", ".github/copilot-instructions.md"])
    assert out == {"added": [".github/copilot-instructions.md"], "skipped": [".vscode/tasks.json"]}
    assert list(outside.iterdir()) == []
