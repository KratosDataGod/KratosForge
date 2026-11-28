"""The engine finds git, java, bash and PowerShell with runtimes.trusted_cli, never shutil.which.

On Windows shutil.which looks in the current folder before PATH, so a git.exe left in a repo's or a
notebook's folder would be run instead of the installed one."""

from __future__ import annotations

import ast
from pathlib import Path

import fabric_desktop

PACKAGE = Path(fabric_desktop.__file__).parent


def test_no_engine_code_calls_shutil_which():
    found = []
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "which" \
                    and isinstance(node.value, ast.Name) and node.value.id == "shutil":
                found.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "shutil" \
                    and any(a.name == "which" for a in node.names):
                found.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert not found, "use engines.runtimes.trusted_cli: " + ", ".join(found)


def test_git_is_found_only_in_paths_absolute_folders(tmp_path, monkeypatch):
    from fabric_desktop.git import cli
    planted = tmp_path / "repo"
    planted.mkdir()
    (planted / "git").write_text("")
    (planted / "git").chmod(0o755)
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", ".")
    assert cli.git_path() is None


def test_a_missing_git_says_where_programs_are_looked_for(monkeypatch):
    import pytest

    from fabric_desktop.engines.runtimes import PATH_HINT
    from fabric_desktop.git import cli
    monkeypatch.setattr(cli, "git_path", lambda: None)
    with pytest.raises(cli.GitError) as err:
        cli.command(None, ["status"])
    assert PATH_HINT in str(err.value) and "network share" in PATH_HINT
