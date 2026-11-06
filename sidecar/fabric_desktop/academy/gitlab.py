"""Source-control exercises: the ``git_lab`` adapter.

The learner's answer is a Git session (the exercise's ``starter``, such as ``session.git``): the
commands they would type in a terminal, one per line, plus ``write`` blocks that give a file new
text (how a conflict in an item definition is resolved)::

    # comments and blank lines are skipped
    git switch -c feature/orders
    write Orders.DataPipeline/pipeline-content.json
    { "properties": { "activities": [] } }
    end
    git add -A
    git commit -m "Add the orders pipeline"

Run and Check both start from a fresh repository the engine builds from the exercise's ``repo``
history (``commits``: each on a ``branch``, optionally made ``from`` another, with ``files`` taken
from the exercise's folder and ``delete`` paths), then replay the whole session with the PC's own
``git`` and fixed author, committer and dates, so the same session always gives the same commits.
A failed Git command is part of the session (a merge that stops on a conflict is the lesson), so
replay goes on; a line this lab refuses stops it.

What the session may do. Git runs as an argument list (never a shell), in that repository only
(``GIT_DIR`` and ``GIT_WORK_TREE`` name it), with no global or system configuration, no hooks, no
editor, no pager, no credential helper and no transports (``protocol.allow=never``), and a time
limit per command and per session. Each command runs in a Job Object on Windows (memory, CPU time
and process limits, no breakaway, killed with everything it started when it ends or runs out of
time) or in its own limited session elsewhere (``sandbox.contained``). Only the subcommands in ``SUBCOMMANDS`` run, and nothing may come
before the subcommand, so ``-c``, ``-C`` and ``--git-dir`` can't change where or how Git runs. Options
that write or read files outside the repository, run another program, or sign are refused
(``REFUSED``), as is any argument that is an absolute path, starts with ``~`` or has a ``..``
segment. ``write`` paths stay inside the repository, never under ``.git`` (in any spelling Windows
treats as ``.git``) and never through a link. No step may make a ``.gitattributes`` file: attributes
name the filter, diff and merge drivers Git runs, so the only attributes are the exercise's own.

Checks (``validation``):

    repo_matches    ``rules``, each one test: ``branch`` + ``exists``; ``head`` (the branch checked
                    out); ``file`` (+ ``ref``, default the work tree) with ``exists``, ``contains``,
                    ``not_contains`` or ``json`` (config_matches rules on the parsed file);
                    ``ancestor`` + ``of`` (merged into); ``ref`` + ``parents`` (a merge commit has 2);
                    ``ref`` + ``since`` + ``count_min``/``count_max`` (commits ahead); ``clean`` (no
                    changes, no merge in progress); ``conflict_markers: false`` (none in the work
                    tree's tracked files). ``why`` says why the rule matters.

A pass is LOCAL VERIFIED: real Git ran the learner's commands on this PC. It proves nothing about a
Fabric workspace's Git integration (Commit, Update, Branch out), which only Fabric shows.

Grading material: ``grading/<id>/solution.git`` must pass and at least two ``wrong-*.git`` must fail.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .. import jsonfile
from . import sandbox, validators
from . import workspace as ws

ADAPTER = "git_lab"
CHECKS = ("repo_matches",)
REPO_DIR = "repo"
MAX_BYTES = 256 * 1024          # a session is a few KB
MAX_STEPS = 200                 # lines that do something
MAX_FILE = 256 * 1024           # one file a check reads from the repository
STEP_SECONDS = 15
SESSION_SECONDS = 90
# Each git command runs in a Job Object (Windows) or its own limited session (elsewhere), killed with
# everything it started when it ends or runs out of time. Git for Windows' git.exe starts the real
# git as a child, and a command may start a few helpers, so a handful of processes are allowed.
MEMORY_LIMIT = 512 * 1024 ** 2   # bytes for one command and everything it starts
CPU_SECONDS = STEP_SECONDS       # CPU time for one command
PROCESSES = 16                   # processes alive at once in one command's job
OUTPUT_CHARS = 4000             # of one command's output, as the screen shows it
# Fixed identity and time: the same session always makes the same commits.
NAME, EMAIL, WHEN = "Academy Learner", "learner@academy.invalid", "2026-01-05T09:00:00+00:00"

SUBCOMMANDS = frozenset({"status", "add", "rm", "mv", "restore", "commit", "branch", "switch", "checkout", "merge",
                         "log", "diff", "show", "tag", "reset", "revert", "cherry-pick", "stash", "ls-files"})
# Options refused for every subcommand: they write or read files outside the repository, run another
# program, or sign (which runs gpg). Matched as the whole option or ``option=value``.
REFUSED = frozenset({"--output", "--no-index", "--ext-diff", "--textconv", "--exec", "--upload-pack",
                     "--receive-pack", "--template", "--pathspec-from-file", "--gpg-sign", "--file", "--strategy",
                     "--sign", "--local-user", "--git-dir", "--work-tree", "--config-env", "--exec-path",
                     "--interactive", "--patch", "--edit-description"})
# Short options refused for some subcommands: -s and -S sign or pick a strategy (a program named
# git-merge-<name>), -F reads a message from a file, -u names a signing key, -i and -p go interactive.
REFUSED_SHORT = {"commit": "SFp", "merge": "sSF", "revert": "sS", "cherry-pick": "sS", "tag": "sSuF", "stash": "p",
                 "add": "ip", "reset": "p", "restore": "p", "checkout": "p", "rm": "", "mv": "", "status": "",
                 "branch": "", "switch": "", "log": "", "diff": "", "show": "", "ls-files": ""}
_MESSAGE_OPTIONS = ("-m", "--message")
_MESSAGE_SUBCOMMANDS = ("commit", "merge", "tag", "stash")   # elsewhere -m is a flag (log -m, branch -m)
_STRATEGY_OPTION = ("merge", "revert", "cherry-pick")   # -X <option> for the merge strategy (-Xtheirs)
# Real options that read like a prefix of a refused one (--text, --textconv). Git refuses abbreviations here
# (GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS), so these whole names are only ever themselves.
_WHOLE_OPTIONS = frozenset({"--text", "--edit"})
_MARKERS = re.compile(r"^(<{7}|>{7})( |$)|^={7}$", re.MULTILINE)
_SHORT_NAME = re.compile(r"(?i)^git~\d+$")   # an 8.3 short name Windows can give .git
_BAD_PATH_CHARS = re.compile(r"[\x00-\x1f:*?\"<>|]")


class Refused(ValueError):
    """A session line this lab won't run; the message says why, in words for the learner."""


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def _ok(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": True, "detail": detail}


# ---- the session

def safe_path(rel: str) -> str:
    """``rel`` as a path inside the repository (forward slashes), or Refused: not absolute, no ``..``,
    nothing under ``.git`` in any spelling Windows reads as ``.git``, no characters Windows refuses."""
    text = str(rel).strip().replace("\\", "/")
    if not text or text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text) or _BAD_PATH_CHARS.search(text):
        raise Refused(f"{rel!r} isn't a path inside the repository.")
    parts = PurePosixPath(text).parts
    for part in parts:
        bare = part.rstrip(". ").lower()
        if (part in (".", "..") or bare in (".git", ".gitattributes") or _SHORT_NAME.match(bare) or not bare
                or ws._DEVICE.match(part)):
            raise Refused(f"{rel!r} isn't a path this lab writes: it must stay in the repository, out of .git, "
                          "not be .gitattributes, and use names Windows allows.")
    if len(parts) > 12 or len(text) > 240:
        raise Refused(f"{rel!r} is nested too deeply.")
    return "/".join(parts)


