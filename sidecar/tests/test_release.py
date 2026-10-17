"""Git status for the project folder, and the release record every promote writes."""

import json
import subprocess

import polars as pl
import pytest

from fabric_desktop import receipts
from fabric_desktop.meta import release
from fabric_desktop.meta.project import GateError
from fabric_desktop.meta.store import META_LAKEHOUSE
from fabric_desktop.service import Service


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def init_repo(path):
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")


def commit_all(path, message="work"):
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.meta_set_environment("test", target="local")
    return s


@pytest.fixture
def pushed(svc, tmp_path):
    """The project in a repository whose main branch is pushed to a remote, nothing uncommitted."""
    repo = tmp_path / "repo"
    (repo / "README.md").write_text("Other things live here too.\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "start")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def deployment(svc, env="test"):
    return svc._store(svc._project(), env).meta("deployments").row(-1, named=True)


def edit(svc, old, new):
    text = svc.meta_entity("silver.orders")["text"]
    assert old in text
    svc.meta_save_entity("silver.orders", text.replace(old, new, 1))


def test_a_promote_from_a_pushed_commit_records_it_and_the_record_checks_out(svc, pushed):
    status = svc.meta_git_status()
    head = git(pushed, "rev-parse", "HEAD").strip()
    assert status["repo"] and status["commit"] == head and status["branch"] == "main"
    assert status["upstream"] == "origin/main" and status["ahead"] == status["behind"] == 0
    assert not status["dirty"] and status["pushed"]
    assert svc.meta_promote_check("dev", "test")["notes"] == []

    result = svc.meta_promote("dev", "test")
    assert result["release"] == {"commit": head, "branch": "main", "dirty": False, "pushed": True,
                                 "receipt_id": result["release"]["receipt_id"]}
    row = deployment(svc)
    assert (row["commit"], row["dirty"], row["pushed"], row["version"]) == (head, False, True, result["version"])
    body = json.loads(row["receipt"])
    assert body["schema"] == receipts.SCHEMA and body["kind"] == "release"
    assert receipts.receipt_id(body) == row["receipt_id"]  # anyone with the row can check it
    assert body["project"] == {"name": "crm", "metadata_version": result["version"], "source_env": "dev",
                               "target_env": "test"}
    assert body["code"]["uncommitted"] == []
    assert {e["name"] for e in body["entities"]} == set(svc._project().entities)
    assert body["environment"]["engine_version"] == row["engine_version"] and len(row["lock_hash"]) == 64
    assert set(body["environment"]["packages"]) == set(release.ENGINE_PACKAGES)
    assert "origin" not in row["receipt"] and str(pushed) not in row["receipt"]  # no remote URL or local path

    body["code"]["dirty"] = True  # a changed record no longer matches its id
    assert receipts.receipt_id(body) != row["receipt_id"]


def test_uncommitted_files_are_a_note_unless_the_target_requires_a_commit(svc, pushed):
    edit(svc, 'expr = "amount - coalesce(discount, 0)"', 'expr = "amount"')
    svc.meta_run(["silver.orders"], backfill="full")
    status = svc.meta_git_status()
    assert status["dirty"] and status["changes"] == [{"path": "metadata/entities/silver.orders.toml", "state": "changed"}]
    assert "1 uncommitted file" in status["message"]
    check = svc.meta_promote_check("dev", "test")
    assert check["errors"] == []
    assert check["notes"] == ["The release record can't name a pushed commit that holds these files: "
                              "1 file changed since the last commit; commit them"]

    envs = svc._project().environments
    svc._write_environments(svc._project(), envs | {"test": envs["test"] | {"require_commit": True}})
    svc._project().reload()
    assert svc.meta_git_status()["dirty"]  # environments.toml itself doesn't count
    assert svc.meta_promote_check("dev", "test")["errors"] == [
        "Test only takes released files: 1 file changed since the last commit; commit them"]
    with pytest.raises(GateError, match="changed since the last commit"):
        svc.meta_promote("dev", "test")
    assert svc._store(svc._project(), "test").meta("deployments").height == 0  # nothing was promoted

    commit_all(pushed)
    assert svc.meta_promote_check("dev", "test")["errors"] == ["Test only takes released files: "
                                                               "1 commit not pushed; push them"]
    git(pushed, "push", "-q")
    result = svc.meta_promote("dev", "test")
    assert result["release"]["pushed"] and "notes" not in result["release"]


def test_a_promote_without_git_still_works_and_says_the_record_has_no_commit(svc):
    assert svc.meta_git_status()["repo"] is False
    check = svc.meta_promote_check("dev", "test")
    assert check["errors"] == [] and "isn't in a Git repository" in check["notes"][0]
    result = svc.meta_promote("dev", "test")
    assert result["release"]["commit"] is None and "isn't in a Git repository" in result["release"]["notes"][0]
    row = deployment(svc)
    body = json.loads(row["receipt"])
    assert body["code"] == {"repo": False, "commit": None, "branch": None, "dirty": None, "pushed": None,
                            "uncommitted": []}
    assert receipts.receipt_id(body) == row["receipt_id"]


def test_no_git_installed_reads_as_such(svc, monkeypatch):
    monkeypatch.setattr(release.cli, "git_path", lambda: None)
    status = svc.meta_git_status()
    assert status == {"available": False, "repo": False,
                      "message": "Git isn't installed on this PC, so releases can't name a commit."}
    assert svc.meta_promote("dev", "test")["release"]["commit"] is None


def test_local_data_settings_and_files_outside_the_project_are_not_uncommitted(svc, pushed):
    (pushed / "README.md").write_text("changed\n", encoding="utf-8")  # the repository, not the project
    (pushed / "crm" / ".local" / "scratch.txt").write_text("x", encoding="utf-8")
    svc.meta_set_environment("test", target="local")  # rewrites environments.toml
    (pushed / "crm" / "transforms" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (pushed / "crm" / "transforms" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    status = svc.meta_git_status()
    assert status["changes"] == [] and not status["dirty"] and status["pushed"]


def test_new_deleted_and_renamed_files_are_listed_relative_to_the_project(svc, pushed):
    root = pushed / "crm"
    (root / "transforms" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    git(pushed, "mv", "crm/metadata/entities/silver.orders.toml", "crm/metadata/entities/renamed.toml")
    (root / "README.md").unlink()
    states = {c["path"]: c["state"] for c in svc.meta_git_status()["changes"]}
    assert states == {"transforms/extra.py": "new", "metadata/entities/renamed.toml": "renamed",
                      "README.md": "deleted"}


def test_branch_without_upstream_and_detached_head(svc, pushed):
    git(pushed, "checkout", "-q", "-b", "feature")
    status = svc.meta_git_status()
    assert status["branch"] == "feature" and status["upstream"] is None and not status["pushed"]
    assert release.problems(status) == ["branch feature has no upstream; push it"]
    git(pushed, "checkout", "-q", "--detach")
    status = svc.meta_git_status()
    assert status["branch"] is None and status["commit"] and "(no branch)" in status["message"]


def test_behind_the_upstream_is_reported_but_doesnt_stop_a_release(svc, pushed, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(other)], check=True)
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "Other")
    (other / "NOTES.md").write_text("x\n", encoding="utf-8")
    commit_all(other)
    git(other, "push", "-q")
    git(pushed, "fetch", "-q")
    status = svc.meta_git_status()
    assert status["behind"] == 1 and status["ahead"] == 0 and "1 behind origin/main" in status["message"]
    assert release.problems(status) == []


def test_an_empty_repository_has_nothing_committed(svc, tmp_path):
    init_repo(tmp_path / "repo")
    status = svc.meta_git_status()
    assert status["repo"] and status["commit"] is None and status["dirty"]
    assert release.problems(status)[0] == "nothing is committed yet"


def test_deployments_written_before_releases_still_read_and_gate(svc, pushed):
    """A deployments table from before this change has four columns; promote adds the rest."""
    project = svc._project()
    test = svc._store(project, "test", create=True)
    old = pl.DataFrame([{"env": "test", "version": "0000000", "source_env": "dev", "at": "2026-01-01T00:00:00+00:00"}])
    test.write(META_LAKEHOUSE, "meta", "deployments", old, mode="append")
    svc.meta_promote("dev", "test")
    rows = test.meta("deployments")
    assert rows.height == 2
    assert rows["commit"].to_list() == [None, git(pushed, "rev-parse", "HEAD").strip()]
    assert rows["receipt_id"][0] is None
    # Test to Prod reads the newest Test deployment as before.
    assert not [e for e in svc.meta_promote_check("test", "prod")["errors"] if "not on metadata" in e]
