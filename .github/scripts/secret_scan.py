"""Fails CI when a file gains something that looks like a secret (a token, a key, a password in a
connection string). Uses detect-secrets, pinned with hashes in .github/requirements/secret-scan.txt.

.secrets.baseline is the reviewed allow-list: every finding already in the repository, each one
checked by hand and marked "is_secret": false (test fixtures, hashes, redaction cases). A finding is
matched by file, kind and the hash of the value, not by line, so moving a line doesn't need a new review.

    check [FILE ...]  scan FILEs (every tracked file when none are given) and exit 1 on a finding the
                      baseline doesn't list, or on a baseline entry nobody has reviewed. Prints where
                      and what kind, never the value.
    baseline          rescan every tracked file and rewrite .secrets.baseline, keeping each entry's
                      review. New entries have no "is_secret" until someone reviews them, so check
                      fails until then: set "is_secret": false when it isn't a secret; when it is,
                      remove it from the file and revoke it.

    added BASE_FILE   a warning for each entry .secrets.baseline gains over BASE_FILE (the base
                      branch's copy), and for each filter or plugin it adds or removes, so a pull
                      request's reviewers see what it lets through.

    pragmas           read a `git diff -U0` on stdin and warn for each added line carrying the pragma below.

    commits BASE HEAD scan each file every commit in BASE..HEAD (merges left out) adds or changes, as that
                      commit has it, and warn for a finding the baseline doesn't list that HEAD no longer
                      has: a value a pull request adds and later takes out is still in its history.

A line can also carry `pragma: allowlist secret` in a comment, which detect-secrets skips.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ".secrets.baseline"
HOW = ("If it isn't a secret, run `python .github/scripts/secret_scan.py baseline` and mark the new entry "
       '"is_secret": false (or add `pragma: allowlist secret` to the line). If it is one, remove it and revoke it: '
       "it is in the branch's history now.")

Key = tuple[str, str, str]  # file, kind, hash of the value


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode("utf-8").split("\0") if name and name != BASELINE]


# detect-secrets is imported where it's used, so the engine's tests can check this file's logic without it.


def _scan(files: list[str]) -> Any:
    from detect_secrets import SecretsCollection

    found = SecretsCollection()  # names relative to the working folder, ROOT (main)
    found.scan_files(*[f for f in files if f != BASELINE and (ROOT / f).is_file()], num_processors=os.cpu_count())
    return found


def _entries(doc: dict) -> list[tuple[Key, dict]]:
    return [((name, entry["type"], entry["hashed_secret"]), entry)
            for name, rows in sorted((doc.get("results") or {}).items()) for entry in rows]


def _prop(value: object) -> str:
    """A workflow command's property value (a file name can hold any of these)."""
    return (str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            .replace(":", "%3A").replace(",", "%2C"))


def _at(name: str, line: object) -> str:
    return f"file={_prop(name)},line={_prop(line)}"


def problems(doc: dict, found: Iterable[tuple[str, Any]]) -> list[str]:
    """What check reports: unreviewed baseline entries, then findings the baseline doesn't list."""
    out = []
    reviewed = set()
    for key, entry in _entries(doc):
        if entry.get("is_secret") is False:
            reviewed.add(key)
        else:
            out.append(f"::error {_at(key[0], entry.get('line_number', 1))}::{BASELINE} lists a {key[1]} here "
                       'that nobody has reviewed: set "is_secret": false if it isn\'t a secret.')
    for name, secret in sorted(found, key=lambda pair: (pair[0], pair[1].line_number or 0)):
        if (name, secret.type, secret.secret_hash) not in reviewed:
            out.append(f"::error {_at(name, secret.line_number)}::Looks like a secret ({secret.type}). {HOW}")
    return out


