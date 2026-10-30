"""The type check (mypy) for the engine, as a ratchet. The engine has hundreds of type errors from
before it was checked, so the job doesn't ask for zero: it fails when a file has more errors of one
kind than sidecar/mypy-baseline.json records, and passes otherwise. Fixing errors lowers the counts;
``--update`` writes the new, lower ones, so they can't creep back.

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
BASELINE = SIDECAR / "mypy-baseline.json"
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
        BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Recorded {sum(sum(c.values()) for c in now.values())} errors in {BASELINE.name}.")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    worse, better = compare(now, baseline)
    total, before = sum(sum(c.values()) for c in now.values()), sum(sum(c.values()) for c in baseline.values())
    if worse:
        print("New type errors. Fix them (or, if mypy is wrong, add a `# type: ignore[code]` that says why):")
        print("\n".join(f"  {w}" for w in worse))
        files = {w.split(":", 1)[0] for w in worse}
        print("\nmypy's errors in those files:")
        print("\n".join(line for line in output.splitlines() if line.split(":", 1)[0].replace("\\", "/") in files))
        return 1
    print(f"No new type errors: {total} now, {before} in {BASELINE.name}.")
    if better:
        print("Fewer errors than recorded; run `python .github/scripts/mypy_ratchet.py --update` to lock that in:")
        print("\n".join(f"  {b}" for b in better))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
