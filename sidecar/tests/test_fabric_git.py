"""Fabric workspace Git awareness (Git-native Phase 2): the workspace's Git connection, the link kept in
fabric-desktop.toml, and this PC / the Git remote / the workspace lined up. Reads only: no Fabric write."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import artifacts, audit, fabric_git, server
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


def test_a_linked_link_file_is_refused(tmp_path):
    target = tmp_path / "elsewhere.toml"
    target.write_text(f'[workspaces.dev]\nworkspace_id = "{WS}"\n', encoding="utf-8")
    (tmp_path / "repo").mkdir()
    try:
        (tmp_path / "repo" / fabric_git.LINK_FILE).symlink_to(target)
    except OSError:
        pytest.skip("this system can't make links")
    out = fabric_git.read_links(tmp_path / "repo")
    assert out["envs"]["dev"] is None and "link to another file" in out["problem"]
    with pytest.raises(GitError):
        fabric_git.write_link(tmp_path / "repo", "dev", {"workspace_id": WS})


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
    assert link == {"workspace_id": WS, "workspace_name": "Sales Dev", "provider": "github",
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


# ---- which items differ (Get Status, Workspace.GitUpdate.All)

H1, H2 = "1" * 40, "2" * 40
NB, PL, LH, SM, DF = (f"{n}aaaaaaa-0000-4000-8000-00000000000{n}" for n in range(1, 6))


def change(logical, item_type, name, ws=None, remote=None, conflict="None"):
    out = {"itemMetadata": {"itemIdentifier": {"logicalId": logical}, "itemType": item_type, "displayName": name},
           "conflictType": conflict}
    if ws:
        out["workspaceChange"] = ws
    if remote:
        out["remoteChange"] = remote
    return out


# Shape from Microsoft Learn, Core > Git > Get Status (checked 2026-11-03).
STATUS = {"workspaceHead": H1, "remoteCommitHash": H2, "changes": [
    change(PL, "DataPipeline", "PL_Daily_Load", remote="Modified"),
    change(NB, "Notebook", "NB_Orders_Silver", ws="Modified", remote="Modified", conflict="Conflict"),
    change(LH, "Lakehouse", "LH_Silver", ws="Modified"),
    change(SM, "SemanticModel", "SM_Sales", ws="Added", remote="Added", conflict="SameChanges"),
    change(DF, "Datamart", "DM_Old", ws="Added"),
]}


def test_status_rows_states_and_order():
    out = fabric_git.status(STATUS)
    assert (out["workspace_head"], out["remote_commit"]) == (H1, H2)
    assert [(i["name"], i["state"]) for i in out["items"]] == [
        ("NB_Orders_Silver", "conflict"), ("PL_Daily_Load", "incoming"), ("DM_Old", "outgoing"),
        ("LH_Silver", "outgoing"), ("SM_Sales", "same_change")]
    assert out["counts"]["conflict"] == 1 and out["counts"]["incoming"] == 1 and out["counts"]["outgoing"] == 2
    assert not out["aligned"]


def test_status_rows_carry_registry_support_and_unknown_types_are_not_supported():
    rows = {i["name"]: i for i in fabric_git.status(STATUS)["items"]}
    assert rows["NB_Orders_Silver"]["git_support"] == "yes" and rows["NB_Orders_Silver"]["round_trip"] == "yes"
    assert rows["SM_Sales"]["git_support"] == "preview"
    assert rows["DM_Old"]["git_support"] == "no"


def test_both_sides_changed_without_a_conflict_is_still_called_out():
    out = fabric_git.status({"workspaceHead": H1, "remoteCommitHash": H2, "changes": [
        change(NB, "Notebook", "nb", ws="Modified", remote="Deleted")]})
    assert out["items"][0]["state"] == "both_changed"


def test_an_aligned_workspace():
    out = fabric_git.status({"workspaceHead": H1, "remoteCommitHash": H1, "changes": []})
    assert out["aligned"] and out["items"] == []


@pytest.mark.parametrize("raw", [None, "x", {"changes": "x"}, {"changes": [1, {"itemMetadata": "x"}]},
                                 {"changes": [{"itemMetadata": {"itemIdentifier": {"logicalId": "nope"}}}]}])
def test_odd_answers_never_invent_rows(raw):
    out = fabric_git.status(raw)
    assert out["items"] == [] and out["workspace_head"] == "" and out["remote_commit"] == ""


def test_undocumented_change_values_are_dropped():
    out = fabric_git.status({"changes": [change(NB, "Notebook", "nb", ws="Renamed", conflict="Maybe")]})
    row = out["items"][0]
    assert (row["workspace_change"], row["conflict"], row["state"]) == ("", "", "unchanged")


def test_fingerprint_changes_with_any_difference():
    a = fabric_git.status(STATUS)["fingerprint"]
    assert fabric_git.status(STATUS)["fingerprint"] == a
    moved = {**STATUS, "remoteCommitHash": "3" * 40}
    assert fabric_git.status(moved)["fingerprint"] != a
    fewer = {**STATUS, "changes": STATUS["changes"][1:]}
    assert fabric_git.status(fewer)["fingerprint"] != a


class StatusFabric(FakeFabric):
    def __init__(self, answers, status_answer):
        super().__init__(answers)
        self.status_answer = status_answer
        self.status_calls: list[str] = []

    def git_status(self, workspace_id):
        self.status_calls.append(workspace_id)
        if isinstance(self.status_answer, Exception):
            raise self.status_answer
        return self.status_answer


class ScopedSvc(Svc):
    def __init__(self, root, fabric):
        super().__init__(root, fabric)
        self.scopes_asked: list[list[str]] = []

    def _scoped(self, scopes):
        self.scopes_asked.append(scopes)
        return self.fabric


@pytest.fixture
def ssvc(tmp_path, repo):
    s = ScopedSvc(tmp_path / "app", StatusFabric({WS: GITHUB}, STATUS))
    s.root.mkdir()
    s.git_open(str(repo.root))
    return s


def test_status_needs_a_link_first(ssvc, repo):
    with pytest.raises(GitError, match="Link this repository"):
        ssvc.fabric_git_status(str(repo.root), "dev")
    assert ssvc.fabric.status_calls == [] and ssvc.scopes_asked == []


def test_status_asks_only_for_git_update_and_compares_the_fetched_remote(ssvc, repo):
    from fabric_desktop.auth import GIT_STATUS_SCOPES

    ssvc.fabric_git_link(str(repo.root), "dev", WS)
    out = ssvc.fabric_git_status(str(repo.root), "dev")
    assert ssvc.scopes_asked == [GIT_STATUS_SCOPES]
    assert GIT_STATUS_SCOPES == ["https://api.fabric.microsoft.com/Workspace.GitUpdate.All"]
    assert ssvc.fabric.status_calls == [WS]
    assert out["counts"]["conflict"] == 1
    assert out["remote_tracking"] == git(repo.root, "rev-parse", "origin/main")
    assert out["remote_fetched_matches"] is False  # Fabric's remote commit (H2) isn't what this PC fetched


def test_status_says_when_this_pc_fetched_the_same_remote_commit(ssvc, repo):
    ssvc.fabric_git_link(str(repo.root), "dev", WS)
    ssvc.fabric.status_answer = {**STATUS, "remoteCommitHash": git(repo.root, "rev-parse", "origin/main")}
    assert ssvc.fabric_git_status(str(repo.root), "dev")["remote_fetched_matches"] is True


def test_status_explains_a_workspace_no_longer_connected(ssvc, repo):
    ssvc.fabric_git_link(str(repo.root), "dev", WS)
    ssvc.fabric.status_answer = FabricError("x", "WorkspaceNotConnectedToGit", 400)
    with pytest.raises(FabricError, match="isn't connected to Git any more"):
        ssvc.fabric_git_status(str(repo.root), "dev")


def test_status_is_a_listed_read():
    assert "fabric_git_status" in server.METHODS and "fabric_git_status" in audit.READ_ONLY


@pytest.mark.parametrize("branch", ["--output=/tmp/x", "main..evil"])
def test_a_branch_name_that_looks_like_an_option_or_range_is_never_passed_to_git(ssvc, repo, monkeypatch, branch):
    ssvc.fabric_git_link(str(repo.root), "dev", WS)
    links = fabric_git.read_links(repo.root)["envs"]["dev"]
    fabric_git.write_link(repo.root, "dev", {**links, "branch": branch})
    seen: list[tuple] = []
    real = Repository._try
    monkeypatch.setattr(Repository, "_try", lambda self, *a, **k: (seen.append(a), real(self, *a, **k))[1])
    out = ssvc.fabric_git_status(str(repo.root), "dev")
    assert out["remote_tracking"] is None
    assert not any(branch in " ".join(a) for a in seen)


# ---- Update from Git: re-checked in the engine, a choice for conflicts, a typed confirm

def test_update_plan_without_a_conflict_choice_needs_one():
    plan = fabric_git.update_plan(fabric_git.status(STATUS))
    assert plan["needs_policy"] and plan["conflicts"] == 1 and not plan["nothing"]
    assert [i["name"] for i in plan["changes"]] == ["PL_Daily_Load"]
    assert [i["name"] for i in plan["overwrites"]] == ["LH_Silver"]


def test_update_plan_keep_workspace_and_take_git():
    st = fabric_git.status(STATUS)
    keep = fabric_git.update_plan(st, "PreferWorkspace")
    assert not keep["needs_policy"] and [i["name"] for i in keep["overwrites"]] == ["LH_Silver"]
    assert "NB_Orders_Silver" in [i["name"] for i in keep["kept"]]
    take = fabric_git.update_plan(st, "PreferRemote")
    assert [i["name"] for i in take["overwrites"]] == ["NB_Orders_Silver", "LH_Silver"]
    assert "lost" in take["overwrites"][0]["why"]
    kept = {i["name"]: i["why"] for i in take["kept"]}
    assert "doesn't carry" in kept["DM_Old"]


def test_a_change_made_only_in_the_workspace_counts_as_at_risk():
    # Learn doesn't say Update from Git keeps an uncommitted workspace change, so the plan doesn't promise it.
    for policy in ("PreferWorkspace", "PreferRemote"):
        plan = fabric_git.update_plan(fabric_git.status(STATUS), policy)
        risky = {i["name"]: i["why"] for i in plan["overwrites"]}
        assert "commit it in Fabric first" in risky["LH_Silver"]
        assert "LH_Silver" not in [i["name"] for i in plan["kept"]]


def test_update_plan_refuses_an_unknown_policy():
    with pytest.raises(GitError):
        fabric_git.update_plan(fabric_git.status(STATUS), "Overwrite")


def test_update_plan_with_nothing_incoming():
    st = fabric_git.status({"workspaceHead": H1, "remoteCommitHash": H1, "changes": [
        change(LH, "Lakehouse", "LH", ws="Modified")]})
    assert fabric_git.update_plan(st)["nothing"]


class UpdateFabric(StatusFabric):
    def __init__(self, answers, status_answer):
        super().__init__(answers, status_answer)
        self.updates: list[tuple] = []
        self.names = {WS: "Sales Dev"}

    def get_workspace(self, workspace_id):
        return {"id": workspace_id, "displayName": self.names.get(workspace_id, "")}

    def git_update_from_git(self, *args, **kw):
        self.updates.append((args, kw))
        return {}


@pytest.fixture
def usvc(tmp_path, repo):
    s = ScopedSvc(tmp_path / "app", UpdateFabric({WS: GITHUB}, STATUS))
    s.root.mkdir()
    s.git_open(str(repo.root))
    s.fabric_git_link(str(repo.root), "dev", WS, "Sales Dev")
    return s


def fp():
    return fabric_git.status(STATUS)["fingerprint"]


def test_update_writes_nothing_without_every_gate(usvc, repo):
    r = str(repo.root)
    assert usvc.fabric_git_update(r, "dev", "")["stale"]
    assert usvc.fabric_git_update(r, "dev", "old")["stale"]
    assert usvc.fabric_git_update(r, "dev", fp())["needs_choice"]
    out = usvc.fabric_git_update(r, "dev", fp(), "PreferRemote")
    assert out["needs_override"] and not out["allow_override"]
    assert usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", "Sales Dev")["needs_override"]
    out = usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", allow_override=True)
    assert out["needs_confirm"] and out["confirm_text"] == "Sales Dev" and not out["name_differs"]
    assert usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", "sales dev", True)["needs_confirm"]  # exact
    assert usvc.fabric.updates == []


def test_update_runs_with_a_fresh_preview_a_choice_and_the_name(usvc, repo):
    out = usvc.fabric_git_update(str(repo.root), "dev", fp(), "PreferWorkspace", "Sales Dev", True)
    assert out["done"]
    assert usvc.fabric.updates == [((WS, H2, H1, "PreferWorkspace"), {"allow_override": True})]
    assert usvc.fabric.status_calls == [WS]  # status read again right before the write


def test_the_name_to_type_is_fabrics_not_the_link_files(usvc, repo):
    r = str(repo.root)
    # Someone with push rights edited fabric-desktop.toml; Fabric still calls the workspace "Sales Prod".
    usvc.fabric.names[WS] = "Sales Prod"
    out = usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", "Sales Dev", True)
    assert out["needs_confirm"] and out["confirm_text"] == "Sales Prod"
    assert out["name_differs"] and out["linked_name"] == "Sales Dev"
    assert usvc.fabric.updates == []
    assert usvc.fabric_git_update(r, "dev", fp(), "PreferRemote", "Sales Prod", True)["done"]
    assert len(usvc.fabric.updates) == 1


def test_override_is_sent_only_when_asked(usvc, repo):
    # Only a conflict, kept on the workspace side: Fabric still counts it as incoming, so consent is asked.
    usvc.fabric.status_answer = {"workspaceHead": H1, "remoteCommitHash": H2, "changes": [
        change(NB, "Notebook", "NB", ws="Modified", remote="Modified", conflict="Conflict")]}
    f = fabric_git.status(usvc.fabric.status_answer)["fingerprint"]
    r = str(repo.root)
    out = usvc.fabric_git_update(r, "dev", f, "PreferWorkspace", "Sales Dev")
    assert out["needs_override"] and out["plan"]["overwrites"] == [] and usvc.fabric.updates == []
    assert usvc.fabric_git_update(r, "dev", f, "PreferWorkspace", "Sales Dev", allow_override=True)["done"]
    assert usvc.fabric.updates == [((WS, H2, H1, "PreferWorkspace"), {"allow_override": True})]


def test_update_stops_when_fabric_changed_since_the_preview(usvc, repo):
    old = fp()
    usvc.fabric.status_answer = {**STATUS, "remoteCommitHash": "3" * 40}
    out = usvc.fabric_git_update(str(repo.root), "dev", old, "PreferRemote", "Sales Dev")
    assert out["stale"] and out["status"]["remote_commit"] == "3" * 40 and not out["done"]
    assert usvc.fabric.updates == []


def test_update_with_nothing_to_bring_in(usvc, repo):
    usvc.fabric.status_answer = {"workspaceHead": H1, "remoteCommitHash": H1, "changes": []}
    f = fabric_git.status(usvc.fabric.status_answer)["fingerprint"]
    assert usvc.fabric_git_update(str(repo.root), "dev", f, "", "Sales Dev")["nothing"]
    assert usvc.fabric.updates == []


def test_update_is_audited_not_read_only():
    assert "fabric_git_update" in server.METHODS and "fabric_git_update" not in audit.READ_ONLY


def test_update_body_matches_microsoft_learn():
    from fabric_desktop.fabric_api import FabricClient

    sent = {}

    class Resp:
        status_code = 200
        content = b"{}"
        headers: dict = {}

        def json(self):
            return {}

    class Session:
        def request(self, method, url, headers=None, timeout=None, json=None, **kw):
            sent.update(method=method, url=url, json=json)
            return Resp()

    FabricClient(lambda: "t", session=Session()).git_update_from_git(WS, H2, H1, "PreferRemote", True)
    assert sent["method"] == "POST" and sent["url"].endswith(f"/workspaces/{WS}/git/updateFromGit")
    assert sent["json"] == {"remoteCommitHash": H2, "workspaceHead": H1, "options": {"allowOverrideItems": True},
                            "conflictResolution": {"conflictResolutionType": "Workspace",
                                                   "conflictResolutionPolicy": "PreferRemote"}}
def test_status_rows_carry_the_registry_limits():
    rows = {r["name"]: r for r in fabric_git.status(STATUS)["items"]}
    assert any("never go" in limit for limit in rows["LH_Silver"]["limits"])
    assert rows["LH_Silver"]["limits"] == list(artifacts.capability("Lakehouse").limits)