def newly_allowed(doc: dict, before: dict) -> list[str]:
    """A warning for each entry ``doc`` lists that ``before`` (the base branch's baseline) didn't, so the
    pull request that lets a value through shows it to its reviewers."""
    known = {key for key, _ in _entries(before)}
    out = [f"::warning {_at(key[0], entry.get('line_number', 1))}::This pull request adds a {key[1]} here to "
           f"{BASELINE}, so the secret scan lets it through. Check it isn't a real secret."
           for key, entry in _entries(doc) if key not in known]
    if before:  # the scan's own settings: a filter added or a plugin dropped can let values through unseen
        for field in ("filters_used", "plugins_used"):
            was, now = _settings(before, field), _settings(doc, field)
            out += [f"::warning file={_prop(BASELINE)}::This pull request {verb} the secret scan's {field}: "
                    f"{annotation(item)}. Check it doesn't hide a real secret."
                    for verb, items in (("adds to", now - was), ("removes from", was - now)) for item in sorted(items)]
    return out


def _settings(doc: dict, field: str) -> set[str]:
    return {json.dumps(item, sort_keys=True) for item in doc.get(field) or []}


def annotation(text: str) -> str:
    """``text`` as a workflow command's message."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


PRAGMA = "pragma: allowlist secret"


def pragmas(diff: str) -> list[str]:
    """A warning for each line a ``git diff -U0`` adds that carries the pragma, which hides it from the scan.

    Each hunk header says how many lines it removes and adds, so its body is read by count: an added line
    that itself starts with "++ " or "@@ " is content, never taken for a file or hunk header."""
    out, name, line, old, new = [], "", 0, 0, 0
    for text in diff.splitlines():
        if old or new:  # inside a hunk's body
            if text.startswith("-") and old:
                old -= 1
            elif text.startswith("+") and new:
                if name and PRAGMA in text:
                    out.append(f"::warning {_at(name, line)}::This pull request adds a line the secret scan skips "
                               f"(`{PRAGMA}`). Check it isn't a real secret.")
                line += 1
                new -= 1
            elif not text.startswith("\\"):  # "\ No newline at end of file" belongs to the line before
                old = new = 0  # a malformed hunk: back to reading headers
            continue
        if text.startswith("diff --git "):
            name = ""
        elif text.startswith("+++ "):
            # git quotes a name with unusual characters ("b/..."); the warning names it as quoted
            name = text[6:] if text.startswith("+++ b/") else text[7:-1] if text.startswith('+++ "b/') else ""
        elif hunk := re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", text):
            old = int(hunk[1]) if hunk[1] is not None else 1
            line = int(hunk[2])
            new = int(hunk[3]) if hunk[3] is not None else 1
    return out


def removed_later(doc: dict, found: Iterable[tuple[str, str, Any]], final: set[Key]) -> list[str]:
    """A warning for each finding (commit, file, secret) the baseline doesn't list and HEAD no longer has
    (``final``: what HEAD's versions of the same files hold), once per value, at the first commit adding it."""
    reviewed = {key for key, entry in _entries(doc) if entry.get("is_secret") is False}
    out, seen = [], set()
    for commit, name, secret in found:
        key = (name, secret.type, secret.secret_hash)
        if key in reviewed or key in final or key in seen:
            continue
        seen.add(key)
        out.append(f"::warning {_at(name, secret.line_number)}::Looks like a secret ({secret.type}) in commit "
                   f"{_prop(commit[:12])}. A later commit took it out, but it stays in this pull request's history, "
                   "which anyone who can read the repository can see. If it is a secret, revoke it.")
    return out


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def commits(base: str, head: str) -> int:
    from detect_secrets.core import baseline as baselines
    from detect_secrets.settings import configure_settings_from_baseline

    doc = baselines.load_from_file(str(ROOT / BASELINE))
    configure_settings_from_baseline(doc, filename=BASELINE)
    shas = _git("rev-list", "--no-merges", "--reverse", head, "--not", base).decode().split()
    found, names = [], set()
    for sha in shas:
        out = _git("diff-tree", "--root", "--no-commit-id", "-r", "-z", "--diff-filter=AM", sha)
        raw = [part for part in out.split(b"\0") if part]  # ":old new old-blob new-blob status", name, ...
        blobs = {}  # name -> the blob this commit gives it; a submodule (mode 160000) has no file to scan
        for meta, name in zip(raw[0::2], raw[1::2], strict=True):
            _, mode, _, blob, _ = meta.decode().split(" ")
            if mode != "160000" and name.decode("utf-8") != BASELINE:
                blobs[name.decode("utf-8")] = blob
        changed = sorted(blobs)
        with tempfile.TemporaryDirectory() as tmp:  # each file as this commit has it, under its own name
            for name in changed:
                (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / name).write_bytes(_git("cat-file", "blob", blobs[name]))
            os.chdir(tmp)  # detect-secrets names files relative to the working folder
            try:
                from detect_secrets import SecretsCollection

                scan = SecretsCollection()
                scan.scan_files(*changed, num_processors=os.cpu_count())
            finally:
                os.chdir(ROOT)
        found += sorted(((sha, name, secret) for name, secret in scan), key=lambda f: (f[1], f[2].line_number or 0))
        names.update(changed)
    final = {(name, secret.type, secret.secret_hash) for name, secret in _scan(sorted(names))}
    lines = removed_later(doc, found, final)
    for line in lines:
        print(line)
    if not lines:
        print(f"Nothing that looks like a secret was added and later taken out in {len(shas)} commit(s).")
    return 0  # a warning, not a failure: failing can't take it out of the history, and a false positive
    # there could never be marked reviewed (the baseline lists only what the tree holds now)


def check(files: list[str]) -> int:
    from detect_secrets.core import baseline as baselines
    from detect_secrets.settings import configure_settings_from_baseline

    doc = baselines.load_from_file(str(ROOT / BASELINE))
    configure_settings_from_baseline(doc, filename=BASELINE)
    found = _scan(files or tracked())
    lines = problems(doc, found)
    for line in lines:
        print(line)
    if lines:
        return 1
    print(f"No new secrets in {f'{len(files)} changed file(s)' if files else 'any tracked file'}.")
    return 0


def rebuild() -> int:
    from detect_secrets.core import baseline as baselines
    from detect_secrets.settings import configure_settings_from_baseline, default_settings

    path = ROOT / BASELINE
    old = baselines.load_from_file(str(path)) if path.exists() else None
    if old:  # the same plugins and filters as before
        configure_settings_from_baseline(old, filename=BASELINE)
        doc = baselines.format_for_output(_scan(tracked()))
    else:
        with default_settings():
            doc = baselines.format_for_output(_scan(tracked()))
    doc.pop("generated_at", None)  # the file changes only when what it lists does
    reviews = {key: entry["is_secret"] for key, entry in _entries(old or {}) if "is_secret" in entry}
    for key, entry in _entries(doc):
        if key in reviews:
            entry["is_secret"] = reviews[key]
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    new = sum(1 for key, entry in _entries(doc) if "is_secret" not in entry)
    print(f"{BASELINE}: {len(_entries(doc))} entries, {new} to review.")
    return 0


def main(argv: list[str]) -> int:
    os.chdir(ROOT)  # detect-secrets names files relative to the working folder
    if argv[:1] == ["check"]:
        return check(argv[1:])
    if argv[:1] == ["added"] and len(argv) == 2:
        try:
            before = json.loads(Path(argv[1]).read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            before = {}  # no baseline on the base branch: every entry is new
        for line in newly_allowed(json.loads((ROOT / BASELINE).read_text(encoding="utf-8")), before):
            print(line)
        return 0
    if argv[:1] == ["commits"] and len(argv) == 3:
        return commits(argv[1], argv[2])
    if argv == ["pragmas"]:
        for line in pragmas(sys.stdin.read()):
            print(line)
        return 0
    if argv == ["baseline"]:
        return rebuild()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
