"""Running the person's own ``git``, safely.

Every call is an argument list (never a shell), runs in one repository folder with ``-C``, and
has a timeout. Git never prompts in a terminal (there is none); a remote that needs a sign-in
uses Git's credential helper, which on Windows is Git Credential Manager and opens its own
window. SSH runs in batch mode unless the person set their own SSH command, so a missing key
fails with a message instead of waiting forever. Only ``https``, ``ssh`` and ``file`` transports
are allowed, so an address can't run a program (``ext::``). Pathspecs are literal, so a file name
can't be read as a Git pattern. Everything Git prints is redacted before it leaves here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .secrets import redact

TIMEOUT = 60
NETWORK_TIMEOUT = 1800
ALLOWED_PROTOCOLS = "file:https:ssh"


class GitError(RuntimeError):
    """A Git command failed. ``str()`` says what happened in plain words; ``details`` is Git's
    own (redacted) message."""

    def __init__(self, message: str, details: str = "", kind: str = "failed"):
        super().__init__(message)
        self.details = details
        self.kind = kind


def git_path() -> str | None:
    return shutil.which("git")


def version() -> str | None:
    exe = git_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out.stdout.decode("utf-8", "replace"))
    return m.group(1) if m else None


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def environment(read_only: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",          # no terminal to ask in; the credential helper still can
        "GIT_ALLOW_PROTOCOL": ALLOWED_PROTOCOLS,
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_MERGE_AUTOEDIT": "no",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
        "LANGUAGE": "C",
    })
    if not env.get("GIT_SSH_COMMAND") and not env.get("GIT_SSH"):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"  # overridden by core.sshCommand when set
    if read_only:
        env["GIT_OPTIONAL_LOCKS"] = "0"     # a status shouldn't take the index lock
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):  # every call names its own folder
        env.pop(key, None)
    return env


# -c options on every call: plain output with real file names.
BASE_OPTIONS = ["-c", "core.quotepath=false", "-c", "color.ui=false", "-c", "core.pager=cat",
                "-c", "advice.detachedHead=false"]


def command(cwd: Path | str | None, args: list[str]) -> list[str]:
    exe = git_path()
    if not exe:
        raise GitError("Git isn't installed on this PC. Install Git for Windows (git-scm.com), then try again.",
                       kind="no_git")
    for a in args:
        if not isinstance(a, str) or "\0" in a:
            raise GitError("A Git argument wasn't plain text.", kind="invalid")
    pre = [exe, *BASE_OPTIONS]
    if cwd is not None:
        pre += ["-C", str(cwd)]
    return pre + args


def run(cwd: Path | str | None, args: list[str], *, timeout: float = TIMEOUT, input: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,), read_only: bool = False) -> subprocess.CompletedProcess:
    """Runs git and returns the finished process; raises GitError with a plain message when the
    exit code isn't one of ``ok_codes``."""
    cmd = command(cwd, args)
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input,
                             stdin=None if input is not None else subprocess.DEVNULL,
                             env=environment(read_only), creationflags=_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired:
        raise GitError(f"Git took longer than {int(timeout)} seconds and was stopped.", kind="timeout") from None
    except OSError as exc:
        raise GitError(f"Git couldn't start: {exc}", kind="no_git") from None
    if out.returncode not in ok_codes:
        stderr = out.stderr.decode("utf-8", "replace")
        raise explain(stderr or out.stdout.decode("utf-8", "replace"), args)
    return out


def text(cwd: Path | str | None, args: list[str], **kw: Any) -> str:
    return run(cwd, args, **kw).stdout.decode("utf-8", "replace")


