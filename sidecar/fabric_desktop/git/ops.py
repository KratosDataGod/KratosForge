"""Clone, fetch, pull and push: the Git work that talks to a remote, run in the background.

The engine answers one request at a time, so a long clone mustn't run inside one. ``start``
returns at once with an id; the page asks ``status`` for progress and can ``cancel``. Progress
comes from Git's own ``--progress`` lines. Nothing here force-pushes, and pull never rebases:
it fast-forwards, or merges when the person chose Merge.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import cli
from .cli import GitError
from .remote import check_url, repo_name
from .repo import Repository, _folder
from .secrets import redact

KEEP = 50  # finished operations remembered for the page
_PROGRESS = re.compile(r"(?P<phase>[A-Za-z][A-Za-z ]+):\s+(?P<pct>\d{1,3})%")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")



def _writable_then_retry(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    """Git keeps its objects and packs read-only, and Windows won't delete a read-only file: make it
    writable and try once more. Anything else still in the way is left (the caller ignores it)."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def remove_clone(dest: Path) -> None:
    """Remove what a failed clone left, read-only Git objects included, so cloning to the same folder
    again doesn't stop at "already exists and isn't empty"."""
    shutil.rmtree(dest, onexc=_writable_then_retry)

class Operations:
    def __init__(self) -> None:
        self._ops: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ---- public

    def clone(self, url: str, parent: str, name: str | None = None) -> dict[str, Any]:
        url = check_url(url)
        folder = _folder(parent, must_exist=False)
        if folder.exists() and not folder.is_dir():
            raise GitError(f"{folder} is a file, not a folder.", kind="invalid")
        folder.mkdir(parents=True, exist_ok=True)  # such as source\repos the first time
        name = (name or repo_name(url)).strip()
        if not re.match(r"^[A-Za-z0-9._ -]{1,100}$", name) or name in (".", ".."):
            raise GitError("A folder name is letters, numbers, spaces, . _ and -.", kind="invalid")
        dest = folder / name
        if dest.exists() and any(dest.iterdir()):
            raise GitError(f"{dest} already exists and isn't empty. Choose another folder or name.", kind="exists")
        created = not dest.exists()

        def cleanup(ok: bool) -> None:
            if not ok and created and dest.exists():  # only the folder this clone made
                remove_clone(dest)

        args = ["clone", "--progress", "--", url, str(dest)]
        return self._start("clone", None, args, cwd=folder, target=str(dest), after=cleanup,
                           result=lambda: {"root": str(Repository(dest).root)})

    def fetch(self, repo: Repository, remote: str | None = None) -> dict[str, Any]:
        remotes = [r["name"] for r in repo.remotes()]
        if not remotes:
            raise GitError("This repository has no remote to fetch from. Add one in Repository settings.", kind="no_remote")
        if remote and remote not in remotes:
            raise GitError(f"There's no remote called {remote}.", kind="invalid")
        args = ["fetch", "--progress", "--prune"] + ([remote] if remote else ["--all"])
        return self._start("fetch", repo, args)

    def pull(self, repo: Repository, mode: str = "ff-only") -> dict[str, Any]:
        st = repo.status()
        if st["merging"]:
            raise GitError("A merge is in progress. Finish it or undo it before pulling.", kind="merging")
        if not st["branch"]:
            raise GitError("You're not on a branch. Switch to a branch to pull.", kind="invalid")
        if not st["upstream"]:
            raise GitError("This branch isn't linked to a remote branch yet. Push it once to link it.", kind="no_upstream")
        if mode not in ("ff-only", "merge"):
            raise GitError("Pull either fast-forwards or merges.", kind="invalid")
        args = ["pull", "--progress", "--no-rebase", "--no-edit"] + (["--ff-only"] if mode == "ff-only" else ["--no-ff"])
        return self._start("pull", repo, args, result=lambda: _after_pull(repo))

    def push(self, repo: Repository, remote: str | None = None) -> dict[str, Any]:
        st = repo.status()
        branch = st["branch"]
        if not branch:
            raise GitError("You're not on a branch. Create a branch to push your commits.", kind="invalid")
        if not st["commit"]:
            raise GitError("Nothing is committed yet. Commit first, then push.", kind="nothing")
        remotes = [r["name"] for r in st["remotes"]]
        if not remotes:
            raise GitError("This repository has no remote to push to. Add one in Repository settings.", kind="no_remote")
        if st["upstream"] and not remote:
            args = ["push", "--progress"]
        else:
            target = remote or ("origin" if "origin" in remotes else remotes[0])
            if target not in remotes:
                raise GitError(f"There's no remote called {target}.", kind="invalid")
            args = ["push", "--progress", "--set-upstream", target, f"HEAD:refs/heads/{branch}"]
        return self._start("push", repo, args, result=lambda: {"status": repo.status()})

    def status(self, op_id: str) -> dict[str, Any]:
        with self._lock:
            op = self._ops.get(op_id)
            if op is None:
                raise GitError("That Git operation isn't known any more.", kind="invalid")
            return dict(op)

    def list(self, repo_root: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ops = [dict(o) for o in self._ops.values() if repo_root is None or o.get("repo") == repo_root]
        return sorted(ops, key=lambda o: o["started_at"], reverse=True)

    def running(self, repo_root: str) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(o) for o in self._ops.values() if o.get("repo") == repo_root and o["status"] == "running"), None)

    def cancel(self, op_id: str) -> dict[str, Any]:
        with self._lock:
            op = self._ops.get(op_id)
            proc = self._procs.get(op_id)
            if op is None:
                raise GitError("That Git operation isn't known any more.", kind="invalid")
            if op["status"] != "running":
                return dict(op)
            op["cancel_requested"] = True
        if proc is not None:
            _kill(proc)
        return self.status(op_id)

    # ---- running

    def _start(self, kind: str, repo: Repository | None, args: list[str], cwd: Path | None = None,
               target: str | None = None, after: Callable[[bool], None] | None = None,
               result: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
        root = str(repo.root) if repo else target
        if repo is not None and self.running(str(repo.root)):
            raise GitError("Another Git operation is still running in this repository. Wait for it or cancel it.",
                           kind="busy")
        cmd = cli.command(cwd or (repo.root if repo else None), args)
        op = {"id": uuid.uuid4().hex[:12], "kind": kind, "repo": root, "status": "running", "phase": "Starting",
              "percent": None, "message": "", "details": "", "started_at": _now(), "finished_at": None,
              "result": None, "error_kind": None, "remote": True}
        with self._lock:
            self._ops[op["id"]] = op
            self._trim()
        threading.Thread(target=self._run, args=(op["id"], cmd, after, result), daemon=True,
                         name=f"git-{kind}").start()
        return dict(op)

    def _trim(self) -> None:
        done = [o for o in self._ops.values() if o["status"] != "running"]
        for o in sorted(done, key=lambda o: o["started_at"])[:max(0, len(done) - KEEP)]:
            self._ops.pop(o["id"], None)

    def _update(self, op_id: str, **fields: Any) -> None:
        with self._lock:
            if op_id in self._ops:
                self._ops[op_id].update(fields)

    def _run(self, op_id: str, cmd: list[str], after: Callable[[bool], None] | None,
             result: Callable[[], dict[str, Any]] | None) -> None:
        tail: list[str] = []
        ok = False
        started = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=cli.environment(), creationflags=cli._NO_WINDOW | _NEW_GROUP,
                                    start_new_session=not _WINDOWS)
        except OSError as exc:
            self._finish(op_id, "failed", f"Git couldn't start: {exc}", "", "no_git")
            return
        with self._lock:
            self._procs[op_id] = proc
        timer = threading.Timer(cli.NETWORK_TIMEOUT, lambda: _kill(proc))
        timer.daemon = True
        timer.start()
        try:
            out_lines: list[str] = []
            reader = threading.Thread(target=lambda: out_lines.extend(
                proc.stdout.read().decode("utf-8", "replace").splitlines()), daemon=True)  # type: ignore[union-attr]
            reader.start()
            buf = b""
            assert proc.stderr is not None
            while True:
                chunk = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = re.split(rb"[\r\n]", buf)
                for raw in lines:
                    line = redact(raw.decode("utf-8", "replace").strip())
                    if not line:
                        continue
                    m = _PROGRESS.search(line)
                    if m:
                        self._update(op_id, phase=m.group("phase").strip(), percent=int(m.group("pct")))
                    else:
                        tail.append(line)
                        del tail[:-40]
            if buf.strip():
                tail.append(redact(buf.decode("utf-8", "replace").strip()))
            code = proc.wait()
            reader.join(timeout=5)
            tail.extend(redact(l) for l in out_lines[-20:] if l.strip())
        finally:
            timer.cancel()
            with self._lock:
                self._procs.pop(op_id, None)
        with self._lock:
            cancelled = bool(self._ops.get(op_id, {}).get("cancel_requested"))
        details = "\n".join(tail[-40:])
        if cancelled:
            if after:
                after(False)
            self._finish(op_id, "cancelled", "Cancelled. Nothing more was sent or received.", details, "cancelled")
            return
        if code == 0:
            ok = True
            res: dict[str, Any] = {}
            try:
                res = result() if result else {}
            except GitError as exc:
                res = {"note": str(exc)}
            if after:
                after(True)
            took = round(time.monotonic() - started, 1)
            with self._lock:
                kind = self._ops.get(op_id, {}).get("kind", "")
            self._finish(op_id, "ok", _done_message(details) or _DONE.get(kind, "Done."), details, None, res, took)
            return
        if time.monotonic() - started >= cli.NETWORK_TIMEOUT - 1:
            err = GitError(f"Git took longer than {cli.NETWORK_TIMEOUT // 60} minutes and was stopped.", details, "timeout")
        else:
            err = cli.explain(details)
        res = None
        if err.kind == "conflict" and result:
            try:
                res = result()
            except GitError:
                res = None
        if after:
            after(ok)
        self._finish(op_id, "failed", str(err), err.details, err.kind, res)

    def _finish(self, op_id: str, status: str, message: str, details: str, kind: str | None,
                result: dict[str, Any] | None = None, took: float | None = None) -> None:
        self._update(op_id, status=status, message=message, details=details, error_kind=kind, result=result,
                     finished_at=_now(), percent=100 if status == "ok" else None, seconds=took)


_WINDOWS = hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _kill(proc: subprocess.Popen) -> None:
    """Ends git and the helpers it started (git-remote-https, ssh)."""
    if proc.poll() is not None:
        return
    try:
        if _WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=cli._NO_WINDOW)
        else:
            import os
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired, ProcessLookupError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


_DONE = {"clone": "Cloned.", "fetch": "Fetched. The branch lists and counts are current.",
         "pull": "Pulled.", "push": "Pushed: your commits are on the remote."}


def _done_message(details: str) -> str:
    for pattern, message in ((r"Already up to date", "Already up to date."),
                             (r"Everything up-to-date", "Nothing new to push."),
                             (r"Fast-forward", "Pulled: your branch moved forward."),
                             (r"Merge made by", "Pulled and merged.")):
        if re.search(pattern, details):
            return message
    return ""


def _after_pull(repo: Repository) -> dict[str, Any]:
    st = repo.status()
    return {"status": st, "conflicts": [c["path"] for c in st["conflicts"]]}
