"""vscode_setup.py paths that mutation testing (batch 74, docs/quality/mutation-testing.md) showed no
test pinned: the exact JSON layout of each file, the problem matcher's groups, which files count as
new, the same or already there, refusing a list that isn't one of the files, and writing into folders
that don't exist yet or already do."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import vscode_setup as vs


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_json_files_are_indented_two_spaces_and_end_with_a_line_break():
    assert vs._json({"a": [1]}) == '{\n  "a": [\n    1\n  ]\n}\n'


def test_each_file_has_its_text():
    tasks = json.loads(vs._tasks())["tasks"]
    assert [t["label"] for t in tasks] == ["KratosForge: Validate notebook", "KratosForge: Open notebook in KratosForge"]
    assert json.loads(vs._mcp())["servers"][vs.SERVER_NAME]["args"] == ["--client", "${input:kratosforge-client}"]
    assert json.loads(vs._extensions())["recommendations"] == ["ms-toolsai.jupyter", "ms-python.python",
                                                               "github.copilot-chat"]


def test_the_problem_matcher_reads_each_group_of_a_finding():
    validate = json.loads(vs._tasks())["tasks"][0]
    assert validate["problemMatcher"]["pattern"] == {"regexp": vs.PROBLEM_PATTERN, "file": 1, "line": 2,
                                                    "column": 3, "severity": 4, "message": 5}
    assert validate["presentation"] == {"reveal": "silent", "clear": True}


def test_a_plan_tells_new_the_same_and_already_there_apart(tmp_path):
    root = tmp_path / "repo"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode" / "tasks.json").write_text(vs._tasks(), encoding="utf-8")  # the same text
    (root / ".vscode" / "mcp.json").write_text("{}\n", encoding="utf-8")  # someone else's
    (root / ".vscode" / "extensions.json").mkdir()  # a folder where the file would go
    out = vs.plan(root)
    states = {f["path"]: f["state"] for f in out["files"]}
    assert states == {".vscode/tasks.json": "same", ".vscode/mcp.json": "exists",
                      ".vscode/extensions.json": "exists", ".github/copilot-instructions.md": "new"}
    assert out["new"] == 1


@pytest.mark.parametrize("paths", [None, ".vscode/tasks.json", [".vscode/tasks.json", "secrets.txt"]])
def test_only_a_list_of_the_listed_files_is_accepted(tmp_path, paths):
    with pytest.raises(ValueError, match="Only the files Set up for VS Code lists"):
        vs.apply(tmp_path, paths)
    assert not (tmp_path / ".vscode").exists()


def test_files_are_written_into_folders_that_dont_exist_yet_or_already_do(tmp_path):
    root = tmp_path / "not" / "made" / "yet"
    out = vs.apply(root, [".vscode/tasks.json", ".vscode/mcp.json"])
    assert out == {"added": [".vscode/tasks.json", ".vscode/mcp.json"], "skipped": []}
    assert json.loads((root / ".vscode" / "mcp.json").read_text(encoding="utf-8"))["servers"]
