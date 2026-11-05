"""Set up a repository for VS Code (vscode_setup.py) and `kratosforge nb validate` (cli.py)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from fabric_desktop import vscode_setup
from fabric_desktop.git.cli import GitError
from fabric_desktop.integrations.config import cli_launcher_path
from fabric_desktop.service import Service

from test_git import git_home  # noqa: F401  (an autouse fixture)

DOCS = Path(__file__).resolve().parents[2] / "docs"
SIDECAR = Path(__file__).resolve().parents[1]


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


def repo_of(svc, tmp_path, name="sales-notebooks"):
    svc.git_create(str(tmp_path / name), "notebooks")
    return str((tmp_path / name).resolve())


def test_setup_adds_only_new_files_and_commits_nothing(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    (Path(root) / ".github").mkdir()
    (Path(root) / ".github" / "copilot-instructions.md").write_text("Ours.\n", encoding="utf-8")
    plan = svc.git_vscode_plan(root)
    states = {f["path"]: f["state"] for f in plan["files"]}
    assert states == {".vscode/tasks.json": "new", ".vscode/mcp.json": "new", ".vscode/extensions.json": "new",
                      ".github/copilot-instructions.md": "exists"}
    assert plan["new"] == 3 and plan["name"] == "sales-notebooks"
    out = svc.git_vscode_apply(root, [".vscode/tasks.json", ".vscode/mcp.json", ".vscode/extensions.json",
                                      ".github/copilot-instructions.md"])
    assert out["added"] == [".vscode/tasks.json", ".vscode/mcp.json", ".vscode/extensions.json"]
    assert out["skipped"] == [".github/copilot-instructions.md"]
    assert (Path(root) / ".github" / "copilot-instructions.md").read_text(encoding="utf-8") == "Ours.\n"
    assert out["status"]["dirty"]  # changes to review, nothing committed
    assert cli_launcher_path(svc.root).is_file()  # the command the task runs
    again = {f["path"]: f["state"] for f in svc.git_vscode_plan(root)["files"]}
    assert again[".vscode/tasks.json"] == "same"
    (Path(root) / ".vscode" / "mcp.json").write_text("{}", encoding="utf-8")
    assert svc.git_vscode_apply(root, [".vscode/mcp.json"])["skipped"] == [".vscode/mcp.json"]  # never replaced
    assert (Path(root) / ".vscode" / "mcp.json").read_text(encoding="utf-8") == "{}"


def test_setup_writes_only_its_own_files_in_opened_repositories(svc, tmp_path):
    root = repo_of(svc, tmp_path)
    with pytest.raises(GitError, match="Only the files"):
        svc.git_vscode_apply(root, ["../outside.json"])
    with pytest.raises(GitError, match="Open this repository"):
        svc.git_vscode_plan(str(tmp_path))
    assert not (tmp_path / "outside.json").exists()


def test_mcp_file_holds_no_connection_id():
    mcp = json.loads(vscode_setup.FILES[".vscode/mcp.json"][2]())
    server = mcp["servers"]["fabric-desktop"]
    assert server["args"] == ["--client", "${input:kratosforge-client}"]
    assert mcp["inputs"][0]["id"] == "kratosforge-client"
    assert "cl_" not in json.dumps(mcp)


def test_copilot_instructions_match_the_docs():
    doc = (DOCS / "ai" / "agent-instructions.md").read_text(encoding="utf-8")
    block = re.search(r"## The instructions\n\n```markdown\n(.*?)```", doc, re.S).group(1)
    assert block == vscode_setup.COPILOT_INSTRUCTIONS


def _env(tmp_path: Path, **more: str) -> dict[str, str]:
    """This process's environment (Windows needs SystemRoot and the rest to import polars), with
    the engine pointed at this test's own folders."""
    return {**os.environ, "PYTHONPATH": str(SIDECAR), "LOCALAPPDATA": str(tmp_path / "la"), "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path)} | more


def _validate(path: Path, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    env = _env(tmp_path)
    return subprocess.run([sys.executable, "-m", "fabric_desktop.cli", "nb", "validate", str(path), *extra],
                          capture_output=True, text=True, env=env, timeout=120)


def test_validate_prints_what_the_task_problem_matcher_reads(tmp_path):
    nb = {"cells": [{"cell_type": "code", "source": ["print(total)\n"], "metadata": {}, "outputs": []},
                    {"cell_type": "code", "source": ["total = 1\n"], "metadata": {}, "outputs": []}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    path = tmp_path / "Daily sales.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    run = _validate(path, tmp_path)
    assert run.returncode == 1, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    pattern = json.loads(vscode_setup.FILES[".vscode/tasks.json"][2]())["tasks"][0]["problemMatcher"]["pattern"]["regexp"]
    hits = [re.match(pattern, line) for line in lines[:-1]]
    assert hits and all(hits), lines
    assert hits[0].group(1) == str(path.resolve()) and hits[0].group(4) == "error"
    assert hits[0].group(5).startswith("Cell 1 uses a name defined later.")
    assert lines[-1].startswith("1 errors")
    out = json.loads(_validate(path, tmp_path, "--json", "--runs-on", "pc").stdout)
    assert out["runs_on"] == "pc" and out["counts"]["error"] == 1


def test_the_task_passes_the_file_outside_the_command_line(tmp_path):
    task = json.loads(vscode_setup.FILES[".vscode/tasks.json"][2]())["tasks"][0]
    assert "${file}" not in json.dumps(task["args"])  # cmd.exe would parse a name such as a&b.ipynb
    assert task["options"]["env"] == {vscode_setup.PATH_VARIABLE: "${file}"}
    path = tmp_path / "a&b (1).ipynb"
    path.write_text(json.dumps({"cells": [], "metadata": {}}), encoding="utf-8")
    env = _env(tmp_path, **{vscode_setup.PATH_VARIABLE: str(path)})
    run = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", *task["args"]], capture_output=True, text=True,
                         env=env, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.startswith(f"{path}:1:1: info: ")
    del env[vscode_setup.PATH_VARIABLE]
    missing = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", *task["args"]], capture_output=True, text=True,
                             env=env, timeout=120)
    assert missing.returncode == 2 and "KRATOSFORGE_FILE isn't set" in missing.stderr


def test_validate_says_when_a_file_isnt_a_notebook(tmp_path):
    path = tmp_path / "x.ipynb"
    path.write_text("[]", encoding="utf-8")
    run = _validate(path, tmp_path)
    assert run.returncode == 2
    assert run.stdout.strip().endswith("it isn't a Jupyter notebook (.ipynb) file")


def test_a_file_path_names_its_notebook_only_in_places_the_app_knows(svc, tmp_path):
    svc.save_notebook("Sales/Daily", [{"type": "code", "source": "x = 1"}])
    home = svc.notebook_file_path("Sales/Daily")["path"]
    assert svc.notebook_name_for_path(home) == {"name": "Sales/Daily"}
    root = repo_of(svc, tmp_path)
    nb = svc.new_notebook(place=root)
    assert svc.notebook_name_for_path(svc.notebook_file_path(nb["name"])["path"]) == {"name": nb["name"]}
    stray = tmp_path / "elsewhere" / "Daily.ipynb"
    stray.parent.mkdir()
    stray.write_text(Path(home).read_text(encoding="utf-8"), encoding="utf-8")
    assert svc.notebook_name_for_path(str(stray)) == {"name": None}
    assert svc.notebook_name_for_path(str(tmp_path / "missing.ipynb")) == {"name": None}
    assert svc.notebook_name_for_path(home.replace(".ipynb", ".py")) == {"name": None}


def test_a_network_path_is_refused_before_anything_reads_it(svc, tmp_path, monkeypatch):
    svc.save_notebook("Daily", [{"type": "code", "source": "x = 1"}])
    touched = []
    real_resolve, real_is_file = Path.resolve, Path.is_file
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: touched.append(str(self)) or real_resolve(self, *a, **k))
    monkeypatch.setattr(Path, "is_file", lambda self: touched.append(str(self)) or real_is_file(self))
    for remote in [r"\\host\share\x.ipynb", "//host/share/x.ipynb", r"\\host@SSL\dav\x.ipynb", r"/\host\share\x.ipynb",
                   r"\\?\UNC\host\share\x.ipynb", r"\\.\C:\x.ipynb", "x.ipynb", "notebooks/x.ipynb", "", None, 3,
                   str(tmp_path / "app" / "notebooks" / ".." / ".." / "outside.ipynb")]:
        touched.clear()
        assert svc.notebook_name_for_path(remote) == {"name": None}, remote
        assert not any("host" in t or "outside" in t for t in touched), (remote, touched)
    from fabric_desktop.notebook_files_service import _local_path
    assert not _local_path("C:/a\x00.ipynb") and not _local_path(None)


def test_a_link_inside_a_known_folder_is_not_followed(svc, tmp_path):
    svc.save_notebook("Daily", [{"type": "code", "source": "x = 1"}])
    home = Path(svc.notebook_file_path("Daily")["path"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Away.ipynb").write_text(home.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        (home.parent / "Linked").symlink_to(elsewhere, target_is_directory=True)
        (home.parent / "Alias.ipynb").symlink_to(home)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    assert svc.notebook_name_for_path(str(home.parent / "Linked" / "Away.ipynb")) == {"name": None}
    assert svc.notebook_name_for_path(str(home.parent / "Alias.ipynb")) == {"name": None}
    assert svc.notebook_name_for_path(str(home)) == {"name": "Daily"}


def test_only_links_and_junctions_count_as_links_not_cloud_files():
    from types import SimpleNamespace
    from fabric_desktop.notebook_files_service import _is_link
    plain = 0o100644
    assert _is_link(SimpleNamespace(st_mode=plain, st_reparse_tag=0xA000000C))  # symbolic link
    assert _is_link(SimpleNamespace(st_mode=plain, st_reparse_tag=0xA0000003))  # junction
    assert _is_link(SimpleNamespace(st_mode=plain, st_reparse_tag=0xA000001D))  # WSL symbolic link
    assert _is_link(SimpleNamespace(st_mode=0o120777))
    for cloud in (0x9000001A, 0x9000101A, 0x9000601A, 0x8000001B):  # OneDrive placeholders, app exec alias
        assert not _is_link(SimpleNamespace(st_mode=plain, st_reparse_tag=cloud))
    assert not _is_link(SimpleNamespace(st_mode=plain))


def test_open_prints_a_link_the_shell_reads(tmp_path):
    path = tmp_path / "Daily sales.ipynb"
    path.write_text('{"cells": []}', encoding="utf-8")
    env = _env(tmp_path)
    run = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", "nb", "open", str(path), "--print"],
                         capture_output=True, text=True, env=env, timeout=120)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "kratosforge://notebook?path=" + quote(path.resolve().as_posix(), safe="")  # C: is C%3A


def test_the_open_task_passes_the_file_outside_the_command_line(tmp_path):
    task = json.loads(vscode_setup.FILES[".vscode/tasks.json"][2]())["tasks"][1]
    assert task["args"][:2] == ["nb", "open"] and "${file}" not in json.dumps(task["args"])
    path = tmp_path / "a&b.ipynb"
    path.write_text('{"cells": []}', encoding="utf-8")
    env = _env(tmp_path, **{vscode_setup.PATH_VARIABLE: str(path)})
    run = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", *task["args"], "--print"], capture_output=True,
                         text=True, env=env, timeout=120)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().endswith("a%26b.ipynb")


def test_validate_reads_a_notebook_it_runs_from_the_same_folder(tmp_path):
    def write(name, source):
        nb = {"cells": [{"cell_type": "code", "source": [source], "metadata": {}, "outputs": []}],
              "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        (tmp_path / f"{name}.ipynb").write_text(json.dumps(nb), encoding="utf-8")
    write("Helpers", "import notebookutils as nu\nnu.fs.mount('abfss://c@a', '/m')\n")
    write("Main", "%run Helpers\n")
    out = json.loads(_validate(tmp_path / "Main.ipynb", tmp_path, "--json", "--runs-on", "pc").stdout)
    assert [f["text"] for f in out["findings"] if f["code"] == "runs_on_pc"] == [
        "It runs Helpers (%run), whose cell 1 uses Fabric notebook utilities that need Fabric."]
    (tmp_path / "Helpers.ipynb").unlink()
    out = json.loads(_validate(tmp_path / "Main.ipynb", tmp_path, "--json", "--runs-on", "pc").stdout)
    assert [f["code"] for f in out["findings"] if f["code"].startswith("runs_on_pc")] == ["runs_on_pc_unknown"]