def _arg_ok(arg: str) -> bool:
    value = arg.split("=", 1)[1] if arg.startswith("--") and "=" in arg else arg
    text = value.replace("\\", "/")
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        return False
    parts = text.split("/")
    return ".." not in parts and parts[-1].rstrip(". ").lower() != ".gitattributes"


def _check_git(argv: list[str]) -> None:
    if not argv:
        raise Refused("A git line needs a subcommand, such as git status.")
    sub = argv[0]
    if sub.startswith("-"):
        raise Refused(f"Options before the subcommand ({sub}) aren't run here: this lab chooses where and how Git runs.")
    if sub not in SUBCOMMANDS:
        raise Refused(f"git {sub} isn't part of this lab. It runs: {', '.join(sorted(SUBCOMMANDS))}.")
    short = REFUSED_SHORT.get(sub, "")
    takes_message = sub in _MESSAGE_SUBCOMMANDS
    after_message = False
    for arg in argv[1:]:
        if after_message and not arg.startswith("-"):   # a commit message is text, not a path
            after_message = False
            continue
        after_message = False
        if takes_message and arg in _MESSAGE_OPTIONS:
            after_message = True
            continue
        if takes_message and arg.startswith("--message="):
            continue
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            # Git takes an unambiguous prefix of a long option (--fil for --file), so a prefix is refused too.
            if len(name) > 2 and name not in _WHOLE_OPTIONS and any(r.startswith(name) for r in REFUSED):
                hit = next(r for r in REFUSED if r.startswith(name))
                raise Refused(f"git {sub} {hit} isn't run here: it reaches outside the repository, runs another program or signs.")
        elif arg.startswith("-") and len(arg) > 1:
            flags = arg[1:]
            if not flags[0].isdigit():   # -3 (log -n 3) is a number, not flags
                letters = re.match(r"[A-Za-z]*", flags).group()   # type: ignore[union-attr]
                glued = flags[len(letters):]
                if takes_message and "m" in letters:   # -m"text", -am"text": what follows m is the message
                    letters, glued = letters.split("m", 1)[0], ""
                elif sub in _STRATEGY_OPTION and "X" in letters:   # -Xtheirs: what follows X is its value
                    letters, after = letters.split("X", 1)
                    glued = after + glued
                bad = [f for f in letters if f in short]
                if bad:
                    raise Refused(f"git {sub} -{bad[0]} isn't run here: it signs, reads a file, picks a merge program or asks interactively.")
                # -O/etc/x: what follows any flag letter may be its value (and -X's value is glued)
                for rest in [letters[n:] + glued for n in range(1, len(letters) + 1)] + [glued]:
                    if rest and not _arg_ok(rest):
                        raise Refused(f"{arg!r} points outside the repository or at .gitattributes; use other paths inside it.")
                continue
        if not _arg_ok(arg):
            raise Refused(f"{arg!r} points outside the repository or at .gitattributes; use other paths inside it.")


