"""The type check (mypy) for the engine, as a ratchet. The engine has hundreds of type errors from
before it was checked, so the job doesn't ask for zero: it fails when a file has more errors of one
kind than sidecar/mypy-baseline/ records, and passes otherwise. Fixing errors lowers the counts;
``--update`` writes the new, lower ones, so they can't creep back.

The baseline is one small JSON file per source file (sidecar/mypy-baseline/<path>.json, {code: n}),
so pull requests that clear different modules each delete or edit their own file and never
conflict with one another. A file with no errors has no baseline file.

    python .github/scripts/mypy_ratchet.py            # run mypy in sidecar/ and compare (CI)
    python .github/scripts/mypy_ratchet.py --update   # after fixing errors: record the lower counts

Settings (which files, Python version) are in sidecar/pyproject.toml [tool.mypy]. Errors are counted
per file and error code, not per line, so moving code within a file doesn't break the check. Two
changes do shift the counts, so run ``--update`` in the same pull request: renaming or moving a file
(its errors count as new under the new name), and moving a package version in constraints.txt or
mypy's own in ci-constraints.txt (new type information can add or remove errors).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SIDECAR = Path(__file__).resolve().parents[2] / "sidecar"
BASELINE = SIDECAR / "mypy-baseline"
SUFFIX = ".json"


def load(folder: Path = BASELINE) -> dict[str, dict[str, int]]:
    """The recorded counts, {file: {code: n}}, from one JSON file per source file."""
    out: dict[str, dict[str, int]] = {}
    for f in sorted(folder.rglob("*" + SUFFIX)):
        out[f.relative_to(folder).as_posix()[:-len(SUFFIX)]] = json.loads(f.read_text(encoding="utf-8"))
    return out


def save(counts: dict[str, dict[str, int]], folder: Path = BASELINE) -> None:
    """Write ``counts`` as one file per source file, removing the files of sources with no errors."""
    for f in folder.rglob("*" + SUFFIX):
        if f.relative_to(folder).as_posix()[:-len(SUFFIX)] not in counts:
            f.unlink()
    for file, codes in counts.items():
        target = folder / (file + SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(codes, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
# fabric_desktop/cli.py:223: error: Incompatible types in assignment (...)  [assignment]
ERROR = re.compile(r"^(?P<file>[^:\s]+\.pyi?):\d+(?::\d+)?: error: (?P<message>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$")


def count(output: str) -> dict[str, dict[str, int]]:
    """mypy's errors as {file: {code: n}}; an error without a code counts as "misc"."""
    found: Counter[tuple[str, str]] = Counter()
    for line in output.splitlines():
        m = ERROR.match(line.strip())
        if m:
            found[(m["file"].replace("\\", "/"), m["code"] or "misc")] += 1
    out: dict[str, dict[str, int]] = {}
    for (file, code), n in sorted(found.items()):
        out.setdefault(file, {})[code] = n
    return out


def compare(now: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    """What got worse (fails the check) and what got better (counts --update can lower)."""
    worse, better = [], []
    for file in sorted(set(now) | set(baseline)):
        for code in sorted(set(now.get(file, {})) | set(baseline.get(file, {}))):
            n, was = now.get(file, {}).get(code, 0), baseline.get(file, {}).get(code, 0)
            if n > was:
                worse.append(f"{file}: {n} [{code}] errors, {was} before")
            elif n < was:
                better.append(f"{file}: {n} [{code}] errors, {was} before")
    return worse, better


def problem(returncode: int, stdout: str, stderr: str) -> str:
    """Why mypy's run can't be counted, or "" when it can. Exit 1 means type errors, so an exit 1
    with none of them is mypy not running at all, such as "No module named mypy" when it isn't
    installed; counting that as 0 errors would pass the check without checking anything."""
    if returncode == 0 or (returncode == 1 and count(stdout)):
        return ""
    if "No module named mypy" in stderr:
        return (f"mypy isn't installed for {sys.executable}. Install the pinned version: "
                "pip install -c sidecar/constraints.txt -c sidecar/ci-constraints.txt mypy")
    # 2: mypy itself failed (bad settings, a crash), not type errors
    return f"mypy failed (exit {returncode}) without reporting type errors:\n{stdout}{stderr}"


def run_mypy() -> str:
    done = subprocess.run([sys.executable, "-m", "mypy", "--no-error-summary"], cwd=SIDECAR,
                          capture_output=True, text=True)
    if why := problem(done.returncode, done.stdout, done.stderr):
        sys.exit(why)
    return done.stdout


def main(argv: list[str]) -> int:
    output = run_mypy()
    now = count(output)
    if "--update" in argv:
        save(now)
        print(f"Recorded {sum(sum(c.values()) for c in now.values())} errors in {BASELINE.name}/.")
        return 0
    baseline = load()
    worse, better = compare(now, baseline)
    total, before = sum(sum(c.values()) for c in now.values()), sum(sum(c.values()) for c in baseline.values())
    if worse:
        print("New type errors. Fix them (or, if mypy is wrong, add a `# type: ignore[code]` that says why):")
        print("\n".join(f"  {w}" for w in worse))
        files = {w.split(":", 1)[0] for w in worse}
        print("\nmypy's errors in those files:")
        print("\n".join(line for line in output.splitlines() if line.split(":", 1)[0].replace("\\", "/") in files))
        return 1
    print(f"No new type errors: {total} now, {before} in {BASELINE.name}/.")
    if better:
        print("Fewer errors than recorded; lower (or delete) those files in sidecar/mypy-baseline/, or run "
              "`python .github/scripts/mypy_ratchet.py --update`, to lock that in:")
        print("\n".join(f"  {b}" for b in better))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
