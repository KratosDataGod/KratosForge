"""One Git repository on this PC: status, branches, staging, commits, history and diffs.

Everything here is local: no call talks to a remote (fetch, pull, push and clone are in
``ops``). Steps that can lose work refuse unless the caller passes ``confirm=True``, and the UI
asks the person first: deleting a branch, discarding changes, undoing a merge. Switching
branches with uncommitted changes refuses until the caller says whether to put them aside
(a stash, which keeps them) or bring them along.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import cli, secrets
from .cli import GitError
from .remote import check_url, links, parse, safe_display

MAX_DIFF_LINES = 5000
MAX_FILE_BYTES = 5_000_000
LOG_LIMIT = 200
_SHA = re.compile(r"^[0-9a-fA-F]{4,64}$")
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Literal pathspecs (cli.environment) stop ``stash -u`` from clearing the files it saved, so a
# stash, which takes no file names from anyone, turns them off.
_STASH = ("--no-literal-pathspecs", "stash")

STATE_WORDS = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "T": "type changed",
               "U": "conflict", "?": "untracked"}


def confirm_needed(what: str) -> GitError:
    return GitError(f"{what} Confirm to go ahead.", kind="confirm")


def init(path: str | Path, branch: str = "main") -> "Repository":
    """Makes ``path`` a new Git repository (creating the folder when its parent exists). Refuses a
    folder that is already inside a repository, so an existing one is never re-initialized."""
    p = _folder(path, must_exist=False)
    if not p.parent.exists():
        raise GitError(f"The folder {p.parent} doesn't exist.", kind="invalid")
    if p.exists() and not p.is_dir():
        raise GitError(f"{p} is a file, not a folder.", kind="invalid")
    if p.exists():
        inside = _toplevel(p)
        if inside is not None:
            raise GitError(f"{p} is already in the Git repository at {inside}. Open that one instead.", kind="exists")
    check_branch_name(branch)
    p.mkdir(exist_ok=True)
    cli.run(p, ["init", "-q", "-b", branch])
    return Repository(p)


def _folder(path: str | Path, must_exist: bool = True) -> Path:
    if not path or not isinstance(path, (str, Path)) or "\0" in str(path):
        raise GitError("Choose a folder.", kind="invalid")
    p = Path(os.path.expandvars(os.path.expanduser(str(path).strip())))
    if not p.is_absolute():
        raise GitError("Use the folder's full path, such as C:\\Users\\you\\source\\project.", kind="invalid")
    p = p.resolve()
    if must_exist and not p.is_dir():
        raise GitError(f"The folder {p} doesn't exist.", kind="invalid")
    return p


def _toplevel(p: Path) -> Path | None:
    try:
        out = cli.text(p, ["rev-parse", "--show-toplevel"], read_only=True).strip()
    except GitError:
        return None
    return Path(out).resolve() if out else None


def check_branch_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name.startswith("-") or len(name) > 244:
        raise GitError("Enter a branch name, such as feature/orders.", kind="invalid")
    try:
        cli.run(None, ["check-ref-format", "--branch", name], read_only=True)
    except GitError:
        raise GitError(f"{name!r} can't be a branch name. Use letters, numbers, - _ / and no spaces.",
                       kind="invalid") from None
    return name


class Repository:
    def __init__(self, path: str | Path):
        p = _folder(path)
        top = _toplevel(p)
        if top is None:
            raise GitError(f"{p} isn't in a Git repository. Initialize it, or clone one.", kind="not_repo")
        self.root = top

    # ---- helpers

    def git(self, *args: str, **kw: Any) -> str:
        return cli.text(self.root, list(args), **kw)

    def _try(self, *args: str, **kw: Any) -> str | None:
        try:
            return self.git(*args, read_only=True, **kw)
        except GitError:
            return None

    def rel(self, path: str) -> str:
        """A repository-relative path in Git's form, refusing anything outside the repository or in .git."""
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise GitError("No file was given.", kind="invalid")
        raw = path.strip().replace("\\", "/")
        p = Path(raw)
        if p.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            try:
                raw = p.resolve().relative_to(self.root).as_posix()
            except ValueError:
                raise GitError(f"{path} isn't in this repository.", kind="invalid") from None
        parts = PurePosixPath(raw).parts
        clean: list[str] = []
        for part in parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not clean:
                    raise GitError(f"{path} isn't in this repository.", kind="invalid")
                clean.pop()
                continue
            clean.append(part)
        if not clean or clean[0].lower() == ".git":
            raise GitError(f"{path} can't be changed from here.", kind="invalid")
        rel = "/".join(clean)
        if not (self.root / rel).resolve().is_relative_to(self.root):
            raise GitError(f"{path} isn't in this repository.", kind="invalid")
        return rel

    def rels(self, paths: list[str]) -> list[str]:
        if not paths:
            raise GitError("Choose at least one file.", kind="invalid")
        return [self.rel(p) for p in paths]

    def git_path(self, name: str) -> Path:
        p = Path(self.git("rev-parse", "--git-path", name, read_only=True).strip())
        return p if p.is_absolute() else self.root / p

    def head(self) -> str | None:
        return (self._try("rev-parse", "--verify", "-q", "HEAD") or "").strip() or None

    def branch(self) -> str | None:
        return (self._try("symbolic-ref", "--short", "-q", "HEAD") or "").strip() or None

    def resolve(self, rev: str) -> str:
        """The full commit id for a commit id, branch or tag; refuses anything that looks like an option."""
        rev = (rev or "").strip()
        if not rev or rev.startswith("-") or any(c in rev for c in " \t\r\n\0:") or ".." in rev:
            raise GitError("That isn't a commit or branch.", kind="invalid")
        out = self._try("rev-parse", "--verify", "-q", "--end-of-options", f"{rev}^{{commit}}")
        if not out:
            raise GitError(f"Git couldn't find {rev}.", kind="unknown_ref")
        return out.strip()

    def merging(self) -> bool:
        return self.git_path("MERGE_HEAD").exists()

    # ---- status

    def status(self) -> dict[str, Any]:
        raw = self.git("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", read_only=True)
        info: dict[str, Any] = {"commit": None, "branch": None, "upstream": None, "ahead": None, "behind": None}
        staged, unstaged, untracked, conflicts = [], [], [], []
        entries = raw.split("\0")
        i = 0
        while i < len(entries):
            e = entries[i]
            i += 1
            if not e:
                continue
            if e.startswith("# "):
                key, _, value = e[2:].partition(" ")
                if key == "branch.oid":
                    info["commit"] = None if value == "(initial)" else value
                elif key == "branch.head":
                    info["branch"] = None if value == "(detached)" else value
                elif key == "branch.upstream":
                    info["upstream"] = value
                elif key == "branch.ab":
                    a, b = value.split()
                    info["ahead"], info["behind"] = int(a), -int(b)
                continue
            kind = e[0]
            if kind == "?":
                untracked.append({"path": e[2:], "state": "untracked", "code": "U"})
            elif kind in "12":
                fields = e.split(" ", 8 if kind == "1" else 9)
                xy, path = fields[1], fields[-1]
                orig = None
                if kind == "2":
                    orig = entries[i]
                    i += 1
                if xy[0] != ".":
                    staged.append(_entry(path, xy[0], orig))
                if xy[1] != ".":
                    unstaged.append(_entry(path, xy[1], None))
            elif kind == "u":
                fields = e.split(" ", 10)
                conflicts.append({"path": fields[-1], "state": "conflict", "code": "C", "how": _conflict_kind(fields[1])})
        changed = len(staged) + len(unstaged) + len(untracked) + len(conflicts)
        fetch_head = self.git_path("FETCH_HEAD")
        fetched = (dt.datetime.fromtimestamp(fetch_head.stat().st_mtime, dt.timezone.utc).isoformat()
                   if fetch_head.exists() else None)
        remotes = self.remotes()
        return {**info, "root": str(self.root), "name": self.root.name,
                "staged": staged, "unstaged": unstaged, "untracked": untracked, "conflicts": conflicts,
                "changed": changed, "dirty": changed > 0, "merging": self.merging(),
                "stashes": len([l for l in (self._try("stash", "list") or "").splitlines() if l.strip()]),
                "remotes": remotes, "fetched_at": fetched,
                "links": self._links(remotes, info["branch"], info["commit"]),
                "summary": _summary(info, changed)}

    def _links(self, remotes: list[dict[str, Any]], branch: str | None, commit: str | None) -> dict[str, Any] | None:
        remote = self._push_remote(remotes)
        if not remote:
            return None
        return links(remote["url"], branch, self.default_branch(remote["name"]), commit)

    def _push_remote(self, remotes: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        remotes = self.remotes() if remotes is None else remotes
        if not remotes:
            return None
        branch = self.branch()
        configured = (self._try("config", "--get", f"branch.{branch}.remote") or "").strip() if branch else ""
        return next((r for r in remotes if r["name"] == configured), None) or next(
            (r for r in remotes if r["name"] == "origin"), remotes[0])

    def default_branch(self, remote: str = "origin") -> str | None:
        """The remote's default branch as of the last fetch (origin/HEAD), else main or master."""
        head = (self._try("symbolic-ref", "--short", "-q", f"refs/remotes/{remote}/HEAD") or "").strip()
        if head.startswith(f"{remote}/"):
            return head[len(remote) + 1:]
        for name in ("main", "master"):
            if self._try("rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{name}") or \
               self._try("rev-parse", "--verify", "-q", f"refs/heads/{name}"):
                return name
        return None

    def remotes(self) -> list[dict[str, Any]]:
        out = []
        for name in (self._try("remote") or "").split():
            # The address as configured (what the person typed or cloned), not after insteadOf rewrites.
            url = (self._try("config", "--get", f"remote.{name}.url") or "").strip()
            info = parse(url)
            out.append({"name": name, "url": safe_display(url), "provider": info["provider"], "web": info["web"]})
        return out

    def add_remote(self, name: str, url: str) -> list[dict[str, Any]]:
        name = (name or "origin").strip()
        if not _REMOTE_NAME.match(name):
            raise GitError("A remote name is letters, numbers, . _ and -, such as origin.", kind="invalid")
        if name in [r["name"] for r in self.remotes()]:
            raise GitError(f"This repository already has a remote called {name}.", kind="exists")
        self.git("remote", "add", "--", name, check_url(url))
        return self.remotes()

    # ---- identity and settings

    def identity(self) -> dict[str, Any]:
        def get(key: str, *scope: str) -> str | None:
            return (self._try("config", *scope, "--get", key) or "").strip() or None
        return {"name": get("user.name"), "email": get("user.email"),
                "local_name": get("user.name", "--local"), "local_email": get("user.email", "--local")}

    def set_identity(self, name: str, email: str) -> dict[str, Any]:
        """Sets the commit name and email for this repository only (its own .git/config)."""
        name, email = (name or "").strip(), (email or "").strip()
        if not name or "\n" in name or len(name) > 200:
            raise GitError("Enter your name as it should appear on commits.", kind="invalid")
        if not re.match(r"^[^@\s]+@[^@\s]+$", email):
            raise GitError("Enter an email address, such as you@company.com.", kind="invalid")
        self.git("config", "--local", "user.name", name)
        self.git("config", "--local", "user.email", email)
        return self.identity()

    def settings(self) -> dict[str, Any]:
        helpers = [h for h in (self._try("config", "--get-all", "credential.helper") or "").splitlines() if h.strip()]
        notes = []
        if not helpers:
            notes.append("Git has no credential helper, so it can't save your sign-in for https remotes. Git for "
                         "Windows includes Git Credential Manager; reinstall it with that option on.")
        if any(h.strip().split()[0] in ("store",) for h in helpers):
            notes.append("Git's credential helper is 'store', which keeps passwords in a plain text file. "
                         "Git Credential Manager (manager) keeps them in Windows Credential Manager instead.")
        ssh = (self._try("config", "--get", "core.sshCommand") or "").strip() or None
        return {"root": str(self.root), "identity": self.identity(), "remotes": self.remotes(),
                "credential_helpers": [h.split()[0] for h in helpers], "ssh_command": bool(ssh),
                "git_version": cli.version(), "notes": notes}

    # ---- staging

    def stage(self, paths: list[str]) -> dict[str, Any]:
        self.git("add", "-A", "--", *self.rels(paths))
        return self.status()

    def stage_all(self) -> dict[str, Any]:
        self.git("add", "-A")
        return self.status()

    def unstage(self, paths: list[str] | None = None) -> dict[str, Any]:
        rels = self.rels(paths) if paths else ["."]
        if self.head():
            self.git("restore", "--staged", "--", *rels)
        else:  # nothing committed yet: there is no HEAD to restore from
            self.git("rm", "--cached", "-r", "-q", "--", *rels)
        return self.status()

    def discard(self, paths: list[str], confirm: bool = False) -> dict[str, Any]:
        """Throws away unstaged changes to these files; files Git doesn't track yet are deleted.
        Staged changes are kept."""
        rels = self.rels(paths)
        st = self.status()
        untracked = {e["path"] for e in st["untracked"]}
        conflicted = {e["path"] for e in st["conflicts"]}
        if conflicted & set(rels):
            raise GitError("Fix or undo the merge before discarding a file with conflicts.", kind="conflict")
        if not confirm:
            raise confirm_needed(f"This throws away your changes to {len(rels)} file{'s' if len(rels) != 1 else ''}"
                                 " and can't be undone.")
        tracked = [r for r in rels if r not in untracked]
        if tracked:
            self.git("restore", "--worktree", "--", *tracked)
        for r in rels:
            if r in untracked:
                target = (self.root / r)
                if target.is_file() or target.is_symlink():
                    target.unlink()
        return self.status()

    # ---- commits

    def scan_staged(self) -> list[dict[str, Any]]:
        """Secret findings in what is staged (the index's copy of each added or changed file)."""
        raw = self.git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT", read_only=True)
        found: list[dict[str, Any]] = []
        for path in [p for p in raw.split("\0") if p]:
            try:
                data = cli.run(self.root, ["show", f":{path}"], read_only=True).stdout
            except GitError:
                continue
            found.extend(secrets.scan_file(path, data[:secrets.MAX_SCAN_BYTES]))
        return found

    def commit(self, message: str, allow_secrets: bool = False) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            raise GitError("Write a commit message that says what changed.", kind="invalid")
        st = self.status()
        if st["conflicts"]:
            raise GitError("Some files still have conflicts. Fix them and stage them before committing.", kind="conflict")
        if not st["staged"] and not st["merging"]:
            raise GitError("Nothing is staged. Stage the files to commit first.", kind="nothing")
        ident = self.identity()
        if not ident["name"] or not ident["email"]:
            raise GitError("Git needs your name and email for commits. Set them in Source control > "
                           "Repository settings.", kind="identity")
        found = self.scan_staged()
        if found and not allow_secrets:
            err = GitError(f"{len(found)} possible secret{'s' if len(found) != 1 else ''} in the staged files. "
                           "Remove them, or confirm they aren't secrets.", kind="secrets")
            err.findings = found  # type: ignore[attr-defined]
            raise err
        self.git("commit", "-q", "-F", "-", input=message.encode("utf-8"), timeout=180)
        return {"commit": self.head(), "branch": self.branch(), "status": self.status(), "secrets_ignored": len(found)}

    def log(self, ref: str | None = None, limit: int = 50, skip: int = 0, path: str | None = None) -> dict[str, Any]:
        if not self.head():
            return {"commits": [], "more": False}
        limit = max(1, min(int(limit), LOG_LIMIT))
        args = ["log", f"--format=%H%x00%h%x00%an%x00%ae%x00%aI%x00%P%x00%D%x00%s%x1e",
                f"--max-count={limit + 1}", f"--skip={max(0, int(skip))}"]
        args += ["--end-of-options", self.resolve(ref)] if ref else []
        if path:
            args += ["--", self.rel(path)]
        rows = [r.strip("\n") for r in self.git(*args, read_only=True).split("\x1e") if r.strip("\n")]
        commits = [_commit_row(r) for r in rows[:limit]]
        return {"commits": commits, "more": len(rows) > limit}

    def commit_detail(self, rev: str) -> dict[str, Any]:
        sha = self.resolve(rev)
        row = self.git("log", "-1", "--format=%H%x00%h%x00%an%x00%ae%x00%aI%x00%P%x00%D%x00%s%x00%b",
                       "--end-of-options", sha, read_only=True)
        parts = row.split("\0")
        c = _commit_row("\0".join(parts[:8]))
        c["body"] = parts[8].strip() if len(parts) > 8 else ""
        parent = c["parents"][0] if c["parents"] else self.empty_tree()
        c["files"] = self._name_status(parent, sha)
        c["base"] = parent
        return c

    def compare(self, base: str, target: str) -> dict[str, Any]:
        a, b = self.resolve(base), self.resolve(target)
        counts = (self._try("rev-list", "--left-right", "--count", f"{a}...{b}") or "0 0").split()
        return {"base": a, "target": b, "files": self._name_status(a, b),
                "only_in_base": int(counts[0]), "only_in_target": int(counts[1])}

    def _name_status(self, a: str, b: str) -> list[dict[str, Any]]:
        raw = self.git("diff", "--name-status", "-z", "-M", "--no-ext-diff", a, b, read_only=True)
        items = raw.split("\0")
        out, i = [], 0
        while i < len(items):
            code = items[i]
            i += 1
            if not code:
                continue
            letter = code[0]
            if letter in "RC":
                orig, path = items[i], items[i + 1]
                i += 2
            else:
                orig, path = None, items[i]
                i += 1
            out.append(_entry(path, letter, orig))
        return out

    def empty_tree(self) -> str:
        return self.git("hash-object", "-t", "tree", "--stdin", input=b"", read_only=True).strip()

    def file_at(self, rev: str, path: str) -> dict[str, Any]:
        """A file as it was at a commit, read-only."""
        sha, rel = self.resolve(rev), self.rel(path)
        try:
            data = cli.run(self.root, ["show", f"{sha}:{rel}"], read_only=True).stdout
        except GitError:
            raise GitError(f"{rel} isn't in commit {sha[:7]}.", kind="unknown_ref") from None
        binary = b"\0" in data[:8000]
        return {"path": rel, "commit": sha, "binary": binary, "size": len(data), "read_only": True,
                "truncated": len(data) > MAX_FILE_BYTES,
                "text": None if binary else data[:MAX_FILE_BYTES].decode("utf-8", "replace")}

    # ---- diffs

    def diff(self, path: str, staged: bool = False, base: str | None = None, target: str | None = None,
             notebook: bool = True, context: int = 3) -> dict[str, Any]:
        """Line-level diff of one file: working copy against staged, staged against the last commit,
        or one commit against another. A notebook compares its cells' code (outputs left out)
        unless ``notebook`` is False."""
        rel = self.rel(path)
        context = max(0, min(int(context), 50))
        untracked = False
        if base or target:
            a = self.resolve(base) if base else self.empty_tree()
            b = self.resolve(target) if target else None
            if b is None:
                raise GitError("Choose the commit to compare with.", kind="invalid")
            args = ["diff", f"-U{context}", "--no-ext-diff", "-M", a, b, "--", rel]
            sides = (f"{a}:{rel}", f"{b}:{rel}")
        elif staged:
            args = ["diff", "--cached", f"-U{context}", "--no-ext-diff", "--", rel]
            sides = (f"HEAD:{rel}" if self.head() else None, f":{rel}")
        elif self._try("ls-files", "--unmerged", "--", rel):
            return {"path": rel, "staged": False, "base": None, "target": None, "untracked": False,
                    **conflict_view(self._side("worktree", rel))}
        else:
            tracked = bool(self._try("ls-files", "--error-unmatch", "--", rel))
            untracked = not tracked
            args = ["diff", f"-U{context}", "--no-ext-diff", "--", rel]
            sides = (None if untracked else f":{rel}", "worktree")
        out: dict[str, Any] = {"path": rel, "staged": staged, "base": base, "target": target, "untracked": untracked}
        if notebook and rel.lower().endswith(".ipynb"):
            old, new = self._side(sides[0]), self._side(sides[1], rel)
            if (old is None or _is_text(old)) and (new is None or _is_text(new)):
                a_txt, b_txt = notebook_text(old), notebook_text(new)
                if a_txt is not None and b_txt is not None:
                    udiff = "".join(difflib.unified_diff(a_txt.splitlines(True), b_txt.splitlines(True), n=context))
                    return {**out, "view": "notebook", "binary": False, **parse_unified(udiff)}
        if untracked:
            data = self._side("worktree", rel) or b""
            if not _is_text(data):
                return {**out, "view": "text", "binary": True, "hunks": [], "added": 0, "removed": 0, "truncated": False}
            lines = data.decode("utf-8", "replace").splitlines()
            body = "".join(f"+{l}\n" for l in lines)
            udiff = f"@@ -0,0 +1,{len(lines)} @@\n{body}" if lines else ""
            return {**out, "view": "text", "binary": False, **parse_unified(udiff)}
        raw = self.git(*args, read_only=True)
        if re.search(r"^Binary files .* differ$", raw, re.M):
            return {**out, "view": "text", "binary": True, "hunks": [], "added": 0, "removed": 0, "truncated": False}
        return {**out, "view": "text", "binary": False, **parse_unified(raw)}

    def _side(self, spec: str | None, rel: str | None = None) -> bytes | None:
        if spec is None:
            return None
        if spec == "worktree":
            f = self.root / (rel or "")
            return f.read_bytes()[:MAX_FILE_BYTES] if f.is_file() else None
        try:
            return cli.run(self.root, ["show", spec], read_only=True).stdout[:MAX_FILE_BYTES]
        except GitError:
            return None  # not in that version (added or deleted)

    # ---- branches

    def branches(self) -> dict[str, Any]:
        fmt = "%(refname)%00%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track)%00" \
              "%(committerdate:iso-strict)%00%(subject)%00%(HEAD)"
        raw = self.git("for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes", read_only=True)
        local, remote = [], []
        current = self.branch()
        for line in raw.splitlines():
            f = line.split("\0")
            if len(f) < 8:
                continue
            ref, short, sha, upstream, track, date, subject, head = f[:8]
            if ref.startswith("refs/remotes/"):
                if ref.endswith("/HEAD"):
                    continue
                remote.append({"name": short, "remote": short.split("/", 1)[0], "commit": sha, "date": date,
                               "subject": subject})
            else:
                ahead = re.search(r"ahead (\d+)", track)
                behind = re.search(r"behind (\d+)", track)
                local.append({"name": short, "commit": sha, "upstream": upstream or None, "date": date,
                              "subject": subject, "current": head.strip() == "*" or short == current,
                              "gone": "gone" in track, "ahead": int(ahead.group(1)) if ahead else 0,
                              "behind": int(behind.group(1)) if behind else 0})
        tracked = {b["upstream"] for b in local if b["upstream"]}
        for r in remote:
            r["tracked"] = r["name"] in tracked
        return {"current": current, "detached": current is None and self.head() is not None,
                "local": local, "remote": remote}

    def create_branch(self, name: str, start: str | None = None, switch: bool = True) -> dict[str, Any]:
        name = check_branch_name(name)
        if self._try("rev-parse", "--verify", "-q", f"refs/heads/{name}"):
            raise GitError(f"A branch called {name} already exists.", kind="exists")
        if not self.head() and not start:
            # Nothing committed yet: the branch is just the name the first commit goes on.
            if not switch:
                raise GitError("Commit something first; a new repository's first branch is the one you're on.", kind="invalid")
            self.git("symbolic-ref", "HEAD", f"refs/heads/{name}")
            return self.branches()
        args = ["switch", "-c", name] if switch else ["branch", name]
        if start:
            args += [self.resolve(start)]
        self.git(*args)
        return self.branches()

    def switch(self, name: str, mode: str | None = None) -> dict[str, Any]:
        """Switches to branch ``name`` (a local branch, or a remote one such as origin/x, which makes
        a local branch tracking it). With uncommitted changes, ``mode`` must say what happens to them:
        "stash" puts them aside (restored if the switch fails), "carry" brings them along, which Git
        refuses when a file differs on the other branch."""
        if self.merging():
            raise GitError("Finish or undo the merge before switching branches.", kind="merging")
        name = (name or "").strip()
        local = self._try("rev-parse", "--verify", "-q", f"refs/heads/{name}")
        remote_ref = None if local else self._try("rev-parse", "--verify", "-q", f"refs/remotes/{name}")
        if not local and not remote_ref:
            raise GitError(f"There's no branch called {name}.", kind="unknown_ref")
        if name == self.branch():
            return {"branch": name, "stashed": False, "status": self.status()}
        st = self.status()
        if st["dirty"] and mode not in ("stash", "carry"):
            err = GitError(f"You have {st['changed']} uncommitted change{'s' if st['changed'] != 1 else ''}. Choose "
                           "whether to put them aside or bring them with you.", kind="dirty")
            err.changes = st["staged"] + st["unstaged"] + st["untracked"]  # type: ignore[attr-defined]
            raise err
        stashed = False
        if st["dirty"] and mode == "stash":
            label = f"Fabric Desktop: changes on {st['branch'] or 'detached HEAD'} before switching to {name}"
            self.git(*_STASH, "push", "--include-untracked", "-m", label)
            stashed = True
            if self.status()["dirty"]:  # something didn't go aside: put it all back rather than half-switch
                self.git(*_STASH, "pop", "--index")
                raise GitError("Git couldn't put every change aside, so nothing was switched. Commit the "
                               "changes instead.", kind="stash")
        try:
            if local:
                self.git("switch", name)  # an existing branch; Git never lets one start with "-"
            else:
                local_name = name.split("/", 1)[1]
                check_branch_name(local_name)
                if self._try("rev-parse", "--verify", "-q", f"refs/heads/{local_name}"):
                    raise GitError(f"A local branch called {local_name} already exists. Switch to it instead.", kind="exists")
                self.git("switch", "-c", local_name, "--track", f"refs/remotes/{name}")
        except GitError:
            if stashed:
                self.git(*_STASH, "pop", "--index")
            raise
        return {"branch": self.branch(), "stashed": stashed, "status": self.status()}

    def delete_branch(self, name: str, confirm: bool = False, force: bool = False) -> dict[str, Any]:
        """Deletes a local branch. Without ``force`` Git refuses one whose commits no other branch holds."""
        name = (name or "").strip()
        if not self._try("rev-parse", "--verify", "-q", f"refs/heads/{name}"):
            raise GitError(f"There's no local branch called {name}.", kind="unknown_ref")
        if name == self.branch():
            raise GitError("You can't delete the branch you're on. Switch to another branch first.", kind="invalid")
        if not confirm:
            raise confirm_needed(f"This deletes the branch {name} on this PC (the remote's copy stays).")
        self.git("branch", "-D" if force else "-d", name)
        return self.branches()

    def stashes(self) -> list[dict[str, Any]]:
        raw = self._try("stash", "list", "--format=%gd%x00%aI%x00%gs") or ""
        out = []
        for line in raw.splitlines():
            f = line.split("\0")
            if len(f) == 3:
                out.append({"ref": f[0], "date": f[1], "message": f[2]})
        return out

    def restore_stash(self, ref: str) -> dict[str, Any]:
        """Brings back changes put aside before a switch (git stash pop); kept if it can't apply cleanly."""
        if not re.match(r"^stash@\{\d+\}$", ref or ""):
            raise GitError("Choose a set of put-aside changes.", kind="invalid")
        self.git(*_STASH, "pop", "--index", ref)
        return self.status()

    def abort_merge(self, confirm: bool = False) -> dict[str, Any]:
        if not self.merging():
            raise GitError("No merge is in progress.", kind="invalid")
        if not confirm:
            raise confirm_needed("This undoes the merge and puts every file back as it was before the pull.")
        self.git("merge", "--abort")
        return self.status()




def _entry(path: str, code: str, orig: str | None) -> dict[str, Any]:
    return {"path": path, "state": STATE_WORDS.get(code, "modified"), "code": code if code != "?" else "U",
            **({"from": orig} if orig else {})}


def _conflict_kind(xy: str) -> str:
    return {"DD": "deleted on both sides", "AU": "added here", "UD": "deleted on the remote",
            "UA": "added on the remote", "DU": "deleted here", "AA": "added on both sides",
            "UU": "changed on both sides"}.get(xy, "changed on both sides")


def _commit_row(row: str) -> dict[str, Any]:
    f = row.split("\0")
    f += [""] * (8 - len(f))
    return {"commit": f[0], "short": f[1], "author": f[2], "email": f[3], "date": f[4],
            "parents": f[5].split() if f[5] else [], "refs": [r.strip() for r in f[6].split(",") if r.strip()],
            "subject": f[7]}


def _summary(info: dict[str, Any], changed: int) -> str:
    if not info["commit"]:
        return "Nothing is committed yet." + (f" {changed} file{'s' if changed != 1 else ''} to commit." if changed else "")
    where = info["branch"] or f"{info['commit'][:7]} (no branch)"
    bits = [where]
    bits.append(f"{changed} uncommitted change{'s' if changed != 1 else ''}" if changed else "no uncommitted changes")
    if not info["upstream"]:
        bits.append("not linked to a remote branch")
    else:
        if info["ahead"]:
            bits.append(f"{info['ahead']} to push")
        if info["behind"]:
            bits.append(f"{info['behind']} to pull")
        if not info["ahead"] and not info["behind"]:
            bits.append(f"in step with {info['upstream']} as of the last fetch")
    return ", ".join(bits) + "."


def _is_text(data: bytes) -> bool:
    return b"\0" not in data[:8000]


def notebook_text(data: bytes | None) -> str | None:
    """A notebook as text for comparing: one header line per cell, then its source. Outputs and
    execution counts are left out, so a re-run doesn't show as a change. None when it isn't a
    notebook; empty text when there is no file on that side."""
    if data is None:
        return ""
    try:
        nb = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None
    out = []
    for n, cell in enumerate(nb["cells"], 1):
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type", "code")
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        out.append(f"# ---- cell {n} ({kind}) ----\n")
        out.append(src if src.endswith("\n") or not src else src + "\n")
    return "".join(out)


def conflict_view(data: bytes | None) -> dict[str, Any]:
    """A file with merge conflicts as it is now: every line, with the conflict blocks marked.
    ``side`` is "here" (this branch), "remote" (what the pull brought in) or "marker"."""
    if data is None or not _is_text(data):
        return {"view": "conflict", "binary": data is not None, "hunks": [], "added": 0, "removed": 0,
                "truncated": False, "conflicts": 0}
    lines, side, blocks, truncated = [], None, 0, False
    for n, text in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if len(lines) >= MAX_DIFF_LINES:
            truncated = True
            break
        kind = " "
        if text.startswith("<<<<<<<"):
            side, kind, blocks = "here", "marker", blocks + 1
        elif text.startswith("=======") and side == "here":
            side, kind = "remote", "marker"
        elif text.startswith("|||||||") and side == "here":
            side, kind = "base", "marker"
        elif text.startswith(">>>>>>>") and side in ("remote", "base"):
            side, kind = None, "marker"
        lines.append({"kind": " ", "old": None, "new": n, "text": text,
                      "side": "marker" if kind == "marker" else side})
    hunk = {"header": f"{blocks} conflict{'s' if blocks != 1 else ''} in this file", "old_start": 0, "new_start": 1,
            "context": "", "lines": lines}
    return {"view": "conflict", "binary": False, "hunks": [hunk] if lines else [], "added": 0, "removed": 0,
            "truncated": truncated, "conflicts": blocks}


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def parse_unified(text: str) -> dict[str, Any]:
    """Hunks from a unified diff: each line has its kind (" ", "+", "-") and old and new numbers."""
    hunks: list[dict[str, Any]] = []
    added = removed = count = 0
    truncated = False
    cur: dict[str, Any] | None = None
    old = new = 0
    for line in text.splitlines():
        m = _HUNK.match(line)
        if m:
            old, new = int(m.group(1)), int(m.group(3))
            cur = {"header": line, "old_start": old, "new_start": new, "context": m.group(5), "lines": []}
            hunks.append(cur)
            continue
        if cur is None or line.startswith("\\"):
            continue  # file headers, "\ No newline at end of file"
        if count >= MAX_DIFF_LINES:
            truncated = True
            break
        kind, body = (line[:1] or " "), line[1:]
        if kind == "+":
            cur["lines"].append({"kind": "+", "old": None, "new": new, "text": body})
            new += 1
            added += 1
        elif kind == "-":
            cur["lines"].append({"kind": "-", "old": old, "new": None, "text": body})
            old += 1
            removed += 1
        else:
            cur["lines"].append({"kind": " ", "old": old, "new": new, "text": body})
            old += 1
            new += 1
        count += 1
    return {"hunks": hunks, "added": added, "removed": removed, "truncated": truncated}
