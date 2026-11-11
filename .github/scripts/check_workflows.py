"""Fail when a workflow file under .github/workflows can't be read as a GitHub Actions workflow.

GitHub only reports a broken workflow file after it has merged (the docs site stopped deploying
when one did). This reads every file as YAML and checks the shape Actions needs: a trigger
(``on``), and jobs that each name a runner or a reusable workflow, with steps that each ``run``
or ``uses`` something. It doesn't check expressions or contexts.

A runner must be a named image (ubuntu-24.04, windows-2025-vs2026), never a ``-latest`` label: GitHub
moves those to a new image on its own schedule, and the move to Ubuntu 26.04 turned every pull
request's browser tests red at once. ``${{ matrix.<key> }}`` is checked against each value the
job's matrix gives that key.

It also holds the workflows to four supply-chain and token rules:
- Every action is pinned to a full commit (``owner/repo@<40 hex>``), or is a local ``./`` action or a
  ``docker://`` image pinned by digest: a tag or branch can be moved to other code after review.
- Every job's token is scoped: a top-level ``permissions`` or one on the job, so a job never runs
  with the repository's default token rights.
- Text someone outside the repository can set (``github.event.*``, ``github.head_ref``) and dispatch
  ``inputs.*`` never sit inside a ``run`` or a ``script``: Actions pastes them into the script before it
  runs, so they reach the step through ``env`` instead.
- A ``pull_request_target`` workflow (it has secrets and a write token) checks out only the base
  commit, never the pull request's head.

Usage: python .github/scripts/check_workflows.py [folder]   (default: .github/workflows)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


_MATRIX = re.compile(r"^\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}$")
_PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
_DIGEST = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
# What a ${{ }} inside a script must not name: the event's text (titles, branch names, comments) and
# dispatch inputs. Anything else (github.sha, needs.*.outputs, steps.*.outputs, vars, secrets) is the
# repository's own or comes through a step the workflow wrote.
_UNTRUSTED = re.compile(r"\$\{\{[^}]*\b(github\.event\.|github\.head_ref|inputs\.)[^}]*\}\}")


def runners(job: dict[str, Any]) -> list[str] | None:
    """The runner labels a job can run on, or None when its runs-on can't be read without running it."""
    runs_on = job.get("runs-on")
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    out: list[str] = []
    for label in labels:
        found = _MATRIX.match(label) if isinstance(label, str) else None
        if found:
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            values = matrix.get(found[1], []) if isinstance(matrix, dict) else []
            values = [*(values if isinstance(values, list) else [values]),
                      *(e[found[1]] for e in matrix.get("include", []) if isinstance(e, dict) and found[1] in e)]
            if not values or not all(isinstance(v, str) for v in values):
                return None
            out += values
        elif isinstance(label, str) and "${{" not in label:
            out.append(label)
        else:
            return None
    return out


def unpinned(uses: Any) -> bool:
    """True when ``uses`` names an action or reusable workflow by something that can move."""
    if not isinstance(uses, str):
        return True
    return not (uses.startswith("./") or _PINNED.match(uses) or _DIGEST.match(uses))


def problems(path: Path) -> list[str]:
    try:
        doc: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path.name}: not valid YAML: {' '.join(str(exc).split())}"]
    if not isinstance(doc, dict):
        return [f"{path.name}: not a mapping"]
    out = []
    # YAML 1.1 reads a bare `on` key as true.
    if "on" not in doc and True not in doc:
        out.append(f"{path.name}: no `on` trigger")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [*out, f"{path.name}: no jobs"]
    on = doc.get("on", doc.get(True))
    target = "pull_request_target" in (on if isinstance(on, (dict, list)) else [on])
    for name, job in jobs.items():
        where = f"{path.name}: job {name}"
        if not isinstance(job, dict):
            out.append(f"{where} is not a mapping")
            continue
        if "permissions" not in doc and "permissions" not in job:
            out.append(f"{where} has no permissions; give it (or the workflow) the token rights it needs, "
                       "for example contents: read")
        if "uses" in job:   # a reusable workflow
            if unpinned(job["uses"]):
                out.append(f"{where} uses {job['uses']}, which isn't pinned to a commit")
            continue
        if "runs-on" not in job:
            out.append(f"{where} has no runs-on")
        else:
            labels = runners(job)
            if labels is None:
                out.append(f"{where}: runs-on can't be checked; name the image or use ${{{{ matrix.<key> }}}}")
            for label in labels or []:
                if label.endswith("-latest"):
                    out.append(f"{where} runs on {label}, which GitHub moves to new images on its own; "
                               "name the image (for example ubuntu-24.04 or windows-2025-vs2026)")
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            out.append(f"{where} has no steps")
            continue
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or ("run" in step) == ("uses" in step):
                out.append(f"{where}, step {i}: needs exactly one of run or uses")
                continue
            if "uses" in step and unpinned(step["uses"]):
                out.append(f"{where}, step {i}: {step['uses']} isn't pinned to a commit (owner/repo@<40 hex>)")
            with_ = step.get("with") if isinstance(step.get("with"), dict) else {}
            for key, script in (("run", step.get("run")), ("script", with_.get("script"))):
                found = _UNTRUSTED.search(script) if isinstance(script, str) else None
                if found:
                    out.append(f"{where}, step {i}: {found[0]} is pasted into its {key}; pass it through env")
            if target and str(step.get("uses", "")).startswith("actions/checkout@"):
                ref = str(with_.get("ref", ""))
                if ref and ref.replace(" ", "") not in ("${{github.sha}}", "${{github.event.pull_request.base.sha}}"):
                    out.append(f"{where}, step {i}: a pull_request_target workflow checks out {ref}; "
                               "check out only the base (github.sha)")
    return out


def main(argv: list[str]) -> int:
    folder = Path(argv[1]) if len(argv) > 1 else Path(".github/workflows")
    files = sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])
    if not files:
        print(f"No workflow files in {folder}.")
        return 1
    found = [p for f in files for p in problems(f)]
    for p in found:
        print(f"::error::{p}")
    print(f"{len(files)} workflow files read, {len(found)} problems.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
