"""Remote addresses: which are allowed, and links to the repository on its provider's site.

Ordinary Git work never needs a provider: every operation goes through ``git``. The links here
only open the provider's own pages (the repository, a branch, a commit, a new pull request) in
the browser. Recognized: GitHub, Azure DevOps, GitLab and Bitbucket; anything else is generic
Git, with a link only when its address is https.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .cli import GitError

_SCP = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>(?!/)[^\s]+)$")


def check_url(url: str) -> str:
    """The address, stripped, when Git may use it; raises GitError (kind ``invalid``) otherwise."""
    url = (url or "").strip()
    if not url:
        raise GitError("Enter the repository's address.", kind="invalid")
    if url.startswith("-") or any(c in url for c in "\0\r\n\t ") or "::" in url:
        raise GitError("That isn't a repository address Git can use.", kind="invalid")
    if _SCP.match(url):
        return url
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "http":
        raise GitError("Use the https address: http sends your sign-in unencrypted.", kind="invalid")
    if scheme in ("https", "ssh"):
        if not parts.hostname:
            raise GitError("That address has no server name.", kind="invalid")
        if parts.password is not None or (scheme == "https" and parts.username and _looks_like_token(parts.username)):
            raise GitError("Don't put a password or token in the address. Git asks for your sign-in through "
                           "Git Credential Manager and keeps it in Windows Credential Manager.", kind="invalid")
        return url
    if scheme == "file":
        return url
    if len(scheme) == 1 or not scheme:  # C:\repos\x or /srv/x: a folder on this PC or a share
        p = Path(url)
        if p.is_absolute() and p.exists():
            return str(p)
        raise GitError("That folder doesn't exist.", kind="invalid")
    raise GitError("Only https, ssh and local folder addresses are allowed.", kind="invalid")


def _looks_like_token(user: str) -> bool:
    return len(user) >= 30 or user.startswith(("ghp_", "github_pat_", "gho_"))


def repo_name(url: str) -> str:
    """A folder name for a clone of ``url``: its last path part without .git."""
    m = _SCP.match(url)
    path = m.group("path") if m else (urlsplit(url).path if "://" in url else url)
    name = re.split(r"[\\/]", path.rstrip("/\\"))[-1]
    name = name[:-4] if name.lower().endswith(".git") else name
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip(".-")
    return name or "repository"


def parse(url: str) -> dict[str, Any]:
    """{provider, host, web, owner, repo, ...} for a remote address. ``web`` is None when no
    browser link can be made. The address in the result has no user name or password."""
    url = (url or "").strip()
    host, path = "", ""
    m = _SCP.match(url)
    if m:
        host, path = m.group("host").lower(), m.group("path")
    elif "://" in url:
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path
    path = path.strip("/")
    path = path[:-4] if path.lower().endswith(".git") else path
    segs = [s for s in path.split("/") if s]
    out: dict[str, Any] = {"provider": "git", "host": host or None, "web": None}

    if host in ("github.com", "www.github.com") or host.endswith(".ghe.com"):
        if len(segs) >= 2:
            out.update(provider="github", owner=segs[0], repo=segs[1], web=f"https://{host}/{segs[0]}/{segs[1]}")
        return out
    if host == "ssh.dev.azure.com" and len(segs) >= 4 and segs[0] == "v3":
        org, project, repo = segs[1], segs[2], segs[3]
        out.update(provider="azure_devops", owner=org, project=project, repo=repo,
                   web=f"https://dev.azure.com/{org}/{project}/_git/{repo}")
        return out
    if host == "dev.azure.com" and "_git" in segs:
        i = segs.index("_git")
        if i >= 2 and len(segs) > i + 1:
            org, project, repo = segs[0], segs[i - 1], segs[i + 1]
            out.update(provider="azure_devops", owner=org, project=project, repo=repo,
                       web=f"https://dev.azure.com/{org}/{project}/_git/{repo}")
        return out
    if host.endswith(".visualstudio.com") and "_git" in segs:
        i = segs.index("_git")
        org = host.split(".")[0]
        if i >= 1 and len(segs) > i + 1:
            project, repo = segs[i - 1], segs[i + 1]
            out.update(provider="azure_devops", owner=org, project=project, repo=repo,
                       web=f"https://dev.azure.com/{org}/{project}/_git/{repo}")
        return out
    if host == "gitlab.com" and len(segs) >= 2:
        out.update(provider="gitlab", owner="/".join(segs[:-1]), repo=segs[-1], web=f"https://gitlab.com/{'/'.join(segs)}")
        return out
    if host == "bitbucket.org" and len(segs) >= 2:
        out.update(provider="bitbucket", owner=segs[0], repo=segs[1], web=f"https://bitbucket.org/{segs[0]}/{segs[1]}")
        return out
    if url.lower().startswith("https://") and host and segs:
        out["web"] = f"https://{host}/{'/'.join(segs)}"
    return out


PROVIDER_LABELS = {"github": "GitHub", "azure_devops": "Azure DevOps", "gitlab": "GitLab", "bitbucket": "Bitbucket",
                   "git": "Git"}


def links(url: str, branch: str | None = None, base: str | None = None, commit: str | None = None) -> dict[str, Any]:
    """Browser links on the provider's site: repository, branch, commit, new pull request and
    the pull requests for this branch. Missing when the provider is unknown."""
    info = parse(url)
    web, provider = info["web"], info["provider"]
    out: dict[str, Any] = {"provider": provider, "label": PROVIDER_LABELS[provider], "repository": web,
                           "branch": None, "commit": None, "new_pull_request": None, "pull_requests": None}
    if not web:
        return out
    b = quote(branch, safe="") if branch else None
    base_q = quote(base, safe="") if base else None
    if provider == "github":
        out["branch"] = f"{web}/tree/{quote(branch, safe='/')}" if branch else None
        out["commit"] = f"{web}/commit/{commit}" if commit else None
        if branch:
            out["new_pull_request"] = (f"{web}/compare/{quote(base, safe='/')}...{quote(branch, safe='/')}?expand=1"
                                       if base else f"{web}/compare/{quote(branch, safe='/')}?expand=1")
            out["pull_requests"] = f"{web}/pulls?q={quote(f'is:pr head:{branch}', safe=':')}"
    elif provider == "azure_devops":
        out["branch"] = f"{web}?version=GB{b}" if branch else None
        out["commit"] = f"{web}/commit/{commit}" if commit else None
        if branch:
            out["new_pull_request"] = f"{web}/pullrequestcreate?sourceRef={b}" + (f"&targetRef={base_q}" if base else "")
            out["pull_requests"] = f"{web}/pullrequests?_a=mine"
    elif provider == "gitlab":
        out["branch"] = f"{web}/-/tree/{quote(branch, safe='/')}" if branch else None
        out["commit"] = f"{web}/-/commit/{commit}" if commit else None
        if branch:
            out["new_pull_request"] = (f"{web}/-/merge_requests/new?merge_request%5Bsource_branch%5D={b}"
                                       + (f"&merge_request%5Btarget_branch%5D={base_q}" if base else ""))
            out["pull_requests"] = f"{web}/-/merge_requests?source_branch={b}"
    elif provider == "bitbucket":
        out["branch"] = f"{web}/branch/{b}" if branch else None
        out["commit"] = f"{web}/commits/{commit}" if commit else None
        if branch:
            out["new_pull_request"] = f"{web}/pull-requests/new?source={b}" + (f"&dest={base_q}" if base else "")
            out["pull_requests"] = f"{web}/pull-requests"
    return out


def safe_display(url: str) -> str:
    """The address without any user name or password, for showing and saving."""
    m = _SCP.match(url or "")
    if m:
        return url
    if "://" in (url or ""):
        parts = urlsplit(url)
        if parts.username or parts.password:
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            return parts._replace(netloc=netloc).geturl()
    return url
