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

A line can also carry `pragma: allowlist secret` in a comment, which detect-secrets skips.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def problems(doc: dict, found: Iterable[tuple[str, Any]]) -> list[str]:
    """What check reports: unreviewed baseline entries, then findings the baseline doesn't list."""
    out = []
    reviewed = set()
    for key, entry in _entries(doc):
        if entry.get("is_secret") is False:
            reviewed.add(key)
        else:
            out.append(f"::error file={key[0]},line={entry.get('line_number', 1)}::{BASELINE} lists a {key[1]} here "
                       'that nobody has reviewed: set "is_secret": false if it isn\'t a secret.')
    for name, secret in sorted(found, key=lambda pair: (pair[0], pair[1].line_number or 0)):
        if (name, secret.type, secret.secret_hash) not in reviewed:
            out.append(f"::error file={name},line={secret.line_number}::Looks like a secret ({secret.type}). {HOW}")
    return out


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
    if argv == ["baseline"]:
        return rebuild()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
