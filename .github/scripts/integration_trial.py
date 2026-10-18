"""Trial-merge open pull requests onto a base branch and report conflicts.

Changes nothing that is shared: it reads PR heads from refs/pr/<n> (fetch them first with
`git fetch origin '+refs/pull/*/head:refs/pr/*'`) and merges in a temporary worktree that is
deleted afterwards. See docs/release-management/README.md, "The integration cycle".

    python3 .github/scripts/integration_trial.py --base origin/main 140 147 138 142 148
    python3 .github/scripts/integration_trial.py --base origin/main --json 140 147 > trial.json

For each PR, in the order given:
  alone      whether it merges cleanly onto the base by itself
  in order   whether it merges cleanly after every earlier PR in the list that merged
  files      the conflicting files, if any
A PR that conflicts in order is skipped, so later PRs are tried without it.
"""
import argparse
import json
import subprocess
import sys
import tempfile


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def conflicts_alone(base, ref):
    r = git("merge-tree", "--write-tree", "--name-only", base, ref, check=False)
    if r.returncode == 0:
        return []
    # Output: tree id, the conflicted file names, a blank line, then messages.
    lines = r.stdout.splitlines()[1:]
    return lines[: lines.index("")] if "" in lines else lines


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default="origin/main")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("prs", nargs="+", type=int)
    a = p.parse_args()

    base_sha = git("rev-parse", a.base).stdout.strip()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        git("worktree", "add", "--detach", tmp, base_sha)
        try:
            git("config", "user.email", "integration-trial@localhost", cwd=tmp)
            git("config", "user.name", "integration trial", cwd=tmp)
            for n in a.prs:
                ref = f"refs/pr/{n}"
                if git("rev-parse", "--verify", "-q", ref, check=False).returncode:
                    results.append({"pr": n, "error": f"{ref} not found; fetch PR heads first"})
                    continue
                head = git("rev-parse", ref).stdout.strip()
                alone = conflicts_alone(base_sha, head)
                m = git("merge", "--no-ff", "--no-edit", head, cwd=tmp, check=False)
                in_order = []
                if m.returncode:
                    in_order = git("diff", "--name-only", "--diff-filter=U", cwd=tmp).stdout.split()
                    git("merge", "--abort", cwd=tmp, check=False)
                results.append({"pr": n, "head": head[:7], "alone": "clean" if not alone else "conflict",
                                "in_order": "clean" if not in_order else "conflict",
                                "files": sorted(set(alone) | set(in_order))})
            tip = git("rev-parse", "HEAD", cwd=tmp).stdout.strip()
        finally:
            git("worktree", "remove", "--force", tmp, check=False)

    if a.json:
        print(json.dumps({"base": base_sha, "integrated_tip": tip, "results": results}, indent=2))
        return
    print(f"base {base_sha[:7]}")
    print(f"{'PR':>6}  {'head':7}  {'alone':8}  {'in order':8}  files")
    for r in results:
        if "error" in r:
            print(f"#{r['pr']:>5}  {r['error']}")
        else:
            print(f"#{r['pr']:>5}  {r['head']:7}  {r['alone']:8}  {r['in_order']:8}  {' '.join(r['files'])}")
    print(f"integrated tip {tip[:7]} (temporary; deleted with its worktree, the commits stay as dangling objects)")


if __name__ == "__main__":
    main()
