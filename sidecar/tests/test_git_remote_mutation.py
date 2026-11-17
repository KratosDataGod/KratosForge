"""git/remote.py paths that mutation testing (batch 77, docs/quality/mutation-testing.md) showed no test
pinned: the empty, http and file addresses, where a sign-in name starts to look like a token, folder names
for a clone, the owner and repository each provider's address names, and the links with no branch."""

from __future__ import annotations

import pytest

from fabric_desktop.git import remote
from fabric_desktop.git.cli import GitError


@pytest.mark.parametrize("url", ["", "   ", None])
def test_an_empty_address_asks_for_one(url):
    with pytest.raises(GitError, match=r"^Enter the repository's address\.$"):
        remote.check_url(url)


def test_an_http_address_is_refused_with_the_reason():
    with pytest.raises(GitError, match="Use the https address: http sends your sign-in unencrypted"):
        remote.check_url("http://git.example.com/team/repo.git")


def test_a_file_address_is_allowed_as_given():
    assert remote.check_url("  file:///srv/git/repo.git ") == "file:///srv/git/repo.git"


@pytest.mark.parametrize("user, refused", [
    ("a" * 29, False), ("a" * 30, True),
    ("ghp_x", True), ("github_pat_x", True), ("gho_x", True), ("me", False),
])
def test_a_sign_in_name_that_looks_like_a_token_is_refused(user, refused):
    url = f"https://{user}@github.com/o/r.git"
    if refused:
        with pytest.raises(GitError, match="Don't put a password or token in the address"):
            remote.check_url(url)
    else:
        assert remote.check_url(url) == url


@pytest.mark.parametrize("url, name", [
    ("git@github.com:team/Sales.git", "Sales"),
    ("https://git.example.com/team/etl.git?ref=x", "etl"),
    ("C:\\repos\\Local Copy", "Local-Copy"),
])
def test_a_clone_folder_is_named_from_the_last_part_of_the_address(url, name):
    assert remote.repo_name(url) == name


def test_an_address_with_no_server_has_no_host():
    assert remote.parse("not an address")["host"] is None


@pytest.mark.parametrize("url, owner, repo", [
    ("https://github.com/acme/widgets.git", "acme", "widgets"),
    ("https://bitbucket.org/team/app/src/main", "team", "app"),
    ("https://gitlab.com/group/sub/app.git", "group/sub", "app"),
])
def test_each_provider_names_the_owner_and_repository(url, owner, repo):
    info = remote.parse(url)
    assert info["owner"] == owner and info["repo"] == repo


@pytest.mark.parametrize("url, web", [
    ("https://dev.azure.com/contoso/_git/etl", None),  # no project between the organization and _git
    ("https://dev.azure.com/contoso/Sales/_git", None),  # no repository after _git
    ("https://dev.azure.com/contoso/Sales/_git/etl", "https://dev.azure.com/contoso/Sales/_git/etl"),
    ("https://contoso.visualstudio.com/Sales/_git", None),  # no repository after _git
    ("https://contoso.visualstudio.com/Sales/_git/etl", "https://dev.azure.com/contoso/Sales/_git/etl"),
])
def test_an_azure_devops_address_needs_a_project_and_a_repository(url, web):
    info = remote.parse(url)
    assert info["web"] == web
    if web:
        assert (info["owner"], info["project"], info["repo"]) == ("contoso", "Sales", "etl")


def test_github_links_with_no_branch_have_no_branch_or_pull_request_links():
    out = remote.links("https://github.com/acme/widgets.git")
    assert out["repository"] == "https://github.com/acme/widgets"
    assert out["branch"] is None and out["new_pull_request"] is None and out["pull_requests"] is None


def test_azure_devops_links_name_the_branch_commit_and_target():
    url = "https://dev.azure.com/contoso/Sales/_git/etl"
    out = remote.links(url, branch="feature/x", base="main", commit="abc")
    assert out["branch"] == f"{url}?version=GBfeature%2Fx"
    assert out["commit"] == f"{url}/commit/abc"
    assert out["new_pull_request"] == f"{url}/pullrequestcreate?sourceRef=feature%2Fx&targetRef=main"
    bare = remote.links(url)
    assert bare["branch"] is None and bare["commit"] is None and bare["new_pull_request"] is None


def test_an_address_with_only_a_password_shows_without_it():
    assert remote.safe_display("https://:secret@git.example.com/r.git") == "https://git.example.com/r.git"  # pragma: allowlist secret (fake test value)