# Known causes, matched on Git's own message, in the words people use. First match wins.
_KNOWN: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"not a git repository", re.I), "not_repo", "This folder isn't a Git repository."),
    (re.compile(r"Please tell me who you are|unable to auto-detect email|empty ident name", re.I), "identity",
     "Git needs your name and email for commits. Set them in Source control > Repository settings."),
    (re.compile(r"Authentication failed|could not read (Username|Password)|terminal prompts disabled|"
                r"HTTP Basic: Access denied|returned error: 401|invalid credentials|Logon failed", re.I), "auth",
     "Git couldn't sign in to the remote. Git signs in through Git Credential Manager: sign in when its "
     "window opens, or check that your account can read this repository."),
    (re.compile(r"returned error: 403|Permission to .* denied|access denied|not authorized", re.I), "forbidden",
     "Your account can't do that in this repository. Ask its owner for write access."),
    (re.compile(r"Permission denied \(publickey|no such identity|sign_and_send_pubkey", re.I), "ssh_key",
     "The SSH server didn't accept your key. Add your key to the SSH agent (ssh-add) and to your account on "
     "the Git host, or clone with the HTTPS address instead."),
    (re.compile(r"Host key verification failed", re.I), "ssh_host",
     "This PC doesn't know the SSH server's host key yet. Connect once from a terminal (ssh -T git@host) to "
     "trust it, or use the HTTPS address."),
    (re.compile(r"Could not resolve host|Could not resolve hostname|Failed to connect|Connection timed out|"
                r"Connection refused|Network is unreachable|unable to access", re.I), "network",
     "Couldn't reach the Git server. Check the internet connection and the address, then try again."),
    (re.compile(r"Repository not found|repository '.*' not found|does not appear to be a git repository|"
                r"returned error: 404", re.I), "not_found",
     "The remote repository wasn't found. Check the address, and that your account can see it."),
    (re.compile(r"\[rejected\].*\((fetch first|non-fast-forward)\)|Updates were rejected", re.I), "rejected",
     "The remote has commits you don't have yet. Pull first, then push."),
    (re.compile(r"protected branch|GH006|TF402455", re.I), "protected",
     "That branch is protected on the remote. Push to a new branch and open a pull request instead."),
    (re.compile(r"Not possible to fast-forward|diverging branches|have diverged", re.I), "diverged",
     "Your branch and the remote both have new commits. Pull with Merge to combine them."),
    (re.compile(r"would be overwritten by (merge|checkout)|Your local changes to the following files", re.I), "overwrite",
     "Git stopped so your uncommitted changes aren't overwritten. Commit them, or put them aside, and try again."),
    (re.compile(r"untracked working tree files would be (overwritten|removed)", re.I), "overwrite_untracked",
     "Files you haven't added to Git would be overwritten. Move or commit them, and try again."),
    (re.compile(r"CONFLICT|Automatic merge failed|fix conflicts", re.I), "conflict",
     "Some files changed both here and on the remote. Fix the conflicts, stage the files, then commit."),
    (re.compile(r"You have not concluded your merge|MERGE_HEAD exists|unmerged files|you need to resolve", re.I), "merging",
     "A merge is still in progress. Finish it (fix conflicts, stage, commit) or undo it first."),
    (re.compile(r"no upstream|has no upstream branch|There is no tracking information", re.I), "no_upstream",
     "This branch isn't linked to a remote branch yet. Push it once to link it."),
    (re.compile(r"not fully merged", re.I), "unmerged_branch",
     "That branch has commits no other branch holds. Deleting it would lose them."),
    (re.compile(r"already exists", re.I), "exists", "That already exists."),
    (re.compile(r"nothing to commit|no changes added to commit", re.I), "nothing", "There's nothing staged to commit."),
    (re.compile(r"pathspec .* did not match|invalid reference|unknown revision|bad revision|not a valid object", re.I),
     "unknown_ref", "Git couldn't find that branch, commit or file."),
    (re.compile(r"transport '.*' not allowed|protocol '.*' is not supported", re.I), "protocol",
     "Only https, ssh and local folder addresses are allowed."),
]


def explain(stderr: str, args: list[str] | None = None) -> GitError:
    details = redact(stderr.strip())
    for pattern, kind, message in _KNOWN:
        if pattern.search(details):
            return GitError(message, details, kind)
    first = next((l.strip() for l in details.splitlines() if l.strip().startswith(("fatal:", "error:"))), "")
    message = first.split(":", 1)[1].strip() if first else "Git couldn't do that."
    return GitError(message[:1].upper() + message[1:] if message else "Git couldn't do that.", details)