def parse(text: str) -> list[dict[str, Any]]:
    """The session's steps: ``{"op": "git", "argv", "line", "text"}`` or ``{"op": "write", "path",
    "content", "line", "text"}``. Raises Refused, naming the line, for anything this lab won't run."""
    raw = str(text)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Refused(f"The session is over {MAX_BYTES // 1024} KB; a Git session is much smaller.")
    lines = raw.lstrip("﻿").replace("\r\n", "\n").split("\n")
    steps: list[dict[str, Any]] = []
    n = 0
    while n < len(lines):
        line = lines[n].strip()
        n += 1
        if not line or line.startswith("#"):
            continue
        where = f"Line {n}: "
        if line == "write" or line.startswith("write "):
            start = n
            path = line[len("write"):].strip()
            body: list[str] = []
            while n < len(lines) and lines[n].rstrip() != "end":
                body.append(lines[n])
                n += 1
            if n >= len(lines):
                raise Refused(f"{where}write {path} has no end line; finish the file's text with a line that says end.")
            n += 1
            try:
                steps.append({"op": "write", "path": safe_path(path), "content": "\n".join(body) + ("\n" if body else ""),
                              "line": start, "text": f"write {path}"})
            except Refused as exc:
                raise Refused(where + str(exc)) from None
        else:
            try:
                words = shlex.split(line, posix=True)
            except ValueError as exc:
                raise Refused(f"{where}{exc}; check the quotes.") from None
            if words[0] != "git":
                raise Refused(f"{where}each line is a git command or a write block, not {words[0]!r}.")
            try:
                _check_git(words[1:])
            except Refused as exc:
                raise Refused(where + str(exc)) from None
            steps.append({"op": "git", "argv": words[1:], "line": n, "text": line})
        if len(steps) > MAX_STEPS:
            raise Refused(f"The session has more than {MAX_STEPS} steps.")
    return steps


# ---- running Git

def git_exe() -> str:
    exe = shutil.which("git")
    if not exe:
        raise Refused("Git isn't installed on this PC. Install Git for Windows (git-scm.com), then try again.")
    return exe


