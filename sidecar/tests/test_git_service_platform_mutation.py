"""Source control's service (git_service.py) for a platform repository: what mutation testing (batch 19,
docs/quality/mutation-testing.md) showed no test pinned. The environment check, which environment's key
table export keeps, who may deploy, how ignored files are named, and the note a deploy leaves. The
local platform and real temporary repositories only: nothing reaches a remote or Fabric."""

from __future__ import annotations

import pytest

from fabric_desktop.git.cli import GitError
from tests.test_git import git_home  # noqa: F401 - an autouse fixture
from tests.test_platform_repo import commit, exported, other, read, repo, svc  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata-env"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))




def test_a_diff_against_an_unknown_environment_is_refused(svc, repo):  # noqa: F811
    with pytest.raises(GitError, match="'staging' isn't an environment"):
        svc.git_platform_diff(repo, "staging")


def test_export_keeps_prods_key_table_when_environments_disagree(svc, repo, tmp_path):  # noqa: F811
    svc._platform("dev").call("ensure_key_table", name="region", business_key="dev_code")
    svc._platform("prod").call("ensure_key_table", name="region", business_key="prod_code")
    out = svc.git_platform_export(repo)
    assert read(tmp_path, "key_tables/region.toml")["business_key"] == "prod_code"
    assert [(d["id"], d["env"], d["kept_from"]) for d in out["disagree"]] == [("region", "dev", "prod")]


def test_only_a_platform_admin_deploys(svc, exported, monkeypatch):  # noqa: F811
    real = svc._catalog

    class NotAdmin:
        def __init__(self, cat):
            self.cat = cat

        def call(self, op, **kwargs):
            return {"admin": False} if op == "whoami" else self.cat.call(op, **kwargs)

    monkeypatch.setattr(svc, "_catalog", lambda: NotAdmin(real()))
    with pytest.raises(GitError, match="Only platform admins deploy") as e:
        svc.git_platform_deploy(exported)
    assert e.value.kind == "denied"


def test_ignored_platform_files_are_named_five_at_a_time(svc, exported, tmp_path):  # noqa: F811
    root = tmp_path / "platform"
    (root / ".gitignore").write_text((root / ".gitignore").read_text(encoding="utf-8") + "domains/hidden*.toml\n",
                                     encoding="utf-8")
    commit(svc, exported, "Ignore hidden domains")
    for i in range(6):
        (root / "domains" / f"hidden{i}.toml").write_text(f'id = "hidden{i}"\nname = "H{i}"\n', encoding="utf-8")
    with pytest.raises(GitError) as e:
        svc.git_platform_deploy(exported)
    named = ", ".join(f"domains/hidden{i}.toml" for i in range(5))
    assert str(e.value).startswith(f"Git ignores {named} and more, so it isn't in any commit.")
    (root / "domains" / "hidden5.toml").unlink()
    with pytest.raises(GitError) as e:
        svc.git_platform_deploy(exported)
    assert str(e.value).startswith(f"Git ignores {named}, so it isn't in any commit.")


def test_a_deploy_notes_the_commit_it_came_from(other, exported):  # noqa: F811
    preview = other.git_platform_deploy(exported)
    notes = {s["args"]["note"] for s in preview["steps"] if s["args"].get("note")}
    assert notes == {f"Deployed from the platform repository at commit {preview['commit'][:12]}"}
