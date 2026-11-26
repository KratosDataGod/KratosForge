"""meta/release.py: the mutants that test_release_mutation.py and the module's other tests leave alive. A folder
that isn't there, or a PC without git, has no repository, and the committed files are listed with a read-only
git call."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop.meta import release


def test_a_folder_that_isnt_there_or_no_git_has_no_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "Repository", lambda folder: "repo")
    monkeypatch.setattr(release, "cli", SimpleNamespace(git_path=lambda: "git"))
    assert release._repo(tmp_path) == "repo"
    assert release._repo(tmp_path / "missing") is None
    monkeypatch.setattr(release, "cli", SimpleNamespace(git_path=lambda: None))
    assert release._repo(tmp_path) is None


def test_committed_files_are_listed_read_only(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "_repo", lambda root: SimpleNamespace(
        head=lambda: "abc", file_at=lambda rev, path: {"binary": False, "truncated": False, "text": "x\r\n"}))
    monkeypatch.setattr(release, "cli", SimpleNamespace(
        text=lambda root, args, **kw: calls.append(kw) or "e/a.toml\0e/b.py\0"))
    assert release.committed(tmp_path, "e", ".toml") == {"e/a.toml": "x\n"}
    assert calls == [{"read_only": True}]