def _environment(repo: Path, home: Path) -> dict[str, str]:
    """Only what Git needs: no global or system configuration, a home with nothing in it, and fixed
    identity and dates. Nothing from the app's environment that names a repository or a program."""
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1", "GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS": "1", "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo), "GIT_CEILING_DIRECTORIES": str(repo.parent),
        "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":", "GIT_PAGER": "cat", "PAGER": "cat",
        "GIT_LITERAL_PATHSPECS": "1", "GIT_MERGE_AUTOEDIT": "no", "LC_ALL": "C", "LANGUAGE": "C",
        "GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL, "GIT_AUTHOR_DATE": WHEN,
        "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": EMAIL, "GIT_COMMITTER_DATE": WHEN,
    })
    return env


# Set on every call, after anything a repository's own configuration says.
_OPTIONS = ["-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
            "-c", "core.editor=:", "-c", "sequence.editor=:", "-c", "core.pager=cat", "-c", "credential.helper=",
            "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", "-c", "core.autocrlf=false",
            "-c", "core.quotepath=false", "-c", "color.ui=false", "-c", "advice.detachedHead=false",
            "-c", "init.defaultBranch=main", "-c", "merge.conflictStyle=merge", "-c", "core.symlinks=false",
            "-c", "user.name=" + NAME, "-c", "user.email=" + EMAIL]


def _git(repo: Path, argv: list[str], timeout: float = STEP_SECONDS) -> tuple[int, str]:
    """Git's exit code and what it printed (both streams), in ``repo``."""
    home = repo.parent / ".home"   # beside the repository, so nothing in the session can write there
    if not home.is_dir():
        home.mkdir()
        jsonfile.write_text(home / "gitconfig", "")
    hooks = ["-c", "core.hooksPath=" + str(home / "no-hooks")]   # a folder that is never made
    out = sandbox.contained([git_exe(), *hooks, *_OPTIONS, *argv], repo, _environment(repo, home), timeout=timeout,
                            memory_limit=MEMORY_LIMIT, cpu_limit=CPU_SECONDS, processes=PROCESSES)
    name = argv[0] if argv else ""
    if out["timed_out"]:
        return 124, f"Stopped: git {name} took longer than {int(timeout)} seconds."
    if out["memory_hit"]:
        return 125, f"Stopped: git {name} used more than {MEMORY_LIMIT // 1024 ** 2} MB of memory."
    return int(out["exit_code"]), (out["stdout"] + out["stderr"]).decode("utf-8", "replace")


def _must(repo: Path, argv: list[str]) -> str:
    code, text = _git(repo, argv)
    if code:
        raise RuntimeError(f"Building the exercise's repository failed at git {' '.join(argv)}: {text.strip()[:400]}")
    return text


def _write_file(repo: Path, rel: str, content: str) -> None:
    """``content`` at ``rel`` in the repository: the parent folders made inside it, no link followed."""
    rel = safe_path(rel)
    parent = repo
    for part in PurePosixPath(rel).parts[:-1]:
        here = parent / part
        if os.path.lexists(here):
            ws.inside(parent, part)   # refused when it's a link or junction
            if not here.is_dir():
                raise Refused(f"{rel} can't be written: {part} is a file.")
        else:
            here.mkdir()
        parent = here
    target = ws.inside(parent, PurePosixPath(rel).name, must_exist=False)
    if os.path.lexists(target) and not target.is_file():
        raise Refused(f"{rel} can't be written: it's a folder.")
    jsonfile.write_text(target, content, newline="")   # a temporary file in the folder, then swapped in


