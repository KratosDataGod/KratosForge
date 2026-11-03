"""Fabric workspace Git awareness (Git-native Phase 2): the workspace's Git connection, the link kept in
fabric-desktop.toml, and this PC / the Git remote / the workspace lined up. Reads only: no Fabric write."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import audit, fabric_git, server
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.fabric_git_service import FabricGitMixin
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import Repository, init
from fabric_desktop.git_service import GitMixin

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

WS = "11111111-2222-3333-4444-555555555555"

# Shapes from Microsoft Learn, Core > Git > Get Connection (checked 2026-11-03).
GITHUB = {"gitConnectionState": "ConnectedAndInitialized", "gitConnectionType": "Full",
          "gitProviderDetails": {"gitProviderType": "GitHub", "ownerName": "Contoso", "repositoryName": "Sales",
                                 "branchName": "main", "directoryName": "/fabric"},
          "gitSyncDetails": {"head": "", "lastSyncTime": "2026-11-03T09:00:00Z"}}
AZDO = {"gitConnectionState": "Connected",
        "gitProviderDetails": {"gitProviderType": "AzureDevOps", "organizationName": "contoso",
                               "projectName": "Data", "repositoryName": "sales", "branchName": "dev",
                               "directoryName": ""},
        "gitSyncDetails": None}
NOT_CONNECTED = {"gitProviderDetails": None, "gitSyncDetails": None, "gitConnectionState": "NotConnected"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def git_home(tmp_path, monkeypatch):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Test Person\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
                   "[core]\n\tautocrlf = false\n[protocol \"file\"]\n\tallow = always\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def commit(root: Path, name: str) -> str:
    (root / name).write_text(name, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", name)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Repository:
    """A repository whose origin is GitHub's Contoso/Sales (as configured), with origin/main fetched
    by hand: commits a, b on main, origin/main at b."""
    r = init(tmp_path / "sales")
    commit(r.root, "a")
    commit(r.root, "b")
    git(r.root, "remote", "add", "origin", "https://github.com/contoso/sales.git")
    git(r.root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return r


class FakeFabric:
    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.calls: list[str] = []

    def git_connection(self, workspace_id: str) -> dict:
        self.calls.append(workspace_id)
        answer = self.answers[workspace_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Auth:
    def signed_in_tenant(self) -> str:
        return "tenant-1"


class Svc(GitMixin, FabricGitMixin):
    def __init__(self, root: Path, fabric: FakeFabric):
        self.root = root
        self.fabric = fabric
        self.auth = Auth()


@pytest.fixture
def svc(tmp_path, repo):
    s = Svc(tmp_path / "app", FakeFabric({WS: GITHUB}))
    s.root.mkdir()
    s.git_open(str(repo.root))
    return s


# ---- Fabric's answer, in the app's words


def test_github_connection():
    c = fabric_git.connection(GITHUB)
    assert c["connected"] and c["initialized"] and not c["selective"]
    assert (c["provider"], c["owner"], c["repository"], c["branch"], c["folder"]) == \
        ("github", "Contoso", "Sales", "main", "/fabric")
    assert c["synced_head"] == ""  # an empty head isn't a commit


def test_azure_devops_connection_and_no_sync_yet():
    c = fabric_git.connection(AZDO)
    assert c["connected"] and not c["initialized"]
    assert (c["provider"], c["owner"], c["project"], c["repository"]) == ("azure_devops", "contoso", "Data", "sales")
    assert c["synced_head"] == "" and c["last_sync"] == ""


@pytest.mark.parametrize("raw", [NOT_CONNECTED, {}, None, "x", {"gitConnectionState": "Weird"},
                                 {"gitConnectionState": "Connected", "gitProviderDetails": {"gitProviderType": "GitLab"}}])
def test_not_connected_or_unknown_is_never_guessed(raw):
    c = fabric_git.connection(raw)
    assert not c["connected"] and c["provider"] == "" and c["repository"] == ""


def test_fields_with_control_characters_are_dropped_and_heads_must_be_commits():
    raw = {**GITHUB, "gitProviderDetails": {**GITHUB["gitProviderDetails"], "branchName": "main\nrm -rf"},
           "gitSyncDetails": {"head": "not-a-sha"}}
    c = fabric_git.connection(raw)
    assert c["branch"] == "" and c["synced_head"] == ""


# ---- matching a remote


def test_github_remote_matches_case_insensitively():
    c = fabric_git.connection(GITHUB)
    remotes = [{"name": "fork", "url": "https://github.com/me/sales.git"},
               {"name": "origin", "url": "git@github.com:contoso/SALES.git"}]
    assert fabric_git.matching_remote(c, remotes) == "origin"


def test_github_enterprise_domain_must_match():
    c = fabric_git.connection({**GITHUB, "gitProviderDetails": {**GITHUB["gitProviderDetails"],
                                                                 "customDomainName": "contoso.ghe.com"}})
    assert fabric_git.matching_remote(c, [{"name": "o", "url": "https://github.com/Contoso/Sales"}]) is None
    assert fabric_git.matching_remote(c, [{"name": "o", "url": "https://contoso.ghe.com/Contoso/Sales"}]) == "o"


def test_azure_devops_needs_organization_project_and_repository():
    c = fabric_git.connection(AZDO)
    assert fabric_git.matching_remote(c, [{"name": "o", "url": "https://dev.azure.com/contoso/Other/_git/sales"}]) is None
    assert fabric_git.matching_remote(c, [{"name": "o", "url": "https://contoso.visualstudio.com/Data/_git/sales"}]) == "o"
    assert fabric_git.matching_remote(c, [{"name": "s", "url": "git@ssh.dev.azure.com:v3/contoso/Data/sales"}]) == "s"


def test_nothing_matches_a_workspace_that_isnt_connected():
    assert fabric_git.matching_remote(fabric_git.connection(NOT_CONNECTED),
                                      [{"name": "o", "url": "https://github.com/contoso/sales"}]) is None


# ---- the link file


def test_link_file_keeps_ids_and_names_only(tmp_path):
    out = fabric_git.write_link(tmp_path, "dev", {"workspace_id": WS, "workspace_name": 'Sales "Dev"',
                                                  "branch": "main", "token": "tok-SHOULD-NOT-LAND", "password": "pw-SHOULD-NOT-LAND"})
    text = (tmp_path / fabric_git.LINK_FILE).read_text(encoding="utf-8")
    assert "SHOULD-NOT-LAND" not in text and "token" not in text
    data = tomllib.loads(text)
    assert data["workspaces"]["dev"] == {"workspace_id": WS, "workspace_name": 'Sales "Dev"', "branch": "main"}
    assert out["envs"]["dev"]["workspace_name"] == 'Sales "Dev"' and out["envs"]["test"] is None


def test_link_file_keeps_other_environments_and_unlinks(tmp_path):
    fabric_git.write_link(tmp_path, "dev", {"workspace_id": WS})
    fabric_git.write_link(tmp_path, "prod", {"workspace_id": WS.replace("1", "9")})
    out = fabric_git.write_link(tmp_path, "dev", None)
    assert out["envs"]["dev"] is None and out["envs"]["prod"]["workspace_id"].startswith("9999")


def test_unreadable_link_file_is_reported_and_never_overwritten(tmp_path):
    (tmp_path / fabric_git.LINK_FILE).write_text("[workspaces.dev\n", encoding="utf-8")
    assert "can't be read" in fabric_git.read_links(tmp_path)["problem"]
    with pytest.raises(GitError):
        fabric_git.write_link(tmp_path, "dev", {"workspace_id": WS})
    assert (tmp_path / fabric_git.LINK_FILE).read_text(encoding="utf-8") == "[workspaces.dev\n"


def test_a_link_without_a_workspace_id_is_ignored(tmp_path):
    (tmp_path / fabric_git.LINK_FILE).write_text('[workspaces.dev]\nworkspace_id = "nope"\n', encoding="utf-8")
    assert fabric_git.read_links(tmp_path)["envs"]["dev"] is None


@pytest.mark.parametrize("env", ["", "staging", "../dev"])
def test_only_dev_test_prod(tmp_path, env):
    with pytest.raises(GitError):
        fabric_git.write_link(tmp_path, env, {"workspace_id": WS})


# ---- the service


def test_methods_are_listed_and_reads_are_not_audited():
    for m in ("fabric_git_links", "fabric_git_check", "fabric_git_link", "fabric_git_unlink", "fabric_git_overview"):
        assert m in server.METHODS
    for m in ("fabric_git_links", "fabric_git_check", "fabric_git_overview", "fabric_item_support"):
        assert m in audit.READ_ONLY
    for m in ("fabric_git_link", "fabric_git_unlink"):
        assert m not in audit.READ_ONLY  # they write a file in the repository


def test_only_repositories_opened_here(svc, tmp_path):
    other = init(tmp_path / "other")
    with pytest.raises(GitError):
        svc.fabric_git_check(str(other.root), WS)


def test_check_reads_the_connection_and_finds_the_remote(svc, repo):
    out = svc.fabric_git_check(str(repo.root), WS)
    assert out["remote"] == "origin" and out["connection"]["branch"] == "main"
    assert svc.fabric.calls == [WS]


def test_check_refuses_a_workspace_id_that_isnt_one(svc, repo):
    with pytest.raises(GitError):
        svc.fabric_git_check(str(repo.root), "../../items")
    assert svc.fabric.calls == []


def test_link_writes_the_file_with_what_fabric_says(svc, repo):
    out = svc.fabric_git_link(str(repo.root), "dev", WS, "Sales Dev")
    link = out["envs"]["dev"]
    assert link == {"tenant_id": "tenant-1", "workspace_id": WS, "workspace_name": "Sales Dev", "provider": "github",
                    "owner": "Contoso", "repository": "Sales", "branch": "main", "folder": "/fabric"}
    assert out["remote"] == "origin"
    assert any(f["path"] == fabric_git.LINK_FILE for f in out["status"]["untracked"])  # a change to commit


def test_link_to_a_workspace_on_another_repository_asks_first(svc, repo):
    svc.fabric.answers[WS] = AZDO
    out = svc.fabric_git_link(str(repo.root), "test", WS)
    assert out["needs_confirm"] and "none of this repository's remotes" in out["message"]
    assert not (repo.root / fabric_git.LINK_FILE).exists()
    assert svc.fabric_git_link(str(repo.root), "test", WS, confirm=True)["envs"]["test"]["provider"] == "azure_devops"


def test_link_to_a_workspace_not_connected_asks_first(svc, repo):
    svc.fabric.answers[WS] = NOT_CONNECTED
    out = svc.fabric_git_link(str(repo.root), "dev", WS)
    assert out["needs_confirm"] and "isn't connected to Git" in out["message"]


def test_unlink(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    assert svc.fabric_git_unlink(str(repo.root), "dev")["envs"]["dev"] is None
    assert svc.fabric_git_links(str(repo.root))["envs"]["dev"] is None


def test_overview_without_a_link(svc, repo):
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["linked"] is False and svc.fabric.calls == []


def test_overview_lines_up_this_pc_the_remote_and_the_workspace(svc, repo):
    first = git(repo.root, "rev-parse", "HEAD~1")
    svc.fabric.answers[WS] = {**GITHUB, "gitSyncDetails": {"head": first, "lastSyncTime": "2026-11-03T09:00:00Z"}}
    svc.fabric_git_link(str(repo.root), "dev", WS)
    commit(repo.root, "c")  # one local commit, not pushed
    out = svc.fabric_git_overview(str(repo.root), "dev")
    a = out["alignment"]
    assert a["local"]["branch"] == "main" and a["local"]["ahead_of_remote"] == 1 and a["local"]["behind_remote"] == 0
    assert a["remote"]["name"] == "origin" and a["remote"]["ahead_of_workspace"] == 1
    assert a["remote"]["behind_workspace"] == 0
    assert a["workspace"]["commit"] == first and a["workspace"]["known_here"]
    assert any("doesn't update the workspace" in n for n in a["notes"])
    assert out["changed_in_fabric"] == []


def test_overview_says_when_fabric_now_follows_another_branch(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    svc.fabric.answers[WS] = {**GITHUB, "gitProviderDetails": {**GITHUB["gitProviderDetails"], "branchName": "release"}}
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["changed_in_fabric"] == ["branch"]
    assert any("hasn't fetched origin/release" in n for n in out["alignment"]["notes"])
    assert any("follows release" in n for n in out["alignment"]["notes"])


def test_overview_with_a_synced_commit_this_pc_does_not_have(svc, repo):
    svc.fabric.answers[WS] = {**GITHUB, "gitSyncDetails": {"head": "a" * 40}}
    svc.fabric_git_link(str(repo.root), "dev", WS)
    a = svc.fabric_git_overview(str(repo.root), "dev")["alignment"]
    assert a["workspace"]["known_here"] is False and "ahead_of_workspace" not in a["remote"]
    assert any("isn't on this PC" in n for n in a["notes"])


def test_overview_reports_a_fabric_error_without_raising(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    svc.fabric.answers[WS] = FabricError("GET /workspaces/x/git/connection failed with 403.", "InsufficientPrivileges", 403)
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["connection"] is None and "403" in out["problem"]


def test_overview_of_a_workspace_no_longer_connected(svc, repo):
    svc.fabric_git_link(str(repo.root), "dev", WS)
    svc.fabric.answers[WS] = NOT_CONNECTED
    out = svc.fabric_git_overview(str(repo.root), "dev")
    assert out["alignment"]["remote"] is None and "isn't connected to Git" in out["alignment"]["notes"][0]
