"""Remote addresses (git/remote.py): the refusals and provider links test_git.py leaves out. Nothing
here reaches a provider; the links only open its pages in the browser."""

from __future__ import annotations

import pytest

from fabric_desktop.git import remote
from fabric_desktop.git.cli import GitError


@pytest.mark.parametrize("url, message", [
    ("https:///team/repo.git", "has no server name"),
    ("C:relative\\repo", "That folder doesn't exist"),
    ("/no/such/folder/anywhere", "That folder doesn't exist"),
])
def test_an_address_git_cant_use_says_why(url, message):
    with pytest.raises(GitError, match=message):
        remote.check_url(url)


def test_a_folder_on_this_pc_is_an_address(tmp_path):
    assert remote.check_url(str(tmp_path)) == str(tmp_path)


@pytest.mark.parametrize("url, provider, web", [
    ("git@gitlab.com:group/sub/app.git", "gitlab", "https://gitlab.com/group/sub/app"),
    ("https://bitbucket.org/team/app.git", "bitbucket", "https://bitbucket.org/team/app"),
    ("https://contoso.visualstudio.com/Sales/_git/etl", "azure_devops", "https://dev.azure.com/contoso/Sales/_git/etl"),
    ("https://contoso.visualstudio.com/_git/etl", "git", None),  # no project before _git
])
def test_each_provider_gets_a_link_to_its_own_site(url, provider, web):
    info = remote.parse(url)
    assert info["provider"] == provider and info["web"] == web


def test_gitlab_links_name_the_branch_and_target():
    out = remote.links("https://gitlab.com/group/app.git", branch="feature/x", base="main", commit="abc")
    assert out["label"] == "GitLab"
    assert out["branch"] == "https://gitlab.com/group/app/-/tree/feature/x"
    assert out["commit"] == "https://gitlab.com/group/app/-/commit/abc"
    assert out["new_pull_request"] == ("https://gitlab.com/group/app/-/merge_requests/new?"
                                       "merge_request%5Bsource_branch%5D=feature%2Fx&merge_request%5Btarget_branch%5D=main")
    assert out["pull_requests"] == "https://gitlab.com/group/app/-/merge_requests?source_branch=feature%2Fx"
    bare = remote.links("https://gitlab.com/group/app.git")
    assert bare["branch"] is None and bare["new_pull_request"] is None


def test_bitbucket_links_name_the_branch_and_target():
    out = remote.links("https://bitbucket.org/team/app.git", branch="fix/1", base="develop", commit="def")
    assert out["branch"] == "https://bitbucket.org/team/app/branch/fix%2F1"
    assert out["commit"] == "https://bitbucket.org/team/app/commits/def"
    assert out["new_pull_request"] == "https://bitbucket.org/team/app/pull-requests/new?source=fix%2F1&dest=develop"
    assert out["pull_requests"] == "https://bitbucket.org/team/app/pull-requests"
    assert remote.links("https://bitbucket.org/team/app.git", branch="fix")["new_pull_request"].endswith("source=fix")


def test_an_address_shown_or_saved_has_no_sign_in_in_it():
    assert remote.safe_display("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert remote.safe_display("https://me:secret@git.example.com:8443/r.git") == "https://git.example.com:8443/r.git"
    assert remote.safe_display("https://git.example.com/r.git") == "https://git.example.com/r.git"
    assert remote.safe_display("") == ""