def build(dest: Path, exercise_folder: Path, ex: dict[str, Any]) -> Path:
    """The exercise's starting repository in ``dest/repo``, from its ``repo`` history."""
    spec = ex.get("repo") or {}
    repo = dest / REPO_DIR
    repo.mkdir(parents=True)
    (dest / ".templates").mkdir()
    _must(repo, ["init", "--quiet", "--template=" + str(dest / ".templates")])
    first = True
    for c in spec.get("commits") or []:
        branch = str(c["branch"])
        if first:
            _must(repo, ["checkout", "--quiet", "-b", branch])
        elif _git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])[0] == 0:
            _must(repo, ["checkout", "--quiet", branch])
        else:
            _must(repo, ["checkout", "--quiet", "-b", branch, str(c.get("from") or "HEAD")])
        first = False
        for rel, source in (c.get("files") or {}).items():
            _write_file(repo, rel, (exercise_folder / str(source)).read_text(encoding="utf-8"))
        for rel in c.get("delete") or []:
            _must(repo, ["rm", "--quiet", "--", safe_path(rel)])
        _must(repo, ["add", "-A"])
        _must(repo, ["commit", "--quiet", "--allow-empty", "-m", str(c["message"])])
    if spec.get("head"):
        _must(repo, ["checkout", "--quiet", str(spec["head"])])
    return repo


def replay(repo: Path, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each step run in order: ``{"line", "text", "exit", "output"}``. A failed command doesn't stop the
    session; running out of time does."""
    began = time.monotonic()
    out = []
    for s in steps:
        left = SESSION_SECONDS - (time.monotonic() - began)
        if left <= 0:
            out.append({"line": s["line"], "text": s["text"], "exit": 124,
                        "output": f"Stopped: the session took longer than {SESSION_SECONDS} seconds."})
            break
        if s["op"] == "write":
            try:
                _write_file(repo, s["path"], s["content"])
                out.append({"line": s["line"], "text": s["text"], "exit": 0, "output": f"Wrote {s['path']}."})
            except (Refused, ws.Refused, OSError) as exc:
                out.append({"line": s["line"], "text": s["text"], "exit": 1, "output": str(exc)})
            continue
        code, text = _git(repo, s["argv"], timeout=min(STEP_SECONDS, left))
        out.append({"line": s["line"], "text": s["text"], "exit": code, "output": text})
    return out


def _session(dest: Path, exercise_folder: Path, ex: dict[str, Any], text: str) -> tuple[Path, list[dict[str, Any]]]:
    steps = parse(text)
    repo = build(dest, exercise_folder, ex)
    return repo, replay(repo, steps)


def run(dest: Path, exercise_folder: Path, ex: dict[str, Any], text: str, limit: int = 200) -> dict[str, Any]:
    """What Run shows: each step with Git's output, then the repository's branches, status and history,
    as rows. ``dest`` is an empty folder the caller removes."""
    repo, steps = _session(dest, exercise_folder, ex, text)
    rows = [[s["line"], s["text"], s["exit"], s["output"].strip()[:OUTPUT_CHARS]] for s in steps]
    for label, argv in (("after: status", ["status", "--short", "--branch"]),
                        ("after: history", ["log", "--oneline", "--graph", "--all", "--decorate", "-n", "30"])):
        code, out = _git(repo, argv)
        rows.append(["", label, code, out.strip()[:OUTPUT_CHARS]])
    return {"columns": ["line", "step", "exit", "output"], "rows": rows[:limit], "more": len(rows) > limit}


# ---- checks

def _read_at(repo: Path, rel: str, ref: str | None) -> str | None:
    """The file's text at ``ref`` (a branch, tag or commit), or in the work tree when ``ref`` is None;
    None when it isn't there."""
    rel = safe_path(rel)
    if ref is None:
        try:
            return ws.read_bytes(repo, rel, MAX_FILE).decode("utf-8", "replace")
        except (ws.Refused, FileNotFoundError):
            return None
    code, text = _git(repo, ["cat-file", "-e", f"{ref}:{rel}"])
    if code:
        return None
    code, size = _git(repo, ["cat-file", "-s", f"{ref}:{rel}"])
    if code or not size.strip().isdigit() or int(size) > MAX_FILE:
        return None
    return _git(repo, ["show", f"{ref}:{rel}"])[1]


def _branch(repo: Path, name: str) -> bool:
    return _git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])[0] == 0


def _rev(repo: Path, ref: str) -> str | None:
    code, text = _git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return text.strip() if code == 0 else None


def rule_problems(rule: Any, n: int) -> list[str]:
    """What's wrong with one repo_matches rule, in words (for exercises.problems)."""
    where = f"check repo_matches rule {n}"
    if not isinstance(rule, dict):
        return [f"{where} is an object"]
    tests = [k for k in ("branch", "head", "file", "ancestor", "clean", "conflict_markers") if k in rule]
    if not tests and "ref" in rule:
        tests = ["ref"]
    if len(tests) != 1:
        return [f"{where} needs one of branch, head, file, ancestor, ref (parents or since), clean, conflict_markers"]
    t = tests[0]
    need = {"branch": ("exists",), "ancestor": ("of",)}.get(t, ())
    out = [f"{where} ({t}) needs {k}" for k in need if k not in rule]
    if t == "file":
        if sum(k in rule for k in ("exists", "contains", "not_contains", "json")) != 1:
            out.append(f"{where} (file) needs one test: exists, contains, not_contains or json")
        try:
            safe_path(str(rule["file"]))
        except Refused as exc:
            out.append(f"{where}: {exc}")
        if "json" in rule:
            from . import configlab
            out += [p.replace("config_matches", f"repo_matches rule {n} json") for p in
                    configlab.check_problems({"kind": "config_matches", "rules": rule["json"]})]
    if t == "ref" and not ("parents" in rule or ("since" in rule and ("count_min" in rule or "count_max" in rule))):
        out.append(f"{where} (ref) needs parents, or since with count_min or count_max")
    for k in ("parents", "count_min", "count_max"):
        if k in rule and (not isinstance(rule[k], int) or isinstance(rule[k], bool) or rule[k] < 0):
            out.append(f"{where}: {k} is a whole number from 0")
    if "why" in rule and not (isinstance(rule["why"], str) and rule["why"].strip()):
        out.append(f"{where}: why is a sentence")
    return out


def check_problems(c: dict[str, Any]) -> list[str]:
    if c.get("kind") != "repo_matches":
        return []
    rules = c.get("rules")
    if not isinstance(rules, list) or not rules:
        return ["check repo_matches needs rules"]
    return [p for n, r in enumerate(rules, 1) for p in rule_problems(r, n)]


def spec_problems(eid: str, ex: dict[str, Any]) -> list[str]:
    """What's wrong with the exercise's ``repo`` history, in words."""
    spec = ex.get("repo")
    if not isinstance(spec, dict) or not isinstance(spec.get("commits"), list) or not spec["commits"]:
        return [f"{eid}: a git_lab exercise needs repo.commits, the history its repository starts from"]
    out = []
    names = set(ex.get("files") or [])
    for n, c in enumerate(spec["commits"], 1):
        if not isinstance(c, dict) or not c.get("branch") or not c.get("message"):
            out.append(f"{eid}: repo commit {n} needs a branch and a message")
            continue
        for rel, source in (c.get("files") or {}).items():
            try:
                safe_path(rel)
            except Refused as exc:
                out.append(f"{eid}: repo commit {n}: {exc}")
            if source not in names:
                out.append(f"{eid}: repo commit {n} takes {source!r}, which isn't listed in files")
    return out


def _one(repo: Path, rule: dict[str, Any]) -> str | None:
    """None when the rule holds, else why not."""
    why = f" {rule['why']}" if rule.get("why") else ""
    if "branch" in rule:
        there = _branch(repo, str(rule["branch"]))
        if there != bool(rule["exists"]):
            return f"Branch {rule['branch']} {'is missing' if rule['exists'] else 'is still there'}.{why}"
    elif "head" in rule:
        code, branch = _git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        here = branch.strip() if code == 0 else "(no branch)"
        if here != str(rule["head"]):
            return f"The repository is on {here}; it should be on {rule['head']}.{why}"
    elif "file" in rule:
        ref = str(rule["ref"]) if rule.get("ref") else None
        where = f"{rule['file']} on {ref}" if ref else f"{rule['file']} in the work tree"
        text = _read_at(repo, str(rule["file"]), ref)
        if "exists" in rule:
            if (text is not None) != bool(rule["exists"]):
                return f"{where} {'is missing' if rule['exists'] else 'is still there'}.{why}"
            return None
        if text is None:
            return f"{where} is missing.{why}"
        if "contains" in rule and str(rule["contains"]) not in text:
            return f"{where} doesn't include {rule['contains']!r}.{why}"
        if "not_contains" in rule and str(rule["not_contains"]) in text:
            return f"{where} includes {rule['not_contains']!r}; it shouldn't.{why}"
        if "json" in rule:
            from . import configlab
            doc, problem = configlab.parse(text, str(rule["file"]))
            if problem:
                return f"{problem}{why}"
            got = validators.config_matches(doc, list(rule["json"]))
            if not got["passed"]:
                return f"{where}: {got['detail']}"
    elif "ancestor" in rule:
        a, b = _rev(repo, str(rule["ancestor"])), _rev(repo, str(rule["of"]))
        if not a or not b:
            return f"{rule['ancestor'] if not a else rule['of']} isn't a branch or commit in the repository.{why}"
        if _git(repo, ["merge-base", "--is-ancestor", a, b])[0] != 0:
            return f"{rule['ancestor']} isn't merged into {rule['of']}.{why}"
    elif "clean" in rule:
        _, status = _git(repo, ["status", "--porcelain"])
        merging = os.path.lexists(repo / ".git" / "MERGE_HEAD")
        dirty = bool(status.strip()) or merging
        if dirty == bool(rule["clean"]):
            return ("A merge is still in progress." if merging else
                    f"There are uncommitted changes: {status.strip().splitlines()[0] if status.strip() else ''}.") + why \
                if rule["clean"] else f"The work tree has no changes.{why}"
    elif "conflict_markers" in rule:
        _, files = _git(repo, ["ls-files", "-z"])
        for rel in [f for f in files.split("\0") if f][:2000]:
            text = _read_at(repo, rel, None) or ""
            if bool(_MARKERS.search(text)) != bool(rule["conflict_markers"]):
                return f"{rel} still has conflict markers (<<<<<<<, =======, >>>>>>>).{why}"
    elif "ref" in rule:
        rev = _rev(repo, str(rule["ref"]))
        if not rev:
            return f"{rule['ref']} isn't a branch or commit in the repository.{why}"
        if "parents" in rule:
            _, line = _git(repo, ["rev-list", "--parents", "-n", "1", rev])
            parents = len(line.split()) - 1
            if parents != rule["parents"]:
                return f"The last commit on {rule['ref']} has {parents} parent{'s' if parents != 1 else ''}; it should have {rule['parents']}.{why}"
        if "since" in rule:
            base = _rev(repo, str(rule["since"]))
            if not base:
                return f"{rule['since']} isn't a branch or commit in the repository.{why}"
            _, count = _git(repo, ["rev-list", "--count", f"{base}..{rev}"])
            ahead = int(count.strip() or 0)
            if "count_min" in rule and ahead < rule["count_min"]:
                return f"{rule['ref']} has {ahead} commit{'s' if ahead != 1 else ''} since {rule['since']}; it needs at least {rule['count_min']}.{why}"
            if "count_max" in rule and ahead > rule["count_max"]:
                return f"{rule['ref']} has {ahead} commits since {rule['since']}; it should have at most {rule['count_max']}.{why}"
    return None


def repo_matches(repo: Path, rules: list[dict[str, Any]]) -> dict[str, Any]:
    for rule in rules:
        said = _one(repo, rule)
        if said:
            return _no("repo_matches", said)
    return _ok("repo_matches", f"The repository meets all {len(rules)} rules.")


def grade(text: str, exercise_folder: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """Every check of ``ex`` on the session ``text``, replayed in a fresh repository of its own. A session
    this lab refuses fails every check with the same plain reason."""
    with tempfile.TemporaryDirectory(prefix="kf-academy-git-") as tmp:
        try:
            repo, _ = _session(Path(tmp), exercise_folder, ex, text)
        except Refused as exc:
            results = [_no(str(c["kind"]), str(exc)) for c in ex["validation"]]
        else:
            results = [repo_matches(repo, list(c["rules"])) if c["kind"] == "repo_matches"
                       else _no(str(c["kind"]), f"A Git exercise can't check {c['kind']}.") for c in ex["validation"]]
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}


def self_test(exercise_folder: Path, grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """The reference (``grading/<id>/solution.git``) must pass and every ``wrong-*.git`` fail."""
    return {"solution": grade((grading / "solution.git").read_text(encoding="utf-8"), exercise_folder, ex),
            "wrong": {p.name: grade(p.read_text(encoding="utf-8"), exercise_folder, ex)
                      for p in sorted(grading.glob("wrong-*.git"))}}
